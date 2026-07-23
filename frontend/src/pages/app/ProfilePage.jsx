import { Button } from '../../components/common/Button';
import { PageHeader } from '../../components/common/PageHeader';
import { useAuth } from '../../features/auth/AuthContext';

export function ProfilePage() {
  const { user } = useAuth();
  const initials = user.name.split(' ').map((part) => part[0]).join('');

  return (
    <>
      <PageHeader title="Profile & preferences" />
      <section className="profile-card">
        <span>{initials}</span>
        <div>
          <h2>{user.name}</h2>
          <p>{user.email}</p>
        </div>
        <Button variant="secondary">Edit profile</Button>
      </section>
      <section className="preferences">
        <h2>Preferences</h2>
        <label>
          <span>
            <b>Email notifications</b>
            <small>Receive a summary when an analysis completes.</small>
          </span>
          <input type="checkbox" defaultChecked />
        </label>
        <label>
          <span>
            <b>Default results view</b>
            <small>Open completed analyses in the trace matrix.</small>
          </span>
          <select defaultValue="matrix">
            <option value="matrix">Trace matrix</option>
            <option value="graph">Trace graph</option>
          </select>
        </label>
      </section>
    </>
  );
}
