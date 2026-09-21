// The transport abstraction.
//
// Both modes sit behind this one hook and return the same shape. The rest of
// the UI must not know which transport is active, or the poll-vs-push
// comparison stops being like-for-like.
//
// TRANSPORT=poll  - setInterval on GET /watchlist, jittered so clients do not
//                   align into synchronised spikes.
// TRANSPORT=push  - phase 3: centrifuge-js behind this same interface.

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

const INTERVAL_MS = Number(import.meta.env.VITE_POLL_INTERVAL_SECONDS || 5) * 1000;

export function usePollWatchlist(token) {
  const [items, setItems] = useState([]);
  const [meta, setMeta] = useState(null);
  const [status, setStatus] = useState('connecting');
  const [error, setError] = useState(null);

  // Previous prices, so the UI can flash only the rows that actually moved.
  const previous = useRef(new Map());
  const [changed, setChanged] = useState(new Set());

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const data = await api.watchlist(token);
      const moved = new Set();
      for (const item of data.items) {
        const before = previous.current.get(item.id);
        if (before !== undefined && before !== item.price) moved.add(item.id);
        previous.current.set(item.id, item.price);
      }
      setItems(data.items);
      setMeta({
        readPath: data.read_path,
        cacheHits: data.cache_hits,
        cacheMisses: data.cache_misses,
        asOf: data.as_of,
      });
      setChanged(moved);
      setStatus('live');
      setError(null);
    } catch (err) {
      setStatus('offline');
      setError(err.message);
    }
  }, [token]);

  useEffect(() => {
    if (!token) return undefined;

    let cancelled = false;
    let timer;

    // Jitter the first call. A million clients on a 5s timer otherwise drift
    // into aligned spikes, which is client-side behaviour a server cannot
    // enforce - one of the structural costs of polling.
    const startDelay = Math.random() * INTERVAL_MS;

    // First poll now, so the screen is never empty; then a fixed-phase timer
    // from a jittered offset. Scheduling from the previous *start* rather than
    // the previous *completion* keeps the interval at INTERVAL_MS instead of
    // INTERVAL_MS plus request latency, which is how the Go load generator
    // behaves - the two must poll the same way for the comparison to hold.
    refresh();
    let next = Date.now() + startDelay;
    const tick = async () => {
      if (cancelled) return;
      next += INTERVAL_MS;
      timer = setTimeout(tick, Math.max(0, next - Date.now()));
      await refresh();
    };
    timer = setTimeout(tick, startDelay);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [token, refresh]);

  return { items, meta, status, error, changed, refresh, transport: 'poll' };
}

