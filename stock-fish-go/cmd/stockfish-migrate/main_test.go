package main

import (
	"bytes"
	"context"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMigrationCommandSharesLeaseAndReleasesOnError(t *testing.T) {
	ctx := context.Background()
	target := filepath.Join(t.TempDir(), "TEST_FIXTURE_v2.db")
	store, e := sqlitev2.Open(ctx, target, true)
	if e != nil {
		t.Fatal(e)
	}
	store.Close()
	canonical, release, e := lifecycle.AcquireDatabaseWriter(target, "TEST_FIXTURE_APP_WRITER", false)
	if e != nil {
		t.Fatal(e)
	}
	alias := filepath.Join(t.TempDir(), "TEST_FIXTURE_alias.db")
	if e = os.Symlink(target, alias); e != nil {
		t.Fatal(e)
	}
	for _, command := range []string{"snapshot", "migrate", "incremental", "verify", "stats", "publish"} {
		var out, log bytes.Buffer
		e = run(ctx, []string{command, "--target", alias, "--source", filepath.Join(t.TempDir(), "TEST_FIXTURE_missing.db")}, &out, &log)
		if e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
			t.Fatalf("%s bypassed shared lease: %v", command, e)
		}
	}
	if e = release(); e != nil {
		t.Fatal(e)
	}
	var out, log bytes.Buffer
	e = run(ctx, []string{"publish", "--target", target}, &out, &log)
	if e == nil || strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
		t.Fatal("unverified fixture must fail after acquiring", e)
	}
	if _, e = os.Lstat(canonical + ".go-writer"); !os.IsNotExist(e) {
		t.Fatal("handled error failed to release lease", e)
	}
}
func TestMigrationSnapshotReservesNewTargetAndInventoryRemainsReadOnly(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	target := filepath.Join(dir, "TEST_FIXTURE_new.db")
	_, release, e := lifecycle.AcquireDatabaseWriter(target, "TEST_FIXTURE_RESERVATION", true)
	if e != nil {
		t.Fatal(e)
	}
	defer release()
	var out, log bytes.Buffer
	if e = run(ctx, []string{"snapshot", "--target", target, "--source", filepath.Join(dir, "TEST_FIXTURE_missing.db")}, &out, &log); e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
		t.Fatal(e)
	}
	if _, e = os.Stat(target); !os.IsNotExist(e) {
		t.Fatal("target created while reserved", e)
	}
	e = run(ctx, []string{"inventory", "--target", target, "--source", filepath.Join(dir, "TEST_FIXTURE_missing.db")}, &out, &log)
	if e == nil || strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
		t.Fatal("read-only inventory acquired target lease", e)
	}
}
