import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { FileText, FolderCode, GitBranch, Network, Plug, RefreshCw, Unplug } from 'lucide-react';
import { ConnectRepositoryDialog } from './ConnectRepositoryDialog';
import { SyncDialog } from './SyncDialog';
import { disconnectSource, listSources, shortRef, sourceLocation } from '../api/syncApi';

const ICONS = { requirements: FileText, code: FolderCode, architecture: Network };

/**
 * What a project holds, and where each part comes from.
 *
 * Distinct from the artifacts on a saved run: those record what one analysis
 * was performed against and never change. These are the project's standing
 * entries - the thing a sync refreshes.
 */
export function SourcesPanel({ projectId, onChanged }) {
  const [params, setParams] = useSearchParams();
  const [sources, setSources] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [connecting, setConnecting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const load = () => listSources(projectId)
    .then(setSources)
    .catch((requestError) => { setSources([]); setError(requestError.message); });

  useEffect(() => { load(); }, [projectId]);

  // Reconnecting GitHub leaves the app and comes back here, with the outcome
  // in the address. Say how it went where the user is standing, rather than
  // leaving them to wonder whether it worked, then clear it so a refresh does
  // not repeat a message about something that happened once.
  useEffect(() => {
    const outcome = params.get('github');
    if (!outcome) return;

    setError(outcome === 'error' ? (params.get('message') ?? 'GitHub sign-in failed.') : null);
    setNotice(outcome === 'connected'
      ? `Reconnected as ${params.get('login') || 'your GitHub account'}.`
      : null);

    const next = new URLSearchParams(params);
    ['github', 'message', 'login'].forEach((key) => next.delete(key));
    setParams(next, { replace: true });
    load();
  }, [params]);

  const disconnect = async (source) => {
    const question = source.origin === 'github'
      ? `Stop syncing ${source.location}?`
      : `Stop syncing "${source.name}"?`;
    if (!window.confirm(question)) return;

    setError(null);
    setNotice(null);
    setBusyId(source.source_id);
    try {
      // The API says whether the row went or was only stood down, and why -
      // worth repeating, because "kept" is the surprising half.
      const outcome = await disconnectSource(projectId, source.source_id);
      setNotice(outcome.detail);
      await load();
      onChanged?.();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="artifact-strip">
      <header>
        <b>Project Sources</b>
        <span className="strip-actions">
          <button type="button" className="row-open" onClick={() => setConnecting(true)}>
            <Plug size={13} strokeWidth={2} /> Connect repository
          </button>
          {/* Nothing to bring up to date until the project holds something. */}
          {Boolean(sources?.length) && (
            <button type="button" className="row-rerun" onClick={() => setSyncing(true)}>
              <RefreshCw size={13} strokeWidth={2} /> Sync now
            </button>
          )}
        </span>
      </header>

      {sources === null && <p className="dialog-note">Loading sources…</p>}

      {sources?.length === 0 && (
        <p className="dialog-note">
          Nothing connected yet. Saving an analysis records the files it used as
          sources; connecting a repository lets TraceRAG fetch them itself.
        </p>
      )}

      {Boolean(sources?.length) && (
        <div className="artifact-items">
          {sources.map((source) => {
            const Icon = source.origin === 'github'
              ? GitBranch
              : (ICONS[source.kind] ?? FileText);
            return (
              <article key={source.source_id}>
                <span className="artifact-icon"><Icon size={15} strokeWidth={2} /></span>
                <div>
                  <b>{source.name}</b>
                  <small>
                    {source.kind} · {sourceLocation(source)}
                    {/* Null until the first sync: connected, nothing fetched. */}
                    {source.last_sync_ref
                      ? ` · at ${shortRef(source.last_sync_ref)}`
                      : ' · never synced'}
                  </small>
                </div>
                <button
                  type="button"
                  onClick={() => disconnect(source)}
                  disabled={busyId === source.source_id}
                  title="Stop syncing this source"
                  aria-label={`Disconnect ${source.name}`}
                >
                  <Unplug size={14} strokeWidth={2} />
                  {busyId === source.source_id ? 'Working…' : 'Disconnect'}
                </button>
              </article>
            );
          })}
        </div>
      )}

      {notice && <p className="dialog-note">{notice}</p>}
      {error && <p className="auth-error" role="alert">{error}</p>}

      {connecting && (
        <ConnectRepositoryDialog
          projectId={projectId}
          currentKinds={(sources ?? []).map((source) => source.kind)}
          onClose={() => setConnecting(false)}
          onConnected={() => { setConnecting(false); setNotice(null); load(); onChanged?.(); }}
        />
      )}

      {syncing && (
        <SyncDialog
          projectId={projectId}
          onClose={() => { setSyncing(false); load(); }}
          // A finished sync moved the refs and filed new analyses, so what is
          // on screen behind the dialog is already out of date.
          onFinished={() => { load(); onChanged?.(); }}
        />
      )}
    </section>
  );
}
