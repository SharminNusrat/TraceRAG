import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AlertTriangle, ArrowRight, Info, X } from 'lucide-react';
import { PageHeader } from '../../components/common/PageHeader';
import {
  classifierLabel,
  findKind,
  preprocessorLabel,
  useCapabilities,
} from '../../features/analysis/api/capabilitiesApi';
import { getLineDiff, getReport, listConfigs, listVersions } from '../../features/projects/api/projectsApi';
import { NetChanges } from '../../features/sync/components/NetChanges';
import { TraceGraph } from '../../features/sync/components/TraceGraph';
import { VersionHistory } from '../../features/sync/components/VersionHistory';

const PAGE_SIZE = 50;

// Broken first: it is the only state that always means something is wrong.
const FILTERS = [
  ['broken', 'Broken'],
  ['no_longer_found', 'No longer found'],
  ['new', 'New'],
  ['valid', 'Valid'],
  [null, 'All'],
];

const STATE_LABELS = Object.fromEntries(FILTERS.filter(([key]) => key));

// Two analyses can share their settings, so the kinds and the version they
// are at are part of how one is told apart from another.
const describe = (capabilities, analysis) => {
  const { config } = analysis;
  const kind = (key) => findKind(capabilities, key)?.label ?? key;
  return `${kind(analysis.source_kind)} → ${kind(analysis.target_kind)}`
    + ` · ${preprocessorLabel(capabilities, config.source_preprocessor)} → `
    + `${preprocessorLabel(capabilities, config.target_preprocessor)}`
    + ` · ${classifierLabel(capabilities, config.classifier)} · top ${config.n_results}`
    + (analysis.version_number ? ` · v${analysis.version_number}` : '');
};

/** The last segment of an identifier, which is what a reader recognises. */
const shortName = (identifier) => identifier.split('::').at(-1).split('/').at(-1);

/** Why a link is in the state it is in, from which of its ends changed. */
function reason(link) {
  if (link.source_changed && link.target_changed) return 'Source and target changed';
  if (link.source_changed) return 'Source changed';
  if (link.target_changed) return 'Target changed';
  // Neither end is different, so the files are not why the link moved.
  return link.state === 'no_longer_found' || link.state === 'new'
    ? 'Neither changed - likely classifier variance or top-k eviction'
    : 'Neither changed';
}

/**
 * What changed between two versions of one analysis.
 *
 * Every link of either version is given a state - valid, no longer found,
 * broken or new - and a reason. The files and elements that changed in
 * between are listed with it, and the source elements left without a link.
 */
export function TraceLinksPage() {
  const [params] = useSearchParams();
  const projectId = params.get('project');
  const requested = Number(params.get('config')) || null;
  const { capabilities } = useCapabilities();

  const [configs, setConfigs] = useState(null);
  const [configId, setConfigId] = useState(null);
  const [versions, setVersions] = useState([]);
  const [span, setSpan] = useState({ base: null, head: null });
  const [report, setReport] = useState(null);
  const [filter, setFilter] = useState(null);
  const [view, setView] = useState('links');
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    listConfigs(projectId)
      .then((rows) => {
        if (!active) return;
        setConfigs(rows);
        const preferred = rows.find((row) => row.config_id === requested) ?? rows[0];
        setConfigId(preferred?.config_id ?? null);
      })
      .catch((requestError) => {
        if (active) { setConfigs([]); setError(requestError.message); }
      });
    return () => { active = false; };
  }, [projectId, requested]);

  // A different analysis has different versions, so the span starts over at
  // its newest version against the one before.
  useEffect(() => {
    if (!projectId || !configId) return;
    let active = true;
    setSpan({ base: null, head: null });
    listVersions(projectId, configId)
      .then((rows) => { if (active) setVersions(rows); })
      .catch(() => { if (active) setVersions([]); });
    return () => { active = false; };
  }, [projectId, configId]);

  useEffect(() => {
    if (!projectId || !configId) return;
    let active = true;
    setReport(null);
    setError(null);
    getReport(projectId, configId, span)
      .then((data) => { if (active) setReport(data); })
      .catch((requestError) => {
        if (active) { setReport(null); setError(requestError.message); }
      });
    return () => { active = false; };
  }, [projectId, configId, span.base, span.head]);

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

  const config = configs?.find((row) => row.config_id === configId);
  const kindLabel = (key) => findKind(capabilities, key)?.label ?? key;
  const summary = report?.summary ?? {};
  // Uncompared links have no state to filter by.
  const shown = (report?.links ?? []).filter(
    (link) => !filter || report.no_run_version != null || link.state === filter,
  );
  const page = shown.slice(offset, offset + PAGE_SIZE);
  // Oldest first, as a reader counts them.
  const numbers = versions.map((version) => version.version_number).sort((a, b) => a - b);
  // Versions whose every run was deleted: they have no links to show.
  const runless = new Set(versions.filter((v) => !v.analysis_count).map((v) => v.version_number));
  const versionLabel = (number) => `v${number}${runless.has(number) ? ' (no run)' : ''}`;
  // One of the two has no run, so the links are shown as they are, uncompared.
  const missing = report?.no_run_version ?? null;

  const tiles = [
    ['Valid', summary.valid, null],
    ['No longer found', summary.no_longer_found, null],
    ['Broken', summary.broken, 'problem'],
    ['New', summary.new, null],
    ['Uncovered', summary.uncovered, summary.uncovered ? 'problem' : null],
  ];

  const choose = (field, value) => {
    setOffset(0);
    setSpan((current) => ({ ...current, [field]: value ? Number(value) : null }));
  };

  return (
    <>
      <PageHeader
        title="Trace Links"
        description="Every link between two versions: what holds, what is new, what stopped being found, and what broke."
        actions={(
          <Link className="row-open" to={`/app/history?project=${projectId}`}>
            <X size={13} strokeWidth={2} /> Back to the project
          </Link>
        )}
      />

      {error && <p className="auth-error" role="alert">{error}</p>}

      {configs?.length === 0 && (
        <p className="dialog-note">This project has no saved analysis yet.</p>
      )}

      {Boolean(configs?.length) && (
        <>
          <div className="tabs results-tabs">
            {[['links', 'Links'], ['changes', 'Changes']].map(([key, label]) => (
              <button
                type="button"
                key={key}
                className={view === key ? 'active' : undefined}
                onClick={() => setView(key)}
              >
                {label}
              </button>
            ))}
          </div>

          {configs.length > 1 && (
            <label className="graph-config">
              Analysis
              <select
                value={configId ?? ''}
                onChange={(event) => {
                  setConfigId(Number(event.target.value));
                  setOffset(0);
                }}
              >
                {configs.map((row) => (
                  <option key={row.config_id} value={row.config_id}>
                    {describe(capabilities, row)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {configs.length === 1 && config && (
            <p className="dialog-note">{describe(capabilities, config)}</p>
          )}

          {numbers.length > 1 && (
            <div className="graph-config">
              Version
              <select
                value={span.base ?? report?.base_version ?? ''}
                onChange={(event) => choose('base', event.target.value)}
              >
                {numbers.slice(0, -1).map((number) => (
                  <option key={number} value={number}>{versionLabel(number)}</option>
                ))}
              </select>
              <ArrowRight size={13} strokeWidth={2.2} />
              <select
                value={span.head ?? report?.head_version ?? ''}
                onChange={(event) => choose('head', event.target.value)}
              >
                {numbers.slice(1).map((number) => (
                  <option key={number} value={number} disabled={runless.has(number)}>
                    {versionLabel(number)}
                  </option>
                ))}
              </select>
            </div>
          )}

          {view === 'changes' && report && (
            <NetChanges
              report={report}
              sideLabel={(role) => kindLabel(role === 'source' ? config?.source_kind : config?.target_kind)}
              loadDiff={(row) => getLineDiff(projectId, configId, {
                base: report.base_version,
                head: report.head_version,
                role: row.side.role,
                path: row.path,
                oldPath: row.old_path,
              })}
            />
          )}

          {view === 'links' && missing !== null && (
            <p className="dialog-note">
              {missing === report.head_version
                ? `Version ${missing} has no run, so there are no links to show.`
                : `Version ${missing} has no run, so there is nothing to compare against.`}
            </p>
          )}

          {view === 'links' && (
          <>
          {missing === null && (
          <>
          <section className="result-stats five">
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
          </>
          )}

          {/* Same links as the list below, drawn instead of listed - so the
              tabs narrow both at once. */}
          {Boolean(page.length) && <TraceGraph links={page} />}

          {report === null && !error && <p className="dialog-note">Loading links…</p>}

          {report && !page.length && (
            <p className="dialog-note">
              {filter === 'broken'
                ? 'Nothing is broken. Every link still points at something that exists.'
                : 'No links to show here.'}
            </p>
          )}

          {Boolean(page.length) && (
            <section className="history-card">
              {page.map((link) => (
                <article className="link-row" key={`${link.state}:${link.source_id}>${link.target_id}`}>
                  {missing === null && (
                    <span className={`link-status ${link.state}`}>
                      {link.state === 'broken' && <AlertTriangle size={11} strokeWidth={2.4} />}
                      {STATE_LABELS[link.state]}
                    </span>
                  )}
                  <div className="link-ends">
                    <span
                      className={link.source_present ? undefined : 'gone'}
                      title={link.source_name ? link.source_id : link.source_content ?? link.source_id}
                    >
                      <small>{kindLabel(config?.source_kind)}</small>
                      <b>{link.source_name ?? shortName(link.source_id)}</b>
                      {!link.source_present && <em>no longer exists</em>}
                    </span>
                    <ArrowRight size={13} strokeWidth={2.2} />
                    <span
                      className={link.target_present ? undefined : 'gone'}
                      title={link.target_name ? link.target_id : link.target_content ?? link.target_id}
                    >
                      <small>{kindLabel(config?.target_kind)}</small>
                      <b>{link.target_name ?? shortName(link.target_id)}</b>
                      {!link.target_present && <em>no longer exists</em>}
                    </span>
                    {missing === null && <small className="link-reason">{reason(link)}</small>}
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

          {shown.length > PAGE_SIZE && (
            <div className="compare-bar">
              <span>
                {offset + 1}–{Math.min(offset + PAGE_SIZE, shown.length)} of {shown.length}
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
                disabled={offset + PAGE_SIZE >= shown.length}
              >
                Next
              </button>
            </div>
          )}

          {Boolean(report?.uncovered.length) && (
            <>
              <h2 className="section-heading">Uncovered</h2>
              <p className="dialog-note">
                {kindLabel(config?.source_kind)} elements with no link in v{report.head_version}.
              </p>
              <ul className="dialog-list">
                {report.uncovered.map((identifier) => (
                  <li key={identifier} title={identifier}>
                    {report.uncovered_names[identifier] ?? shortName(identifier)}
                  </li>
                ))}
              </ul>
            </>
          )}

          </>
          )}
        </>
      )}

      {configId && (
        <>
          <h2 className="section-heading">Version History</h2>
          <p className="dialog-note">
            Each version is one state of this analysis's files. A side is marked
            changed when it differs from the version before it.
          </p>
          <VersionHistory projectId={projectId} configId={configId} />
        </>
      )}
    </>
  );
}
