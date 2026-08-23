import { Children, Fragment, useRef, useState } from 'react';

// A column can be dragged narrow but never away: a panel with no readable
// width is just a gap, and one you cannot easily get back.
const MIN_FRACTION = 0.12;
// Doubles as the gutter between panels, so dragging needs no extra spacing.
const HANDLE_WIDTH = 14;
const KEYBOARD_STEP = 0.02;

const equalSizes = (count) => Array(count).fill(1 / count);

function readStored(storageKey, count) {
  if (!storageKey) return null;
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey));
    // A layout saved for a different number of columns cannot be applied.
    const usable = Array.isArray(stored)
      && stored.length === count
      && stored.every((size) => typeof size === 'number' && size >= MIN_FRACTION);
    return usable ? stored : null;
  } catch {
    return null;
  }
}

/**
 * Columns the reader can widen by dragging the gutter between them.
 *
 * Only the two panels either side of a handle move, so widening one column
 * borrows from its neighbour rather than reflowing the whole row. Widths are
 * fractions of the row, which keeps the layout right when the window resizes.
 */
export function ResizableColumns({ children, className, storageKey }) {
  const panels = Children.toArray(children);
  const count = panels.length;
  const containerRef = useRef(null);
  const drag = useRef(null);

  const [sizes, setSizes] = useState(() => readStored(storageKey, count) ?? equalSizes(count));
  const layout = sizes.length === count ? sizes : equalSizes(count);

  const persist = (next) => {
    if (storageKey) localStorage.setItem(storageKey, JSON.stringify(next));
  };

  /** Move the boundary at `index`, taking from one side and giving to the other. */
  const resize = (from, index, fraction) => {
    const pair = from[index] + from[index + 1];
    const clamped = Math.min(Math.max(fraction, MIN_FRACTION), pair - MIN_FRACTION);
    const next = [...from];
    next[index] = clamped;
    next[index + 1] = pair - clamped;
    return next;
  };

  const onPointerDown = (index) => (event) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { index, startX: event.clientX, start: layout, latest: layout };
  };

  const onPointerMove = (event) => {
    const state = drag.current;
    if (!state) return;

    // The gutters are fixed pixels, so only what is left over is shared out.
    const available = (containerRef.current?.clientWidth ?? 0) - HANDLE_WIDTH * (count - 1);
    if (available <= 0) return;

    const moved = (event.clientX - state.startX) / available;
    const next = resize(state.start, state.index, state.start[state.index] + moved);
    state.latest = next;
    setSizes(next);
  };

  const onPointerUp = (event) => {
    if (!drag.current) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    persist(drag.current.latest);
    drag.current = null;
  };

  const nudge = (index, direction) => {
    const next = resize(layout, index, layout[index] + KEYBOARD_STEP * direction);
    setSizes(next);
    persist(next);
  };

  const reset = () => {
    const next = equalSizes(count);
    setSizes(next);
    persist(next);
  };

  return (
    <div
      ref={containerRef}
      className={className ? `resizable-columns ${className}` : 'resizable-columns'}
      style={{
        gridTemplateColumns: layout
          .map((size) => `minmax(0, ${size}fr)`)
          .join(` ${HANDLE_WIDTH}px `),
      }}
    >
      {panels.map((panel, index) => (
        <Fragment key={index}>
          {panel}
          {index < count - 1 && (
            <div
              className="column-handle"
              role="separator"
              aria-orientation="vertical"
              aria-label={`Resize column ${index + 1}`}
              aria-valuenow={Math.round(layout[index] * 100)}
              tabIndex={0}
              title="Drag to resize · double-click to even out"
              onPointerDown={onPointerDown(index)}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              onDoubleClick={reset}
              onKeyDown={(event) => {
                if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
                event.preventDefault();
                nudge(index, event.key === 'ArrowLeft' ? -1 : 1);
              }}
            >
              <span />
            </div>
          )}
        </Fragment>
      ))}
    </div>
  );
}
