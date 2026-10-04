import { httpClient } from '../../../services/httpClient';

/**
 * Where an analysis's two sides come from, and updating one of them.
 *
 * A side is one end of one analysis - "the code lives at myorg/payments on
 * main". It belongs to that analysis alone, so updating it never touches
 * another analysis in the same project.
 */

const analysisPath = (projectId, configId) => `/projects/${projectId}/configs/${configId}`;

/**
 * The providers a side can be connected to.
 *
 * A list rather than a single button, because the next one is coming: adding
 * Jira should be a new entry and a form, not a redesign of the choice.
 */
export const PROVIDERS = [
  { key: 'github', label: 'GitHub', placeholder: 'https://github.com/owner/repo' },
];

/**
 * Whether this user has connected their own GitHub account.
 *
 * `configured` is false when the server has no OAuth app registered, in which
 * case connecting is not possible and a token must be pasted per repository.
 */
export const getGitHubConnection = () => httpClient('/github/connection');

/**
 * Where to send the browser so the user can approve access.
 *
 * `returnTo` is where GitHub's answer should land. Defaults to wherever the
 * user is standing, because being returned to a settings page you never asked
 * for - and having to find your way back - is the wrong end of a reconnect.
 */
export const startGitHubOAuth = (returnTo = window.location.pathname + window.location.search) =>
  httpClient(`/github/oauth/start?return_to=${encodeURIComponent(returnTo)}`);

export const disconnectGitHub = () => httpClient('/github/connection', { method: 'DELETE' });

/** The repositories the connected account can reach. */
export const listMyRepositories = () => httpClient('/github/repositories');

// A POST, not a query string: it carries a token, and query strings end up in
// server logs and browser history.
export const listBranches = (repository, token) => httpClient('/github/branches', {
  method: 'POST',
  body: JSON.stringify({ repository: repository.trim(), token: token?.trim() || null }),
});

/** Take an analysis's code side from a GitHub repository. */
export const connectGitHubSide = (projectId, configId, sourceId, { repository, branch, token }) =>
  httpClient(`${analysisPath(projectId, configId)}/sources/${sourceId}/github`, {
    method: 'POST',
    body: JSON.stringify({
      repository: repository.trim(),
      // Omitted rather than sent empty: the API takes whichever branch the
      // repository itself defaults to.
      branch: branch?.trim() || null,
      // Only for a repository the account connection cannot read. Sent once
      // and never read back.
      token: token?.trim() || null,
    }),
  });

/** Where each side of an analysis stands, without fetching anything. */
export const getSyncStatus = (projectId, configId) => httpClient(
  `${analysisPath(projectId, configId)}/sync/status`,
);

/**
 * Hand over a side's complete current file set.
 *
 * Staged first and named in the update afterwards, so the files can be
 * chosen, checked and replaced without starting anything. The answer says how
 * many of them land where the side's files already are - a requirement is
 * identified by its path, so files arriving somewhere else read as new ones.
 */
export function stageSideFiles(projectId, configId, sourceId, files) {
  const form = new FormData();
  files.forEach((file) => form.append('files', file));
  // The folder each file came from, when it came from one. Kept because it is
  // part of the path the analysis knows the file by.
  form.append('file_paths', JSON.stringify(files.map((f) => f.webkitRelativePath || f.name)));
  return httpClient(`${analysisPath(projectId, configId)}/sources/${sourceId}/files`, {
    method: 'POST',
    body: form,
  });
}

/**
 * Update one side and re-run the analysis over it. Returns immediately with a
 * job to watch, or with `started: false` when there was nothing to do.
 */
export const startUpdate = (projectId, configId, { sourceId, uploadId, note } = {}) => httpClient(
  `${analysisPath(projectId, configId)}/sync`,
  {
    method: 'POST',
    body: JSON.stringify({
      source_id: sourceId,
      // The staged files, for a side that is uploaded rather than fetched.
      upload_id: uploadId ?? null,
      note: note?.trim() || null,
    }),
  },
);

export const getJob = (jobId) => httpClient(`/jobs/${jobId}`);

/** Where the side stands: the short form of a commit, or of a fingerprint. */
export const shortRef = (ref) => (ref ? ref.slice(0, 8) : null);

/** How a side describes itself in one line. */
export function sourceLocation(source) {
  if (source.origin === 'github') {
    return source.branch ? `${source.location} · ${source.branch}` : source.location;
  }
  return 'Uploaded files';
}
