import { API_BASE_URL, httpClient } from '../../../services/httpClient';
import { getToken } from '../../../services/authToken';

export const listProjects = () => httpClient('/projects');

export const getProject = (projectId) => httpClient(`/projects/${projectId}`);

export const createProject = ({ name, description }) => httpClient('/projects', {
  method: 'POST',
  body: JSON.stringify({ project_name: name, description: description ?? null }),
});

export const deleteProject = (projectId) => httpClient(`/projects/${projectId}`, {
  method: 'DELETE',
});

export const getAnalysis = (analysisId) => httpClient(`/analyses/${analysisId}`);

/**
 * Every analysis in a project, or across all of them. An analysis is one
 * relation between two sides with its own settings, versions and graph.
 */
export const listConfigs = (projectId) => httpClient(
  projectId ? `/configs?project_id=${projectId}` : '/configs',
);

/** One changed file's text, earlier version against later, as unified diff lines. */
export const getLineDiff = (projectId, configId, { base, head, role, path, oldPath }) => {
  const query = new URLSearchParams({ base, head, role, path });
  if (oldPath) query.set('old_path', oldPath);
  return httpClient(`/projects/${projectId}/configs/${configId}/report/diff?${query}`);
};

/** One analysis, with both sides and the files each holds now. */
export const getConfig = (projectId, configId) => httpClient(
  `/projects/${projectId}/configs/${configId}`,
);

/** Deletes an analysis with its sides, versions, runs and pins. */
export const deleteConfig = (projectId, configId) => httpClient(
  `/projects/${projectId}/configs/${configId}`,
  { method: 'DELETE' },
);

/** Every state an analysis's files have been in, newest first. */
export const listVersions = (projectId, configId) => httpClient(
  `/projects/${projectId}/configs/${configId}/versions`,
);

/**
 * What changed between two versions of an analysis. Left out, the later
 * version is the newest and the earlier one the version before it.
 */
export const getReport = (projectId, configId, { base, head } = {}) => {
  const query = new URLSearchParams();
  if (base) query.set('base', base);
  if (head) query.set('head', head);
  const suffix = query.toString();
  return httpClient(`/projects/${projectId}/configs/${configId}/report${suffix ? `?${suffix}` : ''}`);
};

export const deleteAnalysis = (analysisId) => httpClient(`/analyses/${analysisId}`, {
  method: 'DELETE',
});

/**
 * Run an analysis again over the same files with the same settings, storing
 * the outcome as another run in the same version. Runs the full pipeline, so
 * it can take minutes.
 */
export const rerunAnalysis = (analysisId, { note } = {}) => httpClient(
  `/analyses/${analysisId}/rerun`,
  {
    method: 'POST',
    body: JSON.stringify({ note: note || null }),
  },
);

/**
 * The artifact kind stored for one side of a saved run.
 *
 * A stored config names its preprocessors and levels by key; which kind the
 * side held is what turns those keys back into the labels the user chose from.
 */
export const kindOfSide = (artifacts, role) =>
  artifacts?.find((artifact) => artifact.role === role)?.artifact_type ?? null;

/**
 * Turns the run draft into the config the API stores alongside a result.
 * Mirrors toAnalyzeRequest() in analyzeApi, so a reopened analysis reports the
 * same settings the run was started with.
 */
export function toAnalysisConfig(draft) {
  return {
    source_preprocessor: draft.sourcePreprocessor,
    target_preprocessor: draft.targetPreprocessor,
    source_output_level: draft.sourceOutputLevel ?? null,
    target_output_level: draft.targetOutputLevel ?? null,
    classifier: draft.classifier,
    n_results: Number(draft.nResults),
    dependency_expansion_depth: Number(draft.dependencyExpansionDepth),
    summarize_elements: Boolean(draft.summarizeElements),
  };
}

export const saveAnalysis = (projectId, { draft, result, note, duration }) => httpClient(
  `/projects/${projectId}/analyses`,
  {
    method: 'POST',
    body: JSON.stringify({
      note: note || null,
      config: toAnalysisConfig(draft),
      // The pipeline response is stored verbatim - no reshaping here, so a
      // reopened analysis renders through exactly the same path as a live one.
      result,
      execution_duration: duration ?? null,
      // Claims the uploaded files the run was held against. The server keeps
      // them for a day after a run, so a visitor can still sign up and save.
      upload_id: result?.upload_id ?? null,
    }),
  },
);

/**
 * Pull one stored artifact down as a zip.
 *
 * Not a plain <a href>: the endpoint needs the bearer token, which a browser
 * navigation will not send. So it is fetched as a blob and handed to a
 * throwaway anchor instead.
 */
export async function downloadArtifact(artifact) {
  const response = await fetch(
    `${API_BASE_URL}/artifacts/${artifact.artifact_id}/download`,
    { headers: { Authorization: `Bearer ${getToken()}` } },
  );

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Download failed (${response.status}).`);
  }

  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${artifact.role}-${artifact.artifact_type}.zip`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** power;
  return `${value >= 10 || power === 0 ? Math.round(value) : value.toFixed(1)} ${units[power]}`;
}

/**
 * When a run happened, always - never its database id, which is an internal
 * key that means nothing to the reader and looks like a count of something.
 */
export function runTimestamp(analysis) {
  const when = new Date(analysis.created_at);
  if (Number.isNaN(when.getTime())) return 'Untitled run';

  return `Run on ${when.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}`
    + `, ${when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;
}


const UNITS = [
  ['minute', 60],
  ['hour', 3600],
  ['day', 86400],
  ['week', 604800],
  ['month', 2592000],
  ['year', 31536000],
];

/** "3 days ago" style label for list rows. */
export function relativeTime(isoString) {
  const then = new Date(isoString);
  if (Number.isNaN(then.getTime())) return '';

  const seconds = Math.max(0, Math.round((Date.now() - then.getTime()) / 1000));
  if (seconds < 60) return 'just now';

  let [label, size] = UNITS[0];
  for (const [unit, unitSeconds] of UNITS) {
    if (seconds < unitSeconds) break;
    [label, size] = [unit, unitSeconds];
  }

  const value = Math.floor(seconds / size);
  return `${value} ${label}${value === 1 ? '' : 's'} ago`;
}
