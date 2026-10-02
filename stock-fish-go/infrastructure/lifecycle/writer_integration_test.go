package lifecycle_test

import (
	"bytes"
	"context"
	"database/sql"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/cli"
)

func TestCLIAndLifecycleShareDatabaseWriterLease(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	original := filepath.Join(root, "TEST_FIXTURE_original.db")
	source := filepath.Join(root, "TEST_FIXTURE_frozen.db")
	target := filepath.Join(root, "TEST_FIXTURE_v2.db")
	db, e := sql.Open("sqlite", sqlitev2.URI(original, false))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec(`CREATE TABLE documents(namespace TEXT,document_id TEXT,payload TEXT,content_hash TEXT,created_at TEXT,PRIMARY KEY(namespace,document_id));CREATE TABLE history_evidence_rows(row_hash TEXT PRIMARY KEY,kind TEXT,instrument_id TEXT,session TEXT,payload TEXT);CREATE TABLE history_partition_members(partition_hash TEXT,row_hash TEXT,kind TEXT,instrument_id TEXT,session TEXT,PRIMARY KEY(partition_hash,row_hash));`); e != nil {
		t.Fatal(e)
	}
	if e = db.Close(); e != nil {
		t.Fatal(e)
	}
	if _, e = sqlitev2.Snapshot(ctx, original, source); e != nil {
		t.Fatal(e)
	}
	if _, e = sqlitev2.Migrate(ctx, source, target, sqlitev2.Options{BatchSize: 5}); e != nil {
		t.Fatal(e)
	}
	if _, e = sqlitev2.Verify(ctx, source, target, sqlitev2.Options{}); e != nil {
		t.Fatal(e)
	}
	if e = sqlitev2.Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
	if _, e = lifecycle.Init(ctx, root, source, target, "127.0.0.1:0"); e != nil {
		t.Fatal(e)
	}
	release, e := lifecycle.AcquireWriter(root, "TEST_FIXTURE_LIFECYCLE_WRITER")
	if e != nil {
		t.Fatal(e)
	}
	var out, log bytes.Buffer
	alias := filepath.Join(root, "TEST_FIXTURE_symlink.db")
	if e = os.Symlink(target, alias); e != nil {
		t.Fatal(e)
	}
	if e = cli.Run(ctx, []string{"cache-clear", "--db", target}, &out, &log, "TEST_FIXTURE_BUILD"); e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
		t.Fatal("CLI did not observe lifecycle lease", e)
	}
	if e = cli.Run(ctx, []string{"cache-clear", "--db", alias}, &out, &log, "TEST_FIXTURE_BUILD"); e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP") {
		t.Fatal("symlink alias bypassed lifecycle lease", e)
	}
	if _, e = os.Lstat(alias + ".go-writer"); !os.IsNotExist(e) {
		t.Fatal("alias received separate writer identity", e)
	}
	if e = release(); e != nil {
		t.Fatal(e)
	}
	if e = cli.Run(ctx, []string{"cache-clear", "--db", target}, &out, &log, "TEST_FIXTURE_BUILD"); e != nil {
		t.Fatal("database writer lease not released", e)
	}
	// Place alias outside the isolated tree because its own path policy rejects
	// symlinks, independently of the CLI's canonical database identity.
	outsideAlias := filepath.Join(t.TempDir(), "TEST_FIXTURE_symlink.db")
	if e = os.Symlink(target, outsideAlias); e != nil {
		t.Fatal(e)
	}
	if e = os.Remove(alias); e != nil {
		t.Fatal(e)
	}
	serveCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- cli.Run(serveCtx, []string{"serve", "--db", outsideAlias, "--listen", "127.0.0.1:0"}, &bytes.Buffer{}, &bytes.Buffer{}, "TEST_FIXTURE_BUILD")
	}()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, e = os.Stat(filepath.Join(target+".go-writer", "owner.json")); e == nil {
			break
		}
		select {
		case err := <-done:
			t.Fatal("CLI writer exited early", err)
		default:
		}
		if time.Now().After(deadline) {
			t.Fatal("CLI lease not acquired")
		}
		time.Sleep(time.Millisecond)
	}
	if _, e = lifecycle.AcquireWriter(root, "TEST_FIXTURE_COLLISION"); e == nil {
		t.Fatal("lifecycle did not observe CLI lease")
	}
	if _, e = os.Stat(filepath.Join(root, "writer.lock")); !os.IsNotExist(e) {
		t.Fatal("failed second claim leaked root lease", e)
	}
	if _, e = lifecycle.Preflight(ctx, root); e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP_BUSY") {
		t.Fatal("read-only preflight ignored CLI lease", e)
	}
	cancel()
	select {
	case e = <-done:
		if e != nil && !errors.Is(e, context.Canceled) {
			t.Fatal(e)
		}
	case <-time.After(6 * time.Second):
		t.Fatal("CLI server failed to close")
	}
	if _, e = lifecycle.Cutover(ctx, root); e != nil {
		t.Fatal("owned preflight rejected its own two leases", e)
	}
	// Root composition uses only AcquireWriter for instance writes; no second db
	// acquisition should collide with the lease it has just acquired itself.
	if e = cli.Run(ctx, []string{"cache-clear", "--instance-root", root}, &out, &log, "TEST_FIXTURE_BUILD"); e != nil {
		t.Fatal("instance writer acquired duplicate lock", e)
	}
}
