import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Share2, Trash2, X } from 'lucide-react';
import { Button } from '../../components/common/Button';
import { PageHeader } from '../../components/common/PageHeader';
import { findKind, useCapabilities } from '../../features/analysis/api/capabilitiesApi';
import { deleteConfig, listConfigs, relativeTime } from '../../features/projects/api/projectsApi';

export function HistoryPage() {
  const { capabilities } = useCapabilities();
  const [params, setParams] = useSearchParams();
  const [analyses, setAnalyses] = useState(null);
  const [error, setError] = useState(null);

  // Opening a project card lands here filtered to that project, which reuses
  // this whole page rather than duplicating it as a project detail view.
  const projectFilter = params.get('project');

  const load = () => listConfigs(projectFilter).then(setAnalyses).catch((e) => {
    setAnalyses([]);
    setError(e.message);
  });

  useEffect(() => { load(); }, [projectFilter]);

  const projectName = projectFilter && analyses?.[0]?.project_name;
  const kindLabel = (key) => findKind(capabilities, key)?.label ?? key ?? 'Unknown';

  const remove = async (analysis) => {
    const label = `${kindLabel(analysis.source_kind)} → ${kindLabel(analysis.target_kind)}`;
    if (!window.confirm(`Delete the analysis ${label} with all its versions? This cannot be undone.`)) return;

    setError(null);
    try {
      await deleteConfig(analysis.project_id, analysis.config_id);
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
          ? 'Saved analyses in this project.'
          : 'Saved analyses across all of your projects.'}
        actions={projectFilter && (
          <>
            {/* Started from the project, so the run is saved into it. */}
            <Link to={`/analysis?project=${projectFilter}`}>
              <Button>+ New Analysis</Button>
            </Link>
            <button type="button" className="row-open" onClick={() => setParams({})}>
              <X size={13} strokeWidth={2} /> Show all projects
            </button>
          </>
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

      {Boolean(analyses?.length) && (
        <section className="history-card">
          {analyses.map((analysis, index) => (
            <article className="analysis-row" key={analysis.config_id}>
              {/* Position in this list - newest first - not the database id. */}
              <span>{index + 1}</span>
              <div>
                <b>
                  {analysis.project_name}
                  {analysis.version_number != null && (
                    <span className="analysis-version">v{analysis.version_number}</span>
                  )}
                </b>
                <small>
                  {kindLabel(analysis.source_kind)} → {kindLabel(analysis.target_kind)} ·{' '}
                  {analysis.link_count} trace links · {analysis.config.classifier} classifier ·{' '}
                  {analysis.analysis_count} run{analysis.analysis_count === 1 ? '' : 's'}
                </small>
              </div>
              <Link
                className="row-open"
                to={`/app/analyses/${analysis.config_id}?project=${analysis.project_id}`}
              >
                Open
              </Link>
              <Link
                className="row-open"
                to={`/app/links?project=${analysis.project_id}&config=${analysis.config_id}`}
              >
                <Share2 size={13} strokeWidth={2} /> Trace links
              </Link>
              <time>{relativeTime(analysis.latest_run_at ?? analysis.created_at)}</time>
              <button
                type="button"
                className="card-delete"
                onClick={() => remove(analysis)}
                aria-label={`Delete analysis ${index + 1}`}
              >
                <Trash2 size={14} strokeWidth={2} />
              </button>
            </article>
          ))}
        </section>
      )}
    </>
  );
}
