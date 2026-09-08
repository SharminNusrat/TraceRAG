import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AlertTriangle, ArrowRight, Info, X } from 'lucide-react';
import { PageHeader } from '../../components/common/PageHeader';
import {
  classifierLabel,
  preprocessorLabel,
  useCapabilities,
} from '../../features/analysis/api/capabilitiesApi';
import { getGraph, listConfigs } from '../../features/projects/api/projectsApi';
import { TraceGraph } from '../../features/sync/components/TraceGraph';
import { VersionHistory } from '../../features/sync/components/VersionHistory';

const PAGE_SIZE = 50;

// Broken first: it is the only state that always means something is wrong.
const FILTERS = [
  ['broken', 'Broken'],
  ['stale', 'No longer found'],
  ['active', 'Current'],
  [null, 'All'],
];

const describe = (capabilities, config) =>
  `${preprocessorLabel(capabilities, config.source_preprocessor)} → `
  + `${preprocessorLabel(capabilities, config.target_preprocessor)}`
  + ` · ${classifierLabel(capabilities, config.classifier)} · top ${config.n_results}`;

/** The last segment of an identifier, which is what a reader recognises. */
const shortName = (identifier) => identifier.split('::').at(-1).split('/').at(-1);

/**
 * A project's trace links as they currently stand, and how its artifacts got
 * here.
 *
 * The saved analyses are what each run found and never change. This is the
 * live answer: which links still hold, which stopped being found, and which
 * lost the element they pointed at.
 */
export function TraceLinksPage() {
  const [params] = useSearchParams();
  const projectId = params.get('project');
  const { capabilities } = useCapabilities();

  const [configs, setConfigs] = useState(null);
  const [configId, setConfigId] = useState(null);
  const [graph, setGraph] = useState(null);
  const [filter, setFilter] = useState('broken');
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    listConfigs(projectId)
      .then((rows) => {
        if (!active) return;
        setConfigs(rows);
        const preferred = rows.find((row) => row.is_default) ?? rows[0];
        setConfigId(preferred?.config_id ?? null);
      })
      .catch((requestError) => {
        if (active) { setConfigs([]); setError(requestError.message); }
      });
    return () => { active = false; };
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !configId) return;
    let active = true;
    setGraph(null);
    getGraph(projectId, { configId, linkStatus: filter, limit: PAGE_SIZE, offset })
      .then((data) => { if (active) setGraph(data); })
      .catch((requestError) => {
        if (active) { setGraph({ links: [], total: 0, summary: {} }); setError(requestError.message); }
      });
    return () => { active = false; };
  }, [projectId, configId, filter, offset]);

  if (!projectId) {
    return (
      <>
        <PageHeader title="Trace Links" description="Open a project to see its links." />
        <p className="dialog-note">
          <Link className="row-open" to="/app/projects">Choose a project</Link>
        </p>
      </>
    );
  }

  const summary = graph?.summary ?? {};
  const config = configs?.find((row) => row.config_id === configId);

  const tiles = [
    ['Current', summary.links_active, null],
    ['No longer found', summary.links_stale, null],
    ['Broken', summary.links_broken, 'problem'],
    ['Elements gone', summary.nodes_gone, summary.nodes_gone ? 'problem' : null],
  ];

  return (
    <>
      <PageHeader
        title="Trace Links"
        description="What holds now, what stopped being found, and what lost the code it pointed at."
        actions={(
          <Link className="row-open" to={`/app/history?project=${projectId}`}>
            <X size={13} strokeWidth={2} /> Back to the project
          </Link>
        )}
      />

      {error && <p className="auth-error" role="alert">{error}</p>}

      {configs?.length === 0 && (
        <p className="dialog-note">
          This project has no saved configuration yet, so there is no graph to show.
        </p>
      )}

      {Boolean(configs?.length) && (
        <>
          {/* One graph per configuration: two of them read the same artifacts
              into different elements, so their links are not comparable. */}
          {configs.length > 1 && (
            <label className="graph-config">
              Configuration
              <select
                value={configId ?? ''}
                onChange={(event) => { setConfigId(Number(event.target.value)); setOffset(0); }}
              >
                {configs.map((row) => (
                  <option key={row.config_id} value={row.config_id}>
                    {describe(capabilities, row.config)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {configs.length === 1 && config && (
            <p className="dialog-note">{describe(capabilities, config.config)}</p>
          )}

          <section className="result-stats">
            {tiles.map(([label, value, tone]) => (
              <div key={label}>
                <b className={tone === 'problem' && value > 0 ? 'stat-problem' : undefined}>
                  {value ?? 0}
                </b>
                <span className="result-stat-label">{label}</span>
              </div>
            ))}
          </section>

          <div className="tabs results-tabs">
            {FILTERS.map(([key, label]) => (
              <button
                type="button"
                key={label}
                className={filter === key ? 'active' : undefined}
                onClick={() => { setFilter(key); setOffset(0); }}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Same links as the table below, drawn instead of listed - so the
              tabs narrow both at once rather than each having its own idea of
              what is being looked at. */}
          {Boolean(graph?.links.length) && <TraceGraph links={graph.links} />}

          {graph === null && <p className="dialog-note">Loading links…</p>}

          {graph?.links.length === 0 && (
            <p className="dialog-note">
              {filter === 'broken'
                ? 'Nothing is broken. Every link still points at something that exists.'
                : 'No links to show here.'}
            </p>
          )}

          {Boolean(graph?.links.length) && (
            <section className="history-card">
              {graph.links.map((link) => (
                <article className="link-row" key={link.edge_id}>
                  <span className={`link-status ${link.status}`}>
                    {link.status === 'broken' && <AlertTriangle size={11} strokeWidth={2.4} />}
                    {link.status === 'stale' ? 'not found' : link.status}
                  </span>
                  <div className="link-ends">
                    <span className={link.from_present ? undefined : 'gone'} title={link.from_identifier}>
                      <small>{link.from_kind}</small>
                      <b>{shortName(link.from_identifier)}</b>
                    </span>
                    <ArrowRight size={13} strokeWidth={2.2} />
                    <span className={link.to_present ? undefined : 'gone'} title={link.to_identifier}>
                      <small>{link.to_kind}</small>
                      <b>{shortName(link.to_identifier)}</b>
                      {/* The element is gone; this is why the link broke. */}
                      {!link.to_present && <em>no longer exists</em>}
                    </span>
                  </div>
                  {link.explanation && (
                    <span className="link-why" title={link.explanation}>
                      <Info size={12} strokeWidth={2.2} />
                    </span>
                  )}
                  <span className={`matrix-score ${link.confidence_level}`}>
                    {link.confidence.toFixed(2)}
                  </span>
                </article>
              ))}
            </section>
          )}

          {graph && graph.total > PAGE_SIZE && (
            <div className="compare-bar">
              <span>
                {offset + 1}–{Math.min(offset + PAGE_SIZE, graph.total)} of {graph.total}
              </span>
              <button
                type="button"
                className="row-open"
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0}
              >
                Previous
              </button>
              <button
                type="button"
                className="row-open"
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={offset + PAGE_SIZE >= graph.total}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}

      <h2 className="section-heading">Version History</h2>
      <p className="dialog-note">
        Each version is one state of the project's artifacts. A source is marked
        changed when it differs from the version before it.
      </p>
      <VersionHistory projectId={projectId} />
    </>
  );
}
