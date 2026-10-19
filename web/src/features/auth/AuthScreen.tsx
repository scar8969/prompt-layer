import { useState, type FormEvent } from 'react';
import { Link as RouterLink, useRouter } from '../../app/router.tsx';
import { auth } from '../../api/endpoints.ts';
import { useSession } from '../../state/session.tsx';
import { Button } from '../../components/Button.tsx';
import { TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { Icon } from '../../components/Icon.tsx';

type AuthMode = 'login' | 'register' | 'forgot' | 'reset';

const COPY: Record<AuthMode, { title: string; subtitle: string; submit: string }> = {
  login: {
    title: 'Sign in',
    subtitle: 'Your connections, keys and models live in this workspace.',
    submit: 'Sign in',
  },
  register: {
    title: 'Create a workspace',
    subtitle: 'One account owns the connections. Every key stays on the server.',
    submit: 'Create account',
  },
  forgot: {
    title: 'Reset your password',
    subtitle: 'Ask the backend for a reset token, then choose a new password.',
    submit: 'Request a reset token',
  },
  reset: {
    title: 'Choose a new password',
    subtitle: 'Paste the reset token the backend issued and set a new password.',
    submit: 'Set the new password',
  },
};

export function AuthScreen({ mode }: { mode: AuthMode }) {
  const { route, navigate } = useRouter();
  const session = useSession();
  const next = route.params.get('next') ?? '/';

  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [token, setToken] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const copy = COPY[mode];

  /** Passwords are read out of the form and then dropped; they are never echoed. */
  const clearSecrets = () => {
    setPassword('');
    setConfirm('');
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (mode === 'register' && password !== confirm) {
      setError(new Error('The two passwords do not match.'));
      return;
    }
    setPending(true);
    try {
      if (mode === 'login') {
        const result = await auth.login({ email: email.trim(), password });
        clearSecrets();
        session.signIn({
          user: result.user,
          workspace: result.workspace,
          csrfToken: result.session.csrf_token,
        });
        navigate(next, { replace: true });
        return;
      }
      if (mode === 'register') {
        const result = await auth.register({
          email: email.trim(),
          password,
          display_name: displayName.trim(),
        });
        clearSecrets();
        session.signIn({
          user: result.user,
          workspace: result.workspace,
          csrfToken: result.session.csrf_token,
        });
        navigate(next, { replace: true });
        return;
      }
      if (mode === 'forgot') {
        const result = await auth.forgotPassword({ email: email.trim() });
        setNotice(
          result.resetToken
            ? `The backend issued a reset token and reported delivery via "${result.delivery ?? 'unknown channel'}". The token is below; the client shows it exactly once.`
            : `The backend answered delivery via "${result.delivery ?? 'unknown channel'}" and did not return a token to the client. Use the link it sent you.`,
        );
        if (result.resetToken) {
          setToken(result.resetToken);
          navigate(`/reset-password?next=${encodeURIComponent(next)}`, { replace: true });
        }
        return;
      }
      await auth.resetPassword({ token: token.trim(), new_password: password });
      clearSecrets();
      setNotice('The password was changed. Sign in with the new one.');
      navigate(next ? `/login?next=${encodeURIComponent(next)}` : '/login', { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="auth">
      <div className="auth__card">
        <div className="auth__brand">
          <span className="rail__mark" aria-hidden="true">
            <Icon name="layers" size={18} />
          </span>
          <span className="rail__name">Prompt Layer Studio</span>
        </div>

        <div className="stack stack--tight">
          <h1 className="page__heading">{copy.title}</h1>
          <p className="page__subtitle">{copy.subtitle}</p>
        </div>

        <form className="stack" onSubmit={submit} noValidate>
          <TextInput
            label="Email"
            type="email"
            name="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />

          {mode === 'register' ? (
            <TextInput
              label="Display name"
              name="display_name"
              autoComplete="name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              hint="Shown in the rail and on jobs you create."
              required
            />
          ) : null}

          {mode === 'reset' ? (
            <TextInput
              label="Reset token"
              name="token"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              hint="The token the backend issued for this reset request."
              required
            />
          ) : null}

          {mode !== 'forgot' ? (
            <TextInput
              label={mode === 'reset' ? 'New password' : 'Password'}
              type="password"
              name="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              hint={
                mode === 'login'
                  ? undefined
                  : 'Sent once to the backend. The client never stores it and never shows it again.'
              }
              required
            />
          ) : null}

          {mode === 'register' ? (
            <TextInput
              label="Confirm password"
              type="password"
              name="confirm"
              autoComplete="new-password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              required
            />
          ) : null}

          {error ? <ErrorNotice error={error} /> : null}

          {notice ? (
            <div className="panel panel--info" role="status">
              <p className="panel__title">Backend response</p>
              <p style={{ margin: 0 }}>{notice}</p>
            </div>
          ) : null}

          <Button type="submit" variant="primary" block disabled={pending}>
            {pending ? 'Working…' : copy.submit}
          </Button>
        </form>

        <div className="auth__foot">
          {mode === 'login' ? (
            <>
              <RouterLink to={`/register?next=${encodeURIComponent(next)}`}>Create a workspace</RouterLink>
              <RouterLink to={`/forgot-password?next=${encodeURIComponent(next)}`}>Forgot your password?</RouterLink>
            </>
          ) : (
            <RouterLink to={`/login?next=${encodeURIComponent(next)}`}>Back to sign in</RouterLink>
          )}
        </div>
      </div>
    </div>
  );
}
