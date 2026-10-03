import { X } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import { findKind, useCapabilities } from '../../analysis/api/capabilitiesApi';

/**
 * Asks before an upload takes the place of a source the project already has.
 *
 * A project takes each kind from one source, so saving a run made from freshly
 * uploaded files changes what that source holds. That is worth a question: the
 * other runs that use the same kind will read the new files from then on.
 */
export function ReplaceSourcesDialog({ sources, onConfirm, onCancel }) {
  const { capabilities } = useCapabilities();
  const label = (source) => findKind(capabilities, source.kind)?.label ?? source.kind;

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Re-upload source">
      <section className="dialog-card">
        <header className="dialog-head">
          <h2>Re-upload {sources.length === 1 ? 'This Source' : 'These Sources'}?</h2>
          <button type="button" onClick={onCancel} aria-label="Close">
            <X size={16} strokeWidth={2.2} />
          </button>
        </header>

        <p className="dialog-note">
          This project already has {sources.length === 1 ? 'a source' : 'sources'} for:
        </p>
        <ul className="dialog-list">
          {sources.map((source) => (
            <li key={source.source_id}>
              <b>{label(source)}</b> - {source.name}
            </li>
          ))}
        </ul>
        <p className="dialog-note">
          If you re-upload, {sources.length === 1 ? 'the source' : 'each source'} will
          hold the files from this new upload.
        </p>

        <div className="dialog-actions">
          <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
          <Button type="button" onClick={onConfirm}>Re-upload</Button>
        </div>
      </section>
    </div>
  );
}
