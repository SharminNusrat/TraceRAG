import { Link } from 'react-router-dom';

/**
 * Mark: a requirements document linked out to two code elements - the trace
 * links the tool recovers.
 *
 * Drawn entirely in currentColor with no opaque fills behind the document, so
 * it works on the light pages and the dark nav/sidebar alike; each surface only
 * has to set `color` on .brand-mark.
 */
export function Brand() {
  return (
    <Link className="brand" to="/" aria-label="TraceRAG home">
      <svg
        className="brand-mark"
        viewBox="0 0 24 24"
        role="img"
        aria-hidden="true"
        stroke="currentColor"
        fill="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      >
        <path d="M10.6 12 L15.9 8 M10.6 12 L15.9 16" fill="none" opacity=".45" />
        <rect x="2.9" y="4.5" width="7.6" height="15" rx="2" fill="none" />
        <path d="M5.1 8.6 h3.2 M5.1 12 h3.2 M5.1 15.4 h2" strokeWidth="1.35" opacity=".85" />
        <circle cx="18.6" cy="7.6" r="2.8" stroke="none" />
        <circle cx="18.6" cy="16.4" r="2.8" stroke="none" opacity=".5" />
      </svg>
      <span className="brand-text">Trace<span>RAG</span></span>
    </Link>
  );
}
