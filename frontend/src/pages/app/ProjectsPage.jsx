import { Link } from 'react-router-dom';
import { Button } from '../../components/common/Button';
import { PageHeader } from '../../components/common/PageHeader';

const projects = ['Atlas workspace', 'Mobile banking', 'Legacy platform'];

export function ProjectsPage() {
  return (
    <>
      <PageHeader
        title="Projects"
        description="Keep saved analyses, artifacts, and history together."
        actions={<Button>+ Create project</Button>}
      />
      <section className="project-grid">
        {projects.map((project, index) => (
          <article className="project-card" key={project}>
            <span>{project[0]}</span>
            <small>Last activity {index + 1} day ago</small>
            <h2>{project}</h2>
            <p>Requirements traceability for the project codebase.</p>
            <footer>
              <b>{index + 2} analyses</b>
              <Link to="/analysis">Open →</Link>
            </footer>
          </article>
        ))}
      </section>
    </>
  );
}
