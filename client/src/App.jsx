import './App.css';
import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { LoginForm } from './LoginForm';
import { SearchBar } from './SearchBar';
import { StatusBar } from './StatusBar';
import { User } from './User';
import { UserContext } from './UserContext';
import { Watchlist } from './Watchlist';
import { api, clearSession, storedSession, storeSession } from './api';
import { pollIntervalMs, useWatchlist } from './usePrices';

function Main() {
  const { token } = useContext(UserContext);
  const { items, meta, status, error, changed, refresh, transport } = useWatchlist(token);

  const watchedIds = useMemo(() => new Set(items.map((item) => item.id)), [items]);

  // Read the price source off the rows, not off a config endpoint. The API
  // does not own PRICE_SOURCE - the price service does - so asking the API
  // reports whatever that container was started with, which silently goes
  // stale the moment the two are configured differently. Every price carries
  // the source that produced it, so the badge cannot drift from the truth.
  const simulated = useMemo(
    () => items.some((item) => item.source && item.source !== 'api'),
    [items],
  );

  const add = useCallback(
    async (securityId) => {
      await api.addItem(token, securityId);
      await refresh();
    },
    [token, refresh],
  );

  const remove = useCallback(
    async (securityId) => {
      await api.removeItem(token, securityId);
      await refresh();
    },
    [token, refresh],
  );

  return (
    <section className="main">
      <StatusBar
        transport={transport}
        status={status}
        meta={meta}
        simulated={simulated}
        intervalSeconds={pollIntervalMs / 1000}
      />
      {error && <p className="error">{error}</p>}
      <SearchBar onAdd={add} watchedIds={watchedIds} />
      <Watchlist items={items} changed={changed} onRemove={remove} />
    </section>
  );
}

export default function App() {
  const [session, setSession] = useState(() => storedSession());

  const login = useCallback((token, refresh, user) => {
    storeSession(token, refresh, user);
    setSession({ token, refresh, user });
  }, []);

  const logout = useCallback(() => {
    const refresh = session?.refresh;
    clearSession();
    setSession(null);
    if (refresh) api.logout(refresh).catch(() => {});
  }, [session]);

  useEffect(() => {
    const onUnauthorized = () => setSession(null);
    // A background refresh rotated the tokens; pick up the new access token
    // so the next poll uses it instead of the one that just expired.
    const onRefreshed = (event) => {
      const { access_token: token, refresh_token: refresh, user } = event.detail;
      setSession({ token, refresh, user });
    };
    window.addEventListener('watchlist:unauthorized', onUnauthorized);
    window.addEventListener('watchlist:refreshed', onRefreshed);
    return () => {
      window.removeEventListener('watchlist:unauthorized', onUnauthorized);
      window.removeEventListener('watchlist:refreshed', onRefreshed);
    };
  }, []);

  const value = useMemo(
    () => ({ token: session?.token ?? null, user: session?.user ?? null, login, logout }),
    [session, login, logout],
  );

  return (
    <UserContext.Provider value={value}>
      <div className="app">
        <header>
          <h1>Ticker watch</h1>
          <User />
        </header>
        {session ? <Main /> : <LoginForm />}
      </div>
    </UserContext.Provider>
  );
}
