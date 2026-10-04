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
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestEmbeddedAndServerInputOrderingUseActualInstants(t *testing.T) {
	ctx := context.Background()
	db := filepath.Join(t.TempDir(), "TEST_FIXTURE_TIME.db")
	store, err := sqlitev2.Open(ctx, db, true)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if _, err := store.DB().Exec("INSERT INTO migration_state VALUES(1,'TEST_FIXTURE','PUBLISHED','{}','TEST_FIXTURE')"); err != nil {
		t.Fatal(err)
	}
	repo := sqlitev2.NewAppRepository(store)
	handler := httpapi.New(ctx, application.New(repo, nil), "TEST_FIXTURE_TOKEN")
	defer handler.Close()
	server := httptest.NewServer(handler)
	defer server.Close()
	put := func(id, cutoff string) {
		raw, _ := json.Marshal(map[string]string{"TEST_FIXTURE": id})
		if err := repo.PutInput(ctx, c.InputRecord{ID: id, Observation: "2026-10-02", Cutoff: cutoff, Raw: raw, RawHash: c.Hash(raw)}); err != nil {
			t.Fatal(err)
		}
	}
	read := func(want string) {
		reader, err := sqlitev2.OpenReadOnly(ctx, db)
		if err != nil {
			t.Fatal(err)
		}
		embedded, err := reader.Inputs(ctx, "2026-10-02")
		reader.Close()
		if err != nil {
			t.Fatal(err)
		}
		response, err := http.Get(server.URL + "/api/daily/inputs?observation=2026-10-02")
		if err != nil {
			t.Fatal(err)
		}
		raw, err := io.ReadAll(response.Body)
		response.Body.Close()
		if err != nil || response.StatusCode != 200 {
			t.Fatal(response.StatusCode, err, string(raw))
		}
		var remote []c.InputRecord
		if err := json.Unmarshal(raw, &remote); err != nil {
			t.Fatal(err)
		}
		if len(embedded) == 0 || len(remote) != len(embedded) || embedded[0].ID != want || remote[0].ID != want {
			t.Fatal(embedded, remote, want)
		}
		for i, in := range embedded {
			if in.ID != remote[i].ID || in.RawHash != remote[i].RawHash || in.Cutoff != remote[i].Cutoff {
				t.Fatal("HTTP changed input identity", embedded, remote)
			}
		}
	}
	put("TEST_FIXTURE_OLDER", "2026-10-02T21:00:00+08:00")
	put("TEST_FIXTURE_Z_NEWER", "2026-10-02T14:00:00Z")
	read("TEST_FIXTURE_Z_NEWER")
	put("TEST_FIXTURE_A_EQUAL", "2026-10-02T22:00:00+08:00")
	put("TEST_FIXTURE_B_EQUAL", "2026-10-02T14:00:00+00:00")
	read("TEST_FIXTURE_A_EQUAL")
	put("TEST_FIXTURE_FRACTION", "2026-10-02T14:00:00.000000001Z")
	put("TEST_FIXTURE_INVALID", "INVALID")
	read("TEST_FIXTURE_FRACTION")
}

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
