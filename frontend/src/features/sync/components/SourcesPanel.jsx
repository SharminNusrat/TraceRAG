import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  AlertTriangle, FileText, FolderCode, GitBranch, Network, Plug, RefreshCw, Unplug,
} from 'lucide-react';
import { findKind, useCapabilities } from '../../analysis/api/capabilitiesApi';
import { ConnectRepositoryDialog } from './ConnectRepositoryDialog';
import { SyncDialog } from './SyncDialog';
import {
  disconnectSource, listPairs, listSources, reconnectSource, shortRef, sourceLocation,
} from '../api/syncApi';

const ICONS = { requirements: FileText, code: FolderCode, architecture: Network };

/**
 * What a project holds, and where each part comes from.
 *
 * Distinct from the artifacts on a saved run: those record what one analysis
 * was performed against and never change. These are the project's standing
 * entries - the thing a sync refreshes.
 *
 * Grouped by pair, because a pair is what gets synced: the two kinds a trace
 * runs between. A kind used by two pairs is listed under both.
 */
export function SourcesPanel({ projectId, onChanged }) {
  const [params, setParams] = useSearchParams();
  const { capabilities } = useCapabilities();
  const [sources, setSources] = useState(null);
  const [pairs, setPairs] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [connecting, setConnecting] = useState(false);
  // The pair being synced, while its dialog is open.
  const [syncing, setSyncing] = useState(null);
  const [busyId, setBusyId] = useState(null);

  // Disconnected sources included: one that is not shown cannot be brought
  // back.
  const load = () => Promise.all([listSources(projectId, true), listPairs(projectId)])
    .then(([projectSources, projectPairs]) => {
      setSources(projectSources);
      setPairs(projectPairs);
    })
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

  const reconnect = async (source) => {
    setError(null);
    setNotice(null);
    setBusyId(source.source_id);
    try {
      await reconnectSource(projectId, source.source_id);
      await load();
      onChanged?.();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusyId(null);
    }
  };

  const connected = (sources ?? []).filter((source) => source.is_active);

  const kindLabel = (key) => findKind(capabilities, key)?.label ?? key;
  const sidesOf = (pair) => [pair.source, pair.target].filter(Boolean);
  // Whatever no pair is showing: disconnected sources, and connected ones
  // nothing has been run against yet.
  const paired = new Set(pairs.flatMap(sidesOf).map((source) => source.source_id));
  const unpaired = (sources ?? []).filter((source) => !paired.has(source.source_id));

  const card = (source) => {
    const Icon = source.origin === 'github'
      ? GitBranch
      : (ICONS[source.kind] ?? FileText);
    return (
      <article
        key={source.source_id}
        className={source.is_active ? undefined : 'disconnected'}
      >
        <span className="artifact-icon"><Icon size={15} strokeWidth={2} /></span>
        <div>
          <b>{source.name}</b>
          <small>
            {kindLabel(source.kind)} · {sourceLocation(source)}
            {/* Null until the first sync: connected, nothing fetched. */}
            {source.last_sync_ref
              ? ` · at ${shortRef(source.last_sync_ref)}`
              : ' · never synced'}
            {!source.is_active && ' · disconnected'}
          </small>
        </div>
        {source.is_active ? (
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
        ) : (
          <button
            type="button"
            onClick={() => reconnect(source)}
            disabled={busyId === source.source_id}
            title="Sync this source again"
            aria-label={`Reconnect ${source.name}`}
          >
            <Plug size={14} strokeWidth={2} />
            {busyId === source.source_id ? 'Working…' : 'Reconnect'}
          </button>
        )}
      </article>
    );
  };

  return (
    <section className="artifact-strip">
      <header>
        <b>Project Sources</b>
        <span className="strip-actions">
          <button type="button" className="row-open" onClick={() => setConnecting(true)}>
            <Plug size={13} strokeWidth={2} /> Connect repository
          </button>
        </span>
      </header>

      {sources === null && <p className="dialog-note">Loading sources…</p>}

      {sources?.length === 0 && (
        <p className="dialog-note">
          Nothing connected yet. Saving an analysis records the files it used as
          sources; connecting a repository lets TraceRAG fetch them itself.
        </p>
      )}

      {pairs.map((pair) => (
        <div className="source-pair" key={`${pair.source_kind}>${pair.target_kind}`}>
          <header>
            <b>{kindLabel(pair.source_kind)} → {kindLabel(pair.target_kind)}</b>
            {pair.out_of_date && (
              <em className="sync-problem">
                <AlertTriangle size={12} strokeWidth={2.2} /> Out of date
              </em>
            )}
            {/* Nothing to bring up to date once both sides are disconnected. */}
            {Boolean(sidesOf(pair).length) && (
              <button type="button" className="row-rerun" onClick={() => setSyncing(pair)}>
                <RefreshCw size={13} strokeWidth={2} /> Sync
              </button>
            )}
          </header>
          <div className="artifact-items">{sidesOf(pair).map(card)}</div>
        </div>
      ))}

      {Boolean(unpaired.length) && (
        <div className="artifact-items">{unpaired.map(card)}</div>
      )}

      {notice && <p className="dialog-note">{notice}</p>}
      {error && <p className="auth-error" role="alert">{error}</p>}

      {connecting && (
        <ConnectRepositoryDialog
          projectId={projectId}
          currentKinds={connected.map((source) => source.kind)}
          onClose={() => setConnecting(false)}
          onConnected={() => { setConnecting(false); setNotice(null); load(); onChanged?.(); }}
        />
      )}

      {syncing && (
        <SyncDialog
          projectId={projectId}
          pair={syncing}
          onClose={() => { setSyncing(null); load(); }}
          // A finished sync moved the refs and filed new analyses, so what is
          // on screen behind the dialog is already out of date.
          onFinished={() => { load(); onChanged?.(); }}
        />
      )}
    </section>
  );
}
