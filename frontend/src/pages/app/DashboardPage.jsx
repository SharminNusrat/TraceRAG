import { Link } from 'react-router-dom';
import { Button } from '../../components/common/Button';
import { PageHeader } from '../../components/common/PageHeader';

const recentAnalyses = [
  ['Authentication coverage', 18, 46],
  ['Release 2.4 requirements', 24, 72],
  ['Payment workflow review', 11, 31],
];

export function DashboardPage() {
  return (
    <>
      <PageHeader
        eyebrow="My workspace"
        title="Your traceability, at a glance"
        actions={<Link to="/analysis"><Button>+ New analysis</Button></Link>}
      />
      <section className="metrics">
        <Metric value="3" label="Active projects" note="Across your workspace" />
        <Metric value="12" label="Saved analyses" note="4 completed this month" />
        <Metric value="86%" label="Average confidence" note="Across recent analyses" />
      </section>
      <section className="dashboard-card">
        <header>
          <div><h2>Recent analyses</h2><p>Pick up where you left off.</p></div>
          <Link to="/app/history">View all →</Link>
        </header>
        {recentAnalyses.map(([name, requirementCount, linkCount], index) => (
          <div className="analysis-row" key={name}>
            <span>↗</span>
            <div><b>{name}</b><small>{requirementCount} requirements · {linkCount} trace links</small></div>
            <em>Completed</em>
            <time>{index + 1}d ago</time>
          </div>
        ))}
      </section>
    </>
  );
}

function Metric({ value, label, note }) {
  return <article className="metric"><b>{value}</b><strong>{label}</strong><small>{note}</small></article>;
}
