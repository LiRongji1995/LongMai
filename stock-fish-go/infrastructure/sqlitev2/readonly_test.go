package sqlitev2

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

func TestReadOnlyInputInstantOrderPreservesRawText(t *testing.T) {
	s, repo, _ := appFixture(t)
	ctx := context.Background()
	inputs := []c.InputRecord{
		{ID: "TEST_FIXTURE_A", Cutoff: "2026-10-02T21:00:00+08:00"},
		{ID: "TEST_FIXTURE_Z", Cutoff: "2026-10-02T14:00:00Z"},
		{ID: "TEST_FIXTURE_B", Cutoff: "2026-10-02T14:00:00+00:00"},
		{ID: "TEST_FIXTURE_C", Cutoff: "2026-10-02T14:00:00.001Z"},
		{ID: "TEST_FIXTURE_INVALID", Cutoff: "invalid"},
	}
	for _, in := range inputs {
		in.Raw = []byte(in.ID)
		in.RawHash = c.Hash(in.Raw)
		in.Observation = "2026-10-02"
		in.Coverage = "TEST_FIXTURE"
		if err := repo.PutInput(ctx, in); err != nil {
			t.Fatal(err)
		}
	}
	r := &ReadOnly{db: s.db}
	got, err := r.Inputs(ctx, "2026-10-02")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"TEST_FIXTURE_C", "TEST_FIXTURE_B", "TEST_FIXTURE_Z", "TEST_FIXTURE_A", "TEST_FIXTURE_INVALID"}
	for i, id := range want {
		if got[i].ID != id {
			t.Fatalf("order %d: %s want %s", i, got[i].ID, id)
		}
	}
	if got[2].Cutoff != inputs[1].Cutoff {
		t.Fatal("raw cutoff was rewritten")
	}
}

func TestReadOnlyCollectStillChecksUnselectedDetails(t *testing.T) {
	s, repo, _ := appFixture(t)
	ctx := context.Background()
	run := c.Run{ID: "TEST_FIXTURE_COLLECT", InputID: "TEST_FIXTURE", Strategy: c.R1, CreatedAt: "2026-10-02T14:00:00Z", ImplementationStatus: "TEST_FIXTURE", Manifest: json.RawMessage(`{"fixture":true}`), Summary: json.RawMessage(`{}`), Items: []c.Item{
		{ID: "TEST001", Payload: json.RawMessage(`{"status":"FAIL","source":"TEST_FIXTURE"}`)},
		{ID: "TEST002", Payload: json.RawMessage(`{"status":"PASS","source":"TEST_FIXTURE"}`)},
	}}
	if err := repo.SaveRun(ctx, run); err != nil {
		t.Fatal(err)
	}
	reader := &ReadOnly{db: s.db}
	got, err := reader.Saved(ctx, run.ID, "TEST001", false)
	if err != nil || len(got.Items) != 1 || got.Items[0].ID != "TEST001" {
		t.Fatalf("saved %v %v", got, err)
	}
	// Fixture-only corruption simulates external damage to an unread member.
	if _, err = s.db.Exec("DROP TRIGGER app_items_no_update"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.db.Exec("UPDATE app_items SET raw_hash='CORRUPT' WHERE run_id=? AND instrument_id='TEST002'", run.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = reader.Saved(ctx, run.ID, "TEST001", false); err == nil || !strings.Contains(err.Error(), "CORRUPT") {
		t.Fatalf("unselected corruption missed: %v", err)
	}
}
