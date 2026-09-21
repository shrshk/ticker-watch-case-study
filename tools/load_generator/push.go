package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/centrifugal/centrifuge-go"
)

// pushCounters are the push-only signals. Everything shared with polling
// (requests, errors, update latency) stays on `counters` so the two reports
// are read the same way.
type pushCounters struct {
	connects     atomic.Int64
	disconnects  atomic.Int64
	subscribed   atomic.Int64
	publications atomic.Int64
	errors       atomic.Int64
}

// runPush opens one real Centrifugo connection per client, subscribes it to
// the tickers on its user's watchlist, and measures update latency at the
// client - from the price's effective_at to the moment the publication
// arrives. The same metric, measured the same way, as polling; otherwise the
// comparison is not like-for-like.
//
// Ordering per connection follows the client: subscribe first (buffering),
// then GET /watchlist for the snapshot, then apply and drain. Here the buffer
// is implicit - a publication older than the snapshot is simply not counted
// as an update.
func runPush(cfg config) {
	wsURL := envOr("CENTRIFUGO_URL", "ws://localhost:8001/connection/websocket")

	ctr := &counters{}
	pctr := &pushCounters{}
	requestLatency := newSamples(cfg.clients * 2)
	updateLatency := newSamples(cfg.clients * 64)

	ctx, cancel := context.WithTimeout(context.Background(), cfg.duration)
	defer cancel()

	transport := &http.Transport{MaxIdleConnsPerHost: 256, IdleConnTimeout: 90 * time.Second}
	httpClient := &http.Client{Transport: transport, Timeout: 30 * time.Second}

	started := time.Now()
	go pushProgress(ctx, ctr, pctr, started)

	var wg sync.WaitGroup
	for i := 0; i < cfg.clients; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			pushClient(ctx, cfg, wsURL, httpClient, n, ctr, pctr, requestLatency, updateLatency)
		}(i)
	}
	wg.Wait()

	reportPush(cfg, ctr, pctr, requestLatency, updateLatency, time.Since(started))
}

func pushClient(
	ctx context.Context,
	cfg config,
	wsURL string,
	httpClient *http.Client,
	n int,
	ctr *counters,
	pctr *pushCounters,
	requestLatency, updateLatency *samples,
) {
	rng := rand.New(rand.NewSource(int64(n)*7919 + 13))
	span := cfg.userIDMax - cfg.userIDMin + 1
	if cfg.logicalUsers < span {
		span = cfg.logicalUsers
	}
	userID := cfg.userIDMin + rng.Intn(span)

	// Stagger connects. A reconnect storm is a separate, deliberate scenario.
	select {
	case <-ctx.Done():
		return
	case <-time.After(time.Duration(rng.Int63n(int64(cfg.rampUp)))):
	}

	// The API access token, for the snapshot; the connection token, for
	// Centrifugo. Same secret signs both - mint both, as the poll client does.
	accessToken, err := mintToken(cfg.jwtSecret, userID, fmt.Sprintf("user-%d", userID), 24*time.Hour)
	if err != nil {
		pctr.errors.Add(1)
		return
	}
	connToken, err := mintToken(cfg.jwtSecret, userID, "", 24*time.Hour)
	if err != nil {
		pctr.errors.Add(1)
		return
	}

	lastSeen := make(map[string]string) // ticker -> effective_at applied
	var mu sync.Mutex

	client := centrifuge.NewJsonClient(wsURL, centrifuge.Config{Token: connToken})
	defer client.Close()

	client.OnConnected(func(centrifuge.ConnectedEvent) { pctr.connects.Add(1) })
	client.OnDisconnected(func(centrifuge.DisconnectedEvent) { pctr.disconnects.Add(1) })
	client.OnError(func(centrifuge.ErrorEvent) { pctr.errors.Add(1) })

	if err := client.Connect(); err != nil {
		pctr.errors.Add(1)
		return
	}

	// Snapshot: one GET /watchlist per connection. This is the only HTTP
	// request a push client makes after login.
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, cfg.apiBase+"/watchlist", nil)
	req.Header.Set("Authorization", "Bearer "+accessToken)
	t0 := time.Now()
	resp, err := httpClient.Do(req)
	if err != nil {
		if ctx.Err() == nil {
			ctr.errors.Add(1)
		}
		return
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	requestLatency.add(time.Since(t0))
	ctr.requests.Add(1)
	ctr.bytes.Add(int64(len(body)))
	if resp.StatusCode != http.StatusOK {
		ctr.httpErrors.Add(1)
		return
	}
	var snapshot watchlistResponse
	if err := json.Unmarshal(body, &snapshot); err != nil {
		ctr.errors.Add(1)
		return
	}

	mu.Lock()
	for _, it := range snapshot.Items {
		lastSeen[it.Ticker] = it.EffectiveAt
	}
	mu.Unlock()

	// Subscribe to one channel per watched ticker. Never one per user.
	for _, it := range snapshot.Items {
		sub, err := client.NewSubscription("ticker:" + it.Ticker)
		if err != nil {
			pctr.errors.Add(1)
			continue
		}
		sub.OnPublication(func(e centrifuge.PublicationEvent) {
			var ev struct {
				Ticker      string `json:"ticker"`
				EffectiveAt string `json:"effective_at"`
			}
			if json.Unmarshal(e.Data, &ev) != nil {
				return
			}
			observed := time.Now()
			pctr.publications.Add(1)

			mu.Lock()
			prev, known := lastSeen[ev.Ticker]
			if known && ev.EffectiveAt <= prev {
				mu.Unlock()
				return // older than the snapshot or a duplicate: not an update
			}
			lastSeen[ev.Ticker] = ev.EffectiveAt
			mu.Unlock()

			if eff, err := time.Parse(time.RFC3339Nano, ev.EffectiveAt); err == nil {
				updateLatency.addMillis(float64(observed.Sub(eff).Nanoseconds()) / 1e6)
				ctr.priceEvents.Add(1)
			}
		})
		if err := sub.Subscribe(); err != nil {
			pctr.errors.Add(1)
			continue
		}
		pctr.subscribed.Add(1)
	}

	<-ctx.Done()
}

func pushProgress(ctx context.Context, ctr *counters, pctr *pushCounters, started time.Time) {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	var prev int64
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			pubs := pctr.publications.Load()
			fmt.Printf("  %5.0fs  %7d connected  %8d subs  %9d publications  %7.0f msg/s  %d errors\n",
				time.Since(started).Seconds(), pctr.connects.Load()-pctr.disconnects.Load(),
				pctr.subscribed.Load(), pubs, float64(pubs-prev)/10.0, pctr.errors.Load())
			prev = pubs
		}
	}
}

func reportPush(cfg config, ctr *counters, pctr *pushCounters, requestLatency, updateLatency *samples, elapsed time.Duration) {
	req := requestLatency.summarize()
	upd := updateLatency.summarize()
	seconds := elapsed.Seconds()

	fmt.Printf("\n%s\n", divider)
	fmt.Printf("transport            push\n")
	fmt.Printf("clients              %d\n", cfg.clients)
	fmt.Printf("logical users        %d\n", cfg.logicalUsers)
	fmt.Printf("duration             %.1fs\n", seconds)
	fmt.Printf("%s\n", divider)
	fmt.Printf("connects             %d\n", pctr.connects.Load())
	fmt.Printf("disconnects          %d\n", pctr.disconnects.Load())
	fmt.Printf("subscriptions        %d\n", pctr.subscribed.Load())
	fmt.Printf("snapshot requests    %d   (the only HTTP after login)\n", ctr.requests.Load())
	fmt.Printf("request rate         %.1f/s   (vs clients/interval under polling)\n", float64(ctr.requests.Load())/seconds)
	fmt.Printf("realtime errors      %d\n", pctr.errors.Load())
	fmt.Printf("non-200 snapshots    %d\n", ctr.httpErrors.Load())
	fmt.Printf("%s\n", divider)
	fmt.Printf("snapshot latency     p50 %.1fms   p95 %.1fms   p99 %.1fms   max %.1fms\n", req.P50, req.P95, req.P99, req.Max)
	if upd.N > 0 {
		fmt.Printf("update latency       p50 %.0fms   p95 %.0fms   p99 %.0fms   max %.0fms   (n=%d)\n", upd.P50, upd.P95, upd.P99, upd.Max, upd.N)
	} else {
		fmt.Printf("update latency       no publications observed (markets closed? try PRICE_SOURCE=simulated)\n")
	}
	fmt.Printf("%s\n", divider)
	fmt.Printf("publications recv    %d   (%.0f/s across all clients)\n", pctr.publications.Load(), float64(pctr.publications.Load())/seconds)
	fmt.Printf("bytes on the wire    %.1f KB of snapshots; realtime frames not counted here (see Centrifugo metrics)\n", float64(ctr.bytes.Load())/1024)
	fmt.Printf("%s\n", divider)
	_ = os.Stdout
}
