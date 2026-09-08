import { useEffect, useState } from 'react';
import { Search, X } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import { useCapabilities } from '../../analysis/api/capabilitiesApi';
import {
  PROVIDERS,
  connectGitHubSource,
  getGitHubConnection,
  listBranches,
  listMyRepositories,
} from '../api/syncApi';

/**
 * Points one of a project's artifact sets at a GitHub repository.
 *
 * Connecting takes over that kind: whatever used to supply the code is stood
 * down, so a sync has one place to fetch from rather than a choice to guess
 * between. The backend says so in its response; this says so up front.
 */
export function ConnectRepositoryDialog({ projectId, kind: fixedKind, currentKinds, onConnected, onClose }) {
  const { capabilities } = useCapabilities();
  // What this user's own GitHub connection can reach. When they have one,
  // there is nothing to type: the repositories are simply listed.
  const [mine, setMine] = useState(null);
  const [provider, setProvider] = useState(PROVIDERS[0].key);
  const [kind, setKind] = useState(fixedKind ?? 'code');
  const [repository, setRepository] = useState('');
  const [branch, setBranch] = useState('');
  const [branches, setBranches] = useState(null);
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);
  const [loadingBranches, setLoadingBranches] = useState(false);

  // Silent when there is no connection: pasting a URL still works, and an
  // error here is not something the user asked for.
  useEffect(() => {
    let active = true;
    getGitHubConnection()
      .then((connection) => (connection.connected ? listMyRepositories() : []))
      .then((repos) => { if (active) setMine(repos); })
      .catch(() => { if (active) setMine([]); });
    return () => { active = false; };
  }, []);

  // Naming a repository is enough to connect it - the branch is only looked up
  // so one can be picked rather than typed from memory.
  const loadBranches = async () => {
    if (!repository.trim()) return;
    setError(null);
    setLoadingBranches(true);
    try {
      setBranches(await listBranches(repository, token));
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
      onConnected(await connectGitHubSource(projectId, { kind, repository, branch, name, token }));
    } catch (requestError) {
      setError(requestError.message);
      setPending(false);
    }
  };

  const replacing = currentKinds?.includes(kind);

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
            TraceRAG will fetch from here whenever you sync, instead of asking you
            to upload the files again.
          </p>

          <label>
            Provider
            <select value={provider} onChange={(event) => setProvider(event.target.value)}>
              {PROVIDERS.map((option) => (
                <option key={option.key} value={option.key}>{option.label}</option>
              ))}
            </select>
          </label>

          {/* Fixed when the caller is filling one particular side of a trace. */}
          {!fixedKind && (
            <label>
              Supplies
              <select value={kind} onChange={(event) => setKind(event.target.value)}>
                {(capabilities?.artifact_kinds ?? []).map((option) => (
                  <option key={option.key} value={option.key}>{option.label}</option>
                ))}
              </select>
              {replacing && (
                <small className="field-hint">
                  This project already takes its {kind} from somewhere else. Connecting
                  here replaces it — the old source is kept, but stops being synced.
                </small>
              )}
            </label>
          )}

          {/* Which of the two ways to name a repository applies is not known
              until the lookup answers. Waiting is better than showing the
              paste-a-URL form and swapping it out underneath the user, which
              reads as the dialog having forgotten their account. */}
          {mine === null ? (
            <label>
              Repository
              <p className="field-hint">Checking your GitHub account…</p>
            </label>
          ) : mine.length ? (
            <label>
              Repository
              <select
                value={repository}
                onChange={(event) => { setRepository(event.target.value); setBranches(null); }}
                required
              >
                <option value="">Choose a repository…</option>
                {mine.map((repo) => (
                  <option key={repo.full_name} value={repo.full_name}>
                    {repo.full_name}{repo.private ? ' (private)' : ''}
                  </option>
                ))}
              </select>
              <small className="field-hint">
                From your connected GitHub account. No token needed — yours is used.
              </small>
            </label>
          ) : (
            <>
              <label>
                Repository
                <div className="field-with-action">
                  <input
                    value={repository}
                    onChange={(event) => { setRepository(event.target.value); setBranches(null); }}
                    required
                    autoFocus
                    autoComplete="off"
                    placeholder={PROVIDERS.find((p) => p.key === provider)?.placeholder}
                  />
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
                  Paste the URL, or write it as owner/name.
                </small>
              </label>

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
                <small className="field-hint">
                  Stored encrypted and never shown again. Connecting your GitHub
                  account on the Profile page avoids needing one per repository.
                </small>
              </label>
            </>
          )}

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

          <label>
            <span className="field-name">
              Name<span className="dialog-optional">optional</span>
            </span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              name="tracerag-source-name"
              autoComplete="off"
              data-lpignore="true"
              data-form-type="other"
              placeholder="Named after the repository"
            />
          </label>

          {error && <p className="auth-error" role="alert">{error}</p>}

          <div className="dialog-actions">
            <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={pending || !repository.trim()}>
              {pending ? 'Connecting…' : 'Connect'}
            </Button>
          </div>
        </form>
      </section>
    </div>
  );
}
