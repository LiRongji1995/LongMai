package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func frozenFixture(t *testing.T) string {
	t.Helper()
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "TEST_FIXTURE.db")
	db, e := sql.Open("sqlite", URI(path, false))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec("PRAGMA journal_mode=WAL; CREATE TABLE fixture(value TEXT); INSERT INTO fixture VALUES('original'); PRAGMA wal_checkpoint(TRUNCATE)"); e != nil {
		t.Fatal(e)
	}
	db.Close()
	out := path + ".frozen"
	if _, e = Snapshot(ctx, path, out); e != nil {
		t.Fatal(e)
	}
	return out
}
func TestSnapshotRejectsCommittedWAL(t *testing.T) {
	ctx := context.Background()
	path := frozenFixture(t)
	before, e := FileHash(ctx, path)
	if e != nil {
		t.Fatal(e)
	}
	db, e := sql.Open("sqlite", URI(path, false))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	if _, e = db.Exec("PRAGMA wal_autocheckpoint=0; INSERT INTO fixture VALUES('WAL committed')"); e != nil {
		t.Fatal(e)
	}
	after, _ := FileHash(ctx, path)
	if before != after {
		t.Fatal("fixture must leave main hash unchanged")
	}
	st, e := os.Stat(path + "-wal")
	if e != nil || st.Size() == 0 {
		t.Fatal("fixture WAL missing", e)
	}
	var n int
	if e = db.QueryRow("SELECT count(*) FROM fixture").Scan(&n); e != nil || n != 2 {
		t.Fatal(n, e)
	}
	if _, e = SnapshotCheck(ctx, path); e == nil {
		t.Fatal("BUG: old main identity accepts SQL-visible committed WAL")
	}
}

func TestFrozenPrivateViewSurvivesConcurrentWriter(t *testing.T) {
	ctx := context.Background()
	path := frozenFixture(t)
	view, e := OpenFrozen(ctx, path)
	if e != nil {
		t.Fatal(e)
	}
	dir := view.dir
	// A real independent SQLite writer commits between verification and first SQL.
	db, e := sql.Open("sqlite", URI(path, false))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	if _, e = db.Exec("INSERT INTO fixture VALUES('later')"); e != nil {
		t.Fatal(e)
	}
	var n int
	if e = view.DB.QueryRow("SELECT count(*) FROM fixture").Scan(&n); e != nil || n != 1 {
		t.Fatal("original proven view lost", n, e)
	}
	if _, e = db.Exec("UPDATE fixture SET value='changed'; PRAGMA wal_checkpoint(TRUNCATE)"); e != nil {
		t.Fatal(e)
	}
	var value string
	if e = view.DB.QueryRow("SELECT value FROM fixture").Scan(&value); e != nil || value != "original" {
		t.Fatal(value, e)
	}
	if _, e = OpenFrozen(ctx, path); e == nil {
		t.Fatal("changed source accepted on resume")
	}
	if e = view.Close(); e != nil {
		t.Fatal(e)
	}
	if _, e = os.Stat(dir); !os.IsNotExist(e) {
		t.Fatal("private view leaked", e)
	}
}
func TestFrozenMetadataAliasJournalAndCancellation(t *testing.T) {
	ctx := context.Background()
	path := frozenFixture(t)
	original, e := os.ReadFile(path + ".snapshot.json")
	if e != nil {
		t.Fatal(e)
	}
	for _, raw := range [][]byte{[]byte("bad json"), []byte(`{"version":"unknown"}`)} {
		if e = os.WriteFile(path+".snapshot.json", raw, 0600); e != nil {
			t.Fatal(e)
		}
		if _, e = OpenFrozen(ctx, path); e == nil {
			t.Fatal("bad metadata accepted")
		}
	}
	os.WriteFile(path+".snapshot.json", original, 0600)
	alias := path + ".alias"
	if e = os.Symlink(path, alias); e != nil {
		t.Fatal(e)
	}
	if _, e = OpenFrozen(ctx, alias); e == nil {
		t.Fatal("alias accepted")
	}
	if e = os.WriteFile(path+"-journal", []byte("TEST_FIXTURE hot or unexplained journal"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = OpenFrozen(ctx, path); e == nil {
		t.Fatal("unidentified journal accepted")
	}
	os.Remove(path + "-journal")
	canceled, cancel := context.WithCancel(ctx)
	cancel()
	if _, e = OpenFrozen(canceled, path); e == nil {
		t.Fatal("cancellation ignored")
	}
	leftovers, _ := filepath.Glob(filepath.Join(filepath.Dir(path), ".verified-frozen-*"))
	if len(leftovers) != 0 {
		t.Fatal(leftovers)
	}
}
func TestVerificationHoldsTargetAndRejectsLaterInserts(t *testing.T) {
	_, source, target := fixture(t)
	ctx := context.Background()
	if _, e := Migrate(ctx, source, target, Options{}); e != nil {
		t.Fatal(e)
	}
	attempted := false
	_, e := Verify(ctx, source, target, Options{Progress: func(stage string, n int64) {
		if attempted {
			return
		}
		attempted = true
		other, e := sql.Open("sqlite", URI(target, false))
		if e != nil {
			t.Fatal(e)
		}
		defer other.Close()
		other.Exec("PRAGMA busy_timeout=1")
		if _, e = other.Exec("INSERT INTO migration_checkpoints VALUES('TEST_FIXTURE_CONCURRENT',0,0,'x')"); e == nil {
			t.Fatal("target writer entered verification")
		}
	}})
	if e != nil {
		t.Fatal(e)
	}
	if !attempted {
		t.Fatal("concurrent probe never ran")
	}
	s, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	if _, e = s.db.Exec("INSERT INTO evidence_objects(raw_sha256,raw_size,codec,payload) VALUES(?,0,'raw',X'')", digest(nil)); e == nil {
		t.Fatal("verified range accepted new evidence")
	}
}

func TestFrozenRelativeMetadataWithAbsoluteCaller(t *testing.T) {
	path := frozenFixture(t)
	raw, e := os.ReadFile(path + ".snapshot.json")
	if e != nil {
		t.Fatal(e)
	}
	var identity SnapshotIdentity
	if e = json.Unmarshal(raw, &identity); e != nil {
		t.Fatal(e)
	}
	cwd, e := os.Getwd()
	if e != nil {
		t.Fatal(e)
	}
	identity.Snapshot, e = filepath.Rel(cwd, path)
	if e != nil {
		t.Fatal(e)
	}
	raw, _ = json.Marshal(identity)
	if e = os.WriteFile(path+".snapshot.json", raw, 0600); e != nil {
		t.Fatal(e)
	}
	f, e := OpenFrozen(context.Background(), path)
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	ctx := context.WithValue(context.Background(), frozenContextKey{}, f)
	borrowed, release, e := acquireFrozen(ctx, path)
	if e != nil {
		t.Fatal(e)
	}
	defer release()
	if borrowed != f {
		t.Fatal("incremental did not reuse original proven view")
	}
}
