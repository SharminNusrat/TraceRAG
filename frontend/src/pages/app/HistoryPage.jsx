import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { GitCompare, RefreshCw, Trash2, X } from 'lucide-react';
import { PageHeader } from '../../components/common/PageHeader';
import { useAnalysis } from '../../features/analysis/AnalysisContext';
import {
  analysisLabel,
  deleteAnalysis,
  getAnalysis,
  listAnalyses,
  relativeTime,
  rerunAnalysis,
} from '../../features/projects/api/projectsApi';

export function HistoryPage() {
  const navigate = useNavigate();
  const { setResult, setRunMeta } = useAnalysis();
  const [params, setParams] = useSearchParams();
  const [analyses, setAnalyses] = useState(null);
  const [error, setError] = useState(null);
  const [rerunning, setRerunning] = useState(null);
  const [selected, setSelected] = useState([]);

  // Opening a project card lands here filtered to that project, which reuses
  // this whole page rather than duplicating it as a project detail view.
  const projectFilter = params.get('project');

  const load = () => listAnalyses(projectFilter).then(setAnalyses).catch((e) => {
    setAnalyses([]);
    setError(e.message);
  });

  useEffect(() => {
    setSelected([]);
    load();
  }, [projectFilter]);

  /**
   * Selection for comparison. Two at a time: picking a third drops the oldest
   * choice, which is less annoying than refusing the click.
   */
  const toggle = (analysisId) => setSelected((current) => (
    current.includes(analysisId)
      ? current.filter((id) => id !== analysisId)
      : [...current, analysisId].slice(-2)
  ));

  const chosen = selected
    .map((id) => analyses?.find((a) => a.analysis_id === id))
    .filter(Boolean);
  // The API only compares within a project, so the bar says why when it cannot.
  const sameProject = chosen.length === 2
    && chosen[0].project_id === chosen[1].project_id;
  // History is newest first, so the later row is the earlier run - compare
  // forwards in time by default.
  const [head, base] = chosen;
  const projectName = projectFilter && analyses?.[0]?.project_name;

  /** Push a fetched analysis into the shared context and show it. */
  const show = (detail) => {
    setResult(detail.result);
    setRunMeta({
      classifier: detail.config.classifier,
      duration: detail.execution_duration,
      // Lets the results view show - and hand back - the files this run was
      // actually performed against.
      artifacts: detail.artifacts,
      savedAs: analysisLabel(detail),
    });
    navigate('/results');
  };

  /**
   * A saved analysis is stored in the same shape a live run returns, so it can
   * be pushed straight into the analysis context and rendered by the existing
   * results view rather than a second read-only one.
   */
  const open = async (analysisId) => {
    setError(null);
    try {
      show(await getAnalysis(analysisId));
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  const rerun = async (analysis) => {
    const label = analysisLabel(analysis);
    if (!window.confirm(
      `Re-run ${label} over its stored artifacts?\n\n`
      + 'This runs the full pipeline again and may take several minutes. '
      + 'The result is saved as a new analysis alongside this one.',
    )) return;

    setError(null);
    setRerunning(analysis.analysis_id);
    try {
      show(await rerunAnalysis(analysis.analysis_id));
    } catch (requestError) {
      setError(requestError.message);
      setRerunning(null);
    }
  };

  const remove = async (analysis) => {
    const label = analysisLabel(analysis);
    if (!window.confirm(`Delete ${label}? This cannot be undone.`)) return;

    setError(null);
    try {
      await deleteAnalysis(analysis.analysis_id);
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  return (
    <>
      <PageHeader
        title={projectName ? projectName : 'Analysis History'}
        description={projectName
          ? 'Saved analysis runs in this project.'
          : 'Saved analysis runs across all of your projects.'}
        actions={projectFilter && (
          <button type="button" className="row-open" onClick={() => setParams({})}>
            <X size={13} strokeWidth={2} /> Show all projects
          </button>
        )}
      />

      {error && <p className="auth-error" role="alert">{error}</p>}

      {analyses === null && <p className="dialog-note">Loading history…</p>}

      {analyses?.length === 0 && (
        <p className="dialog-note">
          {projectFilter
            ? 'This project has no saved analyses yet.'
            : 'Nothing saved yet. Run an analysis and choose “Save results” to keep it here.'}
        </p>
      )}

      {chosen.length > 0 && (
        <div className="compare-bar">
          <span>{chosen.length} of 2 runs selected</span>
          {chosen.length === 2 && !sameProject && (
            <em>Runs must be from the same project to compare.</em>
          )}
          {chosen.length === 2 && sameProject && (
            <Link className="row-open" to={`/app/compare/${base.analysis_id}/${head.analysis_id}`}>
              <GitCompare size={13} strokeWidth={2} /> Compare
            </Link>
          )}
          <button type="button" className="row-open" onClick={() => setSelected([])}>
            Clear
          </button>
        </div>
      )}

      {Boolean(analyses?.length) && (
        <section className="history-card">
          {analyses.map((analysis, index) => {
            const busy = rerunning === analysis.analysis_id;
            return (
              <article className="analysis-row" key={analysis.analysis_id}>
                <input
                  type="checkbox"
                  className="row-select"
                  checked={selected.includes(analysis.analysis_id)}
                  onChange={() => toggle(analysis.analysis_id)}
                  aria-label={`Select ${analysisLabel(analysis)} for comparison`}
                />
                {/* Position in this list - newest first - not the database id. */}
                <span>{index + 1}</span>
                <div>
                  <b>{analysisLabel(analysis)}</b>
                  <small>
                    {analysis.project_name} · {analysis.link_count} trace links ·{' '}
                    {analysis.classifier_type} classifier
                    {analysis.artifact_count > 0 && ` · ${analysis.artifact_count} artifacts`}
                  </small>
                </div>
                <button
                  type="button"
                  className="row-open"
                  onClick={() => open(analysis.analysis_id)}
                  disabled={Boolean(rerunning)}
                >
                  Open
                </button>
                {/* Only an analysis that kept its files can be run again. */}
                {analysis.artifact_count > 0 && (
                  <button
                    type="button"
                    className="row-rerun"
                    onClick={() => rerun(analysis)}
                    disabled={Boolean(rerunning)}
                    title="Run the pipeline again over the stored artifacts"
                  >
                    <RefreshCw size={13} strokeWidth={2} className={busy ? 'spin' : undefined} />
                    {busy ? 'Running…' : 'Re-run'}
                  </button>
                )}
                <time>{relativeTime(analysis.created_at)}</time>
                <button
                  type="button"
                  className="card-delete"
                  onClick={() => remove(analysis)}
                  disabled={Boolean(rerunning)}
                  aria-label={`Delete ${analysisLabel(analysis)}`}
                >
                  <Trash2 size={14} strokeWidth={2} />
                </button>
              </article>
            );
          })}
        </section>
      )}
    </>
  );
}
