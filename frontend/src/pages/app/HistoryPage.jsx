import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { GitCompare, RefreshCw, Share2, Trash2, X } from 'lucide-react';
import { Button } from '../../components/common/Button';
import { PageHeader } from '../../components/common/PageHeader';
import { useAnalysis } from '../../features/analysis/AnalysisContext';
import { RerunDialog } from '../../features/projects/components/RerunDialog';
import { SourcesPanel } from '../../features/sync/components/SourcesPanel';
import {

  deleteAnalysis,
  getAnalysis,
  kindOfSide,
  listAnalyses,
  relativeTime,
  runTimestamp,
} from '../../features/projects/api/projectsApi';

export function HistoryPage() {
  const navigate = useNavigate();
  const { setResult, setRunMeta } = useAnalysis();
  const [params, setParams] = useSearchParams();
  const [analyses, setAnalyses] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState([]);
  // The analysis whose re-run is being set up, if any. The dialog owns the run
  // itself, so the rows only need to know that one is in progress.
  const [configuring, setConfiguring] = useState(null);

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
      // The whole configuration, not just the classifier: the preprocessors
      // and output levels decide what the identifiers in this result refer to,
      // so a reopened run is not readable without them.
      config: detail.config,
      sourceKind: kindOfSide(detail.artifacts, 'source'),
      targetKind: kindOfSide(detail.artifacts, 'target'),
      // Lets the results view show - and hand back - the files this run was
      // actually performed against.
      artifacts: detail.artifacts,
      savedAs: runTimestamp(detail),
      // It is already a stored analysis - opened from history, or just written
      // by a re-run. Without this the results view believes it is looking at an
      // unsaved run and offers to save it, which files a second copy.
      savedTo: {
        analysis_id: detail.analysis_id,
        project_id: detail.project_id,
        project_name: detail.project_name,
      },
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

  const startRerun = (analysis) => {
    setError(null);
    setConfiguring(analysis);
  };

  const remove = async (analysis) => {
    const label = runTimestamp(analysis);
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
          <>
            {/* Started from the project, so the run knows where it belongs -
                which is what lets it take a side from a connected source
                instead of asking for the files again. */}
            <Link to={`/analysis?project=${projectFilter}`}>
              <Button>+ New Analysis</Button>
            </Link>
            {/* The live answer, as opposed to what any one run found. */}
            <Link className="row-open" to={`/app/links?project=${projectFilter}`}>
              <Share2 size={13} strokeWidth={2} /> Trace links
            </Link>
            <button type="button" className="row-open" onClick={() => setParams({})}>
              <X size={13} strokeWidth={2} /> Show all projects
            </button>
          </>
        )}
      />

      {error && <p className="auth-error" role="alert">{error}</p>}

      {/* Only inside a project. Across all projects there is no single set of
          sources to show, and nothing to sync. */}
      {projectFilter && <SourcesPanel projectId={projectFilter} />}

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
            const busy = configuring?.analysis_id === analysis.analysis_id;
            return (
              <article className="analysis-row" key={analysis.analysis_id}>
                <input
                  type="checkbox"
                  className="row-select"
                  checked={selected.includes(analysis.analysis_id)}
                  onChange={() => toggle(analysis.analysis_id)}
                  aria-label={`Select ${runTimestamp(analysis)} for comparison`}
                />
                {/* Position in this list - newest first - not the database id. */}
                <span>{index + 1}</span>
                <div>
                  {/* The project leads the row, as it does on the overview,
                      with when the run happened underneath - that is what
                      identifies a run. The note, if there is one, says why
                      this run exists, which is what two runs are read against. */}
                  <b>
                    {analysis.project_name}
                    {/* Which state of the artifacts this ran against. The
                        version history lists the same events by number, and
                        without this there is nothing linking the two. */}
                    {analysis.version_number != null && (
                      <span className="analysis-version">v{analysis.version_number}</span>
                    )}
                  </b>
                  <small>
                    {runTimestamp(analysis)} · {analysis.link_count} trace links ·{' '}
                    {analysis.classifier_type} classifier
                    {analysis.artifact_count > 0 && (
                      analysis.files_available
                        ? ` · ${analysis.artifact_count} artifacts`
                        : ` · ${analysis.artifact_count} artifacts (files no longer on disk)`
                    )}
                  </small>
                  {analysis.note && <p className="analysis-note">{analysis.note}</p>}
                </div>
                <button
                  type="button"
                  className="row-open"
                  onClick={() => open(analysis.analysis_id)}
                  disabled={Boolean(configuring)}
                >
                  Open
                </button>
                {/* Only an analysis whose files are still on disk can be run
                    again. The rows outlive the bytes, so this is checked here
                    rather than discovered after the dialog has been filled in. */}
                {analysis.artifact_count > 0 && (
                  <button
                    type="button"
                    className="row-rerun"
                    onClick={() => startRerun(analysis)}
                    disabled={Boolean(configuring) || !analysis.files_available}
                    title={analysis.files_available
                      ? 'Run the pipeline again over the stored artifacts'
                      : 'The stored files for this run are no longer on disk'}
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
                  disabled={Boolean(configuring)}
                  aria-label={`Delete ${runTimestamp(analysis)}`}
                >
                  <Trash2 size={14} strokeWidth={2} />
                </button>
              </article>
            );
          })}
        </section>
      )}

      {configuring && (
        <RerunDialog
          analysis={configuring}
          onClose={() => setConfiguring(null)}
          onDone={(detail) => { setConfiguring(null); show(detail); }}
        />
      )}
    </>
  );
}
