import { useEffect, useRef, useState } from 'react';
import { Download, FileSpreadsheet, FileText, FileType, ChevronDown } from 'lucide-react';
import { exportCsv, exportPdf, exportXlsx } from '../exporters';

const FORMATS = [
  { key: 'csv', label: 'CSV', hint: 'Comma separated', icon: FileText, run: exportCsv },
  { key: 'xlsx', label: 'Excel', hint: 'Workbook (.xlsx)', icon: FileSpreadsheet, run: exportXlsx },
  { key: 'pdf', label: 'PDF', hint: 'Printable table', icon: FileType, run: exportPdf },
];

export function ExportMenu({ rows, summary, labels, disabled, scopeLabel, variant = 'secondary' }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (!containerRef.current?.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  // Excel and PDF pull their libraries in on demand, so this can reject.
  const handleExport = async (format) => {
    setBusy(format.key);
    setError(null);
    try {
      await format.run(rows, summary, labels);
      setOpen(false);
    } catch (exportError) {
      setError(`${format.label} export failed: ${exportError.message}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="export-menu" ref={containerRef}>
      <button
        type="button"
        className={`button button-${variant} export-trigger`}
        onClick={() => setOpen((value) => !value)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Download size={14} strokeWidth={2.2} />
        Export
        <ChevronDown size={13} strokeWidth={2.4} className={open ? 'chevron open' : 'chevron'} />
      </button>

      {open && (
        <div className="export-dropdown" role="menu">
          <span className="export-dropdown-label">
            {scopeLabel ?? `${rows.length} row${rows.length === 1 ? '' : 's'} in current view`}
          </span>
          {FORMATS.map((format) => (
            <button
              type="button"
              role="menuitem"
              key={format.key}
              className="export-option"
              onClick={() => handleExport(format)}
              disabled={busy !== null}
            >
              <format.icon size={15} strokeWidth={1.9} />
              <span>
                <b>{format.label}</b>
                <small>{busy === format.key ? 'Preparing…' : format.hint}</small>
              </span>
            </button>
          ))}
          {error && <span className="export-error">{error}</span>}
        </div>
      )}
    </div>
  );
}
