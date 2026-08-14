import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { PanelLeft } from 'lucide-react';
import { Brand } from '../common/Brand';
import { Button } from '../common/Button';
import { useAuth } from '../../features/auth/AuthContext';

const navigation = [
  ['/app', 'Overview', '⌂'],
  ['/app/projects', 'Projects', '▣'],
  ['/app/history', 'Analysis History', '◷'],
  ['/app/profile', 'Profile', '○'],
];

const STORAGE_KEY = 'tracerag-sidebar-collapsed';

export function AppLayout() {
  const { user, signOut } = useAuth();
  // Remembered, so the choice survives navigating between pages.
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(STORAGE_KEY) === '1',
  );

  const toggle = () => setCollapsed((current) => {
    localStorage.setItem(STORAGE_KEY, current ? '0' : '1');
    return !current;
  });

  const initials = user.full_name
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2);

  return (
    <div className={collapsed ? 'app-shell collapsed' : 'app-shell'}>
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">
          <span>{user.full_name.slice(0, 1)}</span>
          <div><b>My Workspace</b></div>
        </div>
        <nav>
          {navigation.map(([to, label, icon]) => (
            <NavLink key={to} to={to} end={to === '/app'}>
              <span>{icon}</span>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <Button variant="ghost" onClick={signOut}>Sign out</Button>
          <div className="account">
            <span>{initials}</span>
            <div>
              <b>{user.full_name}</b>
              <small>{user.email}</small>
            </div>
          </div>
        </div>
      </aside>

      {/* Sits outside the sidebar so it stays reachable once it is hidden. */}
      <button
        type="button"
        className="sidebar-toggle"
        onClick={toggle}
        aria-label={collapsed ? 'Show navigation' : 'Hide navigation'}
        aria-expanded={!collapsed}
        title={collapsed ? 'Show navigation' : 'Hide navigation'}
      >
        <PanelLeft size={16} strokeWidth={2} />
      </button>

      <main className="app-content"><Outlet /></main>
    </div>
  );
}
