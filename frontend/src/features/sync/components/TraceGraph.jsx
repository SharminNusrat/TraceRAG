import { useState } from 'react';

/**
 * The links drawn as a picture, rather than read as a list.
 *
 * Requirements down the left, code down the right, a line for each link. The
 * shape answers questions a list cannot: which requirement carries most of the
 * system, which piece of code half of them lean on, and what is left dangling.
 *
 * Opens at a readable number rather than the whole graph, because a few hundred
 * links drawn at once is a hairball. But the rest is one click away: a cap that
 * cannot be lifted hides whole requirements from a picture that looks complete.
 */

// Where it opens. Past this it stops being a diagram and starts being a
// texture - but that is the reader's call to make, not ours.
const MAX_LINKS = 24;

// One viewBox, scaled to whatever width the page gives it.
const WIDTH = 800;
const ROW = 30;
const PADDING = 18;
const LEFT_DOT = 258;
const RIGHT_DOT = 542;

/** The last segment of an identifier - what a reader actually recognises. */
const shortName = (identifier) => identifier.split('::').at(-1).split('/').at(-1);

const truncate = (text, max = 30) =>
  (text.length > max ? `${text.slice(0, max - 1)}…` : text);

export function TraceGraph({ links }) {
  // Which node the reader is holding still, so its own links stand out and
  // everything else steps back.
  const [focus, setFocus] = useState(null);
  const [showAll, setShowAll] = useState(false);

  const capped = links.length > MAX_LINKS;
  const shown = capped && !showAll ? links.slice(0, MAX_LINKS) : links;
  if (!shown.length) return null;

  // Order of first appearance keeps a link's two ends near each other, which
  // matters more than sorting: it is what stops the lines crossing.
  const column = (pick) => {
    const seen = [];
    for (const link of shown) if (!seen.includes(pick(link))) seen.push(pick(link));
    return seen;
  };
  const sources = column((link) => link.source_id);
  const targets = column((link) => link.target_id);
  // A UML component is drawn by the name the server sent for it.
  const names = new Map();
  for (const link of shown) {
    if (link.source_name) names.set(link.source_id, link.source_name);
    if (link.target_name) names.set(link.target_id, link.target_name);
  }
  const label = (identifier) => names.get(identifier) ?? shortName(identifier);

  const rows = Math.max(sources.length, targets.length);
  const height = rows * ROW + PADDING * 2;
  const y = (list, identifier) => PADDING + list.indexOf(identifier) * ROW + ROW / 2;

  const present = new Map();
  for (const link of shown) {
    present.set(`from:${link.source_id}`, link.source_present);
    present.set(`to:${link.target_id}`, link.target_present);
  }

  const dimmed = (link) =>
    focus && focus !== link.source_id && focus !== link.target_id;

  // Following a name means following what it reaches, so the other end of each
  // of its links stays lit with it - otherwise the lines lead into the dark.
  const lit = new Set();
  if (focus) {
    lit.add(focus);
    for (const link of shown) {
      if (link.source_id === focus) lit.add(link.target_id);
      if (link.target_id === focus) lit.add(link.source_id);
    }
  }

  const nodeClass = (side, identifier) => [
    'graph-node',
    present.get(`${side}:${identifier}`) ? '' : 'gone',
    focus && !lit.has(identifier) ? 'dim' : '',
  ].filter(Boolean).join(' ');

  const toggle = (identifier) => setFocus((current) => (current === identifier ? null : identifier));

  return (
    <figure className="trace-graph">
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`${shown.length} trace links drawn between requirements and code`}
        onClick={() => setFocus(null)}
      >
        {/* Lines first, so the labels sit on top of them. */}
        <g>
          {shown.map((link) => {
            const y1 = y(sources, link.source_id);
            const y2 = y(targets, link.target_id);
            return (
              <path
                key={`${link.state}:${link.source_id}>${link.target_id}`}
                className={`graph-edge ${link.state}${dimmed(link) ? ' dim' : ''}`}
                d={`M ${LEFT_DOT} ${y1} C ${LEFT_DOT + 95} ${y1}, ${RIGHT_DOT - 95} ${y2}, ${RIGHT_DOT} ${y2}`}
              >
                <title>
                  {`${label(link.source_id)} → ${label(link.target_id)}`}
                  {` · ${link.state.replaceAll('_', ' ')} · ${link.confidence.toFixed(2)}`}
                </title>
              </path>
            );
          })}
        </g>

        {[
          [sources, 'from', LEFT_DOT, LEFT_DOT - 12, 'end'],
          [targets, 'to', RIGHT_DOT, RIGHT_DOT + 12, 'start'],
        ].map(([list, side, dotX, textX, anchor]) => (
          <g key={side}>
            {list.map((identifier) => (
              <g
                key={identifier}
                className={nodeClass(side, identifier)}
                onClick={(event) => { event.stopPropagation(); toggle(identifier); }}
              >
                <title>{identifier}</title>
                <circle cx={dotX} cy={y(list, identifier)} r="4.5" />
                <text x={textX} y={y(list, identifier)} textAnchor={anchor} dominantBaseline="middle">
                  {truncate(label(identifier))}
                </text>
              </g>
            ))}
          </g>
        ))}
      </svg>

      <figcaption>
        {capped
          ? `Showing ${shown.length} of ${links.length} links.`
          : `${shown.length} link${shown.length === 1 ? '' : 's'}. Click a name to follow just its own.`}
        {capped && (
          <button type="button" className="row-open" onClick={() => setShowAll(!showAll)}>
            {showAll ? 'Show fewer' : `Show all ${links.length}`}
          </button>
        )}
      </figcaption>
    </figure>
  );
}
