import { elementName, sourceName, targetName, toLabel } from './api/analyzeApi';

/**
 * Column definitions drive the table head, the sort keys and every export.
 *
 * The two identifier columns are named after whatever this run actually traced
 * between, so a documentation-to-model matrix does not claim to hold
 * requirements and code.
 */
export function matrixColumns(labels) {
  return [
    { key: 'requirement', label: labels?.source.singular ?? 'Source', width: 150 },
    { key: 'requirementText', label: 'Description', width: 300 },
    { key: 'code', label: labels?.target.singular ?? 'Target', width: 260 },
    { key: 'similarity', label: 'Similarity', width: 85, align: 'right' },
    { key: 'status', label: 'Status', width: 90 },
  ];
}

export const STATUS_LINKED = 'Linked';
export const STATUS_MISSING = 'Missing';

/**
 * Flattens the grouped result into one row per source/target pair, then appends
 * a "Missing" row for every source element the pipeline never linked.
 */
export function buildMatrixRows(view) {
  const rows = [];

  // The two columns show names; the identifiers ride along for tooltips and
  // for the exports that keep them.
  for (const requirement of view.requirements) {
    for (const link of requirement.links) {
      rows.push({
        id: `${link.source_id}→${link.target_id}`,
        requirement: sourceName(link),
        requirementId: link.source_id,
        requirementText: requirement.label,
        code: targetName(link),
        codeId: link.target_id,
        similarity: link.confidence,
        confidenceLevel: link.confidence_level,
        status: STATUS_LINKED,
        explanation: link.explanation ?? '',
      });
    }
  }

  const linkedIds = new Set(view.requirements.map((item) => item.source_id));
  const sources = new Map(view.sourceElements.map((element) => [element.identifier, element]));
  for (const item of view.unimplemented) {
    const identifier = item.identifier ?? item.source_id;
    if (!identifier || linkedIds.has(identifier)) continue;
    const element = sources.get(identifier);
    rows.push({
      id: `${identifier}→unlinked`,
      requirement: element ? elementName(element) : identifier,
      requirementId: identifier,
      requirementText: toLabel(item.content, identifier),
      code: '—',
      codeId: null,
      similarity: null,
      confidenceLevel: null,
      status: STATUS_MISSING,
      explanation: '',
    });
  }

  return rows;
}

/** Search across the text columns; `similarity` is matched numerically below. */
function matchesQuery(row, query) {
  if (!query) return true;
  // The identifiers too, so a search that used to find a row still does.
  const haystack = [row.requirement, row.requirementId, row.requirementText, row.code, row.codeId, row.status]
    .join(' ')
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

export function filterRows(rows, { query, status, confidence }) {
  return rows.filter((row) => {
    if (!matchesQuery(row, query)) return false;
    if (status !== 'all' && row.status !== status) return false;
    if (confidence !== 'all' && row.confidenceLevel !== confidence) return false;
    return true;
  });
}

export function sortRows(rows, { key, direction }) {
  const factor = direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = a[key];
    const right = b[key];

    // Unlinked rows carry a null similarity - keep them at the bottom either way.
    if (left === null && right === null) return 0;
    if (left === null) return 1;
    if (right === null) return -1;

    if (typeof left === 'number' && typeof right === 'number') {
      return (left - right) * factor;
    }
    return String(left).localeCompare(String(right)) * factor;
  });
}

export const formatSimilarity = (value) => (value === null ? '—' : value.toFixed(2));
