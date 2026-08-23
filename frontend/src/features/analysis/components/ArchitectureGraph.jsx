import { useEffect, useMemo, useRef, useState } from 'react';
import { Minus, Plus, Maximize2 } from 'lucide-react';

const NODE_WIDTH = 168;
const NODE_HEIGHT = 60;
// Kept clear around the drawing so labels above the top row are not clipped.
const MARGIN = 18;
// The least a cell may shrink to before the drawing outgrows the panel and is
// scaled down instead. Kept tight: a gutter only has to clear a ball, and a
// generous minimum here is what pushes the grid over the edge and shrinks
// everything to fit - which reads as wasted space, not as breathing room.
const MIN_GAP_X = 30;
const MIN_GAP_Y = 48;

// UML ball-and-socket. The ball is the provided interface; a socket cupping it
// is a consumer requiring it.
const BALL_RADIUS = 8;
const SOCKET_RADIUS = 15;
// How far round the ball a socket reaches. Several consumers can cup the same
// ball at different angles without overlapping.
const SOCKET_SPAN = (150 * Math.PI) / 180;
// How far the ball sits off its component's edge - inside the gutter, so it
// never lands on a neighbouring box.
const STUB_LENGTH = 14;

// Roughly how wide a character is at each label's size. SVG text neither wraps
// nor clips on its own, so a long name runs straight out of its box unless it
// is cut to length first. The full name stays in the tooltip.
const NAME_CHARS = Math.floor((NODE_WIDTH - 26) / 8.4);
const INTERFACE_CHARS = 18;
const clip = (text, limit) => (text.length <= limit ? text : `${text.slice(0, limit - 1)}…`);

// Force settling. Fixed counts rather than an animation: the drawing is the
// same every time, and it is done before the first paint.
const SETTLE_PASSES = 320;
const SEPARATE_PASSES = 80;
const COOLING = 0.975;
// Canvas area per component, as a multiple of the space its box strictly
// needs. Higher spreads the drawing out and renders it smaller.
const BREATHING_ROOM = 1.5;
// Seeds the components on a spiral that spreads them evenly with no randomness.
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

const ZOOM_STEPS = [0.6, 0.8, 1, 1.4, 2, 3];
// Until the panel has been measured. Replaced on the first layout pass.
const ASSUMED_VIEWPORT = { width: 900, height: 520 };

/**
 * An architecture model drawn as a UML component diagram.
 *
 * Interfaces are their own shapes rather than edge labels, in the notation the
 * model is written in: a component provides an interface through a ball on a
 * stem, and a component that requires it reaches round that ball with a
 * socket. An interface nobody consumes still gets its ball, so it is visible
 * rather than implied.
 */
export function ArchitectureGraph({
  elements, activeIds, linkedIds, selectedId, onSelect,
  // The panel size to lay out for. Normally measured; passed in when the
  // drawing is rendered somewhere there is nothing to measure.
  panelSize,
}) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [panning, setPanning] = useState(false);
  const [measured, setMeasured] = useState(null);
  const viewport = panelSize ?? measured ?? ASSUMED_VIEWPORT;
  const frameRef = useRef(null);
  const drag = useRef(null);

  // The drawing is built at the panel's own size, so at 100% a component is
  // drawn the size it is declared - laying out a square and letting the
  // viewBox shrink it to fit a wide, short panel is what made it unreadable.
  useEffect(() => {
    const element = frameRef.current;
    if (!element || panelSize) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setMeasured({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [panelSize]);

  const model = useMemo(() => layout(elements, viewport), [elements, viewport]);

  if (!model.nodes.length) {
    return <p className="panel-empty">No components to draw.</p>;
  }

  const { nodes, interfaces, width, height } = model;
  const selected = nodes.find((node) => node.id === selectedId);

  // Zoom about the middle of the drawing, so the part being read stays put
  // instead of sliding off toward a corner.
  const zoomTo = (next) => {
    const centre = { x: width / 2, y: height / 2 };
    setPan((current) => ({
      x: current.x + (zoom - next) * centre.x,
      y: current.y + (zoom - next) * centre.y,
    }));
    setZoom(next);
  };

  const stepZoom = (direction) => {
    const index = ZOOM_STEPS.indexOf(zoom);
    const next = ZOOM_STEPS[Math.min(Math.max(index + direction, 0), ZOOM_STEPS.length - 1)];
    if (next !== undefined && next !== zoom) zoomTo(next);
  };

  const fit = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const startPan = (event) => {
    // Only the background drags; a press on a component is a selection.
    if (event.target.closest('.arch-node')) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    // The viewBox is scaled to fit the panel, so a pixel of pointer travel is
    // not a unit of drawing - convert before moving anything.
    const box = frameRef.current?.getBoundingClientRect();
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      pan,
      scale: box?.width ? width / box.width : 1,
    };
    setPanning(true);
  };

  const movePan = (event) => {
    const state = drag.current;
    if (!state) return;
    setPan({
      x: state.pan.x + (event.clientX - state.x) * state.scale,
      y: state.pan.y + (event.clientY - state.y) * state.scale,
    });
  };

  const endPan = (event) => {
    if (!drag.current) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current = null;
    setPanning(false);
  };

  const lit = (interfaceNode) => (
    activeIds.has(interfaceNode.provider?.id)
    || interfaceNode.consumers.some((consumer) => activeIds.has(consumer.id))
  );

  return (
    <div className="architecture-graph">
      <div className="arch-controls">
        <button type="button" onClick={() => stepZoom(-1)}
                disabled={zoom === ZOOM_STEPS[0]} aria-label="Zoom out">
          <Minus size={12} strokeWidth={2.6} />
        </button>
        <span>{Math.round(zoom * 100)}%</span>
        <button type="button" onClick={() => stepZoom(1)}
                disabled={zoom === ZOOM_STEPS.at(-1)} aria-label="Zoom in">
          <Plus size={12} strokeWidth={2.6} />
        </button>
        <button type="button" onClick={fit}
                disabled={zoom === 1 && !pan.x && !pan.y} aria-label="Reset view">
          <Maximize2 size={11} strokeWidth={2.4} /> Fit
        </button>
        <small>Drag to pan</small>
      </div>

      {/* The frame is the div, not the svg. An svg carries an intrinsic aspect
          ratio, so leaving its height to the flex layout gives it a size of
          its own choosing and leaves the rest of the panel empty. */}
      <div className="arch-frame" ref={frameRef}>
        <svg
          className={panning ? 'arch-canvas panning' : 'arch-canvas'}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="Architecture component diagram"
          onPointerDown={startPan}
          onPointerMove={movePan}
          onPointerUp={endPan}
          onPointerCancel={endPan}
        >
        <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
          {interfaces.map((item) => (
            <g key={item.key} className={lit(item) ? 'arch-interface lit' : 'arch-interface'}>
              {/* The provider's stem, ending just short of its ball. An
                  interface nothing provides has nothing to hang from. */}
              {item.stem && <path className="arch-stem" d={item.stem} />}
              {item.sockets.map((socket) => (
                <g key={socket.key}>
                  <path className="arch-requires" d={socket.lead} />
                  <path className="arch-socket" d={socket.arc} />
                </g>
              ))}
              <circle className="arch-ball" cx={item.x} cy={item.y} r={BALL_RADIUS} />
              {/* Every name at once is unreadable at this size, so a name
                  appears when its component is picked. Hovering shows it too. */}
              {lit(item) && (
                <text x={item.labelX} y={item.labelY} textAnchor={item.labelAnchor}>
                  {clip(item.name, INTERFACE_CHARS)}
                </text>
              )}
              <title>
                {item.provider
                  ? `${item.provider.label} provides ${item.name}`
                  : `${item.name} (required, not provided in this model)`}
              </title>
            </g>
          ))}

          {nodes.map((node) => {
            const classes = ['arch-node'];
            if (node.id === selectedId) classes.push('selected');
            else if (activeIds.has(node.id)) classes.push('active');
            if (!linkedIds.has(node.id)) classes.push('unlinked');

            return (
              <g
                key={node.id}
                className={classes.join(' ')}
                transform={`translate(${node.x}, ${node.y})`}
                onClick={() => onSelect(node.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect(node.id);
                  }
                }}
              >
                <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx="8" />
                {/* The UML component icon that sits in a classifier's corner. */}
                <g className="arch-icon" transform={`translate(${NODE_WIDTH - 28}, 9)`}>
                  <rect x="3" y="0" width="15" height="12" />
                  <rect x="0" y="2" width="6" height="3" />
                  <rect x="0" y="7" width="6" height="3" />
                </g>
                <text x={NODE_WIDTH / 2} y={25} className="arch-stereotype">«component»</text>
                <text x={NODE_WIDTH / 2} y={45} className="arch-name">
                  {clip(node.label, NAME_CHARS)}
                </text>
                <title>{node.tooltip}</title>
              </g>
            );
          })}
          </g>
        </svg>
      </div>

      {selected ? (
        <div className="arch-detail">
          <b>{selected.label}</b>
          <InterfaceList title="Provides" names={selected.provides} />
          <InterfaceList title="Requires" names={selected.requires} />
          {!selected.provides.length && !selected.requires.length && (
            <small className="arch-detail-empty">No interfaces declared on this component.</small>
          )}
        </div>
      ) : (
        <div className="arch-legend">
          <span><i className="swatch linked" />Linked</span>
          <span><i className="swatch unlinked" />No trace link</span>
          <span><i className="swatch ball" />Provides</span>
          <span><i className="swatch socket" />Requires</span>
        </div>
      )}
    </div>
  );
}

function InterfaceList({ title, names }) {
  if (!names.length) return null;
  return (
    <div className="arch-detail-row">
      <span>{title}</span>
      <div>{names.map((name) => <code key={name}>{name}</code>)}</div>
    </div>
  );
}

/* ---------------------------- geometry ---------------------------- */

/** Where a ray leaving a component's centre crosses its box. */
function boundary(node, towardX, towardY) {
  const dx = towardX - node.cx;
  const dy = towardY - node.cy;
  const stepX = dx === 0 ? Infinity : (NODE_WIDTH / 2) / Math.abs(dx);
  const stepY = dy === 0 ? Infinity : (NODE_HEIGHT / 2) / Math.abs(dy);
  const step = Math.min(stepX, stepY);
  return { x: node.cx + dx * step, y: node.cy + dy * step };
}

const line = (from, to) => `M ${from.x} ${from.y} L ${to.x} ${to.y}`;

/**
 * The direction to hang an interface in, closest to the one wanted that does
 * not put its ball on top of a component.
 *
 * Keeping the components apart is not enough on its own: two boxes offset
 * diagonally satisfy the spacing along both axes while their corners are still
 * close, and a ball leaving at that angle lands on the neighbour.
 */
function clearAngle(anchor, wanted, nodes) {
  const lands = (angle) => {
    const edge = boundary(anchor, anchor.cx + Math.cos(angle), anchor.cy + Math.sin(angle));
    const x = edge.x + Math.cos(angle) * STUB_LENGTH;
    const y = edge.y + Math.sin(angle) * STUB_LENGTH;
    return nodes.some((box) => (
      box !== anchor
      && Math.abs(x - box.cx) < NODE_WIDTH / 2 + BALL_RADIUS
      && Math.abs(y - box.cy) < NODE_HEIGHT / 2 + BALL_RADIUS
    ));
  };

  if (!lands(wanted)) return wanted;
  // Turn away in both directions at once and take the first clear heading, so
  // the interface still points as near its consumers as it can.
  for (let step = 1; step <= 12; step += 1) {
    const offset = (step * Math.PI) / 12;
    if (!lands(wanted + offset)) return wanted + offset;
    if (!lands(wanted - offset)) return wanted - offset;
  }
  return wanted;
}

/** The open half-circle that cups a ball, facing `angle`. */
function socketArc(x, y, angle) {
  const from = angle - SOCKET_SPAN / 2;
  const to = angle + SOCKET_SPAN / 2;
  const at = (a) => `${x + Math.cos(a) * SOCKET_RADIUS} ${y + Math.sin(a) * SOCKET_RADIUS}`;
  return `M ${at(from)} A ${SOCKET_RADIUS} ${SOCKET_RADIUS} 0 0 1 ${at(to)}`;
}

/** Which components depend on which, through the interfaces between them. */
function dependencies(nodes) {
  const providerOf = new Map();
  for (const node of nodes) {
    for (const name of node.provides) if (!providerOf.has(name)) providerOf.set(name, node);
  }

  const links = [];
  const seen = new Set();
  for (const node of nodes) {
    for (const name of node.requires) {
      const provider = providerOf.get(name);
      if (!provider || provider === node) continue;
      const key = `${node.id}->${provider.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      links.push({ from: node, to: provider });
    }
  }
  return links;
}

/**
 * How much room the drawing needs.
 *
 * Packed to the minimum the components would have nowhere to settle into and
 * would end up in rows, which is the look a force layout exists to avoid. So
 * the canvas is given half again as much room as the boxes strictly need; if
 * that is more than the panel holds, it grows and Fit scales it back - which
 * is what the zoom and pan are for.
 */
function contentFrame(count, viewport) {
  const needed = count * (NODE_WIDTH + MIN_GAP_X) * (NODE_HEIGHT + MIN_GAP_Y) * BREATHING_ROOM;
  const available = Math.max(viewport.width * viewport.height, 1);
  const grow = Math.max(1, Math.sqrt(needed / available));
  return { width: viewport.width * grow, height: viewport.height * grow };
}

/**
 * Settle the components by force: every one pushes the others away, and a
 * dependency pulls its two ends together.
 *
 * Seeded on a golden-angle spiral rather than at random, so the same model
 * always settles into the same picture - a layout that reshuffles on every
 * render makes positions useless as a memory aid.
 */
function settle(nodes, links, frame) {
  const ideal = Math.sqrt((frame.width * frame.height) / nodes.length) * 0.75;

  nodes.forEach((node, index) => {
    const angle = index * GOLDEN_ANGLE;
    const spread = Math.sqrt((index + 0.5) / nodes.length);
    node.cx = frame.width / 2 + Math.cos(angle) * spread * frame.width * 0.4;
    node.cy = frame.height / 2 + Math.sin(angle) * spread * frame.height * 0.4;
  });

  let heat = Math.min(frame.width, frame.height) * 0.1;

  for (let pass = 0; pass < SETTLE_PASSES; pass += 1) {
    for (const node of nodes) {
      node.dx = 0;
      node.dy = 0;
    }

    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = nodes[i];
        const b = nodes[j];
        const gap = Math.hypot(a.cx - b.cx, a.cy - b.cy) || 0.01;
        const push = (ideal * ideal) / gap;
        const ux = (a.cx - b.cx) / gap;
        const uy = (a.cy - b.cy) / gap;
        a.dx += ux * push;
        a.dy += uy * push;
        b.dx -= ux * push;
        b.dy -= uy * push;
      }
    }

    for (const link of links) {
      const gap = Math.hypot(link.from.cx - link.to.cx, link.from.cy - link.to.cy) || 0.01;
      const pull = (gap * gap) / ideal;
      const ux = (link.from.cx - link.to.cx) / gap;
      const uy = (link.from.cy - link.to.cy) / gap;
      link.from.dx -= ux * pull;
      link.from.dy -= uy * pull;
      link.to.dx += ux * pull;
      link.to.dy += uy * pull;
    }

    // A weak pull inward, so a component nothing depends on does not drift off.
    for (const node of nodes) {
      node.dx += (frame.width / 2 - node.cx) * 0.015;
      node.dy += (frame.height / 2 - node.cy) * 0.015;
    }

    for (const node of nodes) {
      const move = Math.hypot(node.dx, node.dy) || 1;
      const step = Math.min(move, heat);
      node.cx += (node.dx / move) * step;
      node.cy += (node.dy / move) * step;
    }
    heat *= COOLING;
  }
}

/** Stretch the settled shape across the whole frame, at full component size. */
function spread(nodes, frame) {
  const insetX = NODE_WIDTH / 2 + MARGIN;
  const insetY = NODE_HEIGHT / 2 + MARGIN;
  const room = {
    x: Math.max(frame.width - insetX * 2, 0),
    y: Math.max(frame.height - insetY * 2, 0),
  };

  const xs = nodes.map((node) => node.cx);
  const ys = nodes.map((node) => node.cy);
  const from = { x: Math.min(...xs), y: Math.min(...ys) };
  const span = { x: Math.max(...xs) - from.x, y: Math.max(...ys) - from.y };

  for (const node of nodes) {
    // A single component - or a row of them - has no span on one axis, so it
    // is centred there rather than divided by zero.
    node.cx = span.x > 1 ? insetX + ((node.cx - from.x) / span.x) * room.x : frame.width / 2;
    node.cy = span.y > 1 ? insetY + ((node.cy - from.y) / span.y) * room.y : frame.height / 2;
  }
}

/** Push apart any two components that ended up on top of each other. */
function separate(nodes, frame) {
  const needX = NODE_WIDTH + MIN_GAP_X;
  const needY = NODE_HEIGHT + MIN_GAP_Y;

  for (let pass = 0; pass < SEPARATE_PASSES; pass += 1) {
    let collided = false;

    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = b.cx - a.cx;
        const dy = b.cy - a.cy;
        const overX = needX - Math.abs(dx);
        const overY = needY - Math.abs(dy);
        if (overX <= 0 || overY <= 0) continue;

        collided = true;
        // Part along whichever axis needs the smaller nudge.
        if (overX / needX < overY / needY) {
          const shift = (overX / 2) * (dx < 0 ? -1 : 1);
          a.cx -= shift;
          b.cx += shift;
        } else {
          const shift = (overY / 2) * (dy < 0 ? -1 : 1);
          a.cy -= shift;
          b.cy += shift;
        }
      }
    }

    for (const node of nodes) {
      node.cx = Math.min(Math.max(node.cx, NODE_WIDTH / 2 + MARGIN), frame.width - NODE_WIDTH / 2 - MARGIN);
      node.cy = Math.min(Math.max(node.cy, NODE_HEIGHT / 2 + MARGIN), frame.height - NODE_HEIGHT / 2 - MARGIN);
    }

    if (!collided) break;
  }
}

/**
 * Components settled by force, each interface on the edge of the component
 * that owns it.
 */
function layout(elements, viewport) {
  // Only elements the model preprocessor described. A run reported at document
  // level lists the whole model as one element, which is not a component and
  // must not be drawn as a box.
  const nodes = elements
    .filter((element) => element.model_units)
    .map((element) => ({
      id: element.identifier,
      label: element.model_units.name ?? element.identifier,
      provides: element.model_units.provides ?? [],
      requires: element.model_units.requires ?? [],
    }));

  if (!nodes.length) return { nodes, interfaces: [], width: 0, height: 0 };

  const { width, height } = contentFrame(nodes.length, viewport);
  const frame = { width, height };

  settle(nodes, dependencies(nodes), frame);
  spread(nodes, frame);
  separate(nodes, frame);

  for (const node of nodes) {
    node.tooltip = [
      node.label,
      node.provides.length ? `Provides: ${node.provides.join(', ')}` : null,
      node.requires.length ? `Requires: ${node.requires.join(', ')}` : null,
    ].filter(Boolean).join('\n');
  }

  // Every interface named anywhere in the model, with who offers it and who
  // wants it. One name is one shape, however many components reach for it.
  const named = new Map();
  const entry = (name) => {
    if (!named.has(name)) named.set(name, { name, provider: null, consumers: [] });
    return named.get(name);
  };
  for (const node of nodes) {
    for (const name of node.provides) entry(name).provider ??= node;
    for (const name of node.requires) entry(name).consumers.push(node);
  }

  for (const node of nodes) {
    node.x = node.cx - NODE_WIDTH / 2;
    node.y = node.cy - NODE_HEIGHT / 2;
  }

  // Every interface hangs off the edge of the component that owns it, in the
  // gutter between the boxes. Placing it partway towards its consumers - the
  // obvious idea - drops it on top of whatever box happens to lie in between.
  const placed = [];
  const attached = new Map();
  for (const item of named.values()) {
    const consumers = item.consumers.filter((consumer) => consumer !== item.provider);
    const anchor = item.provider ?? consumers[0];
    if (!anchor) continue;

    // Point it at whoever uses it; with nobody to point at, away from the
    // middle of the drawing, where there is empty space.
    const aim = consumers.length
      ? {
        x: consumers.reduce((sum, c) => sum + c.cx, 0) / consumers.length,
        y: consumers.reduce((sum, c) => sum + c.cy, 0) / consumers.length,
      }
      : { x: anchor.cx - (width / 2 - anchor.cx), y: anchor.cy - (height / 2 - anchor.cy) };

    // Several interfaces on one component would leave by the same door, so
    // each is turned a little further round: 0, +1, -1, +2, …
    const index = attached.get(anchor) ?? 0;
    attached.set(anchor, index + 1);
    const turn = Math.ceil(index / 2) * (index % 2 ? 1 : -1) * 0.62;
    const wanted = Math.atan2(aim.y - anchor.cy, aim.x - anchor.cx) + turn;

    const angle = clearAngle(anchor, wanted, nodes);
    const edge = boundary(anchor, anchor.cx + Math.cos(angle), anchor.cy + Math.sin(angle));
    placed.push({
      ...item,
      anchor,
      consumers,
      angle,
      edge,
      x: edge.x + Math.cos(angle) * STUB_LENGTH,
      y: edge.y + Math.sin(angle) * STUB_LENGTH,
    });
  }

  const interfaces = placed.map((item) => {
    const { anchor, consumers, angle, edge, x, y } = item;
    const away = { x: Math.cos(angle), y: Math.sin(angle) };

    return {
      key: `${anchor.id}:${item.name}`,
      name: item.name,
      provider: item.provider,
      consumers,
      x,
      y,
      // A provided interface hangs off its provider on a solid stem; one that
      // is only required has nothing to hang from, so it shows as a socket.
      stem: item.provider
        ? line(edge, { x: x - away.x * BALL_RADIUS, y: y - away.y * BALL_RADIUS })
        : '',
      sockets: (item.provider ? consumers : [anchor]).map((consumer) => {
        const towards = Math.atan2(consumer.cy - y, consumer.cx - x);
        return {
          key: consumer.id,
          arc: socketArc(x, y, towards),
          lead: line(boundary(consumer, x, y), {
            x: x + Math.cos(towards) * SOCKET_RADIUS,
            y: y + Math.sin(towards) * SOCKET_RADIUS,
          }),
        };
      }),
      // Clear of the box, on the far side of the ball, reading outwards.
      labelX: x + away.x * (SOCKET_RADIUS + 5),
      labelY: y + away.y * (SOCKET_RADIUS + 5) + 4,
      labelAnchor: Math.abs(away.x) < 0.4 ? 'middle' : (away.x > 0 ? 'start' : 'end'),
    };
  });

  return { nodes, interfaces, width, height };
}
