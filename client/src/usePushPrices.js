// TRANSPORT=push: centrifuge-js behind the same interface as polling.
//
// The ordering rule, on connect AND on every reconnect (plan §10):
//   1. subscribe to ticker:* for the watchlist, buffering events without applying
//   2. GET /watchlist for membership and snapshot prices
//   3. apply the snapshot
//   4. drain the buffer, discarding anything older than what was applied
//
// Read-then-subscribe would drop any update landing in the gap. This is cheap
// up front and painful to retrofit, so it is the first thing built.
//
// Channels are per ticker, never per user. Adding or removing a stock
// subscribes or unsubscribes one channel; the rest are untouched.

import { useCallback, useEffect, useRef, useState } from 'react';
import { Centrifuge } from 'centrifuge';
import { api } from './api';

const WS_URL = import.meta.env.VITE_CENTRIFUGO_URL || 'ws://localhost:8001/connection/websocket';

export function usePushWatchlist(token) {
  const [items, setItems] = useState([]);
  const [meta, setMeta] = useState(null);
  const [status, setStatus] = useState('connecting');
  const [error, setError] = useState(null);
  const [changed, setChanged] = useState(new Set());

  const client = useRef(null);
  const subs = useRef(new Map()); // ticker -> Subscription
  const buffering = useRef(true); // true until the snapshot is applied
  const buffer = useRef([]); // events received while buffering
  const byTicker = useRef(new Map()); // ticker -> item (the applied state)

  // Apply one price event if it is newer than what we hold. Returns the
  // security id if the row moved, else null.
  const applyEvent = useCallback((ev) => {
    const cur = byTicker.current.get(ev.ticker);
    if (!cur) return null;
    if (cur.effective_at && ev.effective_at <= cur.effective_at) return null;
    const next = { ...cur, price: ev.price, effective_at: ev.effective_at, source: ev.source };
    byTicker.current.set(ev.ticker, next);
    return next.id;
  }, []);

  const publish = useCallback((movedIds) => {
    setItems([...byTicker.current.values()].sort((a, b) => a.ticker.localeCompare(b.ticker)));
    setChanged(new Set(movedIds));
  }, []);

  const ensureSubscribed = useCallback((ticker) => {
    if (!client.current || subs.current.has(ticker)) return;
    const sub = client.current.newSubscription(`ticker:${ticker}`);
    sub.on('publication', (ctx) => {
      if (buffering.current) {
        buffer.current.push(ctx.data);
        return;
      }
      const id = applyEvent(ctx.data);
      if (id !== null) publish([id]);
    });
    sub.subscribe();
    subs.current.set(ticker, sub);
  }, [applyEvent, publish]);

  const dropSubscription = useCallback((ticker) => {
    const sub = subs.current.get(ticker);
    if (!sub) return;
    sub.unsubscribe();
    client.current?.removeSubscription(sub);
    subs.current.delete(ticker);
  }, []);

  // Steps 1-4. Called on first connect, on every reconnect, and after add/remove.
  const resync = useCallback(async () => {
    if (!token) return;
    buffering.current = true;
    buffer.current = [];
    try {
      const data = await api.watchlist(token);
      byTicker.current = new Map(data.items.map((it) => [it.ticker, it]));
      for (const it of data.items) ensureSubscribed(it.ticker);
      for (const t of [...subs.current.keys()]) if (!byTicker.current.has(t)) dropSubscription(t);
      setMeta({ readPath: data.read_path, cacheHits: data.cache_hits, cacheMisses: data.cache_misses, asOf: data.as_of });

      // Drain: anything that arrived during the snapshot and is newer wins.
      buffering.current = false;
      const moved = [];
      for (const ev of buffer.current) {
        const id = applyEvent(ev);
        if (id !== null) moved.push(id);
      }
      buffer.current = [];
      publish(moved);
      setError(null);
    } catch (err) {
      setError(err.message);
      buffering.current = false;
    }
  }, [token, ensureSubscribed, dropSubscription, applyEvent, publish]);

  useEffect(() => {
    if (!token) return undefined;

    const c = new Centrifuge(WS_URL, {
      // Centrifugo verifies this with the same secret the API signs with.
      getToken: async () => (await api.realtimeToken(token)).token,
    });
    client.current = c;

    c.on('connecting', () => setStatus((s) => (s === 'live' ? 'reconnecting' : 'connecting')));
    c.on('connected', () => {
      setStatus('live');
      resync(); // first connect and every reconnect: snapshot after subscribing
    });
    c.on('disconnected', () => setStatus('offline'));
    c.on('error', (ctx) => setError(ctx.error?.message || 'realtime error'));

    c.connect();
    return () => {
      for (const t of [...subs.current.keys()]) dropSubscription(t);
      c.disconnect();
      client.current = null;
    };
  }, [token, resync, dropSubscription]);

  return { items, meta, status, error, changed, refresh: resync, transport: 'push' };
}
