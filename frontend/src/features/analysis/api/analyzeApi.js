import { httpClient } from '../../../services/httpClient';
import { sideLabels } from '../sideLabels';

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
export function toUploadFormData(draft, artifacts, sides, projectId) {
  const body = new FormData();
  const manifest = [];
  const paths = [];
  let index = 0;

  // An artifact left off both sides is not part of this run, so there is no
  // reason to send its bytes.
  const used = new Set([...sides.sourceIds, ...sides.targetIds]);

  for (const artifact of artifacts.filter((item) => used.has(item.id))) {
    const entry = {
      id: artifact.id,
      name: artifact.name,
      kind: artifact.kind,
    };

    if (artifact.sourceId !== undefined) {
      // Nothing to upload: the server fetches this side from the source the
      // project is connected to, and records the commit it came from.
      entry.source_id = artifact.sourceId;
    } else if (artifact.text !== undefined) {
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
  body.append('summarize_elements', String(Boolean(draft.summarizeElements)));
  if (draft.analysisMode) body.append('analysis_mode', draft.analysisMode);
  // The project a connected source belongs to, and what makes the embedding
  // cache persist between runs of the same project.
  if (projectId) body.append('project_id', String(projectId));

  return body;
}

/**
 * Starts a run and returns the job to watch, not the result.
 *
 * A real analysis takes minutes; waiting on the request meant the browser gave
 * up before the server did and the answer was lost even though the work
 * succeeded.
 */
export const startAnalysisUpload = (draft, artifacts, sides, projectId) => httpClient(
  '/analyze/upload',
  { method: 'POST', body: toUploadFormData(draft, artifacts, sides, projectId) },
);

// Often enough to feel live, rarely enough not to hammer the server.
const POLL_MS = 1500;

/**
 * Watches a job to the end, reporting progress as it goes.
 *
 * The token is what lets a run started without an account be followed: job ids
 * run in sequence, so holding one proves nothing on its own.
 */
export function watchJob(jobId, token, onProgress) {
  return new Promise((resolve, reject) => {
    const poll = async () => {
      try {
        const query = token ? `?token=${encodeURIComponent(token)}` : '';
        const job = await httpClient(`/jobs/${jobId}${query}`);
        onProgress?.(job);

        if (job.state === 'succeeded') { resolve(job.result); return; }
        if (job.state === 'failed') { reject(new Error(job.error ?? 'The run failed.')); return; }
        setTimeout(poll, POLL_MS);
      } catch (requestError) {
        reject(requestError);
      }
    };
    poll();
  });
}

/** Start a run and wait for it, reporting progress while it works. */
export async function runAnalysisUpload(draft, artifacts, sides, projectId, onProgress) {
  const started = await startAnalysisUpload(draft, artifacts, sides, projectId);
  const result = await watchJob(started.job_id, started.token, onProgress);
  // The job stores the pipeline's own response, which already carries the
  // upload id - but a failed save should still know where the files are.
  return { ...result, upload_id: result.upload_id ?? started.upload_id };
}

/** First non-empty line of an element's content, used as a display label. */
export function toLabel(content, fallback, maxLength = 120) {
  const firstLine = (content ?? '').split('\n').map((line) => line.trim()).find(Boolean);
  if (!firstLine) return fallback;
  return firstLine.length > maxLength ? `${firstLine.slice(0, maxLength - 1)}…` : firstLine;
}

/**
 * Reshapes the raw AnalyzeResponse for the results view: links are grouped by
 * source element (the API returns up to n_results per source) and summary keys
 * are mapped to the names the UI reads.
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
    // What this particular run traced between, so headings, table columns and
    // exports name the actual artifacts instead of always saying "requirement".
    labels: {
      source: sideLabels(sourceElements, 'source'),
      target: sideLabels(targetElements, 'target'),
    },
    summary: {
      requirements: apiSummary.total_source_elements ?? requirements.length,
      trace_links: apiSummary.total_links ?? links.length,
      high_confidence: apiSummary.high_confidence ?? 0,
      to_review: requirements.filter((item) => item.best.confidence_level !== 'high').length,
      unimplemented: apiSummary.unimplemented_count ?? unimplemented.length,
    },
  };
}
