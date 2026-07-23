import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Brand } from '../components/common/Brand';
import { Button } from '../components/common/Button';
import { useAnalysis } from '../features/analysis/AnalysisContext';
import { useAuth } from '../features/auth/AuthContext';
import { mockResult } from '../features/analysis/mockResult';

const requirements = [
  'Users can authenticate with email and password.',
  'The system shall create a new project workspace.',
  'Users can review previous analysis results.',
];

export function ResultsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { result = mockResult } = useAnalysis();
  const [selected, setSelected] = useState(0);
  const [saved, setSaved] = useState(false);
  const link = result.trace_links[selected];

  const save = () => {
    if (user) {
      setSaved(true);
      return;
    }

    navigate('/auth?intent=save&returnTo=/results');
  };

  return (
    <main className="results-page">
      <nav className="workflow-nav">
        <Brand />
        <span>Analysis results</span>
        <Button variant="ghost" onClick={() => navigate('/')}>Exit</Button>
      </nav>

      <div className="results-content">
        <header className="results-header">
          <div>
            <div className="eyebrow"><span />Analysis complete</div>
            <h1>Traceability results</h1>
            <p>
              {result.summary.requirements} requirements · {result.summary.trace_links} trace links
              {' · completed just now'}
            </p>
          </div>
          <div>
            <Button variant="secondary">Export</Button>
            <Button onClick={save}>{saved ? 'Saved to project' : 'Save results'}</Button>
          </div>
        </header>

        <section className="result-stats">
          <div><b>{result.summary.trace_links}</b><span>Trace links found</span></div>
          <div><b>{result.summary.high_confidence}</b><span>High-confidence links</span></div>
          <div><b>4</b><span>Requirements to review</span></div>
          <div><b>{result.unimplemented.length}</b><span>Potentially unimplemented</span></div>
        </section>

        <section className="results-workbench">
          <aside className="requirement-panel">
            <header><b>Requirements</b><span>{result.trace_links.length} linked</span></header>
            {result.trace_links.map((item, index) => (
              <button
                className={selected === index ? 'requirement active' : 'requirement'}
                onClick={() => setSelected(index)}
                key={item.source_id}
              >
                <small>{item.source_id}</small>
                <b>{requirements[index]}</b>
                <span>{Math.round(item.confidence * 100)}% confidence</span>
              </button>
            ))}
          </aside>

          <article className="trace-detail">
            <header>
              <div>
                <small>{link.source_id}</small>
                <h2>{requirements[selected]}</h2>
              </div>
              <span className={`confidence ${link.confidence_level}`}>
                {link.confidence_level} confidence
              </span>
            </header>

            <div className="implementation">
              <div>
                <span>↗</span>
                <section>
                  <small>Implementation link</small>
                  <h3>{link.target_id}</h3>
                </section>
                <b>{Math.round(link.confidence * 100)}%</b>
              </div>
              <p>{link.explanation}</p>
              <Button variant="ghost">View code context →</Button>
            </div>

            <div className="connection">
              <div><small>REQUIREMENT</small>{link.source_id}</div>
              <i>→</i>
              <div className="code"><small>CODE</small>{link.target_id.split('/').at(-1)}</div>
            </div>
          </article>
        </section>
      </div>
    </main>
  );
}
