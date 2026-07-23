import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, GitBranch, Search, ShieldCheck } from 'lucide-react';
import { Brand } from '../components/common/Brand';
import { Button } from '../components/common/Button';

const features = [
  {
    icon: Search,
    title: 'Trace discovery',
    desc: 'Automatically recover links between requirements and source code using RAG-powered analysis.',
  },
  {
    icon: GitBranch,
    title: 'Gap detection',
    desc: 'Identify unimplemented requirements and code without traceability coverage.',
  },
  {
    icon: ShieldCheck,
    title: 'Evidence-backed',
    desc: 'Every link comes with an explanation so you can review and trust the results.',
  },
];

export function LandingPage() {
  const navigate = useNavigate();

  return (
    <main className="landing">
      <nav className="topbar">
        <Brand />
        <div>
          <Link className="text-link" to="/auth">Sign in</Link>
          <Button className="compact pill" onClick={() => navigate('/auth?mode=signup')}>
            Create account
          </Button>
        </div>
      </nav>

      <section className="hero">
        <div className="hero-badge">Traceability made visible</div>
        <h1>Trace<span>RAG</span></h1>
        <p>
          Recover meaningful links between your requirements and source code. Review the
          evidence behind each match, identify implementation gaps, and make traceability
          work easier to trust.
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
