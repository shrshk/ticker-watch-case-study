import { useCallback, useContext, useState } from 'react';
import { UserContext } from './UserContext';

export function LoginForm() {
  // Get login function from user context.
  const { user, login } = useContext(UserContext);

  // Form control.
  const [username, setUsername] = useState('');

  // Make call to backend server to login. Save user object to user context.
  const handleLogin = useCallback((event) => {
    event.preventDefault();
    fetch('http://localhost:8000/login/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username }),
    })
    .then((response) => response.json())
    .then((data) => {
      login(data);
      console.info('Logged in successfully');
    })
    .catch((error) => {
      console.error('Unable to login', error);
    });
  }, [username]);

  if (user) return null;

  return (
    <div className='login-form'>
      <form onSubmit={ handleLogin }>
        <h2>Log in</h2>
        <div className="inputs">
          <input
            type="text"
            id="username"
            placeholder="Enter your username"
            value={ username }
            onChange={ (event) => setUsername(event.target.value) }
          />
          <button type="submit">Login</button>
        </div>
      </form>
    </div>
  );
}
