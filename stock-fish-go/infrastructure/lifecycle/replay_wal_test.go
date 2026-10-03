package lifecycle

import (
	"context"
	"database/sql"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"path/filepath"
	"strings"
	"testing"
)

func TestReplayedCheckpointRejectsCommittedWAL(t *testing.T) {
	ctx := context.Background()
	root, _ := isolatedFixture(t)
	conf, e := Cutover(ctx, root)
	if e != nil {
		t.Fatal(e)
	}
	store, e := sqlitev2.Open(ctx, conf.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	if e = sqlitev2.NewAppRepository(store).PutInput(ctx, fixtureInput(t)); e != nil {
		t.Fatal(e)
	}
	store.Close()
	back, e := Rollback(ctx, root)
	if e != nil {
		t.Fatal(e)
	}
	db, e := sql.Open("sqlite", sqlitev2.URI(back.Replay, false))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	if _, e = db.Exec("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; PRAGMA wal_checkpoint(TRUNCATE)"); e != nil {
		t.Fatal(e)
	}
	// TEST_FIXTURE: approve the clean replay checkpoint after enabling WAL mode.
	// Keep the real SQLite writer open so the next commit remains solely in WAL.
	conf, e = Load(root)
	if e != nil {
		t.Fatal(e)
	}
	before, e := sqlitev2.FileHash(ctx, back.Replay)
	if e != nil {
		t.Fatal(e)
	}
	conf.LegacyReplayHash = before
	if e = writeJSON(filepath.Join(root, "instance.json"), conf); e != nil {
		t.Fatal(e)
	}
	if _, e = Preflight(ctx, root); e != nil {
		t.Fatal("clean replay checkpoint rejected", e)
	}
	raw := []byte(`{"TEST_FIXTURE":"committed_after_replay_checkpoint"}`)
	if e = putLegacy(ctx, db, "test_fixture", "late_wal", raw); e != nil {
		t.Fatal(e)
	}
	after, e := sqlitev2.FileHash(ctx, back.Replay)
	if e != nil || before != after {
		t.Fatal("fixture must leave the main file unchanged", e)
	}
	if _, e = Preflight(ctx, root); e == nil || !strings.Contains(e.Error(), "UNHANDLED_LEGACY_INCREMENTAL_CHANGES") {
		t.Fatal("replayed checkpoint silently accepted committed WAL", e)
	}
}
