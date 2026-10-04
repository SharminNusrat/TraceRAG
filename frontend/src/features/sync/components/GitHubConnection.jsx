import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Check, GitBranch } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import { disconnectGitHub, getGitHubConnection, startGitHubOAuth } from '../api/syncApi';

/**
 * Connecting a GitHub account to this user.
 *
 * Held on the account rather than on each repository: a credential belongs to
 * a person. Connect once and every repository you can reach becomes available,
 * and disconnecting covers all of them at once.
 */
export function GitHubConnection() {
  const [params, setParams] = useSearchParams();
  const [connection, setConnection] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  const load = () => getGitHubConnection()
    .then(setConnection)
    .catch((requestError) => { setConnection(null); setError(requestError.message); });

  useEffect(() => { load(); }, []);

  // The OAuth callback lands on the API and sends the browser back here with
  // the outcome in the address. Read it once, then clear it so a refresh does
  // not show a stale message.
  useEffect(() => {
    const outcome = params.get('github');
    if (!outcome) return;
    if (outcome === 'error') setError(params.get('message') ?? 'GitHub sign-in failed.');
    const next = new URLSearchParams(params);
    ['github', 'message', 'login'].forEach((key) => next.delete(key));
    setParams(next, { replace: true });
    load();
  }, [params]);

  const connect = async () => {
    setError(null);
    setPending(true);
    try {
      const { authorize_url: url } = await startGitHubOAuth();
      // A full navigation, not fetch: GitHub's approval page is for the user
      // to look at, and it will not answer a cross-origin request.
      window.location.assign(url);
    } catch (requestError) {
      setError(requestError.message);
      setPending(false);
    }
  };

  const disconnect = async () => {
    if (!window.confirm('Disconnect GitHub? Connected repositories can no longer be fetched.')) return;
    setError(null);
    setPending(true);
    try {
      setConnection(await disconnectGitHub());
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPending(false);
    }
  };

  if (connection === null && !error) return null;

  return (
    <section className="preferences">
      <label>
        <span>
          <b>GitHub</b>
          <small>
            {connection?.connected
              ? `Connected as ${connection.login ?? 'your account'}. TraceRAG can fetch the repositories you granted.`
              : connection?.configured
                ? 'Connect your account so repositories can be fetched without pasting a token each time.'
                : 'Not set up on this server. Repositories can still be connected one at a time with an access token.'}
          </small>
        </span>
        {connection?.connected ? (
          <Button variant="secondary" onClick={disconnect} disabled={pending}>
            {pending ? 'Working…' : 'Disconnect'}
          </Button>
        ) : (
          <Button onClick={connect} disabled={pending || !connection?.configured}>
            <GitBranch size={14} strokeWidth={2.2} />
            {pending ? 'Opening GitHub…' : 'Connect GitHub'}
          </Button>
        )}
      </label>

      {connection?.connected && (
        <p className="field-hint">
          <Check size={12} strokeWidth={2.4} /> Disconnecting removes TraceRAG's copy
          of the credential. To withdraw access entirely, also remove it from your
          GitHub account settings.
        </p>
      )}

      {error && <p className="auth-error" role="alert">{error}</p>}
    </section>
  );
}
