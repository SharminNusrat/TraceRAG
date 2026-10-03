import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ArrowRight, Check, Plug, RefreshCw, UploadCloud, X } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import { ConnectRepositoryDialog } from './ConnectRepositoryDialog';
import {
  classifierLabel,
  findKind,
  preprocessorLabel,
  useCapabilities,
} from '../../analysis/api/capabilitiesApi';
import { getGraph, listConfigs } from '../../projects/api/projectsApi';
import {
  getJob,
  getSyncStatus,
  shortRef,
  sourceLocation,
  stageSourceFiles,
  startGitHubOAuth,
  startSync,
} from '../api/syncApi';

// How often a running job is asked where it has got to. Long enough not to
// hammer the server, short enough that a stage change is noticed.
const POLL_MS = 1500;

const describe = (capabilities, config) => [
  `${preprocessorLabel(capabilities, config.source_preprocessor)} → `
  + `${preprocessorLabel(capabilities, config.target_preprocessor)}`,
  classifierLabel(capabilities, config.classifier),
  `top ${config.n_results}`,
].join(' · ');

/**
 * Bringing a project up to date, from asking what moved to reading what it did.
 *
 * Four things happen in one place because they are one decision: what has
 * changed, what to re-run over it, how far it has got, and what it did to the
 * links. Splitting them would mean losing the answer between screens.
 */
export function SyncDialog({ projectId, pair, onClose, onFinished }) {
  const { capabilities } = useCapabilities();
  const [statuses, setStatuses] = useState(null);
  const [configs, setConfigs] = useState([]);
  const [chosenSources, setChosenSources] = useState([]);
  const [chosenConfigs, setChosenConfigs] = useState([]);
  const [note, setNote] = useState('');

  const [job, setJob] = useState(null);
  const [broken, setBroken] = useState({});
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);
  // The kind an uploaded source is being swapped to a connected one for.
  const [connectingKind, setConnectingKind] = useState(null);
  // Files handed over for sources nothing can fetch, by source id.
  const [staged, setStaged] = useState({});
  const [staging, setStaging] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const timer = useRef(null);
  // The hidden file input belonging to each uploaded source's row.
  const pickers = useRef({});

  useEffect(() => {
    let active = true;
    Promise.all([getSyncStatus(projectId, pair), listConfigs(projectId)])
      .then(([sources, allConfigs]) => {
        if (!active) return;
        // Only this pair's: another pair's configuration reads other kinds.
        const projectConfigs = allConfigs.filter((config) => (
          config.source_kind === pair.source_kind && config.target_kind === pair.target_kind
        ));
        setStatuses(sources);
        setConfigs(projectConfigs);
        // Everything that moved, and every way of reading it: a sync that left
        // half the project behind is the surprising choice, not the default.
        setChosenSources(sources.filter((s) => s.changed).map((s) => s.source_id));
        setChosenConfigs(projectConfigs.map((c) => c.config_id));
      })
      .catch((requestError) => { if (active) { setStatuses([]); setError(requestError.message); } });
    return () => { active = false; };
  }, [projectId, pair.source_kind, pair.target_kind, reloadKey]);

  // Stop polling when the dialog goes away mid-run; the job carries on server
  // side either way.
  useEffect(() => () => clearTimeout(timer.current), []);

  const watch = (jobId) => {
    const poll = async () => {
      try {
        const latest = await getJob(jobId);
        setJob(latest);
        if (latest.state === 'succeeded' || latest.state === 'failed') {
          if (latest.state === 'succeeded') await loadBrokenCounts(latest.result);
          onFinished?.();
          return;
        }
        timer.current = setTimeout(poll, POLL_MS);
      } catch (requestError) {
        setError(requestError.message);
      }
    };
    poll();
  };

  /**
   * How many links each configuration is left with broken.
   *
   * Not in the sync result: that counts what changed between two runs, while
   * broken is a standing fact about the graph - a requirement whose code is
   * gone stays broken until someone deals with it.
   */
  const loadBrokenCounts = async (result) => {
    const counts = {};
    for (const config of result?.configs ?? []) {
      if (config.error) continue;
      try {
        const graph = await getGraph(projectId, { configId: config.config_id, limit: 1 });
        counts[config.config_id] = graph.summary.links_broken;
      } catch {
        // A count that cannot be read is not worth failing a finished sync for.
      }
    }
    setBroken(counts);
  };

  const toggle = (list, setList, id) => setList(
    list.includes(id) ? list.filter((item) => item !== id) : [...list, id],
  );

  /**
   * Take the files chosen for a source that cannot be fetched.
   *
   * Sent as soon as they are picked rather than with the sync, so the answer -
   * how many of them land where the source's files already are - is there to
   * read before anything is re-run.
   */
  const stage = async (sourceId, files) => {
    if (!files.length) return;
    setError(null);
    setStaging(sourceId);
    try {
      const result = await stageSourceFiles(projectId, sourceId, files);
      setStaged((current) => ({ ...current, [sourceId]: result }));
      // Uploading these is the whole reason for the sync, so include them.
      setChosenSources((current) => (
        current.includes(sourceId) ? current : [...current, sourceId]
      ));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setStaging(null);
    }
  };

  /** Send the browser to GitHub to grant access again. */
  const reconnect = async () => {
    setError(null);
    try {
      const { authorize_url: url } = await startGitHubOAuth();
      // A full navigation: GitHub's approval page is for the user to look at,
      // and it will not answer a cross-origin request.
      window.location.assign(url);
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  const run = async (force = false) => {
    setError(null);
    setPending(true);
    try {
      // Only for the sources actually being synced: a staged upload left out
      // of the run should stay staged rather than be taken in.
      const replacements = {};
      chosenSources.forEach((id) => {
        if (staged[id]) replacements[id] = staged[id].upload_id;
      });
      const started = await startSync(projectId, pair, {
        sourceIds: chosenSources,
        configIds: chosenConfigs,
        replacements,
        force,
        note,
      });
      if (!started.started) {
        // Nothing moved, so no job was filed. Say so and offer to run anyway.
        setJob({ state: 'nothing', stage: null, result: null, error: null, detail: started.detail });
        setPending(false);
        return;
      }
      setJob({ state: 'queued', progress_current: 0, progress_total: 0 });
      watch(started.job_id);
    } catch (requestError) {
      setError(requestError.message);
      setPending(false);
    }
  };

  const changed = (statuses ?? []).filter((s) => s.changed);
  const unreachable = (statuses ?? []).filter((s) => s.error);
  // Sources the project already holds newer files for than this pair last
  // read. Nothing to fetch, but the pair still has catching up to do.
  const behind = (statuses ?? []).filter((s) => s.behind && !s.changed);
  const kindLabel = (key) => findKind(capabilities, key)?.label ?? key;
  const running = job && (job.state === 'queued' || job.state === 'running');
  const finished = job && (job.state === 'succeeded' || job.state === 'failed');
  const nothingToDo = job?.state === 'nothing';
  const percent = job?.progress_total
    ? Math.round((job.progress_current / job.progress_total) * 100)
    : 0;

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Sync project">
      <section className="dialog-card wide">
        <header className="dialog-head">
          <h2>Sync {kindLabel(pair.source_kind)} → {kindLabel(pair.target_kind)}</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={2.2} />
          </button>
        </header>

        {statuses === null && <p className="dialog-note">Checking what has changed…</p>}

        {/* ---- 1 & 2: what moved, and what to run over it ---- */}
        {statuses !== null && !job && (
          <>
            <p className="dialog-note">
              {changed.length
                ? `${changed.length} source${changed.length === 1 ? ' has' : 's have'} moved since the last sync.`
                : unreachable.length
                  // "Nothing has moved" would be a claim about something we
                  // never managed to ask.
                  ? `${unreachable.length} source${unreachable.length === 1 ? '' : 's'} could not be checked, so what has moved is unknown.`
                  : behind.length
                    ? `${behind.length} source${behind.length === 1 ? ' has' : 's have'} changed since this pair was last run.`
                    : 'Nothing has moved since the last sync.'}
            </p>

            <div className="sync-list">
              {statuses.map((source) => {
                const held = staged[source.source_id];
                const selectable = source.changed || Boolean(held);
                return (
                  <label
                    key={source.source_id}
                    className={selectable ? 'sync-row' : 'sync-row muted'}
                  >
                    <input
                      type="checkbox"
                      checked={chosenSources.includes(source.source_id)}
                      disabled={!selectable}
                      onChange={() => toggle(chosenSources, setChosenSources, source.source_id)}
                    />
                    <div>
                      <b>{source.name}</b>
                      <small>{kindLabel(source.kind)} · {sourceLocation(source)}</small>
                    </div>
                    <span className="sync-state">
                      {source.error ? (
                        // Only the short verdict here. The sentence explaining
                        // it gets its own line below, where it has the width to
                        // be read instead of squeezing the name out of shape.
                        <>
                          <em className="sync-problem">
                            <AlertTriangle size={12} strokeWidth={2.2} /> Could not check
                          </em>
                          {source.needs_reconnect && (
                            <button
                              type="button"
                              className="row-open"
                              onClick={(event) => { event.preventDefault(); reconnect(); }}
                            >
                              <Plug size={12} strokeWidth={2} /> Reconnect GitHub
                            </button>
                          )}
                        </>
                      ) : source.changed ? (
                        <>
                          {shortRef(source.last_sync_ref) ?? 'new'}
                          <ArrowRight size={12} strokeWidth={2.2} />
                          <b>{shortRef(source.latest_ref)}</b>
                        </>
                      ) : source.checkable ? (
                        <>{source.behind ? 'Out of date' : 'Up to date'}</>
                      ) : held ? (
                        <>
                          <b>{held.file_count} file{held.file_count === 1 ? '' : 's'}</b>
                          {' ready'}
                          {held.missing > 0 && (
                            <em className="sync-problem" title={
                              `${held.missing} file(s) the project holds are not in this `
                              + 'upload. Their links will be reported broken.'
                            }>
                              <AlertTriangle size={12} strokeWidth={2.2} />
                              {held.missing} not included
                            </em>
                          )}
                        </>
                      ) : (
                        // A sync cannot reach into your machine for new files,
                        // so rather than only saying so, offer both ways out:
                        // supply them once more, or stop having to.
                        <>
                          {source.behind ? 'out of date' : 'carried over'}
                          <button
                            type="button"
                            className="row-open"
                            title="Upload this source's files again"
                            onClick={(event) => {
                              event.preventDefault();
                              pickers.current[source.source_id]?.click();
                            }}
                          >
                            <UploadCloud size={12} strokeWidth={2} />
                            {staging === source.source_id ? ' Uploading…' : ' Upload new'}
                          </button>
                          <input
                            // Opened by the button above: a file input styles
                            // itself, and this row is a label already, so it
                            // cannot hold one that would be clicked directly.
                            ref={(element) => { pickers.current[source.source_id] = element; }}
                            type="file"
                            multiple
                            hidden
                            accept={(findKind(capabilities, source.kind)?.extensions ?? []).join(',')}
                            onChange={(event) => {
                              stage(source.source_id, Array.from(event.target.files));
                              // Cleared so picking the same files again still
                              // counts as a change.
                              event.target.value = '';
                            }}
                          />
                          <button
                            type="button"
                            className="row-open"
                            onClick={(event) => {
                              event.preventDefault();
                              setConnectingKind(source.kind);
                            }}
                          >
                            <Plug size={12} strokeWidth={2} /> Connect
                          </button>
                        </>
                      )}
                    </span>
                    {/* Explained only where explaining is what helps. A dead
                        connection is mended by the button beside it, so a
                        paragraph about it is just something else to read. */}
                    {source.error && !source.needs_reconnect && (
                      <p className="sync-error">{source.error}</p>
                    )}
                  </label>
                );
              })}
            </div>

            {configs.length > 1 && (
              <>
                <p className="dialog-note">
                  Each configuration reads the artifacts its own way and keeps its own
                  graph, so each is re-run separately.
                </p>
                <div className="sync-list">
                  {configs.map((config) => (
                    <label key={config.config_id} className="sync-row">
                      <input
                        type="checkbox"
                        checked={chosenConfigs.includes(config.config_id)}
                        onChange={() => toggle(chosenConfigs, setChosenConfigs, config.config_id)}
                      />
                      <div>
                        <b>{describe(capabilities, config.config)}</b>
                        <small>
                          {config.analysis_count} run{config.analysis_count === 1 ? '' : 's'}
                          {config.is_default ? ' · default' : ''}
                        </small>
                      </div>
                    </label>
                  ))}
                </div>
              </>
            )}

            <label>
              <span className="field-name">
                Note<span className="dialog-optional">optional</span>
              </span>
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                name="tracerag-sync-note"
                autoComplete="off"
                data-lpignore="true"
                data-form-type="other"
                placeholder="e.g. After the payment refactor"
              />
            </label>

            {error && <p className="auth-error" role="alert">{error}</p>}

            <div className="dialog-actions">
              <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
              {/* With nothing selected the button asks to run anyway rather
                  than going dead. A project can want re-running when nothing
                  has moved - after a failed run, or to see what a changed
                  configuration makes of the same files - and refusing to offer
                  it leaves no way in at all. */}
              <Button
                onClick={() => run(!chosenSources.length && !behind.length)}
                disabled={pending || !chosenConfigs.length || !statuses.length}
              >
                {pending ? 'Starting…'
                  : !chosenSources.length ? (behind.length ? 'Bring up to date' : 'Re-run anyway')
                  : `Sync ${chosenSources.length} source${chosenSources.length === 1 ? '' : 's'}`}
              </Button>
            </div>

            {connectingKind && (
              <ConnectRepositoryDialog
                projectId={projectId}
                kind={connectingKind}
                onClose={() => setConnectingKind(null)}
                // Connecting takes this kind over from the upload, so what has
                // changed is a different answer now.
                onConnected={() => { setConnectingKind(null); setReloadKey((n) => n + 1); }}
              />
            )}
          </>
        )}

        {/* ---- nothing moved ---- */}
        {nothingToDo && (
          <>
            <p className="dialog-note">{job.detail}</p>
            <div className="dialog-actions">
              <Button type="button" variant="secondary" onClick={onClose}>Close</Button>
              <Button onClick={() => { setJob(null); run(true); }}>Re-run anyway</Button>
            </div>
          </>
        )}

        {/* ---- 3: how far it has got ---- */}
        {running && (
          <>
            <p className="dialog-note">
              <RefreshCw size={13} strokeWidth={2.2} className="spin" />{' '}
              {job.stage ?? 'Starting…'}
            </p>
            <div className="sync-progress"><span style={{ width: `${percent}%` }} /></div>
            <p className="field-hint">
              This runs on the server. Closing this window will not stop it — reopen
              the project to see the result.
            </p>
            <div className="dialog-actions">
              <Button type="button" variant="secondary" onClick={onClose}>Close</Button>
            </div>
          </>
        )}

        {/* ---- 4: what it did ---- */}
        {finished && (
          <>
            {job.state === 'failed' ? (
              <p className="auth-error" role="alert">{job.error}</p>
            ) : (
              <>
                <p className="dialog-note">
                  <Check size={13} strokeWidth={2.4} /> {job.result.detail}
                </p>

                <div className="sync-list">
                  {job.result.sources.map((source) => (
                    <div className="sync-row" key={source.source_id}>
                      <div>
                        <b>{source.name}</b>
                        <small>{kindLabel(source.kind)}</small>
                      </div>
                      <span className="sync-state">
                        {source.refreshed
                          ? (
                            <>
                              <b>{shortRef(source.ref)}</b>
                              {source.origin === 'github' ? ' fetched' : ' uploaded'}
                            </>
                          )
                          : <>carried over</>}
                      </span>
                    </div>
                  ))}
                </div>

                <div className="sync-list">
                  {job.result.configs.map((config) => (
                    <div className="sync-row" key={config.config_id}>
                      <div>
                        <b>{config.error ? 'Failed' : `${config.trace_links} trace links`}</b>
                        <small>{config.error ?? `configuration ${config.config_key.slice(0, 8)}`}</small>
                      </div>
                      {!config.error && (
                        <span className="sync-state">
                          <em className="sync-added">+{config.added}</em>
                          <em className="sync-removed">−{config.removed}</em>
                          {broken[config.config_id] > 0 && (
                            <em className="sync-problem">
                              <AlertTriangle size={12} strokeWidth={2.2} />
                              {broken[config.config_id]} broken
                            </em>
                          )}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}

            <div className="dialog-actions">
              {/* A count is only useful if it can be followed. */}
              {Object.values(broken).some((count) => count > 0) && (
                <Link
                  className="row-open"
                  to={`/app/links?project=${projectId}`}
                  onClick={onClose}
                >
                  See what broke
                </Link>
              )}
              <Button onClick={onClose}>Done</Button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
