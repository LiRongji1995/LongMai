package sqlitev2

import (
	"context"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"sort"
	"time"
)

// RefreshBase selects chronology by parsed instants and preserves original
// immutable bytes. It does not infer history readiness from job success.
func (r *ReadOnly) RefreshBase(ctx context.Context, observation string, cutoff time.Time) (c.InputRecord, error) {
	rows, e := r.db.QueryContext(ctx, "SELECT input_id,raw_hash,observation,cutoff,coverage FROM app_inputs WHERE observation<?", observation)
	if e != nil {
		return c.InputRecord{}, e
	}
	var candidates []c.InputRecord
	seen := 0
	for rows.Next() {
		seen++
		if seen > 10000 {
			rows.Close()
			return c.InputRecord{}, errors.New("REFRESH_BASE_SELECTION_LIMIT")
		}
		var in c.InputRecord
		if e = rows.Scan(&in.ID, &in.RawHash, &in.Observation, &in.Cutoff, &in.Coverage); e != nil {
			rows.Close()
			return in, e
		}
		if _, e = time.Parse("2006-01-02", in.Observation); e != nil {
			rows.Close()
			return in, errors.New("REFRESH_BASE_INVALID_OBSERVATION")
		}
		stamp, err := time.Parse(time.RFC3339Nano, in.Cutoff)
		if err != nil {
			rows.Close()
			return in, errors.New("REFRESH_BASE_INVALID_CUTOFF")
		}
		if !stamp.After(cutoff) && in.Coverage == "PROSPECTIVE_CAPTURED" {
			candidates = append(candidates, in)
		}
	}
	e = errors.Join(rows.Err(), rows.Close())
	if e != nil {
		return c.InputRecord{}, e
	}
	if len(candidates) == 0 {
		return c.InputRecord{}, errors.New("REFRESH_PRIOR_INPUT_NOT_FOUND")
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].Observation != candidates[j].Observation {
			return candidates[i].Observation > candidates[j].Observation
		}
		a, _ := time.Parse(time.RFC3339Nano, candidates[i].Cutoff)
		b, _ := time.Parse(time.RFC3339Nano, candidates[j].Cutoff)
		return a.After(b)
	})
	chosen := candidates[0]
	stamp, _ := time.Parse(time.RFC3339Nano, chosen.Cutoff)
	for _, other := range candidates[1:] {
		t, _ := time.Parse(time.RFC3339Nano, other.Cutoff)
		if other.Observation == chosen.Observation && t.Equal(stamp) && other.RawHash != chosen.RawHash {
			return chosen, errors.New("REFRESH_BASE_AMBIGUOUS_SAME_INSTANT")
		}
	}
	return r.ReviewInput(ctx, chosen.ID)
}
