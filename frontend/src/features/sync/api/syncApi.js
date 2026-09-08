import { httpClient } from '../../../services/httpClient';

/**
 * Where a project's artifacts come from, and keeping them current.
 *
 * A source is the project's standing entry for one artifact set - "the code
 * lives at myorg/payments on main". Saved analyses record what a run used;
 * these say where to get it again.
 */

export const listSources = (projectId, includeDisconnected = false) => httpClient(
  `/projects/${projectId}/sources?include_disconnected=${includeDisconnected}`,
);

/**
 * The providers a source can be connected from.
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

export const connectGitHubSource = (projectId, { kind, repository, branch, name, token }) => httpClient(
  `/projects/${projectId}/sources/github`,
  {
    method: 'POST',
    body: JSON.stringify({
      kind,
      repository: repository.trim(),
      // Omitted rather than sent empty: the API takes whichever branch the
      // repository itself defaults to.
      branch: branch?.trim() || null,
      name: name?.trim() || null,
      // Only for a repository the server's own token cannot read. Sent once
      // and never read back.
      token: token?.trim() || null,
    }),
  },
);

export const disconnectSource = (projectId, sourceId) => httpClient(
  `/projects/${projectId}/sources/${sourceId}`,
  { method: 'DELETE' },
);

/** What a sync would pick up, without fetching anything. */
export const getSyncStatus = (projectId) => httpClient(`/projects/${projectId}/sync/status`);

/**
 * Hand over new files for a source nothing can fetch.
 *
 * Staged first and named in the sync afterwards, so the files can be chosen,
 * checked and replaced without starting anything. The answer says how many of
 * them land where the source's files already are - a requirement is identified
 * by its path, so files arriving somewhere else read as new ones.
 */
export function stageSourceFiles(projectId, sourceId, files) {
  const form = new FormData();
  files.forEach((file) => form.append('files', file));
  // The folder each file came from, when it came from one. Kept because it is
  // part of the path the graph knows the file by.
  form.append('file_paths', JSON.stringify(files.map((f) => f.webkitRelativePath || f.name)));
  return httpClient(`/projects/${projectId}/sources/${sourceId}/files`, {
    method: 'POST',
    body: form,
  });
}

/**
 * Ask for a sync. Returns immediately with a job to watch, or with
 * `started: false` when there was nothing to do.
 */
export const startSync = (
  projectId,
  { sourceIds, configIds, replacements, force, note } = {},
) => httpClient(
  `/projects/${projectId}/sync`,
  {
    method: 'POST',
    body: JSON.stringify({
      source_ids: sourceIds ?? null,
      config_ids: configIds ?? null,
      // source_id -> staged upload id, for the sources whose files were
      // uploaded rather than fetched.
      replacements: replacements ?? {},
      force: Boolean(force),
      note: note?.trim() || null,
    }),
  },
);

export const getJob = (jobId) => httpClient(`/jobs/${jobId}`);

/** Where the source stands: the short form of a commit, or of a fingerprint. */
export const shortRef = (ref) => (ref ? ref.slice(0, 8) : null);

/** How a source describes itself in one line. */
export function sourceLocation(source) {
  if (source.origin === 'github') {
    return source.branch ? `${source.location} · ${source.branch}` : source.location;
  }
  return 'Uploaded files';
}
