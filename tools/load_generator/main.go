// Load generator for the stock watchlist service.
//
// Not part of the product. It shares no code with the services, builds
// separately, and runs only with explicit arguments. It lives in this repo
// because the measurements are the point: anyone checking the README's
// numbers should not have to clone a second repo, and a separate repo
// drifts from the API contract the first time an endpoint changes.
//
// Logical users and real connections are independent parameters. Logical users
// exercise database size, watchlist distribution and popularity skew; real
// connections exercise socket limits, memory, networking and fanout.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

type config struct {
	apiBase      string
	jwtSecret    string
	clients      int
	logicalUsers int
	duration     time.Duration
	interval     time.Duration
	transport    string
	rampUp       time.Duration
	userIDMin    int
	userIDMax    int

	// Push-only scenario knobs. Zero values mean "off".
	celebrity     string        // every client also subscribes to this ticker (the hot-ticker run)
	stormAt       time.Duration // at this offset, storm clients disconnect and reconnect (the reconnect storm)
	stormFraction float64       // share of clients that take part in the storm
	slowFraction  float64       // share of clients that read slowly (the slow-consumer run)
	slowDelay     time.Duration // how long a slow client blocks per publication
	measureAfter  time.Duration // discard update-latency samples before this offset (ramp)
}

type watchlistResponse struct {
	Items []struct {
		ID          int64   `json:"id"`
		Ticker      string  `json:"ticker"`
		Price       float64 `json:"price"`
		EffectiveAt string  `json:"effective_at"`
	} `json:"items"`
	ReadPath    string `json:"read_path"`
	CacheHits   int    `json:"cache_hits"`
	CacheMisses int    `json:"cache_misses"`
}

type counters struct {
	requests    atomic.Int64
	errors      atomic.Int64
	httpErrors  atomic.Int64
	bytes       atomic.Int64
	priceEvents atomic.Int64
}

func main() {
	cfg := parseFlags()

	if err := preflight(cfg); err != nil {
		log.Fatalf("preflight failed: %v\n\nA run against a half-started stack produces\n"+
			"numbers that look real and are not. Start it with 'make up-detached'.", err)
	}

	fmt.Printf("\ntransport=%s clients=%d logical-users=%d interval=%s duration=%s\n\n",
		cfg.transport, cfg.clients, cfg.logicalUsers, cfg.interval, cfg.duration)

	switch cfg.transport {
	case "poll":
		runPoll(cfg)
	case "push":
		runPush(cfg)
	default:
		log.Fatalf("unknown transport %q", cfg.transport)
	}
}

func parseFlags() config {
	cfg := config{}
	flag.StringVar(&cfg.apiBase, "api", envOr("API_BASE", "http://localhost:8000"), "API base URL")
	flag.StringVar(&cfg.jwtSecret, "jwt-secret", envOr("JWT_SECRET", "dev-only-not-a-secret"),
		"shared secret used to mint client tokens")
	flag.IntVar(&cfg.clients, "clients", 1000, "concurrent real clients")
	flag.IntVar(&cfg.logicalUsers, "logical-users", 100000,
		"size of the seeded user population to draw from")
	flag.DurationVar(&cfg.duration, "duration", 60*time.Second, "how long to run")
	flag.DurationVar(&cfg.interval, "interval", 5*time.Second, "client poll interval")
	flag.DurationVar(&cfg.rampUp, "ramp-up", 5*time.Second, "spread client starts over this long")
	flag.StringVar(&cfg.transport, "transport", "poll", "poll or push")
	flag.IntVar(&cfg.userIDMin, "user-id-min", 0,
		"lowest seeded user id to impersonate (required; the runner reads it from the DB)")
	flag.IntVar(&cfg.userIDMax, "user-id-max", 0,
		"highest seeded user id to impersonate (required)")
	flag.StringVar(&cfg.celebrity, "celebrity", "", "push: every client also subscribes to this ticker")
	flag.DurationVar(&cfg.stormAt, "storm-at", 0, "push: disconnect+reconnect a fraction of clients at this offset")
	flag.Float64Var(&cfg.stormFraction, "storm-fraction", 0.5, "push: share of clients in the storm")
	flag.Float64Var(&cfg.slowFraction, "slow-fraction", 0, "push: share of clients that read slowly")
	flag.DurationVar(&cfg.slowDelay, "slow-delay", 2*time.Second, "push: block per publication for slow clients")
	flag.DurationVar(&cfg.measureAfter, "measure-after", 0,
		"discard update-latency samples before this offset, so a connect ramp does not inflate them")
	flag.Parse()
	if cfg.userIDMin <= 0 || cfg.userIDMax < cfg.userIDMin {
		log.Fatal("-user-id-min and -user-id-max are required and must describe a real range.\n" +
			"Ids are not contiguous from 3: a reseed deletes and re-inserts users, so any\n" +
			"assumed range silently turns into a wall of 401s. tools/bench/run_load.sh reads\n" +
			"the live range from Postgres and passes it.")
	}
	return cfg
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// preflight refuses to generate load against a stack that is not fully up, and
// checks that the minted tokens are actually accepted.
func preflight(cfg config) error {
	client := &http.Client{Timeout: 10 * time.Second}

	resp, err := client.Get(cfg.apiBase + "/health")
	if err != nil {
		return fmt.Errorf("GET /health: %w", err)
	}
	defer resp.Body.Close()

	var health struct {
		Status string            `json:"status"`
		Schema string            `json:"schema"`
		Checks map[string]string `json:"checks"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&health); err != nil {
		return fmt.Errorf("decode /health: %w", err)
	}
	if health.Status != "ok" {
		return fmt.Errorf("stack is %s: %v", health.Status, health.Checks)
	}
	if health.Schema != "ready" {
		return fmt.Errorf("schema is %q; run 'make migrate'", health.Schema)
	}
	fmt.Printf("preflight: stack ok, schema ready, checks=%v\n", health.Checks)

	// Prove the real login path works before bypassing it.
	body := `{"username":"user1","password":"password"}`
	loginResp, err := client.Post(cfg.apiBase+"/auth/login", "application/json",
		stringReader(body))
	if err != nil {
		return fmt.Errorf("POST /auth/login: %w", err)
	}
	defer loginResp.Body.Close()
	if loginResp.StatusCode != http.StatusOK {
		return fmt.Errorf("login returned %d (run 'make createusers')", loginResp.StatusCode)
	}
	fmt.Println("preflight: /auth/login works")

	// And prove a minted token is accepted, so a secret mismatch fails here
	// rather than as a wall of 401s in the results.
	token, err := mintToken(cfg.jwtSecret, 1, "user1", time.Hour)
	if err != nil {
		return fmt.Errorf("mint token: %w", err)
	}
	req, _ := http.NewRequest(http.MethodGet, cfg.apiBase+"/watchlist", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	checkResp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("GET /watchlist: %w", err)
	}
	defer checkResp.Body.Close()
	if checkResp.StatusCode != http.StatusOK {
		return fmt.Errorf("minted token rejected (%d); JWT_SECRET does not match the API",
			checkResp.StatusCode)
	}
	fmt.Println("preflight: minted tokens accepted")

	// Prove the id range is real before generating load against it. Sampling
	// only user 1 would pass while every seeded id 401s - which is exactly what
	// happened after a reseed moved the load users from ids 3..1,000,002 to
	// 1,000,004..2,000,003.
	const samples = 25
	rng := rand.New(rand.NewSource(1))
	ok := 0
	for i := 0; i < samples; i++ {
		id := cfg.userIDMin + rng.Intn(cfg.userIDMax-cfg.userIDMin+1)
		tok, err := mintToken(cfg.jwtSecret, id, fmt.Sprintf("user-%d", id), time.Hour)
		if err != nil {
			return fmt.Errorf("mint token for %d: %w", id, err)
		}
		r, _ := http.NewRequest(http.MethodGet, cfg.apiBase+"/watchlist", nil)
		r.Header.Set("Authorization", "Bearer "+tok)
		resp, err := client.Do(r)
		if err != nil {
			return fmt.Errorf("GET /watchlist as %d: %w", id, err)
		}
		resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			ok++
		}
	}
	if ok < samples*9/10 {
		return fmt.Errorf("only %d/%d sampled user ids in [%d, %d] are valid; the range is wrong",
			ok, samples, cfg.userIDMin, cfg.userIDMax)
	}
	fmt.Printf("preflight: %d/%d sampled user ids in [%d, %d] accepted\n",
		ok, samples, cfg.userIDMin, cfg.userIDMax)
	return nil
}

func runPoll(cfg config) {
	transport := &http.Transport{
		// One reusable connection per client. Without this the generator
		// burns an ephemeral port per request and hits an OS limit long
		// before the server does.
		MaxIdleConns:        cfg.clients + 100,
		MaxIdleConnsPerHost: cfg.clients + 100,
		MaxConnsPerHost:     0,
		IdleConnTimeout:     90 * time.Second,
		DialContext: (&net.Dialer{
			Timeout:   10 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
	}
	defer transport.CloseIdleConnections()

	ctr := &counters{}
	requestLatency := newSamples(cfg.clients * 64)
	updateLatency := newSamples(cfg.clients * 64)

	ctx, cancel := context.WithTimeout(context.Background(), cfg.duration)
	defer cancel()

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-signals
		fmt.Println("\ninterrupted, reporting what was collected")
		cancel()
	}()

	started := time.Now()
	go progress(ctx, ctr, started)

	var wg sync.WaitGroup
	for i := 0; i < cfg.clients; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			pollClient(ctx, cfg, transport, n, ctr, requestLatency, updateLatency)
		}(i)
	}
	wg.Wait()

	report(cfg, ctr, requestLatency, updateLatency, time.Since(started))
}

func pollClient(
	ctx context.Context,
	cfg config,
	transport *http.Transport,
	n int,
	ctr *counters,
	requestLatency, updateLatency *samples,
) {
	rng := rand.New(rand.NewSource(int64(n)*7919 + 13))

	// Draw from the real seeded range, never from an assumed one.
	span := cfg.userIDMax - cfg.userIDMin + 1
	if cfg.logicalUsers < span {
		span = cfg.logicalUsers
	}
	userID := cfg.userIDMin + rng.Intn(span)
	token, err := mintToken(cfg.jwtSecret, userID, fmt.Sprintf("user-%d", userID), 24*time.Hour)
	if err != nil {
		ctr.errors.Add(1)
		return
	}

	client := &http.Client{Transport: transport, Timeout: 30 * time.Second}

	// Stagger the start. Real clients that all begin at once stay aligned
	// forever and produce a spike pattern no server would actually see.
	stagger := time.Duration(rng.Int63n(int64(cfg.rampUp)))
	select {
	case <-ctx.Done():
		return
	case <-time.After(stagger):
	}

	// Jitter the phase within the interval for the same reason.
	jitter := time.Duration(rng.Int63n(int64(cfg.interval)))
	select {
	case <-ctx.Done():
		return
	case <-time.After(jitter):
	}

	lastSeen := make(map[int64]string)
	ticker := time.NewTicker(cfg.interval)
	defer ticker.Stop()

	for {
		poll(ctx, client, cfg, token, ctr, requestLatency, updateLatency, lastSeen)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func poll(
	ctx context.Context,
	client *http.Client,
	cfg config,
	token string,
	ctr *counters,
	requestLatency, updateLatency *samples,
	lastSeen map[int64]string,
) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, cfg.apiBase+"/watchlist", nil)
	if err != nil {
		return
	}
	req.Header.Set("Authorization", "Bearer "+token)

	start := time.Now()
	resp, err := client.Do(req)
	if err != nil {
		if ctx.Err() == nil {
			ctr.errors.Add(1)
		}
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	elapsed := time.Since(start)
	if err != nil {
		ctr.errors.Add(1)
		return
	}

	ctr.requests.Add(1)
	ctr.bytes.Add(int64(len(body)))
	requestLatency.add(elapsed)

	if resp.StatusCode != http.StatusOK {
		ctr.httpErrors.Add(1)
		return
	}

	var payload watchlistResponse
	if err := json.Unmarshal(body, &payload); err != nil {
		ctr.errors.Add(1)
		return
	}

	// Update latency: from when the price became effective to when this client
	// first saw it. Measured at the client, and measured the same way under
	// push, or the comparison is not like-for-like.
	observedAt := time.Now()
	for _, item := range payload.Items {
		if item.EffectiveAt == "" || lastSeen[item.ID] == item.EffectiveAt {
			continue
		}
		if _, known := lastSeen[item.ID]; known {
			if effective, err := time.Parse(time.RFC3339Nano, item.EffectiveAt); err == nil {
				updateLatency.addMillis(float64(observedAt.Sub(effective).Nanoseconds()) / 1e6)
				ctr.priceEvents.Add(1)
			}
		}
		lastSeen[item.ID] = item.EffectiveAt
	}
}

func progress(ctx context.Context, ctr *counters, started time.Time) {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	var previous int64
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			total := ctr.requests.Load()
			fmt.Printf("  %5.0fs  %9d requests  %7.0f req/s  %d errors\n",
				time.Since(started).Seconds(), total, float64(total-previous)/10.0,
				ctr.errors.Load()+ctr.httpErrors.Load())
			previous = total
		}
	}
}

func report(cfg config, ctr *counters, requestLatency, updateLatency *samples, elapsed time.Duration) {
	req := requestLatency.summarize()
	upd := updateLatency.summarize()
	seconds := elapsed.Seconds()
	requests := ctr.requests.Load()

	fmt.Printf("\n%s\n", divider)
	fmt.Printf("transport            %s\n", cfg.transport)
	fmt.Printf("clients              %d\n", cfg.clients)
	fmt.Printf("logical users        %d\n", cfg.logicalUsers)
	fmt.Printf("duration             %.1fs\n", seconds)
	fmt.Printf("%s\n", divider)
	fmt.Printf("requests             %d\n", requests)
	fmt.Printf("request rate         %.0f/s\n", float64(requests)/seconds)
	fmt.Printf("transport errors     %d\n", ctr.errors.Load())
	fmt.Printf("non-200 responses    %d\n", ctr.httpErrors.Load())
	fmt.Printf("%s\n", divider)
	fmt.Printf("request latency      p50 %.1fms   p95 %.1fms   p99 %.1fms   max %.1fms\n",
		req.P50, req.P95, req.P99, req.Max)
	if upd.N > 0 {
		fmt.Printf("update latency       p50 %.0fms   p95 %.0fms   p99 %.0fms   max %.0fms   (n=%d)\n",
			upd.P50, upd.P95, upd.P99, upd.Max, upd.N)
	} else {
		fmt.Printf("update latency       no price changes observed " +
			"(markets closed? try PRICE_SOURCE=simulated)\n")
	}
	fmt.Printf("%s\n", divider)
	bytesPerClientPerMinute := float64(ctr.bytes.Load()) / float64(cfg.clients) / (seconds / 60)
	fmt.Printf("bytes on the wire    %.1f KB total, %.0f B per client per minute\n",
		float64(ctr.bytes.Load())/1024, bytesPerClientPerMinute)
	fmt.Printf("price updates seen   %d\n", ctr.priceEvents.Load())
	fmt.Printf("%s\n", divider)
}

const divider = "--------------------------------------------------------------------"
