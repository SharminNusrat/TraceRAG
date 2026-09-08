import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import {
  findKind,
  findPreprocessor,
  useCapabilities,
} from '../../analysis/api/capabilitiesApi';
import { getAnalysis, kindOfSide, rerunAnalysis, runTimestamp } from '../api/projectsApi';

/**
 * Re-runs a saved analysis over the files it already holds.
 *
 * Repeating the original settings answers "did anything change?"; changing them
 * answers "what would this have found reported at class level, or with
 * summaries off?" - which the API has always supported but nothing could ask.
 */
export function RerunDialog({ analysis, onDone, onClose }) {
  const { capabilities } = useCapabilities();
  const [detail, setDetail] = useState(null);
  const [note, setNote] = useState('');
  const [reuseSettings, setReuseSettings] = useState(true);
  const [config, setConfig] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    getAnalysis(analysis.analysis_id)
      .then((data) => {
        if (!active) return;
        setDetail(data);
        setConfig(data.config);
      })
      .catch((requestError) => { if (active) setError(requestError.message); });
    return () => { active = false; };
  }, [analysis.analysis_id]);

  const set = (field, value) => setConfig((current) => ({ ...current, [field]: value }));

  const submit = async (event) => {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      onDone(await rerunAnalysis(analysis.analysis_id, {
        note: note.trim(),
        config: reuseSettings ? null : config,
      }));
    } catch (requestError) {
      setError(requestError.message);
      setPending(false);
    }
  };

  const sides = detail ? [
    ['Source', 'source', 'source_preprocessor', 'source_output_level'],
    ['Target', 'target', 'target_preprocessor', 'target_output_level'],
  ] : [];

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Re-run analysis">
      <section className="dialog-card wide">
        <header className="dialog-head">
          {/* The project, not the run: an unnamed run is called "Run on …",
              which would read "Re-run Run on …". Which run is being repeated
              is said once, in the sentence below. */}
          <h2>Re-run in {analysis.project_name}</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={2.2} />
          </button>
        </header>

        {!detail && !error && <p className="dialog-note">Loading this run's settings…</p>}

        {detail && (
          <form onSubmit={submit}>
            <p className="dialog-note">
              Repeats <b>{runTimestamp(analysis)}</b> over the {detail.artifacts.length} stored
              artifact{detail.artifacts.length === 1 ? '' : 's'} it was run against, and saves
              the outcome as a new analysis alongside it. This can take several minutes.
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
                // The example is one of the changes this dialog itself offers,
                // worded the way the control below words it.
                placeholder="e.g. Reported at class level"
              />
              <small className="field-hint">
                What is different about this run, for when you compare it with the original.
              </small>
            </label>

            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={reuseSettings}
                onChange={(event) => setReuseSettings(event.target.checked)}
              />
              <span>
                Repeat the original settings
                <small>Uncheck to re-run the same files with a different configuration.</small>
              </span>
            </label>

            {!reuseSettings && config && (
              <div className="rerun-settings">
                {sides.map(([title, role, preprocessorField, levelField]) => {
                  const kind = findKind(capabilities, kindOfSide(detail.artifacts, role));
                  const preprocessor = findPreprocessor(kind, config[preprocessorField]);
                  const levels = preprocessor?.output_levels ?? [];

                  if (!kind) {
                    return (
                      <p className="form-hint" key={role}>
                        The stored {title.toLowerCase()} artifact type is no longer supported,
                        so its settings cannot be changed.
                      </p>
                    );
                  }

                  return (
                    <div className="rerun-side" key={role}>
                      <h4>{title} · {kind.label}</h4>
                      <label>
                        Split into
                        <select
                          value={config[preprocessorField] ?? ''}
                          onChange={(event) => {
                            const next = findPreprocessor(kind, event.target.value);
                            set(preprocessorField, event.target.value);
                            // The old level may not exist on the new
                            // preprocessor, which the API would reject.
                            if (!next?.output_levels.some((l) => l.key === config[levelField])) {
                              set(levelField, next?.output_levels[0]?.key ?? null);
                            }
                          }}
                        >
                          {kind.preprocessors.map((option) => (
                            <option key={option.key} value={option.key}>{option.label}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Report links at
                        <select
                          value={config[levelField] ?? ''}
                          onChange={(event) => set(levelField, event.target.value || null)}
                        >
                          {levels.map((level) => (
                            <option key={level.key} value={level.key}>{level.label}</option>
                          ))}
                        </select>
                      </label>
                    </div>
                  );
                })}

                <div className="rerun-side">
                  <h4>Run</h4>
                  <label>
                    Classifier
                    <select
                      value={config.classifier}
                      onChange={(event) => set('classifier', event.target.value)}
                    >
                      {(capabilities?.classifiers ?? []).map((option) => (
                        <option key={option.key} value={option.key}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Candidates per source element
                    <input
                      type="number"
                      min="1"
                      max="25"
                      value={config.n_results}
                      onChange={(event) => set('n_results', Number(event.target.value))}
                    />
                  </label>
                  <label>
                    Dependency expansion depth
                    <input
                      type="number"
                      min="0"
                      max="3"
                      value={config.dependency_expansion_depth}
                      onChange={(event) => set('dependency_expansion_depth', Number(event.target.value))}
                    />
                  </label>
                  <label>
                    Summarize before embedding
                    <select
                      value={config.summarize_elements ? 'on' : 'off'}
                      onChange={(event) => set('summarize_elements', event.target.value === 'on')}
                    >
                      <option value="on">On</option>
                      <option value="off">Off</option>
                    </select>
                  </label>
                </div>
              </div>
            )}

            {error && <p className="auth-error" role="alert">{error}</p>}

            <div className="dialog-actions">
              <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={pending}>
                {pending ? 'Running…' : 'Re-run analysis'}
              </Button>
            </div>
          </form>
        )}

        {error && !detail && (
          <>
            <p className="auth-error" role="alert">{error}</p>
            <div className="dialog-actions">
              <Button type="button" variant="secondary" onClick={onClose}>Close</Button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
