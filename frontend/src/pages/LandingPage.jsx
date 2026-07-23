import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { Brand } from '../components/common/Brand';
import { Button } from '../components/common/Button';

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
        <h1>Trace<span>RAG</span></h1>
        <Button className="start-button pill" onClick={() => navigate('/analysis')}>
          Start analysis <ArrowRight size={17} strokeWidth={2.4} />
        </Button>
        <p>
          Recover meaningful links between your requirements and source code. Review the
          evidence behind each match, identify implementation gaps, and make traceability
          work easier to trust.
        </p>
        <p className="hero-note">
          Run an analysis right away. Sign in only when you want to save results, create
          projects, or return to previous work.
        </p>
      </section>
    </main>
  );
}
