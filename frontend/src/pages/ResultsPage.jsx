import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, CheckCircle2, GitBranch, Info, Layers, Link2 } from 'lucide-react';
import { WorkflowNav } from '../components/common/WorkflowNav';
import { Button } from '../components/common/Button';
import { useAnalysis } from '../features/analysis/AnalysisContext';
import { useAuth } from '../features/auth/AuthContext';
import { normalizeResult } from '../features/analysis/api/analyzeApi';
import { buildMatrixRows } from '../features/analysis/matrix';
import { TraceabilityMatrix } from '../features/analysis/components/TraceabilityMatrix';
import { TracePanels } from '../features/analysis/components/TracePanels';
import { ExportMenu } from '../features/analysis/components/ExportMenu';
import { SaveAnalysisDialog } from '../features/projects/components/SaveAnalysisDialog';
import { ArtifactStrip } from '../features/projects/components/ArtifactStrip';
import { RunConfigStrip } from '../features/projects/components/RunConfigStrip';
import { mockResult } from '../features/analysis/mockResult';

const TABS = [
  ['panels', 'Linked Artifacts'],
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
  const { draft, result, runMeta } = useAnalysis();
  const classifier = runMeta?.classifier;
  const [selected, setSelected] = useState(0);
  // A run started from a project is already filed away by the time it lands
  // here, so there is nothing left to save.
  const [saved, setSaved] = useState(runMeta?.savedTo ?? null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState('panels');

  // Falls back to the sample response when the page is opened directly.
  const view = useMemo(() => normalizeResult(result ?? mockResult), [result]);
  const matrixRows = useMemo(() => buildMatrixRows(view), [view]);

  const requirements = view.requirements;
  const activeIndex = Math.min(selected, Math.max(requirements.length - 1, 0));
  const requirement = requirements[activeIndex];
  const link = requirement?.best;

  // Named after what this run actually traced between, not "requirements to
  // code" - either side can be any artifact kind the backend supports.
  const { source, target } = view.labels;

  const stats = [
    { icon: Link2, value: view.summary.trace_links, label: 'Trace Links Found' },
    { icon: CheckCircle2, value: view.summary.high_confidence, label: 'High-Confidence Links' },
    { icon: Layers, value: view.summary.to_review, label: `${source.plural} to Review` },
    { icon: GitBranch, value: view.summary.unimplemented, label: `${source.plural} With No Link` },
  ];

  // Only a real run can be saved. Opening /results directly falls back to the
  // sample response, and filing that under a project would store fiction.
  const canSave = Boolean(result);

  const save = () => {
    if (!user) {
      navigate('/auth?intent=save&returnTo=/results');
      return;
    }
    setSaving(true);
  };

  return (
    <main className="results-page">
      <WorkflowNav title="Analysis Results" exitLabel="Exit Results" />

      <div className={tab === 'panels' ? 'results-content wide' : 'results-content'}>
        <header className="results-header">
          <div>
            <div className="eyebrow"><span />Analysis complete</div>
            <h1>Traceability Results</h1>
            <p>
              {view.summary.requirements} {source.lowerPlural} → {view.targetElements.length}{' '}
              {target.lowerPlural} · {view.summary.trace_links} trace links
            </p>
          </div>
          <div className="results-actions">
            <ExportMenu
              rows={matrixRows}
              summary={view.summary}
              labels={view.labels}
              disabled={!matrixRows.length}
              scopeLabel={`Complete matrix · ${matrixRows.length} rows`}
            />
            <Button
              onClick={save}
              disabled={!canSave || Boolean(saved)}
              title={canSave ? undefined : 'Run an analysis first'}
            >
              {saved ? `Saved to ${saved.project_name}` : 'Save results'}
            </Button>
          </div>
        </header>

        {runMeta?.saveError && (
          <p className="auth-error" role="alert">
            The analysis ran, but saving it failed: {runMeta.saveError}. Use
            “Save Results” to try again.
          </p>
        )}

        <RunConfigStrip
          config={runMeta?.config}
          sourceKind={runMeta?.sourceKind}
          targetKind={runMeta?.targetKind}
        />

        <ArtifactStrip artifacts={runMeta?.artifacts} savedAs={runMeta?.savedAs} />

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

        {tab === 'panels' && <TracePanels view={view} />}

        {tab === 'matrix' && (
          <TraceabilityMatrix rows={matrixRows} summary={view.summary} labels={view.labels} />
        )}

        {tab === 'explorer' && (
          <section className="results-workbench">
            <aside className="requirement-panel">
              <header><b>{source.plural}</b><span>{requirements.length} linked</span></header>
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
                  <p className="requirement-empty">No linked {source.lowerPlural}.</p>
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
                      <small>Linked {target.lower}</small>
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
                  <div><small>{source.singular.toUpperCase()}</small>{link.source_id}</div>
                  <i>→</i>
                  <div className="code">
                    <small>{target.singular.toUpperCase()}</small>
                    {link.target_id.split('/').at(-1)}
                  </div>
                </div>
              </article>
            ) : (
              <article className="trace-detail">
                <header>
                  <div><h2>No Trace Links Recovered</h2></div>
                </header>
                <p className="trace-detail-empty">
                  The classifier did not link any {source.lower} to a {target.lower}. Try a
                  coarser output level on the target side, or raise the candidates
                  considered per {source.lower}.
                </p>
              </article>
            )}
          </section>
        )}
      </div>

      {saving && (
        <SaveAnalysisDialog
          draft={draft}
          result={result}
          duration={runMeta?.duration}
          onSaved={(analysis) => { setSaved(analysis); setSaving(false); }}
          onClose={() => setSaving(false)}
        />
      )}
    </main>
  );
}
