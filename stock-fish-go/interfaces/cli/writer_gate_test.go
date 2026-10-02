package cli_test

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/cli"
)

func TestAllCLIOpenPathsShareDatabaseLease(t *testing.T) {
	store, _, _ := appFixture(t)
	ctx := context.Background()
	alias := filepath.Join(t.TempDir(), "TEST_FIXTURE_alias.db")
	if e := os.Symlink(store.Path, alias); e != nil {
		t.Fatal(e)
	}
	canonical, release, e := lifecycle.AcquireDatabaseWriter(store.Path, "TEST_FIXTURE_SERVER", false)
	if e != nil {
		t.Fatal(e)
	}
	defer release()
	wantCanonical, e := filepath.EvalSymlinks(store.Path)
	if e != nil || canonical != wantCanonical {
		t.Fatal(canonical, wantCanonical, e)
	}
	for _, command := range []string{"health", "readiness", "runs", "show", "detail", "summary", "legacy", "cache-clear", "serve"} {
		var out, log bytes.Buffer
		e = cli.Run(ctx, []string{command, "--db", alias}, &out, &log, "TEST_FIXTURE_BUILD")
		if e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
			t.Fatalf("%s bypassed lease: %v", command, e)
		}
	}
	for _, command := range []string{"snapshot", "migrate", "incremental", "verify", "publish", "project-batches", "verify-batches"} {
		var out, log bytes.Buffer
		e = cli.Run(ctx, []string{"database", command, "--target", alias, "--source", filepath.Join(t.TempDir(), "TEST_FIXTURE_missing.db")}, &out, &log, "TEST_FIXTURE_BUILD")
		if e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
			t.Fatalf("database %s bypassed lease: %v", command, e)
		}
	}
	if _, e = os.Lstat(alias + ".go-writer"); !os.IsNotExist(e) {
		t.Fatal("alias obtained separate lease", e)
	}
}

func TestCLINewTargetCanonicalLeaseAndFailureRelease(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	real := filepath.Join(root, "TEST_FIXTURE_actual")
	if e := os.Mkdir(real, 0700); e != nil {
		t.Fatal(e)
	}
	alias := filepath.Join(root, "TEST_FIXTURE_alias")
	if e := os.Symlink(real, alias); e != nil {
		t.Fatal(e)
	}
	realCanonical, e := filepath.EvalSymlinks(real)
	if e != nil {
		t.Fatal(e)
	}
	canonical := filepath.Join(realCanonical, "TEST_FIXTURE_new.db")
	target := filepath.Join(alias, "TEST_FIXTURE_new.db")
	resolved, release, e := lifecycle.AcquireDatabaseWriter(target, "TEST_FIXTURE_RESERVED", true)
	if e != nil {
		t.Fatal(e)
	}
	if resolved != canonical {
		t.Fatal(resolved, canonical)
	}
	var out, log bytes.Buffer
	e = cli.Run(ctx, []string{"database", "migrate", "--target", target, "--source", filepath.Join(root, "TEST_FIXTURE_missing.db")}, &out, &log, "TEST_FIXTURE_BUILD")
	if e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
		t.Fatal("new target bypassed reserve", e)
	}
	if _, e = os.Stat(canonical); !os.IsNotExist(e) {
		t.Fatal("busy target unexpectedly created", e)
	}
	if e = release(); e != nil {
		t.Fatal(e)
	}
	e = cli.Run(ctx, []string{"database", "migrate", "--target", target, "--source", filepath.Join(root, "TEST_FIXTURE_missing.db")}, &out, &log, "TEST_FIXTURE_BUILD")
	if e == nil || strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
		t.Fatal("fixture must fail for missing source after acquiring lease", e)
	}
	if _, e = os.Lstat(canonical + ".go-writer"); !os.IsNotExist(e) {
		t.Fatal("handled command failure leaked lease", e)
	}
}

func TestCLIQueryCannotInitializeExtensionWithoutLease(t *testing.T) {
	store, _, _ := appFixture(t)
	ctx := context.Background()
	// Simulate an older authority DB lacking this additive extension. The fixture
	// has no member records; dropping its extension must not touch core/app data.
	rows, e := store.DB().Query("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'batch_*'")
	if e != nil {
		t.Fatal(e)
	}
	var names []string
	for rows.Next() {
		var name string
		if e = rows.Scan(&name); e != nil {
			t.Fatal(e)
		}
		names = append(names, name)
	}
	if e = rows.Close(); e != nil {
		t.Fatal(e)
	}
	if _, e = store.DB().Exec("PRAGMA foreign_keys=OFF"); e != nil {
		t.Fatal(e)
	}
	for _, name := range names {
		if _, e = store.DB().Exec("DROP TABLE " + name); e != nil {
			t.Fatal(e)
		}
	}
	if _, e = store.DB().Exec("PRAGMA foreign_keys=ON"); e != nil {
		t.Fatal(e)
	}
	_, release, e := lifecycle.AcquireDatabaseWriter(store.Path, "TEST_FIXTURE_EXISTING_WRITER", false)
	if e != nil {
		t.Fatal(e)
	}
	var out, log bytes.Buffer
	if e = cli.Run(ctx, []string{"health", "--db", store.Path}, &out, &log, "TEST_FIXTURE_BUILD"); e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
		t.Fatal(e)
	}
	var n int
	if e = store.DB().QueryRow("SELECT count(*) FROM sqlite_master WHERE name='batch_projection_versions'").Scan(&n); e != nil || n != 0 {
		t.Fatal("query wrote schema without lease", n, e)
	}
	if e = release(); e != nil {
		t.Fatal(e)
	}
	if e = cli.Run(ctx, []string{"health", "--db", store.Path}, &out, &log, "TEST_FIXTURE_BUILD"); e == nil || !strings.Contains(e.Error(), "BATCH_OWNER_COVERAGE_INCOMPLETE") {
		t.Fatal("new schema must remain unavailable until its owner backfill", e)
	}
	if e = store.DB().QueryRow("SELECT count(*) FROM sqlite_master WHERE name='batch_projection_versions'").Scan(&n); e != nil || n != 1 {
		t.Fatal("leased schema initialization missing", n, e)
	}
}
