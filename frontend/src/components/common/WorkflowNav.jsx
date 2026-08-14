import { Link, useNavigate } from 'react-router-dom';
import { LayoutGrid, X } from 'lucide-react';
import { Brand } from './Brand';
import { useAuth } from '../../features/auth/AuthContext';

/**
 * Top bar for the full-page workflow screens, which sit outside the app
 * layout and so have no sidebar of their own. Without the workspace link,
 * leaving one of these pages means going back to the landing page and in
 * again.
 */
export function WorkflowNav({ title, exitLabel = 'Exit' }) {
  const navigate = useNavigate();
  const { user } = useAuth();

  return (
    <nav className="workflow-nav">
      <Link to={user ? '/app' : '/'}><Brand /></Link>
      <span>{title}</span>

      {user && (
        <Link className="workflow-link" to="/app">
          <LayoutGrid size={14} strokeWidth={2} />
          <span>Workspace</span>
        </Link>
      )}

      <button
        type="button"
        className="workflow-exit-btn"
        onClick={() => navigate(user ? '/app' : '/')}
        aria-label={exitLabel}
      >
        <X size={15} strokeWidth={2.2} />
        <span>{exitLabel}</span>
      </button>
    </nav>
  );
}
