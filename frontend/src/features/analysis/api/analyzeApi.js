import { httpClient } from '../../../services/httpClient';

/**
 * Maps the draft onto the path-based AnalyzeRequest schema. Requires the
 * artifacts to already exist on the API host, so the browser flow uses
 * runAnalysisUpload() instead; this stays for scripted/server-side runs.
 */
export function toAnalyzeRequest(draft, paths = {}, projectId) {
  return {
    source_type: paths.requirementsText ? 'text' : 'document',
    requirements_path: paths.requirementsPath ?? '',
    requirements_text: paths.requirementsText ?? '',
    codebase_path: paths.codebasePath ?? '',
    source_preprocessor: draft.sourcePreprocessor,
    target_preprocessor: draft.targetPreprocessor,
    classifier: draft.classifier,
    n_results: Number(draft.nResults),
    source_output_level: draft.sourceOutputLevel ?? null,
    target_output_level: draft.targetOutputLevel ?? null,
    dependency_expansion_depth: Number(draft.dependencyExpansionDepth),
    analysis_mode: projectId ? 'project' : 'session',
    project_id: projectId ?? null,
  };
}

/** Path-based run. Requires artifacts to already exist on the API host. */
export const runAnalysis = (draft, paths, projectId) => httpClient('/analyze', {
  method: 'POST',
  body: JSON.stringify(toAnalyzeRequest(draft, paths, projectId)),
});

/**
 * Builds the multipart body for POST /analyze/upload.
 *
 * Every file from every artifact is appended in order, and each artifact
 * records the indexes it owns. `file_paths` carries each file's relative path
 * so folder uploads keep their directory structure on the server.
 */
export function toUploadFormData(draft, artifacts, sides) {
  const body = new FormData();
  const manifest = [];
  const paths = [];
  let index = 0;

  for (const artifact of artifacts) {
    const entry = {
      id: artifact.id,
      name: artifact.name,
      kind: artifact.kind,
    };

    if (artifact.text !== undefined) {
      entry.text = artifact.text;
    } else {
      entry.file_indexes = artifact.entries.map(() => index++);
      for (const item of artifact.entries) {
        body.append('files', item.file);
        paths.push(item.path);
      }
    }

    manifest.push(entry);
  }

  body.append('artifacts', JSON.stringify(manifest));
  body.append('file_paths', JSON.stringify(paths));
  body.append('source_artifact_ids', JSON.stringify(sides.sourceIds));
  body.append('target_artifact_ids', JSON.stringify(sides.targetIds));
  body.append('source_preprocessor', draft.sourcePreprocessor);
  body.append('target_preprocessor', draft.targetPreprocessor);
  if (draft.sourceOutputLevel) body.append('source_output_level', draft.sourceOutputLevel);
  if (draft.targetOutputLevel) body.append('target_output_level', draft.targetOutputLevel);
  body.append('classifier', draft.classifier);
  body.append('n_results', String(Number(draft.nResults)));
  body.append('dependency_expansion_depth', String(Number(draft.dependencyExpansionDepth)));
  if (draft.analysisMode) body.append('analysis_mode', draft.analysisMode);

  return body;
}

export const runAnalysisUpload = (draft, artifacts, sides) => httpClient('/analyze/upload', {
  method: 'POST',
  body: toUploadFormData(draft, artifacts, sides),
});

/** First non-empty line of an element's content, used as a display label. */
export function toLabel(content, fallback, maxLength = 120) {
  const firstLine = (content ?? '').split('\n').map((line) => line.trim()).find(Boolean);
  if (!firstLine) return fallback;
  return firstLine.length > maxLength ? `${firstLine.slice(0, maxLength - 1)}…` : firstLine;
}

/**
 * Reshapes the raw AnalyzeResponse for the results view: links are grouped by
 * requirement (the API returns up to n_results per source) and summary keys are
 * mapped to the names the UI reads.
 */
export function normalizeResult(response) {
  const links = response?.trace_links ?? [];
  const unimplemented = response?.unimplemented ?? [];
  const apiSummary = response?.summary ?? {};
  const sourceElements = response?.source_elements ?? [];
  const targetElements = response?.target_elements ?? [];

  // Adjacency in both directions, so selecting an element on either side can
  // resolve its counterparts in one lookup.
  const targetsBySource = new Map();
  const sourcesByTarget = new Map();
  for (const link of links) {
    if (!targetsBySource.has(link.source_id)) targetsBySource.set(link.source_id, []);
    targetsBySource.get(link.source_id).push(link);
    if (!sourcesByTarget.has(link.target_id)) sourcesByTarget.set(link.target_id, []);
    sourcesByTarget.get(link.target_id).push(link);
  }

  const groups = new Map();
  for (const link of links) {
    if (!groups.has(link.source_id)) {
      groups.set(link.source_id, {
        source_id: link.source_id,
        source_content: link.source_content ?? '',
        label: toLabel(link.source_content, link.source_id),
        links: [],
      });
    }
    groups.get(link.source_id).links.push(link);
  }

  const requirements = [...groups.values()]
    .map((group) => {
      const sorted = [...group.links].sort((a, b) => b.confidence - a.confidence);
      return { ...group, links: sorted, best: sorted[0] };
    })
    .sort((a, b) => b.best.confidence - a.best.confidence);

  return {
    requirements,
    links,
    sourceElements,
    targetElements,
    targetsBySource,
    sourcesByTarget,
    unimplemented,
    summary: {
      requirements: apiSummary.total_source_elements ?? requirements.length,
      trace_links: apiSummary.total_links ?? links.length,
      high_confidence: apiSummary.high_confidence ?? 0,
      to_review: requirements.filter((item) => item.best.confidence_level !== 'high').length,
      unimplemented: apiSummary.unimplemented_count ?? unimplemented.length,
    },
  };
}
