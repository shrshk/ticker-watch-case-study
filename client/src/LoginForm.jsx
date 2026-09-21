import { useCallback, useContext, useState } from 'react';
import { UserContext } from './UserContext';
import { api } from './api';

export function LoginForm() {
  const { login } = useContext(UserContext);

  const [username, setUsername] = useState('user1');
  const [password, setPassword] = useState('password');
  const [mode, setMode] = useState('login');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = useCallback(
    async (event) => {
      event.preventDefault();
      setBusy(true);
      setError(null);
      try {
        const data =
          mode === 'login'
            ? await api.login(username, password)
            : await api.register(username, password);
        login(data.access_token, data.user);
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    },
    [mode, username, password, login],
  );

  return (
    <div className="login-form">
      <form onSubmit={submit}>
        <h2>{mode === 'login' ? 'Log in' : 'Create an account'}</h2>
        <div className="inputs">
          <input
            type="text"
            placeholder="Username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <input
            type="password"
            placeholder="Password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <button type="submit" disabled={busy}>
            {busy ? '...' : mode === 'login' ? 'Log in' : 'Register'}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        <p className="hint">
          {mode === 'login' ? (
            <>
              No account? <button type="button" className="link" onClick={() => setMode('register')}>Register</button>
            </>
          ) : (
            <>
              Already registered? <button type="button" className="link" onClick={() => setMode('login')}>Log in</button>
            </>
          )}
        </p>
      </form>
    </div>
  );
}
