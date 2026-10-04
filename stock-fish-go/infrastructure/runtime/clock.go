package runtime

import "time"

// Wall is civil UTC time; Mono is elapsed process time, with an arbitrary epoch.
// Separate readings permit deterministic suspend/clock-jump tests.
type Clock interface {
	Wall() time.Time
	Mono() time.Duration
}
type systemClock struct{ start time.Time }

func (c systemClock) Wall() time.Time     { return time.Now().UTC() }
func (c systemClock) Mono() time.Duration { return time.Since(c.start) }

// Pair both observations at one instant; GC or scheduling between two separate
// reads must not manufacture a wall/monotonic discrepancy.
func (c systemClock) Sample() (time.Time, time.Duration) {
	now := time.Now()
	return now.UTC(), now.Sub(c.start)
}
func clockSample(c Clock) (time.Time, time.Duration) {
	if pair, ok := c.(interface {
		Sample() (time.Time, time.Duration)
	}); ok {
		return pair.Sample()
	}
	return c.Wall(), c.Mono()
}

func (b *Budget) checkClockLocked() {
	wall, mono := clockSample(b.clock)
	elapsed := mono - b.monoStart
	waited := wall.Sub(b.wallStart)
	b.m.Elapsed = elapsed.Seconds()
	b.m.WaitElapsed = waited.Seconds()
	b.m.ClockStatus = "CONSISTENT"
	drift := waited - elapsed
	if drift > 2*time.Second || drift < -2*time.Second || wall.Before(b.wallLast.Add(-2*time.Second)) || mono < b.monoLast {
		b.m.ClockStatus = "ANOMALY"
		if b.failure == nil {
			b.failure = errClock
		}
		b.cancel()
	} else if waited >= b.duration {
		b.m.ClockStatus = "WAIT_EXPIRED"
		if b.failure == nil {
			b.failure = errWait
		}
		b.cancel()
	}
	b.wallLast = wall
	b.monoLast = mono
}
