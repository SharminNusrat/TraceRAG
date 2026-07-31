import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown, Check, Search, SlidersHorizontal } from 'lucide-react';
import {
  MATRIX_COLUMNS,
  STATUS_LINKED,
  STATUS_MISSING,
  filterRows,
  formatSimilarity,
  sortRows,
} from '../matrix';
import { ExportMenu } from './ExportMenu';

const STATUS_OPTIONS = [
  ['all', 'All statuses'],
  [STATUS_LINKED, 'Linked only'],
  [STATUS_MISSING, 'Missing only'],
];

const CONFIDENCE_OPTIONS = [
  ['all', 'All confidence'],
  ['high', 'High'],
  ['medium', 'Medium'],
  ['low', 'Low'],
];

export function TraceabilityMatrix({ rows, summary }) {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [confidence, setConfidence] = useState('all');
  const [sort, setSort] = useState({ key: 'similarity', direction: 'desc' });

  const visibleRows = useMemo(() => sortRows(
    filterRows(rows, { query, status, confidence }),
    sort,
  ), [rows, query, status, confidence, sort]);

  const toggleSort = (key) => {
    setSort((current) => (
      current.key === key
        ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: key === 'similarity' ? 'desc' : 'asc' }
    ));
  };

  const sortIcon = (key) => {
    if (sort.key !== key) return <ArrowUpDown size={12} strokeWidth={2.2} />;
    return sort.direction === 'asc'
      ? <ArrowUp size={12} strokeWidth={2.6} />
      : <ArrowDown size={12} strokeWidth={2.6} />;
  };

  const filtersActive = query || status !== 'all' || confidence !== 'all';

  return (
    <section className="matrix-panel">
      <header className="matrix-toolbar">
        <div className="matrix-search">
          <Search size={15} strokeWidth={2} />
          <input
            type="search"
            value={query}
            placeholder="Search requirements or code…"
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search the traceability matrix"
          />
        </div>

        <div className="matrix-filters">
          <SlidersHorizontal size={14} strokeWidth={2} />
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
            {STATUS_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <select
            value={confidence}
            onChange={(e) => setConfidence(e.target.value)}
            aria-label="Filter by confidence"
            disabled={status === STATUS_MISSING}
          >
            {CONFIDENCE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>

        <ExportMenu rows={visibleRows} summary={summary} disabled={!visibleRows.length} />
      </header>

      <div className="matrix-meta">
        <span>
          Showing <b>{visibleRows.length}</b> of {rows.length} rows
        </span>
        {filtersActive && (
          <button
            type="button"
            className="matrix-clear"
            onClick={() => { setQuery(''); setStatus('all'); setConfidence('all'); }}
          >
            Clear filters
          </button>
        )}
      </div>

      <div className="matrix-scroll">
        <table className="matrix-table">
          <thead>
            <tr>
              {MATRIX_COLUMNS.map((column) => (
                <th
                  key={column.key}
                  style={{ width: column.width }}
                  className={column.align === 'right' ? 'align-right' : undefined}
                >
                  <button
                    type="button"
                    className={sort.key === column.key ? 'matrix-sort active' : 'matrix-sort'}
                    onClick={() => toggleSort(column.key)}
                  >
                    {column.label}
                    {sortIcon(column.key)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={row.id}>
                <td><code className="matrix-id">{row.requirement}</code></td>
                <td className="matrix-desc" title={row.requirementText}>{row.requirementText}</td>
                <td>
                  {row.status === STATUS_MISSING
                    ? <span className="matrix-empty">—</span>
                    : <code className="matrix-code" title={row.code}>{row.code}</code>}
                </td>
                <td className="align-right">
                  {row.similarity === null ? (
                    <span className="matrix-empty">—</span>
                  ) : (
                    <span className={`matrix-score ${row.confidenceLevel}`}>
                      {formatSimilarity(row.similarity)}
                    </span>
                  )}
                </td>
                <td>
                  {row.status === STATUS_LINKED ? (
                    <span className="matrix-status linked"><Check size={12} strokeWidth={3} /> Linked</span>
                  ) : (
                    <span className="matrix-status missing">Missing</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {!visibleRows.length && (
          <p className="matrix-no-results">
            {rows.length
              ? 'No rows match the current search and filters.'
              : 'The analysis produced no trace links.'}
          </p>
        )}
      </div>
    </section>
  );
}
