package main

import (
	"sort"
	"sync"
	"time"
)

// samples is a lock-protected slice of millisecond observations. A histogram
// would be the right structure at a much higher rate; at a few thousand
// requests per second over a few minutes the raw samples cost a few megabytes
// and keep the percentiles exact.
type samples struct {
	mu     sync.Mutex
	values []float64
}

func newSamples(capacity int) *samples {
	return &samples{values: make([]float64, 0, capacity)}
}

func (s *samples) add(d time.Duration) {
	s.mu.Lock()
	s.values = append(s.values, float64(d.Nanoseconds())/1e6)
	s.mu.Unlock()
}

func (s *samples) addMillis(ms float64) {
	s.mu.Lock()
	s.values = append(s.values, ms)
	s.mu.Unlock()
}

type summary struct {
	N   int
	P50 float64
	P95 float64
	P99 float64
	Max float64
}

func (s *samples) summarize() summary {
	s.mu.Lock()
	defer s.mu.Unlock()

	if len(s.values) == 0 {
		return summary{}
	}
	ordered := make([]float64, len(s.values))
	copy(ordered, s.values)
	sort.Float64s(ordered)

	at := func(q float64) float64 {
		i := int(float64(len(ordered)) * q)
		if i >= len(ordered) {
			i = len(ordered) - 1
		}
		return ordered[i]
	}
	return summary{
		N:   len(ordered),
		P50: at(0.50),
		P95: at(0.95),
		P99: at(0.99),
		Max: ordered[len(ordered)-1],
	}
}
