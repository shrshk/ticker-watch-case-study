import { useContext } from 'react';
import { UserContext } from './UserContext.jsx';

export function User() {
  const { user, logout } = useContext(UserContext);

  if (!user) return null;

  const name =
    user.first_name && user.last_name
      ? `${user.first_name} ${user.last_name}`
      : user.username;

  return (
    <div className="user">
      <span>Signed in as {name}</span>
      <button type="button" onClick={logout}>
        Log out
      </button>
    </div>
  );
}
