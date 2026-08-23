import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Trash2, X } from 'lucide-react';
import { Button } from '../../components/common/Button';
import { PageHeader } from '../../components/common/PageHeader';
import {
  createProject,
  deleteProject,
  listProjects,
  relativeTime,
} from '../../features/projects/api/projectsApi';

/**
 * Creating a project names it and then goes straight into its first analysis,
 * which is saved back to that project automatically. Naming it here rather
 * than at the save step means the run has somewhere to belong from the start,
 * instead of the button promising a project and producing nothing.
 */
export function ProjectsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState(null);
  const [error, setError] = useState(null);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [creating, setCreating] = useState(false);

  const load = () => listProjects().then(setProjects).catch((e) => {
    setProjects([]);
    setError(e.message);
  });

  useEffect(() => { load(); }, []);

  const remove = async (project) => {
    // Deleting a project takes its saved analyses with it, so this is a
    // confirm-first action.
    const count = project.analysis_count;
    const detail = count ? ` and its ${count} saved analys${count === 1 ? 'is' : 'es'}` : '';
    if (!window.confirm(`Delete "${project.project_name}"${detail}? This cannot be undone.`)) {
      return;
    }

    setError(null);
    try {
      await deleteProject(project.project_id);
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  const create = async (event) => {
    event.preventDefault();
    setError(null);
    setCreating(true);
    try {
      const project = await createProject({
        name: name.trim(),
        description: description.trim() || null,
      });
      // Straight into the first run, which will file itself under this project.
      navigate(`/analysis?project=${project.project_id}`);
    } catch (requestError) {
      setError(requestError.message);
      setCreating(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Projects"
        description="Keep saved analyses, artifacts, and history together."
        actions={<Button onClick={() => setNaming(true)}>+ Create Project</Button>}
      />

      {naming && (
        <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Create project">
          <section className="dialog-card">
            <header className="dialog-head">
              <h2>Create a Project</h2>
              <button type="button" onClick={() => setNaming(false)} aria-label="Close">
                <X size={16} strokeWidth={2.2} />
              </button>
            </header>
            <form onSubmit={create}>
              <label>
                Project Name
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                  autoFocus
                  autoComplete="off"
                  placeholder="eTour traceability"
                />
              </label>
              <label>
                <span className="field-name">
                  Description<span className="dialog-optional">optional</span>
                </span>
                <textarea
                  rows="2"
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="What this project traces, and why."
                />
              </label>
              <p className="dialog-note">
                You will go straight to a new analysis, and its results are saved
                into this project automatically.
              </p>
              <div className="dialog-actions">
                <Button type="button" variant="secondary" onClick={() => setNaming(false)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={creating || !name.trim()}>
                  {creating ? 'Creating…' : 'Create & Start Analysis'}
                </Button>
              </div>
            </form>
          </section>
        </div>
      )}

      {error && <p className="auth-error" role="alert">{error}</p>}

      {projects === null && <p className="dialog-note">Loading projects…</p>}

      {projects?.length === 0 && (
        <p className="dialog-note">
          No projects yet. Use <b>Create Project</b> to name one and run its first analysis.
        </p>
      )}

      {Boolean(projects?.length) && (
        <section className="project-grid">
          {projects.map((project) => (
            <article className="project-card" key={project.project_id}>
              {/* The card body opens the project; the delete button sits
                  outside the link so it is not a nested interactive element. */}
              <Link to={`/app/history?project=${project.project_id}`}>
                <span className="project-avatar">{project.project_name[0].toUpperCase()}</span>
                <small>Last activity {relativeTime(project.updated_at)}</small>
                <h2>{project.project_name}</h2>
                {project.description && <p>{project.description}</p>}
              </Link>
              <footer>
                <span>
                  {project.analysis_count} saved analys
                  {project.analysis_count === 1 ? 'is' : 'es'}
                </span>
                <button
                  type="button"
                  className="card-delete"
                  onClick={() => remove(project)}
                  aria-label={`Delete ${project.project_name}`}
                >
                  <Trash2 size={14} strokeWidth={2} />
                </button>
              </footer>
            </article>
          ))}
        </section>
      )}
    </>
  );
}
