import { useMemo, useState } from 'react';
import { ArrowRight, Link2, Search } from 'lucide-react';
import { toLabel } from '../api/analyzeApi';

const SORTS = [
  ['none', 'None'],
  ['confidence', 'Confidence'],
  ['source', 'Source'],
  ['target', 'Target'],
];

const linkKey = (link) => `${link.source_id}→${link.target_id}`;

/**
 * Three linked panels: recovered links on the left, every source element in the
 * middle, every target element on the right.
 *
 * Selecting anything drives one shared highlight model:
 *  - a link      -> its own source and target light up
 *  - an element  -> every element it links to on the other side lights up
 * Elements that take part in no link at all are dimmed so coverage gaps are
 * visible at a glance.
 */
export function TracePanels({ view }) {
  const { links, sourceElements, targetElements, targetsBySource, sourcesByTarget } = view;

  const [selection, setSelection] = useState(null); // {kind:'link'|'source'|'target', id}
  const [sort, setSort] = useState('none');
  const [query, setQuery] = useState('');
  const [highlightsOnly, setHighlightsOnly] = useState(false);

  const linkedSourceIds = useMemo(() => new Set(links.map((l) => l.source_id)), [links]);
  const linkedTargetIds = useMemo(() => new Set(links.map((l) => l.target_id)), [links]);

  // One selection resolves to the set of ids highlighted in each panel.
  const active = useMemo(() => {
    if (!selection) return { sources: new Set(), targets: new Set(), links: new Set() };

    if (selection.kind === 'link') {
      const link = links.find((item) => linkKey(item) === selection.id);
      return link
        ? { sources: new Set([link.source_id]), targets: new Set([link.target_id]), links: new Set([selection.id]) }
        : { sources: new Set(), targets: new Set(), links: new Set() };
    }

    const related = selection.kind === 'source'
      ? (targetsBySource.get(selection.id) ?? [])
      : (sourcesByTarget.get(selection.id) ?? []);

    return {
      sources: new Set(related.map((l) => l.source_id)),
      targets: new Set(related.map((l) => l.target_id)),
      links: new Set(related.map(linkKey)),
    };
  }, [selection, links, targetsBySource, sourcesByTarget]);

  const sortedLinks = useMemo(() => {
    const rows = [...links];
    if (sort === 'confidence') rows.sort((a, b) => b.confidence - a.confidence);
    if (sort === 'source') rows.sort((a, b) => a.source_id.localeCompare(b.source_id));
    if (sort === 'target') rows.sort((a, b) => a.target_id.localeCompare(b.target_id));
    if (!query.trim()) return rows;
    const needle = query.toLowerCase();
    return rows.filter((l) => `${l.source_id} ${l.target_id}`.toLowerCase().includes(needle));
  }, [links, sort, query]);

  const visibleLinks = highlightsOnly && selection
    ? sortedLinks.filter((l) => active.links.has(linkKey(l)))
    : sortedLinks;

  const toggle = (kind, id) => setSelection((current) => (
    current && current.kind === kind && current.id === id ? null : { kind, id }
  ));

  return (
    <section className="trace-panels">
      <Panel
        title="Trace Links"
        count={`${visibleLinks.length} of ${links.length}`}
        toolbar={(
          <>
            <div className="panel-search">
              <Search size={13} strokeWidth={2} />
              <input
                type="search"
                value={query}
                placeholder="Filter links…"
                onChange={(event) => setQuery(event.target.value)}
                aria-label="Filter trace links"
              />
            </div>
            <label className="panel-sort">
              Sort by
              <select value={sort} onChange={(event) => setSort(event.target.value)}>
                {SORTS.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className={highlightsOnly ? 'panel-chip active' : 'panel-chip'}
              onClick={() => setHighlightsOnly((value) => !value)}
              disabled={!selection}
            >
              Highlights
            </button>
          </>
        )}
      >
        {visibleLinks.map((link) => {
          const key = linkKey(link);
          return (
            <button
              type="button"
              key={key}
              className={`panel-item link-item${active.links.has(key) ? ' active' : ''}`}
              onClick={() => toggle('link', key)}
            >
              <div className="link-item-row">
                <code>{link.source_id}</code>
                <ArrowRight size={12} strokeWidth={2.4} />
                <code>{link.target_id}</code>
              </div>
              <span className={`confidence ${link.confidence_level}`}>
                {Math.round(link.confidence * 100)}%
              </span>
            </button>
          );
        })}
        {!visibleLinks.length && <p className="panel-empty">No trace links to show.</p>}
      </Panel>

      <ElementPanel
        title="Source Artifact"
        elements={sourceElements}
        selection={selection}
        kind="source"
        activeIds={active.sources}
        linkedIds={linkedSourceIds}
        countFor={(id) => (targetsBySource.get(id) ?? []).length}
        onSelect={(id) => toggle('source', id)}
      />

      <ElementPanel
        title="Target Artifact"
        elements={targetElements}
        selection={selection}
        kind="target"
        activeIds={active.targets}
        linkedIds={linkedTargetIds}
        countFor={(id) => (sourcesByTarget.get(id) ?? []).length}
        onSelect={(id) => toggle('target', id)}
      />
    </section>
  );
}

function Panel({ title, count, toolbar, children }) {
  return (
    <div className="trace-panel">
      <header className="trace-panel-header">
        <b>{title}</b>
        <span>{count}</span>
      </header>
      {toolbar && <div className="trace-panel-toolbar">{toolbar}</div>}
      <div className="trace-panel-body">{children}</div>
    </div>
  );
}

function ElementPanel({ title, elements, kind, selection, activeIds, linkedIds, countFor, onSelect }) {
  const [query, setQuery] = useState('');

  const visible = useMemo(() => {
    if (!query.trim()) return elements;
    const needle = query.toLowerCase();
    return elements.filter((element) => (
      `${element.identifier} ${element.content}`.toLowerCase().includes(needle)
    ));
  }, [elements, query]);

  return (
    <Panel
      title={title}
      count={`${visible.length} of ${elements.length}`}
      toolbar={(
        <div className="panel-search">
          <Search size={13} strokeWidth={2} />
          <input
            type="search"
            value={query}
            placeholder={`Filter ${title.toLowerCase()}…`}
            onChange={(event) => setQuery(event.target.value)}
            aria-label={`Filter ${title}`}
          />
        </div>
      )}
    >
      {visible.map((element, index) => {
        const isSelected = selection?.kind === kind && selection.id === element.identifier;
        const isActive = activeIds.has(element.identifier);
        const isLinked = linkedIds.has(element.identifier);
        const linkCount = countFor(element.identifier);

        const classes = ['panel-item', 'element-item'];
        if (isSelected) classes.push('selected');
        else if (isActive) classes.push('active');
        if (!isLinked) classes.push('unlinked');

        return (
          <button
            type="button"
            key={element.identifier}
            className={classes.join(' ')}
            onClick={() => onSelect(element.identifier)}
            aria-pressed={isSelected}
          >
            <div className="element-head">
              <span className="element-index">{index + 1}</span>
              <code className="element-id">{element.identifier}</code>
              <span className="element-level">{element.level}</span>
              {linkCount > 0 && (
                <span className="element-links" title={`${linkCount} trace link${linkCount === 1 ? '' : 's'}`}>
                  <Link2 size={11} strokeWidth={2.4} />{linkCount}
                </span>
              )}
            </div>
            <p className={isSelected ? 'element-content expanded' : 'element-content'}>
              {isSelected ? element.content : toLabel(element.content, element.identifier, 240)}
            </p>
          </button>
        );
      })}
      {!visible.length && <p className="panel-empty">Nothing matches that filter.</p>}
    </Panel>
  );
}
