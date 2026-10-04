package daily_test

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/cli"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/daily"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/httpapi"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestServerIdentityAndNoLocalFallback(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	db := filepath.Join(dir, "TEST_FIXTURE.db")
	store, e := sqlitev2.Open(ctx, db, true)
	if e != nil {
		t.Fatal(e)
	}
	defer store.Close()
	_, e = store.DB().Exec("INSERT INTO migration_state VALUES(1,'TEST_FIXTURE','PUBLISHED','{}','TEST_FIXTURE')")
	if e != nil {
		t.Fatal(e)
	}
	repo := sqlitev2.NewAppRepository(store)
	raw := []byte(`{"coverage":"TEST_FIXTURE"}`)
	if e = repo.PutInput(ctx, c.InputRecord{ID: "TEST_FIXTURE", Raw: raw, RawHash: c.Hash(raw)}); e != nil {
		t.Fatal(e)
	}
	run := c.Run{ID: "TEST_FIXTURE_RUN", InputID: "TEST_FIXTURE", Strategy: c.R1, CreatedAt: "2026-01-01", Manifest: json.RawMessage(`{}`), Summary: json.RawMessage(`{}`)}
	if e = repo.SaveRun(ctx, run); e != nil {
		t.Fatal(e)
	}
	svc := application.New(repo, nil)
	handler := httpapi.New(ctx, svc, "TEST_FIXTURE_TOKEN")
	defer handler.Close()
	server := httptest.NewServer(handler)
	defer server.Close()
	config := filepath.Join(dir, "config.json")
	var out, log bytes.Buffer
	if e = cli.Run(ctx, []string{"init", "--config", config, "--db", db, "--mode", "server", "--server-url", server.URL}, &out, &log, "TEST_FIXTURE_SOURCE"); e != nil {
		t.Fatal(e)
	}
	out.Reset()
	if e = cli.Run(ctx, []string{"picks", "--config", config, "--run-id", run.ID, "--format", "json"}, &out, &log, "TEST_FIXTURE_SOURCE"); e != nil {
		t.Fatal(e)
	}
	var v daily.Saved
	if e = json.Unmarshal(out.Bytes(), &v); e != nil || v.Run.ID != run.ID {
		t.Fatal(e)
	}
	rawConfig, _ := os.ReadFile(config)
	var conf daily.Config
	_ = json.Unmarshal(rawConfig, &conf)
	conf.DatabasePathHash = "WRONG_TEST_FIXTURE_DATABASE"
	changed, _ := json.Marshal(conf)
	if e = os.WriteFile(config, changed, 0600); e != nil {
		t.Fatal(e)
	}
	if e = cli.Run(ctx, []string{"picks", "--config", config, "--run-id", run.ID}, &out, &log, "TEST_FIXTURE_SOURCE"); e == nil {
		t.Fatal("server identity mismatch accepted")
	}
	if e = os.WriteFile(config, rawConfig, 0600); e != nil {
		t.Fatal(e)
	}
	server.Close()
	if e = cli.Run(ctx, []string{"picks", "--config", config, "--run-id", run.ID}, &out, &log, "TEST_FIXTURE_SOURCE"); e == nil {
		t.Fatal("silently fell back to accessible local database")
	}
}
