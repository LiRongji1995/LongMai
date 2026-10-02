//go:build darwin

package lifecycle

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
)

func TestActualAPFSClonePreservesSourceAndWALCommit(t *testing.T) {
	root, conf := isolatedFixture(t)
	ctx := context.Background()
	possible, e := cloneAvailable(conf.Target, root)
	if e != nil {
		t.Fatal(e)
	}
	if !possible {
		t.Fatal("FORMAL_LOCAL_APFS_CLONE_TEST_REQUIRES_APFS")
	}
	release, e := AcquireWriter(root, "TEST_FIXTURE_NATIVE_CLONE")
	if e != nil {
		t.Fatal(e)
	}
	defer release()
	db, e := sql.Open("sqlite", sqlitev2.URI(conf.Target, false))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec("PRAGMA journal_mode=WAL; CREATE TABLE TEST_FIXTURE_clone_commit(value TEXT); INSERT INTO TEST_FIXTURE_clone_commit VALUES('WAL_COMMITTED')"); e != nil {
		t.Fatal(e)
	}
	// Keep a connection open so the committed WAL is present, then the lifecycle
	// checkpoint must actually fold it into the main file before clonefile.
	if _, e = os.Stat(conf.Target + "-wal"); e != nil {
		t.Fatal(e)
	}
	target := filepath.Join(root, "TEST_FIXTURE_cow.db")
	identity, e := isolatedSnapshot(ctx, root, conf.Target, target, true)
	if e != nil {
		t.Fatal(e)
	}
	if e = db.Close(); e != nil {
		t.Fatal(e)
	}
	if identity.Version != "ISOLATED_APFS_CLONEFILE_V1" {
		t.Fatal(identity)
	}
	copied, e := sql.Open("sqlite", sqlitev2.URI(target, false))
	if e != nil {
		t.Fatal(e)
	}
	var value string
	if e = copied.QueryRow("SELECT value FROM TEST_FIXTURE_clone_commit").Scan(&value); e != nil || value != "WAL_COMMITTED" {
		t.Fatal(value, e)
	}
	if _, e = copied.Exec("UPDATE TEST_FIXTURE_clone_commit SET value='CLONE_ONLY'"); e != nil {
		t.Fatal(e)
	}
	if e = copied.Close(); e != nil {
		t.Fatal(e)
	}
	original, e := readDB(ctx, conf.Target)
	if e != nil {
		t.Fatal(e)
	}
	defer original.Close()
	if e = original.QueryRow("SELECT value FROM TEST_FIXTURE_clone_commit").Scan(&value); e != nil || value != "WAL_COMMITTED" {
		t.Fatal("clone is not independent", value, e)
	}
	hash, e := sqlitev2.FileHash(ctx, conf.Target)
	if e != nil {
		t.Fatal(e)
	}
	if hash != identity.SHA256 {
		t.Fatal("writing clone changed frozen source bytes")
	}
	t.Logf("actual clonefile succeeded: version=%s bytes=%d hash=%s", identity.Version, identity.Bytes, identity.SHA256)
}
func TestCloneRequiresLeaseAndRejectsFrozenWAL(t *testing.T) {
	root, conf := isolatedFixture(t)
	ctx := context.Background()
	target := filepath.Join(root, "TEST_FIXTURE_forbidden.db")
	if _, e := isolatedSnapshot(ctx, root, conf.Source, target, false); e == nil {
		t.Fatal("clone without lease")
	}
	release, e := AcquireWriter(root, "TEST_FIXTURE_FROZEN_WAL")
	if e != nil {
		t.Fatal(e)
	}
	defer release()
	if e = os.WriteFile(conf.Source+"-wal", []byte("TEST_FIXTURE_PENDING_WAL"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = isolatedSnapshot(ctx, root, conf.Source, target, false); e == nil {
		t.Fatal("frozen WAL omitted")
	}
	if _, e = os.Stat(target); !os.IsNotExist(e) {
		t.Fatal("failed clone published target", e)
	}
}
