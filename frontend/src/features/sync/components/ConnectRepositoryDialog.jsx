import { useEffect, useState } from 'react';
import { GitBranch, Search, X } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import {
  PROVIDERS,
  connectGitHubSide,
  getGitHubConnection,
  listBranches,
  listMyRepositories,
  startGitHubOAuth,
} from '../api/syncApi';

/**
 * Takes the code side of one analysis from a GitHub repository.
 *
 * Only that side of that analysis: another analysis tracing the same code
 * keeps its own side, and is pointed at a repository on its own.
 *
 * The account connection is the way in: connected, the repository is simply
 * chosen and every fetch uses that connection; not connected, the one thing
 * offered is to connect. A token for this one repository is still possible,
 * but kept out of the way.
 */
export function ConnectRepositoryDialog({
  projectId, configId, sourceId, needsReconnect, onConnected, onClose,
}) {
  const [connection, setConnection] = useState(null);
  // What this user's own GitHub connection can reach. When they have one,
  // there is nothing to type: the repositories are simply listed.
  const [mine, setMine] = useState([]);
  const [useToken, setUseToken] = useState(false);
  const [provider, setProvider] = useState(PROVIDERS[0].key);
  const [repository, setRepository] = useState('');
  const [branch, setBranch] = useState('');
  const [branches, setBranches] = useState(null);
  const [token, setToken] = useState('');
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);
  const [loadingBranches, setLoadingBranches] = useState(false);

  useEffect(() => {
    let active = true;
    getGitHubConnection()
      .then(async (found) => {
        const repos = found.connected ? await listMyRepositories().catch(() => []) : [];
        if (active) { setConnection(found); setMine(repos); }
      })
      .catch(() => { if (active) setConnection({ connected: false, configured: false }); });
    return () => { active = false; };
  }, []);

  /** Off to GitHub, and back to this analysis with this dialog open again. */
  const connect = async () => {
    setError(null);
    setPending(true);
    try {
      const returnTo = `/app/analyses/${configId}?project=${projectId}&connect=${sourceId}`;
      const { authorize_url: url } = await startGitHubOAuth(returnTo);
      // A full navigation: GitHub's approval page is for the user to look at,
      // and it will not answer a cross-origin request.
      window.location.assign(url);
    } catch (requestError) {
      setError(requestError.message);
      setPending(false);
    }
  };

  // Naming a repository is enough to connect it - the branch is only looked up
  // so one can be picked rather than typed from memory.
  const loadBranches = async () => {
    if (!repository.trim()) return;
    setError(null);
    setLoadingBranches(true);
    try {
      setBranches(await listBranches(repository, useToken ? token : null));
    } catch (requestError) {
      setBranches(null);
      setError(requestError.message);
    } finally {
      setLoadingBranches(false);
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      // No token on the account path: the side then holds none of its own,
      // and every fetch uses the account connection.
      onConnected(await connectGitHubSide(projectId, configId, sourceId, {
        repository, branch, token: useToken ? token : null,
      }));
    } catch (requestError) {
      setError(requestError.message);
      setPending(false);
    }
  };

  const chooseRepository = (value) => { setRepository(value); setBranches(null); };
  const connected = connection?.connected;
  const reconnect = needsReconnect || connection?.needs_reconnect;
  const showForm = connected || useToken;

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Connect a source">
      <section className="dialog-card">
        <header className="dialog-head">
          <h2>Connect a Source</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={2.2} />
          </button>
        </header>

        <form onSubmit={submit}>
          <p className="dialog-note">
            TraceRAG will fetch from here whenever you update this side, instead of
            asking you to upload the files again.
          </p>

          <label>
            Provider
            <select value={provider} onChange={(event) => setProvider(event.target.value)}>
              {PROVIDERS.map((option) => (
                <option key={option.key} value={option.key}>{option.label}</option>
              ))}
            </select>
          </label>

          {connection === null && <p className="field-hint">Checking your GitHub account…</p>}

          {connection !== null && !showForm && (
            <Button type="button" onClick={connect} disabled={pending || !connection.configured}>
              <GitBranch size={14} strokeWidth={2.2} />
              {reconnect ? 'Reconnect GitHub' : 'Connect GitHub'}
            </Button>
          )}

          {showForm && (
            <label>
              Repository
              <div className="field-with-action">
                {connected && !useToken && mine.length ? (
                  <select
                    value={repository}
                    onChange={(event) => chooseRepository(event.target.value)}
                    required
                  >
                    <option value="">Choose a repository…</option>
                    {mine.map((repo) => (
                      <option key={repo.full_name} value={repo.full_name}>
                        {repo.full_name}{repo.private ? ' (private)' : ''}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={repository}
                    onChange={(event) => chooseRepository(event.target.value)}
                    required
                    autoFocus
                    autoComplete="off"
                    placeholder={PROVIDERS.find((p) => p.key === provider)?.placeholder}
                  />
                )}
                <button
                  type="button"
                  onClick={loadBranches}
                  disabled={!repository.trim() || loadingBranches}
                  title="Look up the branches of this repository"
                >
                  <Search size={13} strokeWidth={2} />
                  {loadingBranches ? 'Checking…' : 'Find branches'}
                </button>
              </div>
              <small className="field-hint">
                {connected && !useToken
                  ? `Read with your GitHub connection${connection.login ? ` as ${connection.login}` : ''}.`
                  : 'Paste the URL, or write it as owner/name.'}
              </small>
            </label>
          )}

          {useToken && (
            <label>
              <span className="field-name">
                Access token<span className="dialog-optional">private repositories</span>
              </span>
              <input
                type="password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                name="tracerag-source-token"
                autoComplete="new-password"
                data-lpignore="true"
                data-form-type="other"
                placeholder="Leave empty for a public repository"
              />
              <small className="field-hint">Stored encrypted and never shown again.</small>
            </label>
          )}

          {showForm && (
            <label>
              <span className="field-name">
                Branch<span className="dialog-optional">optional</span>
              </span>
              {branches ? (
                <select value={branch} onChange={(event) => setBranch(event.target.value)}>
                  <option value="">Repository default</option>
                  {branches.map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
              ) : (
                <input
                  value={branch}
                  onChange={(event) => setBranch(event.target.value)}
                  autoComplete="off"
                  placeholder="Leave empty for the default branch"
                />
              )}
            </label>
          )}

          {connection !== null && !useToken && (
            <button type="button" className="text-toggle" onClick={() => setUseToken(true)}>
              Use an access token instead
            </button>
          )}

          {error && <p className="auth-error" role="alert">{error}</p>}

          <div className="dialog-actions">
            <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
            {showForm && (
              <Button type="submit" disabled={pending || !repository.trim()}>
                {pending ? 'Connecting…' : 'Connect'}
              </Button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
