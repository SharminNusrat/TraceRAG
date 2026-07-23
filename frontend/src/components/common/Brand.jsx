import { Link } from 'react-router-dom';

export function Brand() {
  return (
    <Link className="brand" to="/" aria-label="TraceRAG home">
      <span className="brand-mark">
        <i />
        <i />
        <i />
      </span>
      <span className="brand-text">Trace<span>RAG</span></span>
    </Link>
  );
}
