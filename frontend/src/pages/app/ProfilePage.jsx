import { PageHeader } from '../../components/common/PageHeader';
import { useAuth } from '../../features/auth/AuthContext';
import { GitHubConnection } from '../../features/sync/components/GitHubConnection';

/**
 * Read-only for now. Editing a profile, email notifications and a choice of
 * default results view were all mocked up with nothing behind them, so they
 * are gone rather than left as controls that quietly do nothing.
 */
export function ProfilePage() {
  const { user } = useAuth();
  const initials = user.full_name.split(' ').map((part) => part[0]).join('').slice(0, 2);
  const joined = new Date(user.created_at);

  return (
    <>
      <PageHeader title="Profile" />
      <section className="profile-card">
        <span>{initials}</span>
        <div>
          <h2>{user.full_name}</h2>
          <p>{user.email}</p>
        </div>
      </section>
      {!Number.isNaN(joined.getTime()) && (
        <p className="dialog-note">
          Member since {joined.toLocaleDateString(undefined, {
            day: 'numeric', month: 'long', year: 'numeric',
          })}.
        </p>
      )}

      {/* Where the OAuth callback sends the browser back to, so this is also
          the page that reports whether connecting worked. */}
      <h2 className="section-heading">Connected Accounts</h2>
      <GitHubConnection />
    </>
  );
}
