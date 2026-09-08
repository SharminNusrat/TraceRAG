import { useEffect, useState } from 'react';
import { GitBranch, Plug, X } from 'lucide-react';
import { Button } from '../../../components/common/Button';
import { ConnectRepositoryDialog } from './ConnectRepositoryDialog';
import { listProjects } from '../../projects/api/projectsApi';
import { listSources, sourceLocation } from '../api/syncApi';

/**
 * Picks a source to stand in for an upload.
 *
 * A source belongs to a project, so one has to be settled first. A run started
 * from a project already has it; one started from anywhere else is asked here
 * rather than being told to go back and start again.
 *
 * Only offers what can actually be fetched: an uploaded source has no address
 * to fetch from, so choosing it would mean uploading anyway.
 */
export function SourcePicker({ projectId, onPicked, onClose }) {
  const [projects, setProjects] = useState(null);
  const [chosenProject, setChosenProject] = useState(projectId ?? '');
  const [sources, setSources] = useState(null);
  const [error, setError] = useState(null);
  const [connecting, setConnecting] = useState(false);

  // Only when the run does not already belong to one.
  useEffect(() => {
    if (projectId) return;
    let active = true;
    listProjects()
      .then((rows) => {
        if (!active) return;
        setProjects(rows);
        if (rows.length === 1) setChosenProject(String(rows[0].project_id));
      })
      .catch((requestError) => {
        if (active) { setProjects([]); setError(requestError.message); }
      });
    return () => { active = false; };
  }, [projectId]);

  const load = () => {
    if (!chosenProject) return;
    setSources(null);
    listSources(chosenProject)
      .then(setSources)
      .catch((requestError) => { setSources([]); setError(requestError.message); });
  };

  useEffect(() => { load(); }, [chosenProject]);

  const fetchable = (sources ?? []).filter((source) => source.origin !== 'upload');
  const needsProject = !projectId && !chosenProject;

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Use a connected source">
      <section className="dialog-card">
        <header className="dialog-head">
          <h2>Use a Connected Source</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={2.2} />
          </button>
        </header>

        {/* Choosing here also decides where this run will be saved, so it is
            said plainly rather than left to be discovered afterwards. */}
        {!projectId && (
          <label>
            Project
            <select
              value={chosenProject}
              onChange={(event) => setChosenProject(event.target.value)}
            >
              <option value="">Choose a project…</option>
              {(projects ?? []).map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.project_name}
                </option>
              ))}
            </select>
            <small className="field-hint">
              Sources belong to a project. This run will be saved into the one you
              pick here.
            </small>
          </label>
        )}

        {projects?.length === 0 && (
          <p className="dialog-note">
            You have no projects yet. Create one from the Projects page, then a
            repository can be connected to it.
          </p>
        )}

        {!needsProject && sources === null && <p className="dialog-note">Loading sources…</p>}

        {!needsProject && sources !== null && !fetchable.length && (
          <p className="dialog-note">
            Nothing is connected to this project yet. Connect a repository and it
            can be used here instead of uploading the files.
          </p>
        )}

        {Boolean(fetchable.length) && (
          <>
            <p className="dialog-note">
              TraceRAG fetches this at the moment you run, and records the commit it
              came from — so the next sync knows exactly what changed.
            </p>
            <div className="sync-list">
              {fetchable.map((source) => (
                <button
                  type="button"
                  className="sync-row pickable"
                  key={source.source_id}
                  onClick={() => onPicked(source, chosenProject)}
                >
                  <span className="artifact-icon"><GitBranch size={15} strokeWidth={2} /></span>
                  <div>
                    <b>{source.name}</b>
                    <small>{source.kind} · {sourceLocation(source)}</small>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        {error && <p className="auth-error" role="alert">{error}</p>}

        <div className="dialog-actions">
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            type="button"
            onClick={() => setConnecting(true)}
            disabled={needsProject}
            title={needsProject ? 'Choose a project first' : undefined}
          >
            <Plug size={14} strokeWidth={2} /> Connect a source
          </Button>
        </div>

        {connecting && (
          <ConnectRepositoryDialog
            projectId={chosenProject}
            currentKinds={(sources ?? []).map((source) => source.kind)}
            onClose={() => setConnecting(false)}
            // Straight into the run: connecting from here is one step of
            // choosing what to analyse, not a separate errand.
            onConnected={(source) => { setConnecting(false); onPicked(source, chosenProject); }}
          />
        )}
      </section>
    </div>
  );
}
