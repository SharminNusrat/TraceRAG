import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ArrowRight, Check, Plug, RefreshCw, UploadCloud, X } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import { ChangeSummary } from './ChangeSummary';
import { ConnectRepositoryDialog } from './ConnectRepositoryDialog';
import { findKind, useCapabilities } from '../../analysis/api/capabilitiesApi';
import {
  getJob,
  getSyncStatus,
  shortRef,
  sourceLocation,
  stageSideFiles,
  startGitHubOAuth,
  startUpdate,
} from '../api/syncApi';

// How often a running job is asked where it has got to. Long enough not to
// hammer the server, short enough that a stage change is noticed.
const POLL_MS = 1500;

const KIND_CODE = 'code';

/**
 * Updating one side of one analysis, from what it holds now to what the run did.
 *
 * The side is given its complete current file set - uploaded, or fetched from
 * GitHub for a code side - and the analysis is re-run over it as its next
 * version. Nothing else in the project is touched.
 */
export function UpdateDialog({ projectId, config, side, connectOnOpen, onClose, onFinished }) {
  const { capabilities } = useCapabilities();
  const [status, setStatus] = useState(null);
  const [note, setNote] = useState('');
  const [staged, setStaged] = useState(null);
  const [staging, setStaging] = useState(false);
  // Open straight away when coming back from connecting GitHub for this side.
  const [connecting, setConnecting] = useState(Boolean(connectOnOpen));
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const timer = useRef(null);
  const picker = useRef(null);

  useEffect(() => {
    let active = true;
    getSyncStatus(projectId, config.config_id)
      .then((rows) => {
        if (active) setStatus(rows.find((row) => row.source_id === side.source_id) ?? null);
      })
      .catch((requestError) => { if (active) { setStatus(null); setError(requestError.message); } });
    return () => { active = false; };
  }, [projectId, config.config_id, side.source_id, reloadKey]);

  // Stop polling when the dialog goes away mid-run; the job carries on server
  // side either way.
  useEffect(() => () => clearTimeout(timer.current), []);

  const watch = (jobId) => {
    const poll = async () => {
      try {
        const latest = await getJob(jobId);
        setJob(latest);
        if (latest.state === 'succeeded' || latest.state === 'failed') {
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
   * Take the files chosen for this side.
   *
   * Sent as soon as they are picked rather than with the update, so the
   * answer - how many of them land where the side's files already are - is
   * there to read before anything is re-run.
   */
  const stage = async (files) => {
    if (!files.length) return;
    setError(null);
    setStaging(true);
    try {
      setStaged(await stageSideFiles(projectId, config.config_id, side.source_id, files));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setStaging(false);
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

  const run = async () => {
    setError(null);
    setPending(true);
    try {
      const started = await startUpdate(projectId, config.config_id, {
        sourceId: side.source_id,
        uploadId: staged?.upload_id,
        note,
      });
      if (!started.started) {
        // Nothing moved, so no job was filed. Say so.
        setJob({ state: 'nothing', detail: started.detail });
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

  const kind = findKind(capabilities, side.kind);
  const fetchable = status?.checkable && status.changed;
  const running = job && (job.state === 'queued' || job.state === 'running');
  const finished = job && (job.state === 'succeeded' || job.state === 'failed');
  const percent = job?.progress_total
    ? Math.round((job.progress_current / job.progress_total) * 100)
    : 0;

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Update side">
      <section className="dialog-card wide">
        <header className="dialog-head">
          <h2>Update {kind?.label ?? side.kind}</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={2.2} />
          </button>
        </header>

        {status === null && !error && <p className="dialog-note">Checking this side…</p>}

        {/* ---- what this side holds, and what it will be updated to ---- */}
        {status !== null && !job && (
          <>
            <div className="sync-list">
              <div className="sync-row">
                <div>
                  <b>{side.name}</b>
                  <small>{kind?.label ?? side.kind} · {sourceLocation(side)}</small>
                </div>
                <span className="sync-state">
                  {status.error ? (
                    <>
                      <em className="sync-problem">
                        <AlertTriangle size={12} strokeWidth={2.2} /> Could not check
                      </em>
                      {status.needs_reconnect && (
                        <button type="button" className="row-open" onClick={reconnect}>
                          <Plug size={12} strokeWidth={2} /> Reconnect GitHub
                        </button>
                      )}
                    </>
                  ) : staged ? (
                    <>
                      <b>{staged.file_count} file{staged.file_count === 1 ? '' : 's'}</b>
                      {' ready'}
                      {staged.missing > 0 && (
                        <em className="sync-problem" title={
                          `${staged.missing} file(s) this side holds are not in this `
                          + 'upload. Their links will be reported broken.'
                        }>
                          <AlertTriangle size={12} strokeWidth={2.2} />
                          {staged.missing} not included
                        </em>
                      )}
                    </>
                  ) : status.checkable ? (
                    status.changed ? (
                      <>
                        {shortRef(status.last_sync_ref) ?? 'new'}
                        <ArrowRight size={12} strokeWidth={2.2} />
                        <b>{shortRef(status.latest_ref)}</b>
                      </>
                    ) : <>Up to date</>
                  ) : null}
                  <button
                    type="button"
                    className="row-open"
                    title="Upload this side's complete current files"
                    onClick={() => picker.current?.click()}
                  >
                    <UploadCloud size={12} strokeWidth={2} />
                    {staging ? ' Uploading…' : ' Upload files'}
                  </button>
                  <input
                    ref={picker}
                    type="file"
                    multiple
                    hidden
                    accept={(kind?.extensions ?? []).join(',')}
                    onChange={(event) => {
                      stage(Array.from(event.target.files));
                      // Cleared so picking the same files again still counts.
                      event.target.value = '';
                    }}
                  />
                  {side.kind === KIND_CODE && side.origin !== 'github' && (
                    <button type="button" className="row-open" onClick={() => setConnecting(true)}>
                      <Plug size={12} strokeWidth={2} /> Connect
                    </button>
                  )}
                </span>
                {status.error && !status.needs_reconnect && (
                  <p className="sync-error">{status.error}</p>
                )}
              </div>
            </div>

            {staged && <ChangeSummary changes={staged.changes} />}

            <label>
              <span className="field-name">
                Note<span className="dialog-optional">optional</span>
              </span>
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                name="tracerag-update-note"
                autoComplete="off"
                data-lpignore="true"
                data-form-type="other"
              />
            </label>

            {error && <p className="auth-error" role="alert">{error}</p>}

            <div className="dialog-actions">
              <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
              <Button
                onClick={run}
                disabled={pending || !((staged && staged.changes?.meaningful !== false) || fetchable)}
              >
                {pending ? 'Starting…' : 'Update'}
              </Button>
            </div>

            {connecting && (
              <ConnectRepositoryDialog
                projectId={projectId}
                configId={config.config_id}
                sourceId={side.source_id}
                needsReconnect={status?.needs_reconnect}
                onClose={() => setConnecting(false)}
                // The side now comes from the repository, so where it stands is
                // a different answer.
                onConnected={() => { setConnecting(false); setReloadKey((n) => n + 1); onFinished?.(); }}
              />
            )}
          </>
        )}

        {/* ---- nothing to do ---- */}
        {job?.state === 'nothing' && (
          <>
            <p className="dialog-note">{job.detail}</p>
            <div className="dialog-actions">
              <Button type="button" onClick={onClose}>Close</Button>
            </div>
          </>
        )}

        {/* ---- how far it has got ---- */}
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

        {/* ---- what it did ---- */}
        {finished && (
          <>
            {job.state === 'failed' ? (
              <p className="auth-error" role="alert">{job.error}</p>
            ) : (
              <>
                <p className="dialog-note">
                  <Check size={13} strokeWidth={2.4} /> {job.result.detail}
                </p>
                <ChangeSummary changes={job.result.changes} />
                {job.result.synced && (
                  <div className="sync-list">
                    <div className="sync-row">
                      <div>
                        <b>{job.result.trace_links} trace links</b>
                        <small>version {job.result.version_number}</small>
                      </div>
                      <span className="sync-state">
                        <em className="sync-added">+{job.result.added}</em>
                        <em className="sync-removed">−{job.result.removed}</em>
                      </span>
                    </div>
                  </div>
                )}
              </>
            )}

            <div className="dialog-actions">
              {job.state === 'succeeded' && job.result.synced && (
                <Link
                  className="row-open"
                  to={`/app/links?project=${projectId}&config=${config.config_id}`}
                  onClick={onClose}
                >
                  Trace links
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
