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

export const listAnalyses = (projectId) => httpClient(
  projectId ? `/analyses?project_id=${projectId}` : '/analyses',
);

export const getAnalysis = (analysisId) => httpClient(`/analyses/${analysisId}`);

/** Every way a project has been read. One graph belongs to each. */
export const listConfigs = (projectId) => httpClient(`/projects/${projectId}/configs`);

/** Every state a project's artifacts have been in, newest first. */
export const listVersions = (projectId) => httpClient(`/projects/${projectId}/versions`);

/**
 * One configuration's graph as it currently stands.
 *
 * `config_id` may be left out only when the project has a single configuration
 * - two of them read the artifacts into different elements, so there is no
 * single answer to give.
 */
export const getGraph = (projectId, { configId, linkStatus, limit, offset } = {}) => {
  const query = new URLSearchParams();
  if (configId) query.set('config_id', configId);
  if (linkStatus) query.set('link_status', linkStatus);
  if (limit) query.set('limit', limit);
  if (offset) query.set('offset', offset);
  const suffix = query.toString();
  return httpClient(`/projects/${projectId}/graph${suffix ? `?${suffix}` : ''}`);
};

export const deleteAnalysis = (analysisId) => httpClient(`/analyses/${analysisId}`, {
  method: 'DELETE',
});

/** Diff two saved runs of the same project: base is the "before" side. */
export const compareAnalyses = (baseId, headId) => httpClient(
  `/analyses/${baseId}/compare/${headId}`,
);

/**
 * Run a saved analysis again over the files it already holds, storing the
 * outcome as a new analysis in the same project.
 *
 * `config` omitted repeats the original settings; supplying one answers "what
 * would this have found at a different granularity?". Runs the full pipeline,
 * so it can take minutes.
 */
export const rerunAnalysis = (analysisId, { note, config } = {}) => httpClient(
  `/analyses/${analysisId}/rerun`,
  {
    method: 'POST',
    body: JSON.stringify({
      note: note || null,
      config: config ?? null,
    }),
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
