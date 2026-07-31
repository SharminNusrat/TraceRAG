import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, CheckCircle2, GitBranch, Info, Layers, Link2, X } from 'lucide-react';
import { Brand } from '../components/common/Brand';
import { Button } from '../components/common/Button';
import { useAnalysis } from '../features/analysis/AnalysisContext';
import { useAuth } from '../features/auth/AuthContext';
import { normalizeResult } from '../features/analysis/api/analyzeApi';
import { buildMatrixRows } from '../features/analysis/matrix';
import { TraceabilityMatrix } from '../features/analysis/components/TraceabilityMatrix';
import { ExportMenu } from '../features/analysis/components/ExportMenu';
import { mockResult } from '../features/analysis/mockResult';

const TABS = [
  ['explorer', 'Trace Explorer'],
  ['matrix', 'Traceability Matrix'],
];

/**
 * Per-link explanation disclosure. The reasoning classifier emits an
 * `<explanation>` per decision; the simple classifier always returns null, so
 * the button says why nothing is there rather than silently showing an empty box.
 */
function ExplainToggle({ link, classifier }) {
  const [open, setOpen] = useState(false);
  const explanation = link.explanation?.trim();

  return (
    <div className="explain">
      <button
        type="button"
        className={open ? 'explain-btn open' : 'explain-btn'}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <Info size={13} strokeWidth={2.2} />
        {open ? 'Hide explanation' : 'Explain'}
      </button>

      {open && (
        explanation ? (
          <p className="explain-body">{explanation}</p>
        ) : (
          <p className="explain-body muted">
            {classifier === 'simple'
              ? 'Explanation not available — the simple classifier only returns a yes/no decision. Re-run with the reasoning classifier to see why this link was recovered.'
              : 'Explanation not available for this link.'}
          </p>
        )
      )}
    </div>
  );
}

export function ResultsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { result, runMeta } = useAnalysis();
  const classifier = runMeta?.classifier;
  const [selected, setSelected] = useState(0);
  const [saved, setSaved] = useState(false);
  const [tab, setTab] = useState('matrix');

  // Falls back to the sample response when the page is opened directly.
  const view = useMemo(() => normalizeResult(result ?? mockResult), [result]);
  const matrixRows = useMemo(() => buildMatrixRows(view), [view]);

  const requirements = view.requirements;
  const activeIndex = Math.min(selected, Math.max(requirements.length - 1, 0));
  const requirement = requirements[activeIndex];
  const link = requirement?.best;

  const stats = [
    { icon: Link2, value: view.summary.trace_links, label: 'Trace links found' },
    { icon: CheckCircle2, value: view.summary.high_confidence, label: 'High-confidence links' },
    { icon: Layers, value: view.summary.to_review, label: 'Requirements to review' },
    { icon: GitBranch, value: view.summary.unimplemented, label: 'Potentially unimplemented' },
  ];

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
        <button
          type="button"
          className="workflow-exit-btn"
          onClick={() => navigate('/')}
          aria-label="Exit results"
        >
          <X size={15} strokeWidth={2.2} />
          <span>Exit</span>
        </button>
      </nav>

      <div className="results-content">
        <header className="results-header">
          <div>
            <div className="eyebrow"><span />Analysis complete</div>
            <h1>Traceability results</h1>
            <p>
              {view.summary.requirements} requirements · {view.summary.trace_links} trace links ·
              completed just now
            </p>
          </div>
          <div className="results-actions">
            <ExportMenu
              rows={matrixRows}
              summary={view.summary}
              disabled={!matrixRows.length}
              scopeLabel={`Complete matrix · ${matrixRows.length} rows`}
            />
            <Button onClick={save}>{saved ? 'Saved to project' : 'Save results'}</Button>
          </div>
        </header>

        <section className="result-stats">
          {stats.map(({ icon: Icon, value, label }) => (
            <div key={label}>
              <span className="result-stat-icon"><Icon size={16} strokeWidth={2} /></span>
              <b>{value}</b>
              <span className="result-stat-label">{label}</span>
            </div>
          ))}
        </section>

        <div className="tabs results-tabs">
          {TABS.map(([key, label]) => (
            <button
              type="button"
              key={key}
              className={tab === key ? 'active' : undefined}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === 'matrix' && (
          <TraceabilityMatrix rows={matrixRows} summary={view.summary} />
        )}

        {tab === 'explorer' && (
          <section className="results-workbench">
            <aside className="requirement-panel">
              <header><b>Requirements</b><span>{requirements.length} linked</span></header>
              <div className="requirement-list">
                {requirements.map((item, index) => (
                  <button
                    type="button"
                    className={activeIndex === index ? 'requirement active' : 'requirement'}
                    onClick={() => setSelected(index)}
                    key={item.source_id}
                  >
                    <small>{item.source_id}</small>
                    <b>{item.label}</b>
                    <span>
                      {Math.round(item.best.confidence * 100)}% · {item.links.length} match
                      {item.links.length === 1 ? '' : 'es'}
                    </span>
                  </button>
                ))}
                {!requirements.length && (
                  <p className="requirement-empty">No linked requirements.</p>
                )}
              </div>
            </aside>

            {link ? (
              <article className="trace-detail">
                <header>
                  <div>
                    <small>{link.source_id}</small>
                    <h2>{requirement.label}</h2>
                  </div>
                  <span className={`confidence ${link.confidence_level}`}>
                    {link.confidence_level} confidence
                  </span>
                </header>

                <div className="implementation">
                  <div>
                    <span><ArrowRight size={15} strokeWidth={2.2} /></span>
                    <section>
                      <small>Implementation link</small>
                      <h3>{link.target_id}</h3>
                    </section>
                    <b>{Math.round(link.confidence * 100)}%</b>
                  </div>
                  <ExplainToggle link={link} classifier={classifier} />
                </div>

                {requirement.links.length > 1 && (
                  <div className="other-matches">
                    <span className="other-matches-label">
                      Other candidates ({requirement.links.length - 1})
                    </span>
                    {requirement.links.slice(1).map((other) => (
                      <div className="other-match" key={other.target_id}>
                        <div className="other-match-head">
                          <code>{other.target_id}</code>
                          <span className={`matrix-score ${other.confidence_level}`}>
                            {other.confidence.toFixed(2)}
                          </span>
                        </div>
                        <ExplainToggle link={other} classifier={classifier} />
                      </div>
                    ))}
                  </div>
                )}

                <div className="connection">
                  <div><small>REQUIREMENT</small>{link.source_id}</div>
                  <i>→</i>
                  <div className="code">
                    <small>CODE</small>{link.target_id.split('/').at(-1)}
                  </div>
                </div>
              </article>
            ) : (
              <article className="trace-detail">
                <header>
                  <div><h2>No trace links recovered</h2></div>
                </header>
                <p className="trace-detail-empty">
                  The classifier did not link any requirement to the codebase. Try a coarser
                  code granularity or a higher links-per-requirement setting.
                </p>
              </article>
            )}
          </section>
        )}
      </div>
    </main>
  );
}
