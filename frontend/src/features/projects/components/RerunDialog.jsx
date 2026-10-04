import { useState } from 'react';
import { X } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import { rerunAnalysis, runTimestamp } from '../api/projectsApi';

/**
 * Re-runs an analysis over the files it already holds, with its own settings.
 *
 * Nothing else can be changed here: other settings are another analysis, and
 * other files are an update. What a re-run answers is whether the same
 * question asked again gets the same links.
 */
export function RerunDialog({ analysis, onDone, onClose }) {
  const [note, setNote] = useState('');
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      onDone(await rerunAnalysis(analysis.analysis_id, { note: note.trim() }));
    } catch (requestError) {
      setError(requestError.message);
      setPending(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Re-run analysis">
      <section className="dialog-card">
        <header className="dialog-head">
          {/* The project, not the run: an unnamed run is called "Run on …",
              which would read "Re-run Run on …". Which run is being repeated
              is said once, in the sentence below. */}
          <h2>Re-run in {analysis.project_name}</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={2.2} />
          </button>
        </header>

        <form onSubmit={submit}>
          <p className="dialog-note">
            Repeats <b>{runTimestamp(analysis)}</b> over the same files with the same
            settings, and saves the outcome as another run of the same version. This can
            take several minutes.
          </p>

          <label>
            <span className="field-name">
              Note<span className="dialog-optional">optional</span>
            </span>
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              // Free text lands in the database, and browsers offer saved
              // form values for exactly this kind of field.
              name="tracerag-rerun-note"
              autoComplete="off"
              data-lpignore="true"
              data-form-type="other"
            />
            <small className="field-hint">
              Anything worth remembering about this run when you come back to it.
            </small>
          </label>

          {error && <p className="auth-error" role="alert">{error}</p>}

          <div className="dialog-actions">
            <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={pending}>
              {pending ? 'Running…' : 'Re-run analysis'}
            </Button>
          </div>
        </form>
      </section>
    </div>
  );
}
