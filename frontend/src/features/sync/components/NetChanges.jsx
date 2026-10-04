import { useState } from 'react';
import { ChangeSummary } from './ChangeSummary';

const PAGE_SIZE = 50;

const FILE_FILTERS = [
  [null, 'All'],
  ['added', 'Added'],
  ['removed', 'Removed'],
  ['modified', 'Modified'],
  ['renamed', 'Renamed'],
];

// Files whose bytes are a document format, which have no lines to compare.
const NOT_TEXT = ['.pdf', '.docx'];

/** Whether a changed file's text can be shown old against new. */
const hasLineDiff = (row) => (row.change === 'modified' || row.change === 'renamed')
  && !NOT_TEXT.some((suffix) => row.path.toLowerCase().endsWith(suffix));

/** "a → b" when a name changed, otherwise just the name. */
const renamed = (old, now) => (old && now && old !== now ? `${old} → ${now}` : now ?? old);

/**
 * What changed between the two versions of a change report.
 *
 * By default the net change - the two versions compared directly, grouped by
 * file. "Step by step" shows instead what each version in between recorded.
 */
export function NetChanges({ report, sideLabel, loadDiff }) {
  const [stepByStep, setStepByStep] = useState(false);
  const [filter, setFilter] = useState(null);
  const [shown, setShown] = useState(PAGE_SIZE);
  const [open, setOpen] = useState(null);
  // Line diffs already fetched, by row, so reopening a row asks nothing.
  const [diffs, setDiffs] = useState({});

  const toggle = async (key, row) => {
    setOpen(open === key ? null : key);
    if (open === key || diffs[key] || !hasLineDiff(row)) return;
    try {
      const diff = await loadDiff(row);
      setDiffs((current) => ({ ...current, [key]: diff }));
    } catch (requestError) {
      setDiffs((current) => ({ ...current, [key]: { lines: [], error: requestError.message } }));
    }
  };

  if (report.base_version === null) {
    return <p className="dialog-note">This is the first version, so there is nothing to compare it with.</p>;
  }

  // Element counts from sides whose elements are whole files would only
  // repeat the file counts, so they are left out of the totals.
  const total = (key, sides) => sides.reduce((sum, side) => sum + side.counts[key], 0);
  const split = report.net.filter((side) => !side.whole_documents);
  const counts = [
    ['Files added', total('files_added', report.net)],
    ['Files removed', total('files_removed', report.net)],
    ['Files modified', total('files_modified', report.net)],
    ['Files renamed', total('files_renamed', report.net)],
    ['Elements added', total('elements_added', split)],
    ['Elements removed', total('elements_removed', split)],
    ['Elements modified', total('elements_modified', split)],
    ['Elements moved', total('elements_moved', split)],
  ];

  const rows = report.net
    .flatMap((side) => side.files.map((file) => ({ ...file, side })))
    .filter((row) => !filter || row.change === filter);

  return (
    <>
      <label className="graph-config">
        <input
          type="checkbox"
          checked={stepByStep}
          onChange={(event) => setStepByStep(event.target.checked)}
        />
        Step by step
      </label>

      {stepByStep && report.versions.map((version) => (
        <details className="change-step" key={version.version_number}>
          <summary>v{version.version_number}</summary>
          {version.changes.map((changes) => <ChangeSummary key={changes.role} changes={changes} />)}
        </details>
      ))}

      {!stepByStep && !report.net_available && (
        <p className="dialog-note">
          Some stored files of these versions are no longer on disk, so the net change cannot be
          worked out. Step by step still shows what each version recorded.
        </p>
      )}

      {!stepByStep && report.net_available && (
        <>
          <section className="change-counts">
            {counts.map(([label, value]) => (
              <div key={label}>
                <b>{value}</b>
                <span className="result-stat-label">{label}</span>
                {label === 'Elements moved' && <small>Only changed position</small>}
              </div>
            ))}
          </section>

          <div className="tabs results-tabs">
            {FILE_FILTERS.map(([key, label]) => (
              <button
                type="button"
                key={label}
                className={filter === key ? 'active' : undefined}
                onClick={() => { setFilter(key); setShown(PAGE_SIZE); }}
              >
                {label}
              </button>
            ))}
          </div>

          {!rows.length && <p className="dialog-note">No changes to show here.</p>}

          {Boolean(rows.length) && (
            <section className="history-card">
              {rows.slice(0, shown).map((row) => {
                const key = `${row.side.role}:${row.path}`;
                const listsElements = !row.side.whole_documents && row.elements.length > 0;
                const expandable = listsElements || hasLineDiff(row);
                const diff = diffs[key];
                return (
                  <article className="change-file" key={key}>
                    <div className="link-row">
                      <span className={`link-status ${row.change}`}>{row.change}</span>
                      <div className="link-ends">
                        <span title={renamed(row.old_path, row.path)}>
                          <small>{sideLabel(row.side.role)}</small>
                          <b>{renamed(row.old_path, row.path)}</b>
                        </span>
                      </div>
                      <span
                        className="change-links"
                        title={`${row.changed} of them say the ${row.side.role} changed`}
                      >
                        {row.links} link{row.links === 1 ? '' : 's'}
                      </span>
                      {expandable && (
                        <button
                          type="button"
                          className="row-open"
                          aria-expanded={open === key}
                          onClick={() => toggle(key, row)}
                        >
                          {listsElements
                            ? `${row.elements.length} element${row.elements.length === 1 ? '' : 's'}`
                            : 'Diff'}
                        </button>
                      )}
                    </div>
                    {listsElements && open === key && (
                      <ul className="change-elements">
                        {row.elements.map((element) => (
                          <li key={`${element.change}:${element.old}:${element.new}`}>
                            <span className={`link-status ${element.change}`}>{element.change}</span>
                            <b>{element.label}</b>
                            {element.old && element.new && element.old !== element.new && (
                              <code>{element.old} → {element.new}</code>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                    {hasLineDiff(row) && open === key && (
                      diff?.error ? <p className="auth-error" role="alert">{diff.error}</p>
                        : diff ? (
                          <pre className="line-diff">
                            {diff.lines.map((line, index) => (
                              <span
                                // Lines repeat, so their place is part of the key.
                                key={`${index}:${line}`}
                                className={line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : undefined}
                              >
                                {line}
                                {'\n'}
                              </span>
                            ))}
                            {diff.truncated && <em>Cut at 200 lines.</em>}
                          </pre>
                        ) : <p className="dialog-note">Loading the diff…</p>
                    )}
                  </article>
                );
              })}
            </section>
          )}

          {rows.length > shown && (
            <button type="button" className="text-toggle" onClick={() => setShown(shown + PAGE_SIZE)}>
              Show more
            </button>
          )}
        </>
      )}
    </>
  );
}
