import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import { createProject, listProjects, saveAnalysis } from '../api/projectsApi';

const NEW_PROJECT = '__new__';

/**
 * Picks the project a finished run should be filed under, creating one first
 * if the account has none. Closes itself on success and reports the saved
 * analysis back to the caller.
 */
export function SaveAnalysisDialog({ draft, result, duration, onSaved, onClose }) {
  const [projects, setProjects] = useState(null);
  const [projectId, setProjectId] = useState(NEW_PROJECT);
  const [newName, setNewName] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    listProjects()
      .then((rows) => {
        if (!active) return;
        setProjects(rows);
        // Default to the most recently active project; only fall back to
        // creating one when there is nothing to file this under.
        if (rows.length) setProjectId(String(rows[0].project_id));
      })
      .catch((requestError) => {
        if (active) {
          setProjects([]);
          setError(requestError.message);
        }
      });
    return () => { active = false; };
  }, []);

  const isNew = projectId === NEW_PROJECT;

  const submit = async (event) => {
    event.preventDefault();
    setError(null);
    setPending(true);

    try {
      const target = isNew
        ? await createProject({ name: newName.trim() })
        : { project_id: Number(projectId) };

      const saved = await saveAnalysis(target.project_id, {
        draft,
        result,
        note: note.trim(),
        duration,
      });
      onSaved(saved);
    } catch (requestError) {
      setError(requestError.message);
      setPending(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Save analysis">
      <section className="dialog-card">
        <header className="dialog-head">
          <h2>Save This Analysis</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={2.2} />
          </button>
        </header>

        {projects === null ? (
          <p className="dialog-note">Loading your projects…</p>
        ) : (
          <form onSubmit={submit}>
            <label>
              Project
              <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
                {projects.map((project) => (
                  <option key={project.project_id} value={project.project_id}>
                    {project.project_name}
                  </option>
                ))}
                <option value={NEW_PROJECT}>＋ New project…</option>
              </select>
            </label>

            {isNew && (
              <label>
                New Project Name
                <input
                  value={newName}
                  onChange={(event) => setNewName(event.target.value)}
                  required
                  autoFocus
                  autoComplete="off"
                  placeholder="Atlas workspace"
                />
              </label>
            )}

            <label>
              <span className="field-name">
                Note<span className="dialog-optional">optional</span>
              </span>
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                // A free-text field is exactly what browsers offer saved form
                // values for, and whatever they inject is stored against the
                // run. Chrome ignores autocomplete="off" on its own, but
                // honours an unrecognised token.
                name="tracerag-note"
                autoComplete="off"
                data-lpignore="true"
                data-form-type="other"
                placeholder="e.g. Baseline, method level"
              />
              <small className="field-hint">
                Anything worth remembering about this run when you come back to it.
              </small>
            </label>

            {error && <p className="auth-error" role="alert">{error}</p>}

            <div className="dialog-actions">
              <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={pending || (isNew && !newName.trim())}>
                {pending ? 'Saving…' : 'Save analysis'}
              </Button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
