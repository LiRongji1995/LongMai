package sqlitev2

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/legacy"
	rt "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func fixture(t *testing.T) (string, string, string) {
	t.Helper()
	d := t.TempDir()
	src := filepath.Join(d, "TEST_FIXTURE_source.db")
	db, e := sql.Open("sqlite", URI(src, false))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	_, e = db.Exec(`PRAGMA journal_mode=WAL;CREATE TABLE documents(namespace TEXT,document_id TEXT,payload TEXT,content_hash TEXT,created_at TEXT,PRIMARY KEY(namespace,document_id));CREATE TABLE history_evidence_rows(row_hash TEXT PRIMARY KEY,kind TEXT,instrument_id TEXT,session TEXT,payload TEXT);CREATE TABLE history_partition_members(partition_hash TEXT,row_hash TEXT,kind TEXT,instrument_id TEXT,session TEXT,PRIMARY KEY(partition_hash,row_hash));CREATE TABLE jobs(job_id TEXT PRIMARY KEY,kind TEXT,state TEXT,revision INTEGER,updated_at TEXT);CREATE TABLE job_events(event_id INTEGER PRIMARY KEY,job_id TEXT,state TEXT);CREATE TABLE yaogu_derived_features_v2(cache_key TEXT PRIMARY KEY,payload TEXT);INSERT INTO jobs VALUES('test_job','TEST_FIXTURE','RUNNING',2,'2026-01-01');INSERT INTO job_events VALUES(1,'test_job','RUNNING');INSERT INTO yaogu_derived_features_v2 VALUES('discard','{}')`)
	if e != nil {
		t.Fatal(e)
	}
	raw := []byte(`{"amount_cny":"500000000.00","close":"10.0","event_time":"2026-01-01T07:00:00Z","source_published_at":"2026-01-01T07:00:00Z","first_seen_at":"2026-01-01T07:00:00Z","ingested_at":"2026-01-01T07:00:00Z","instrument_id":"600001.SH","session":"2026-01-01","source_refs":[],"quality":"VALID"}`)
	rh := digest(raw)
	v, _ := json.Marshal([]string{rh, "bar", "600001.SH", "2026-01-01"})
	h := sha256.Sum256(append(v, '\n'))
	ph := hex.EncodeToString(h[:])
	manifest := fmt.Sprintf(`{"artifact_version":"CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1","partitions":[{"partition_hash":%q,"row_count":1,"session":"2026-01-01"}],"receipts":[]}`, ph)
	mh := digest([]byte(manifest))
	_, e = db.Exec("INSERT INTO documents VALUES('history_artifact_manifests',?,?,?,'TEST_FIXTURE')", "history_"+mh, manifest, mh)
	if e != nil {
		t.Fatal(e)
	}
	_, e = db.Exec("INSERT INTO history_evidence_rows VALUES(?,'bar','600001.SH','2026-01-01',?)", rh, string(raw))
	if e != nil {
		t.Fatal(e)
	}
	_, e = db.Exec("INSERT INTO history_partition_members VALUES(?,?,'bar','600001.SH','2026-01-01')", ph, rh)
	if e != nil {
		t.Fatal(e)
	}
	snap := filepath.Join(d, "snapshot.db")
	target := filepath.Join(d, "v2.db")
	if _, e = Snapshot(context.Background(), src, snap); e != nil {
		t.Fatal(e)
	}
	return src, snap, target
}
func TestSnapshotWALAndMigrationResumePublish(t *testing.T) {
	_, src, target := fixture(t)
	ctx := context.Background()
	r, e := Migrate(ctx, src, target, Options{BatchSize: 1, MaxBatches: 2})
	if e != nil {
		t.Fatal(e)
	}
	if r.Status != "INTERRUPTED_CHECKPOINTED" {
		t.Fatal(r)
	}
	s, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	if s.Ready(ctx) == nil {
		t.Fatal("partial migration exposed")
	}
	s.Close()
	if _, e = Migrate(ctx, src, target, Options{BatchSize: 1}); e != nil {
		t.Fatal(e)
	}
	v, e := Verify(ctx, src, target, Options{})
	if e != nil {
		t.Fatal(e)
	}
	if v.Checked["history_raw_and_projections"] != 1 {
		t.Fatal(v)
	}
	if e = Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
	s, e = Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	if e = s.Ready(ctx); e != nil {
		t.Fatal(e)
	}
	var n int
	if e = s.db.QueryRow("SELECT count(*) FROM historical_jobs WHERE executable=0 AND state='RUNNING'").Scan(&n); e != nil || n != 1 {
		t.Fatal(n, e)
	}
	if _, e = Migrate(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
}
func TestRawHashMismatchAndProjectionTamper(t *testing.T) {
	_, src, target := fixture(t)
	ctx := context.Background()
	if _, e := Migrate(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if _, e := Verify(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if e := Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
	s, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.db.Exec("UPDATE history_rows SET close='11'"); e == nil {
		t.Fatal("immutable update accepted")
	}
	if _, e = s.db.Exec("DROP TRIGGER history_rows_no_UPDATE;UPDATE history_rows SET close='11';CREATE TRIGGER history_rows_no_UPDATE BEFORE UPDATE ON history_rows BEGIN SELECT RAISE(ABORT,'immutable evidence'); END"); e != nil {
		t.Fatal(e)
	}
	s.Close()
	if _, e = Verify(ctx, src, target, Options{}); e == nil {
		t.Fatal("typed tamper accepted")
	}
	s, e = Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	if e = s.Ready(ctx); e == nil {
		t.Fatal("failed revalidation left publication gate open")
	}
	s.Close()
	if e = Publish(ctx, target); e == nil {
		t.Fatal("published unverified")
	}
}

func TestFullVerificationRejectsTypedRelationTampering(t *testing.T) {
	for _, cse := range []struct{ table, change string }{
		{"snapshots", "UPDATE snapshots SET observation_date='2026-01-02'"},
		{"snapshot_partitions", "UPDATE snapshot_partitions SET session='2026-01-02'"},
		{"historical_jobs", "UPDATE historical_jobs SET state='SUCCEEDED'"},
	} {
		t.Run(cse.table, func(t *testing.T) {
			_, source, target := fixture(t)
			ctx := context.Background()
			if _, e := Migrate(ctx, source, target, Options{}); e != nil {
				t.Fatal(e)
			}
			s, e := Open(ctx, target, false)
			if e != nil {
				t.Fatal(e)
			}
			q := fmt.Sprintf("DROP TRIGGER %s_no_UPDATE;%s;CREATE TRIGGER %s_no_UPDATE BEFORE UPDATE ON %s BEGIN SELECT RAISE(ABORT,'immutable evidence'); END", cse.table, cse.change, cse.table, cse.table)
			if _, e = s.db.Exec(q); e != nil {
				t.Fatal(e)
			}
			s.Close()
			if _, e = Verify(ctx, source, target, Options{}); e == nil {
				t.Fatal("changed relation accepted")
			}
		})
	}
}

func TestCompactExactSetsRejectMissingExtraReplacementAndMetadata(t *testing.T) {
	for _, cse := range []struct{ name, sql, code string }{
		{"missing", "DELETE FROM partition_members", "PARTITION_MISSING_MEMBER"},
		{"extra", "INSERT INTO partitions(partition_hash) VALUES('cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc');INSERT INTO partition_members SELECT (SELECT max(id) FROM partitions),id FROM history_rows", "PARTITION_EXTRA_MEMBER"},
		{"same_count_changed_key", "UPDATE history_rows SET row_hash='dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd'", "HISTORY_EXTRA_IDENTITY"},
		{"metadata", "UPDATE history_rows SET instrument_id='600002.SH'", "HISTORY_IDENTITY_METADATA_MISMATCH"},
	} {
		t.Run(cse.name, func(t *testing.T) {
			_, source, target := fixture(t)
			ctx := context.Background()
			if _, e := Migrate(ctx, source, target, Options{}); e != nil {
				t.Fatal(e)
			}
			s, e := Open(ctx, target, false)
			if e != nil {
				t.Fatal(e)
			}
			for _, table := range []string{"history_rows", "partition_members"} {
				for _, op := range []string{"UPDATE", "DELETE"} {
					if _, e = s.db.Exec("DROP TRIGGER " + table + "_no_" + op); e != nil {
						t.Fatal(e)
					}
				}
			}
			if _, e = s.db.Exec(cse.sql); e != nil {
				t.Fatal(e)
			}
			for _, table := range []string{"history_rows", "partition_members"} {
				for _, op := range []string{"UPDATE", "DELETE"} {
					if _, e = s.db.Exec(fmt.Sprintf("CREATE TRIGGER %s_no_%s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT,'immutable evidence'); END", table, op, op, table)); e != nil {
						t.Fatal(e)
					}
				}
			}
			s.Close()
			if _, e = Verify(ctx, source, target, Options{}); e == nil || !strings.Contains(e.Error(), cse.code) {
				t.Fatalf("got %v want %s", e, cse.code)
			}
		})
	}
}
func TestSameKeyDifferentContent(t *testing.T) {
	ctx := context.Background()
	s, e := Open(ctx, filepath.Join(t.TempDir(), "v2.db"), true)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	tx, _ := s.db.BeginTx(ctx, nil)
	defer tx.Rollback()
	a, b := []byte(`{"a":1}`), []byte(`{"a":2}`)
	if e = putDocument(ctx, tx, "unknown", "TEST_FIXTURE", a, digest(a), ""); e != nil {
		t.Fatal(e)
	}
	if e = putDocument(ctx, tx, "unknown", "TEST_FIXTURE", a, digest(a), ""); e != nil {
		t.Fatal(e)
	}
	if e = putDocument(ctx, tx, "unknown", "TEST_FIXTURE", b, digest(b), ""); e == nil {
		t.Fatal("conflict accepted")
	}
}
func TestSourceChangedResumeRefused(t *testing.T) {
	_, src, target := fixture(t)
	ctx := context.Background()
	if _, e := Migrate(ctx, src, target, Options{MaxBatches: 1}); e != nil {
		t.Fatal(e)
	}
	f, e := os.OpenFile(src, os.O_WRONLY|os.O_APPEND, 0600)
	if e != nil {
		t.Fatal(e)
	}
	f.Write([]byte("changed"))
	f.Close()
	if _, e = Migrate(ctx, src, target, Options{}); e == nil {
		t.Fatal("changed source accepted")
	}
}
func TestCancelledSQLAndSnapshotTargetGuard(t *testing.T) {
	src, _, target := fixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Nanosecond)
	defer cancel()
	time.Sleep(time.Millisecond)
	if _, e := Snapshot(ctx, src, target); e == nil {
		t.Fatal("cancel ignored")
	}
	if e := os.WriteFile(target, []byte("protected"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e := Snapshot(context.Background(), src, target); e == nil {
		t.Fatal("existing file overwritten")
	}
}

func TestIncrementalLateArrivalAndMutableRevision(t *testing.T) {
	src, snap, target := fixture(t)
	ctx := context.Background()
	if _, e := Migrate(ctx, snap, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if _, e := Verify(ctx, snap, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if e := Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
	db, e := sql.Open("sqlite", URI(src, false))
	if e != nil {
		t.Fatal(e)
	}
	raw := []byte(`{"TEST_FIXTURE":"late_same_timestamp"}`)
	if _, e = db.Exec("INSERT INTO documents VALUES('test_fixture','late',?,?,'TEST_FIXTURE')", string(raw), digest(raw)); e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec("UPDATE jobs SET state='SUCCEEDED',revision=3 WHERE job_id='test_job'"); e != nil {
		t.Fatal(e)
	}
	db.Close()
	next := filepath.Join(filepath.Dir(src), "next.db")
	if _, e = Snapshot(ctx, src, next); e != nil {
		t.Fatal(e)
	}
	r, e := Incremental(ctx, next, target, Options{BatchSize: 1, MaxBatches: 1})
	if e != nil {
		t.Fatal(e)
	}
	if r.Status != "INTERRUPTED_CHECKPOINTED" {
		t.Fatal(r)
	}
	r, e = Incremental(ctx, next, target, Options{BatchSize: 1})
	if e != nil {
		t.Fatal(e)
	}
	if r.Status != "PUBLISHED" {
		t.Fatal(r)
	}
	s, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	got, _, e := s.LegacyDocument(ctx, "test_fixture", "late")
	if e != nil || string(got) != string(raw) {
		t.Fatal(string(got), e)
	}
	var count int
	if e = s.db.QueryRow("SELECT count(*) FROM historical_jobs WHERE job_id='test_job' AND executable=0").Scan(&count); e != nil || count != 2 {
		t.Fatal(count, e)
	}
	if _, e = Incremental(ctx, next, target, Options{}); e != nil {
		t.Fatal(e)
	}
}

func TestHistoryRawIdentitySurvivesProjection(t *testing.T) {
	_, src, target := fixture(t)
	ctx := context.Background()
	if _, e := Migrate(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if _, e := Verify(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if e := Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
	s, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	var ref, parent, rawHash string
	var rawBytes int64
	if e = s.db.QueryRow("SELECT external_id,source_hash FROM legacy_records WHERE namespace='history_artifact_manifests'").Scan(&ref, &parent); e != nil {
		t.Fatal(e)
	}
	if e = s.db.QueryRow("SELECT row_hash,raw_size FROM history_rows").Scan(&rawHash, &rawBytes); e != nil {
		t.Fatal(e)
	}
	b, e := rt.NewBudget(ctx, time.Minute, nil)
	if e != nil {
		t.Fatal(e)
	}
	defer b.Close()
	newHistory, e := s.History(ctx, ref, parent, b)
	if e != nil {
		t.Fatal(e)
	}
	oldHistory, e := legacy.OpenHistory(src, ref, parent, b)
	if e != nil {
		t.Fatal(e)
	}
	defer oldHistory.Close()
	for name, h := range map[string]interface {
		VerifyIndex() error
		BarsWithIdentity(string, string, string, func(c.Row, c.EvidenceIdentity) error) error
	}{"legacy": oldHistory, "v2": newHistory} {
		t.Run(name, func(t *testing.T) {
			if e := h.VerifyIndex(); e != nil {
				t.Fatal(e)
			}
			count := 0
			e := h.BarsWithIdentity("600001.SH", "2026-01-01", "2026-01-01", func(r c.Row, id c.EvidenceIdentity) error {
				count++
				if id.Origin != "HISTORY_ROW" || id.ParentHash != parent || id.RawSHA256 != rawHash || id.Locator != rawHash || id.RawBytes != rawBytes {
					t.Fatalf("bad raw identity: %+v", id)
				}
				if c.RowHash(r) == rawHash {
					t.Fatal("projection defaults lost distinct identity")
				}
				if _, ok := r["raw_sha256"]; ok {
					t.Fatal("identity injected into projection")
				}
				return nil
			})
			if e != nil || count != 1 {
				t.Fatal(count, e)
			}
		})
	}
}

func TestTypedQuoteAndTamper(t *testing.T) {
	_, src, target := fixture(t)
	ctx := context.Background()
	if _, e := Migrate(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if _, e := Verify(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if e := Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
	s, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	var id string
	if e = s.db.QueryRow("SELECT snapshot_id FROM snapshots").Scan(&id); e != nil {
		t.Fatal(e)
	}
	q := QuoteWindow{id, "600001.SH", "2026-01-01", "2026-01-01"}
	n := 0
	if e = s.Quotes(ctx, q, func(q Quote) error {
		n++
		if q.Values["amount_cny"] == nil || *q.Values["amount_cny"] != "500000000.00" {
			t.Fatal(q)
		}
		return nil
	}); e != nil {
		t.Fatal(e)
	}
	if n != 1 {
		t.Fatal(n)
	}
	if _, e = s.db.Exec("DROP TRIGGER history_rows_no_UPDATE;UPDATE history_rows SET amount_cny='499999999.99'"); e != nil {
		t.Fatal(e)
	}
	if e = s.Quotes(ctx, q, func(q Quote) error { return nil }); e == nil {
		t.Fatal("modified typed column accepted")
	}
}

func TestCorruptSourcePayloadFailsBeforeProjection(t *testing.T) {
	src, _, target := fixture(t)
	ctx := context.Background()
	db, e := sql.Open("sqlite", URI(src, false))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec("UPDATE history_evidence_rows SET payload=payload||' '"); e != nil {
		t.Fatal(e)
	}
	db.Close()
	corrupt := filepath.Join(filepath.Dir(src), "TEST_FIXTURE_CORRUPT_SOURCE.db")
	if _, e = Snapshot(ctx, src, corrupt); e != nil {
		t.Fatal(e)
	}
	if _, e = Migrate(ctx, corrupt, target, Options{}); e == nil {
		t.Fatal("source byte corruption canonical-equivalent whitespace was accepted")
	}
}

func TestPreparedMemberBatchStillRejectsMetadataConflict(t *testing.T) {
	src, _, target := fixture(t)
	ctx := context.Background()
	db, e := sql.Open("sqlite", URI(src, false))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec("UPDATE history_partition_members SET instrument_id='600002.SH'"); e != nil {
		t.Fatal(e)
	}
	db.Close()
	frozen := filepath.Join(filepath.Dir(src), "TEST_FIXTURE_member_conflict.db")
	if _, e = Snapshot(ctx, src, frozen); e != nil {
		t.Fatal(e)
	}
	if _, e = Migrate(ctx, frozen, target, Options{BatchSize: 2000}); e == nil || !strings.Contains(e.Error(), "SOURCE_PARTITION_METADATA_CONFLICT") {
		t.Fatal(e)
	}
	s, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	var count int
	if e = s.db.QueryRow("SELECT count(*) FROM partition_members").Scan(&count); e != nil || count != 0 {
		t.Fatal(count, e)
	}
}
func TestCompressedRawTamperDetected(t *testing.T) {
	_, src, target := fixture(t)
	ctx := context.Background()
	if _, e := Migrate(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
	s, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.db.Exec("DROP TRIGGER history_rows_no_UPDATE;UPDATE history_rows SET payload=x'00';CREATE TRIGGER history_rows_no_UPDATE BEFORE UPDATE ON history_rows BEGIN SELECT RAISE(ABORT,'immutable evidence'); END"); e != nil {
		t.Fatal(e)
	}
	s.Close()
	if _, e = Verify(ctx, src, target, Options{}); e == nil {
		t.Fatal("compressed raw corruption accepted")
	}
}
func TestRealSchemaObjectsVerified(t *testing.T) {
	s, e := Open(context.Background(), filepath.Join(t.TempDir(), "TEST_FIXTURE.db"), true)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	if e = s.VerifySchema(context.Background()); e != nil {
		t.Fatal(e)
	}
	if _, e = s.db.Exec("DROP INDEX history_security_window"); e != nil {
		t.Fatal(e)
	}
	if e = s.VerifySchema(context.Background()); e == nil {
		t.Fatal("deleted actual index accepted")
	}
}
