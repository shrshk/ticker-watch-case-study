// The transport abstraction.
//
// Both modes sit behind one hook and return the same shape. The rest of the UI
// must not know which transport is active, or the poll-vs-push comparison
// stops being like-for-like.
//
// TRANSPORT=poll  - GET /watchlist on a fixed-phase 5s timer (usePollPrices.js)
// TRANSPORT=push  - centrifuge-js, subscribe -> snapshot -> drain on every
//                   connect (usePushPrices.js)

import { usePollWatchlist } from './usePollPrices';
import { usePushWatchlist } from './usePushPrices';

const TRANSPORT = import.meta.env.VITE_TRANSPORT || 'poll';
const INTERVAL_MS = Number(import.meta.env.VITE_POLL_INTERVAL_SECONDS || 5) * 1000;

// TRANSPORT is a build-time constant, so this is one hook or the other for the
// life of the page - not a conditional hook call.
export const useWatchlist = TRANSPORT === 'push' ? usePushWatchlist : usePollWatchlist;

export const transport = TRANSPORT;
export const pollIntervalMs = INTERVAL_MS;
