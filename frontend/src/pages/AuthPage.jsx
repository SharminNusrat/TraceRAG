import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Brand } from '../components/common/Brand';
import { Button } from '../components/common/Button';
import { useAuth } from '../features/auth/AuthContext';

export function AuthPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { signIn } = useAuth();
  const searchParams = new URLSearchParams(location.search);
  const [isSignUp, setIsSignUp] = useState(searchParams.get('mode') === 'signup');
  const intent = searchParams.get('intent');
  const returnTo = searchParams.get('returnTo') || '/app';

  const submit = (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);

    signIn({
      name: data.get('name'),
      email: data.get('email'),
    });
    navigate(returnTo);
  };

  return (
    <main className="auth-page">
      <Link className="back-link" to="/">← Back to TraceRAG</Link>
      <section className="auth-card">
        <Brand />
        <div className="auth-heading">
          <div className="eyebrow">
            <span />
            {intent === 'save' ? 'Save this analysis' : 'Project workspace'}
          </div>
          <h1>{isSignUp ? 'Create your account' : 'Welcome back'}</h1>
          <p>
            {intent === 'save'
              ? 'Sign in to save this analysis and access it later from a project.'
              : 'Save analyses, manage projects, and return to your history.'}
          </p>
        </div>

        <form onSubmit={submit}>
          {isSignUp && (
            <label>
              Full name
              <input name="name" required placeholder="Alex Morgan" />
            </label>
          )}
          <label>
            Email address
            <input name="email" type="email" required placeholder="you@company.com" />
          </label>
          <label>
            Password
            <input name="password" type="password" required placeholder="••••••••" />
          </label>
          <Button type="submit">
            {isSignUp ? 'Create account' : 'Sign in'} <span>→</span>
          </Button>
        </form>

        <p className="auth-switch">
          {isSignUp ? 'Already have an account?' : 'New to TraceRAG?'}{' '}
          <button onClick={() => setIsSignUp(!isSignUp)}>
            {isSignUp ? 'Sign in' : 'Create one'}
          </button>
        </p>
      </section>
    </main>
  );
}
