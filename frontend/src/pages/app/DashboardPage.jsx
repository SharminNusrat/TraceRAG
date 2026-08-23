import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '../../components/common/Button';
import { PageHeader } from '../../components/common/PageHeader';
import {
  listAnalyses,
  listProjects,
  relativeTime,
  runTimestamp,
} from '../../features/projects/api/projectsApi';

const RECENT_SHOWN = 5;

export function DashboardPage() {
  const [projects, setProjects] = useState(null);
  const [analyses, setAnalyses] = useState(null);

  useEffect(() => {
    let active = true;
    Promise.all([listProjects(), listAnalyses()])
      .then(([projectRows, analysisRows]) => {
        if (!active) return;
        setProjects(projectRows);
        setAnalyses(analysisRows);
      })
      .catch(() => {
        if (!active) return;
        setProjects([]);
        setAnalyses([]);
      });
    return () => { active = false; };
  }, []);

  const loading = projects === null || analyses === null;
  const totalLinks = (analyses ?? []).reduce((sum, a) => sum + a.link_count, 0);
  const recent = (analyses ?? []).slice(0, RECENT_SHOWN);

  return (
    <>
      <PageHeader
        eyebrow="My Workspace"
        title="Your Traceability, at a Glance"
        actions={<Link to="/analysis"><Button>+ New Analysis</Button></Link>}
      />

      <section className="metrics">
        <Metric value={loading ? '—' : projects.length} label="Projects" />
        <Metric value={loading ? '—' : analyses.length} label="Saved Analyses" />
        <Metric value={loading ? '—' : totalLinks} label="Trace Links Recovered" />
      </section>

      <section className="dashboard-card">
        <header>
          <div><h2>Recent Analyses</h2><p>Pick up where you left off.</p></div>
          <Link to="/app/history">View all →</Link>
        </header>

        {loading && <p className="dialog-note">Loading…</p>}

        {!loading && recent.length === 0 && (
          <p className="dialog-note">
            Nothing saved yet. <Link to="/analysis">Run an analysis</Link> and save it to see
            it here.
          </p>
        )}

        {recent.map((analysis, index) => (
          <Link
            className="analysis-row"
            to="/app/history"
            key={analysis.analysis_id}
          >
            <span>{index + 1}</span>
            <div>
              {/* The project is what identifies the row; when the run happened
                  is the detail underneath it. */}
              <b>{analysis.project_name} · {analysis.link_count} trace links</b>
              <small>{runTimestamp(analysis)}</small>
            </div>
            <time>{relativeTime(analysis.created_at)}</time>
          </Link>
        ))}
      </section>
    </>
  );
}

function Metric({ value, label }) {
  return <article className="metric"><b>{value}</b><strong>{label}</strong></article>;
}
