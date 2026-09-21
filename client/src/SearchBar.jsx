import { useContext, useEffect, useRef, useState } from 'react';
import { UserContext } from './UserContext';
import { api } from './api';

const DEBOUNCE_MS = 250;

export function SearchBar({ onAdd, watchedIds }) {
  const { token } = useContext(UserContext);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [error, setError] = useState(null);
  const latest = useRef(0);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      return undefined;
    }

    const requestId = ++latest.current;
    const timer = setTimeout(async () => {
      try {
        const found = await api.search(token, trimmed);
        // Drop responses that arrived out of order.
        if (requestId === latest.current) setResults(found);
      } catch (err) {
        setError(err.message);
      }
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [query, token]);

  return (
    <div className="search">
      <input
        type="search"
        placeholder="Search by ticker or company name, e.g. NV or Nvidia"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {error && <p className="error">{error}</p>}
      {results.length > 0 && (
        <ul className="results">
          {results.map((security) => (
            <li key={security.id}>
              <span className="ticker">{security.ticker}</span>
              <span className="name">{security.name}</span>
              <button
                type="button"
                disabled={watchedIds.has(security.id)}
                onClick={() => {
                  onAdd(security.id);
                  setQuery('');
                }}
              >
                {watchedIds.has(security.id) ? 'On list' : 'Add'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
