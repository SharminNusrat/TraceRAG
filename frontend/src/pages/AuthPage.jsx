import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Brand } from '../components/common/Brand';
import { Button } from '../components/common/Button';
import { useAuth } from '../features/auth/AuthContext';

export function AuthPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { signIn, signUp } = useAuth();
  const searchParams = new URLSearchParams(location.search);
  const [isSignUp, setIsSignUp] = useState(searchParams.get('mode') === 'signup');
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);
  const intent = searchParams.get('intent');
  const returnTo = searchParams.get('returnTo') || '/app';

  const submit = async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setError(null);
    setPending(true);

    try {
      if (isSignUp) {
        await signUp({
          fullName: data.get('name'),
          email: data.get('email'),
          password: data.get('password'),
        });
      } else {
        await signIn({
          email: data.get('email'),
          password: data.get('password'),
        });
      }
      // Deliberately no setPending(false): the page unmounts on navigate.
      navigate(returnTo, { replace: true });
    } catch (requestError) {
      setError(requestError.message);
      setPending(false);
    }
  };

  const toggleMode = () => {
    setIsSignUp(!isSignUp);
    setError(null);
  };

  return (
    <main className="auth-page">
      <Link className="back-link" to="/">← Back to TraceRAG</Link>
      <section className="auth-card">
        <Brand />
        <div className="auth-heading">
          <div className="eyebrow">
            <span />
            {intent === 'save' ? 'Save This Analysis' : 'Project workspace'}
          </div>
          <h1>{isSignUp ? 'Create Your Account' : 'Welcome Back'}</h1>
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
            <input
              name="password"
              type="password"
              required
              // Matches the API rule, so a too-short password is caught before
              // the round trip. Only enforced on sign-up; an existing account
              // is whatever it already is.
              minLength={isSignUp ? 8 : undefined}
              placeholder={isSignUp ? 'At least 8 characters' : '••••••••'}
            />
          </label>
          {error && <p className="auth-error" role="alert">{error}</p>}
          <Button type="submit" disabled={pending}>
            {pending ? 'Please wait…' : (
              <>
                {isSignUp ? 'Create account' : 'Sign in'} <span>→</span>
              </>
            )}
          </Button>
        </form>

        <p className="auth-switch">
          {isSignUp ? 'Already have an account?' : 'New to TraceRAG?'}{' '}
          <button type="button" onClick={toggleMode}>
            {isSignUp ? 'Sign in' : 'Create one'}
          </button>
        </p>
      </section>
    </main>
  );
}
