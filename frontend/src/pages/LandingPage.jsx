import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, GitCompareArrows, Network, ShieldCheck, Table2 } from 'lucide-react';
import { Brand } from '../components/common/Brand';
import { Button } from '../components/common/Button';
import { useAuth } from '../features/auth/AuthContext';

// What the tool does today, in the order a first visit would ask about it.
const features = [
  {
    icon: Network,
    title: 'Links across artifacts',
    desc: 'Trace requirements, architecture models and source code to each other with retrieval and an LLM classifier.',
  },
  {
    icon: ShieldCheck,
    title: 'Evidence for every link',
    desc: 'Each link carries a confidence score and the reasoning behind it, so it can be reviewed, not just trusted.',
  },
  {
    icon: GitCompareArrows,
    title: 'Change-aware versions',
    desc: 'Update a side by upload or from GitHub and see which links stay valid, are new, stop being found, or break.',
  },
  {
    icon: Table2,
    title: 'Matrix, gaps and export',
    desc: 'Read coverage in a traceability matrix, spot elements with no link, and export to CSV, Excel or PDF.',
  },
];

export function LandingPage() {
  const navigate = useNavigate();
  const { user } = useAuth();

  return (
    <main className="landing">
      <nav className="topbar">
        <Brand />
        <div>
          {/* Already signed in: offer the workspace, not another sign-in. */}
          {user ? (
            <Button className="compact pill" onClick={() => navigate('/app')}>
              Go to workspace
            </Button>
          ) : (
            <>
              <Link className="text-link" to="/auth">Sign in</Link>
              <Button className="compact pill" onClick={() => navigate('/auth?mode=signup')}>
                Create account
              </Button>
            </>
          )}
        </div>
      </nav>

      <section className="hero">
        <div className="hero-badge">Traceability made visible</div>
        <h1>Trace<span>RAG</span></h1>
        <p>
          Recover trace links between requirements, architecture models and source code.
          Review the evidence behind every link, and see exactly what changed when your
          artifacts do.
        </p>
        <button className="button button-primary start-button pill" onClick={() => navigate('/analysis')}>
          Start analysis <ArrowRight size={17} strokeWidth={2.4} />
        </button>
        <p className="hero-note">
          Run an analysis right away. Sign in only when you want to save results, create
          projects, or return to previous work.
        </p>
      </section>

      <section className="features-section">
        {features.map(({ icon: Icon, title, desc }) => (
          <div className="feature-card" key={title}>
            <div className="feature-icon">
              <Icon size={22} strokeWidth={1.8} />
            </div>
            <h3>{title}</h3>
            <p>{desc}</p>
          </div>
        ))}
      </section>

      <div className="landing-glow-bottom" />
    </main>
  );
}
