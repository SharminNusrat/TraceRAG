import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { FileText, FolderCode, GitBranch, Network, RefreshCw, Share2, UploadCloud, X } from 'lucide-react';
import { PageHeader } from '../../components/common/PageHeader';
import {
  classifierLabel,
  findKind,
  preprocessorLabel,
  useCapabilities,
} from '../../features/analysis/api/capabilitiesApi';
import { deleteAnalysis, getConfig, runTimestamp } from '../../features/projects/api/projectsApi';
import { RerunDialog } from '../../features/projects/components/RerunDialog';
import { useOpenRun } from '../../features/projects/useOpenRun';
import { UpdateDialog } from '../../features/sync/components/UpdateDialog';
import { VersionHistory } from '../../features/sync/components/VersionHistory';
import { shortRef, sourceLocation } from '../../features/sync/api/syncApi';

const ICONS = { requirements: FileText, code: FolderCode, architecture: Network };

/**
 * One analysis: its two sides with the files each holds now, and its history.
 *
 * Each side is updated on its own - given its complete current files, or
 * fetched from GitHub for a code side - and every update that changes
 * something becomes the analysis's next version. A re-run repeats the newest
 * version's files and stays inside that version.
 */
export function AnalysisDetailPage() {
  const { configId } = useParams();
  const [params, setParams] = useSearchParams();
  const projectId = params.get('project');
  const { capabilities } = useCapabilities();
  const { open, show } = useOpenRun();

  const [analysis, setAnalysis] = useState(null);
  const [error, setError] = useState(null);
  const [updating, setUpdating] = useState(null);
  // Set when GitHub sent the browser back here from connecting a side, so its
  // dialog opens again where the user left it.
  const [reconnected, setReconnected] = useState(false);
  const [rerunning, setRerunning] = useState(null);
  // Bumped after anything that makes a version or a run, so the history
  // below reads itself again.
  const [historyKey, setHistoryKey] = useState(0);

  const load = () => getConfig(projectId, configId)
    .then(setAnalysis)
    .catch((requestError) => { setAnalysis(null); setError(requestError.message); });

  useEffect(() => { load(); }, [projectId, configId]);

  // Back from GitHub: reopen the side being connected, say if it failed, and
  // clear the address so a refresh does not do it again.
  useEffect(() => {
    const side = analysis?.sides.find((item) => String(item.source_id) === params.get('connect'));
    if (!side) return;
    if (params.get('github') === 'error') setError(params.get('message') ?? 'GitHub sign-in failed.');
    setUpdating(side);
    setReconnected(true);
    setParams({ project: projectId }, { replace: true });
  }, [analysis, params]);

  const refresh = () => { load(); setHistoryKey((n) => n + 1); };
  const kindLabel = (key) => findKind(capabilities, key)?.label ?? key ?? 'Unknown';

  const openRun = async (analysisId) => {
    setError(null);
    try {
      await open(analysisId);
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  const removeRun = async (run) => {
    if (!window.confirm(`Delete ${runTimestamp(run)}? This cannot be undone.`)) return;
    setError(null);
    try {
      await deleteAnalysis(run.analysis_id);
      refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  if (!analysis) {
    return (
      <>
        <PageHeader title="Analysis" />
        {error
          ? <p className="auth-error" role="alert">{error}</p>
          : <p className="dialog-note">Loading the analysis…</p>}
      </>
    );
  }

  const { config } = analysis;
  const stored = analysis.sides.length === 2;

  return (
    <>
      <PageHeader
        eyebrow={analysis.project_name}
        title={`${kindLabel(analysis.source_kind)} → ${kindLabel(analysis.target_kind)}`}
        description={
          `${preprocessorLabel(capabilities, config.source_preprocessor)} → `
          + `${preprocessorLabel(capabilities, config.target_preprocessor)}`
          + ` · ${classifierLabel(capabilities, config.classifier)} · top ${config.n_results}`
          + (analysis.version_number ? ` · v${analysis.version_number}` : '')
        }
        actions={(
          <>
            {stored && analysis.latest_analysis_id && (
              <button
                type="button"
                className="row-rerun"
                onClick={() => setRerunning({
                  analysis_id: analysis.latest_analysis_id,
                  project_name: analysis.project_name,
                  created_at: analysis.latest_run_at,
                })}
              >
                <RefreshCw size={13} strokeWidth={2} /> Re-run
              </button>
            )}
            <Link
              className="row-open"
              to={`/app/links?project=${projectId}&config=${analysis.config_id}`}
            >
              <Share2 size={13} strokeWidth={2} /> Trace links
            </Link>
            <Link className="row-open" to={`/app/history?project=${projectId}`}>
              <X size={13} strokeWidth={2} /> Back to the project
            </Link>
          </>
        )}
      />

      {error && <p className="auth-error" role="alert">{error}</p>}

      <section className="side-grid">
        {analysis.sides.map((side) => {
          const Icon = side.origin === 'github' ? GitBranch : (ICONS[side.kind] ?? FileText);
          return (
            <article className="artifact-strip" key={side.source_id}>
              <header>
                <b>
                  <Icon size={14} strokeWidth={2} /> {kindLabel(side.kind)}
                  <span className="side-role">{side.role}</span>
                </b>
                <span className="strip-actions">
                  <button
                    type="button"
                    className="row-open"
                    onClick={() => setUpdating(side)}
                    disabled={!stored}
                  >
                    <UploadCloud size={13} strokeWidth={2} /> Update
                  </button>
                </span>
              </header>
              <p className="dialog-note">
                {side.name} · {sourceLocation(side)}
                {side.last_sync_ref && <> · <code>{shortRef(side.last_sync_ref)}</code></>}
              </p>
              <ul className="side-files">
                {side.files.map((path) => <li key={path}><code>{path}</code></li>)}
              </ul>
            </article>
          );
        })}
      </section>

      {!stored && (
        <p className="dialog-note">
          This analysis was saved without its files, so it cannot be updated or re-run.
        </p>
      )}

      <h2 className="section-heading">Version History</h2>
      <VersionHistory
        key={historyKey}
        projectId={projectId}
        configId={analysis.config_id}
        onOpenRun={openRun}
        onDeleteRun={removeRun}
      />

      {updating && (
        <UpdateDialog
          projectId={projectId}
          config={analysis}
          side={updating}
          connectOnOpen={reconnected}
          onClose={() => { setUpdating(null); setReconnected(false); }}
          onFinished={refresh}
        />
      )}

      {rerunning && (
        <RerunDialog
          analysis={rerunning}
          onClose={() => setRerunning(null)}
          onDone={(detail) => { setRerunning(null); show(detail); }}
        />
      )}
    </>
  );
}
