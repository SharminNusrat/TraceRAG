/**
 * What changed on one side: files by path and content, and the elements
 * inside the files that changed.
 *
 * Worked out before anything runs, so it can be read before deciding to
 * update - and stored on the version afterwards.
 */
export function ChangeSummary({ changes }) {
  if (!changes) return null;
  if (!changes.changed) {
    return <p className="dialog-note">No file differs from what this side holds.</p>;
  }

  const moved = (items) => items.map((item) => `${item.old} → ${item.new}`);
  const groups = [
    ['Files added', changes.files.added],
    ['Files removed', changes.files.removed],
    ['Files modified', changes.files.modified],
    ['Files renamed', moved(changes.files.renamed)],
    ['Elements added', changes.elements.added],
    ['Elements removed', changes.elements.removed],
    ['Elements modified', changes.elements.modified],
    ['Elements moved', moved(changes.elements.moved)],
  ].filter(([, items]) => items.length);

  return (
    <div className="change-summary">
      {groups.map(([label, items]) => (
        <details key={label}>
          <summary>{label} <b>{items.length}</b></summary>
          <ul className="dialog-list">
            {items.map((item) => <li key={item}><code>{item}</code></li>)}
          </ul>
        </details>
      ))}
      {!changes.meaningful && (
        <p className="field-hint">No element the analysis compares changed.</p>
      )}
    </div>
  );
}
