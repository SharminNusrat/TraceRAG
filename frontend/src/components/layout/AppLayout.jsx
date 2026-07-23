import { NavLink, Outlet } from 'react-router-dom';
import { Brand } from '../common/Brand';
import { Button } from '../common/Button';
import { useAuth } from '../../features/auth/AuthContext';

const navigation = [
  ['/app', 'Overview', '⌂'],
  ['/app/projects', 'Projects', '▣'],
  ['/app/history', 'Analysis history', '◷'],
  ['/app/profile', 'Profile', '○'],
];

export function AppLayout() {
  const { user, signOut } = useAuth();
  const initials = user.name
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">
          <span>{user.name.slice(0, 1)}</span>
          <div>
            <b>My workspace</b>
            <small>Project mode</small>
          </div>
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
              <b>{user.name}</b>
              <small>{user.email}</small>
            </div>
          </div>
        </div>
      </aside>
      <main className="app-content"><Outlet /></main>
    </div>
  );
}
