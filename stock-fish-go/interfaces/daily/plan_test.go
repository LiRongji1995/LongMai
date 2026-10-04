package daily

import (
	"context"
	"testing"
	"time"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

type planFixtureBackend struct{ inputs []c.InputRecord }

func (b planFixtureBackend) Identity() map[string]string                { return nil }
func (b planFixtureBackend) Ready(context.Context) (c.Readiness, error) { return c.Readiness{}, nil }
func (b planFixtureBackend) Inputs(context.Context, string) ([]c.InputRecord, error) {
	return b.inputs, nil
}
func (b planFixtureBackend) Runs(context.Context, int, int) ([]c.Run, error) { return nil, nil }
func (b planFixtureBackend) Saved(context.Context, string, string, bool) (c.Run, error) {
	return c.Run{}, nil
}
func (b planFixtureBackend) Close() error { return nil }

func TestLatestEligibleInputUsesInstantsAndStableIdentity(t *testing.T) {
	now, _ := time.Parse(time.RFC3339Nano, "2026-10-03T00:00:00Z")
	makeInput := func(id, cutoff string) c.InputRecord {
		return c.InputRecord{ID: id, Observation: "2026-10-02", Cutoff: cutoff, RawHash: "TEST_FIXTURE_" + id}
	}
	cases := []struct {
		name   string
		inputs []c.InputRecord
		want   string
	}{
		{"timezone", []c.InputRecord{makeInput("older", "2026-10-02T21:00:00+08:00"), makeInput("newer", "2026-10-02T14:00:00Z")}, "newer"},
		{"fraction", []c.InputRecord{makeInput("older", "2026-10-02T22:00:00+08:00"), makeInput("newer", "2026-10-02T14:00:00.000000001+00:00")}, "newer"},
		{"same instant", []c.InputRecord{makeInput("z", "2026-10-02T14:00:00Z"), makeInput("b", "2026-10-02T14:00:00+00:00"), makeInput("a", "2026-10-02T22:00:00+08:00")}, "a"},
		{"invalid future intraday", []c.InputRecord{makeInput("bad", "INVALID"), makeInput("future", "2026-10-04T00:00:00Z"), makeInput("intraday", "2026-10-02T14:59:59+08:00"), makeInput("valid", "2026-10-02T15:00:00+08:00")}, "valid"},
		{"no eligible", []c.InputRecord{makeInput("bad", "INVALID"), makeInput("future", "2026-10-04T00:00:00Z"), makeInput("intraday", "2026-10-02T14:00:00+08:00")}, ""},
		{"observation mismatch", []c.InputRecord{{ID: "wrong", Observation: "2026-10-01", Cutoff: "2026-10-02T22:00:00+08:00"}}, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			for round := 0; round < 2; round++ {
				in, ok := latestEligibleInput(tc.inputs, "2026-10-02", now)
				if in.ID != tc.want || ok != (tc.want != "") {
					t.Fatalf("selected %+v found=%t want=%s", in, ok, tc.want)
				}
				for i, j := 0, len(tc.inputs)-1; i < j; i, j = i+1, j-1 {
					tc.inputs[i], tc.inputs[j] = tc.inputs[j], tc.inputs[i]
				}
			}
		})
	}
	if _, ok := latestEligibleInput(cases[0].inputs, "INVALID", now); ok {
		t.Fatal("invalid observation accepted")
	}
}

func TestPlanCannotSelectOlderInputDueToBackendOrdering(t *testing.T) {
	now, _ := time.Parse(time.RFC3339, "2026-10-03T00:00:00Z")
	b := planFixtureBackend{[]c.InputRecord{{ID: "older", Observation: "2026-10-02", Cutoff: "2026-10-02T21:00:00+08:00"}, {ID: "newer", Observation: "2026-10-02", Cutoff: "2026-10-02T14:00:00Z"}}}
	cal := map[string]any{"target_session": "2026-10-08", "required_observation": "2026-10-02", "observation_complete": true}
	p, err := makePlan(context.Background(), b, Config{Mode: "embedded", Budget: 1}, c.R1, "TEST_FIXTURE_BUILD", nil, cal, now)
	if err != nil || p.InputID != "newer" || p.Cutoff != "2026-10-02T14:00:00Z" {
		t.Fatalf("%+v %v", p, err)
	}
	if p.InputChoice == "" {
		t.Fatal("choice policy missing")
	}
}
