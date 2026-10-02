package legacy

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func historyFixture(t *testing.T) (string, string, string, string) {
	t.Helper()
	p := filepath.Join(t.TempDir(), "TEST_FIXTURE.sqlite3")
	db, e := sql.Open("sqlite", p)
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	for _, s := range []string{"CREATE TABLE documents(namespace TEXT,document_id TEXT,payload TEXT,content_hash TEXT)", "CREATE TABLE history_evidence_rows(row_hash TEXT PRIMARY KEY,kind TEXT,instrument_id TEXT,session TEXT,payload TEXT)", "CREATE TABLE history_partition_members(partition_hash TEXT,row_hash TEXT,kind TEXT,instrument_id TEXT,session TEXT)"} {
		if _, e = db.Exec(s); e != nil {
			t.Fatal(e)
		}
	}
	r, _ := fixturePart(t, contractFixture(), "bars")
	r["close"] = json.RawMessage(`"10.00"`)
	r, e = ValidateEvidenceRow(context.Background(), "bar", r)
	if e != nil {
		t.Fatal(e)
	}
	raw, _ := json.Marshal(r)
	raw, _ = Canonical(raw)
	rh := Hash(raw)
	descriptor, _ := json.Marshal([]string{rh, "bar", "TEST_FIXTURE.SZ", "2026-01-05"})
	descriptor, _ = Canonical(descriptor)
	ph := Hash(append(descriptor, '\n'))
	parent, _ := fixturePart(t, contractFixture(), "receipts")
	m := map[string]any{"artifact_version": "CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1", "partitions": []map[string]any{{"session": "2026-01-05", "partition_hash": ph, "row_count": 1}}, "receipts": []Row{parent}}
	mr, _ := json.Marshal(m)
	mr, _ = Canonical(mr)
	mh := Hash(mr)
	for _, q := range []struct {
		s string
		a []any
	}{{"INSERT INTO documents VALUES(?,?,?,?)", []any{"history_artifact_manifests", "history_" + mh, string(mr), mh}}, {"INSERT INTO history_evidence_rows VALUES(?,?,?,?,?)", []any{rh, "bar", "TEST_FIXTURE.SZ", "2026-01-05", string(raw)}}, {"INSERT INTO history_partition_members VALUES(?,?,?,?,?)", []any{ph, rh, "bar", "TEST_FIXTURE.SZ", "2026-01-05"}}} {
		if _, e = db.Exec(q.s, q.a...); e != nil {
			t.Fatal(e)
		}
	}
	return p, "history_" + mh, mh, rh
}
func TestHistoryIntegrityReadOnlyAndCleanup(t *testing.T) {
	p, ref, hash, rh := historyFixture(t)
	before, _ := os.ReadFile(p)
	b, _ := NewBudget(context.Background(), 5*time.Second, nil)
	defer b.Close()
	h, e := OpenHistory(p, ref, hash, b)
	if e != nil {
		t.Fatal(e)
	}
	if e = h.VerifyIndex(); e != nil {
		t.Fatal(e)
	}
	n := 0
	if e = h.Bars("TEST_FIXTURE.SZ", "2026-01-05", "2026-01-05", func(r Row) error {
		n++
		if RowHash(r) != rh {
			t.Fatal("identity mismatch")
		}
		return nil
	}); e != nil || n != 1 {
		t.Fatal(n, e)
	}
	if _, e = h.tx.ExecContext(b.Context, "DELETE FROM history_evidence_rows"); e == nil {
		t.Fatal("read-only connection allowed write")
	}
	h.Close()
	after, _ := os.ReadFile(p)
	if Hash(before) != Hash(after) {
		t.Fatal("database changed")
	}
	db, _ := sql.Open("sqlite", p)
	defer db.Close()
	if _, e = db.Exec("UPDATE history_evidence_rows SET payload='{}'"); e != nil {
		t.Fatal("snapshot not released", e)
	}
	h, e = OpenHistory(p, ref, hash, b)
	if e != nil {
		t.Fatal(e)
	}
	defer h.Close()
	if e = h.Bars("TEST_FIXTURE.SZ", "2026-01-05", "2026-01-05", func(Row) error { return nil }); e == nil {
		t.Fatal("corrupt payload accepted")
	}
}
func TestHistoryIndexCorruption(t *testing.T) {
	p, ref, hash, _ := historyFixture(t)
	db, _ := sql.Open("sqlite", p)
	_, e := db.Exec("UPDATE history_partition_members SET session='2026-01-06'")
	db.Close()
	if e != nil {
		t.Fatal(e)
	}
	b, _ := NewBudget(context.Background(), 5*time.Second, nil)
	defer b.Close()
	h, e := OpenHistory(p, ref, hash, b)
	if e != nil {
		t.Fatal(e)
	}
	defer h.Close()
	if e = h.VerifyIndex(); e == nil {
		t.Fatal("corrupt partition accepted")
	}
}
func TestResultIntegrityAndBudget(t *testing.T) {
	root := t.TempDir()
	b, _ := NewBudget(context.Background(), 5*time.Second, nil)
	defer b.Close()
	d := Diagnostic{RunStatus: "INCOMPLETE_IMPLEMENTATION", CandidateAvailability: "UNAVAILABLE", InputBytesHash: "TEST_FIXTURE", Facts: []StockFacts{{ID: "TEST_FIXTURE", Amount: N("1").Div(N("3"))}}, Decisions: []Decision{{InstrumentID: "TEST_FIXTURE"}}, Qualification: Qualification{Eligible: []string{"TEST_FIXTURE"}}}
	m := Manifest{InputHash: "TEST_FIXTURE"}
	if e := SaveDiagnostic(root, d, m, b); e != nil {
		t.Fatal(e)
	}
	if _, e := VerifyDiagnostic(root, b); e != nil {
		t.Fatal(e)
	}
	if _, e := os.Stat(filepath.Join(root, "completion.json")); !os.IsNotExist(e) {
		t.Fatal("research success marker forbidden")
	}
	if e := os.WriteFile(filepath.Join(root, "report.md"), []byte("changed"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e := VerifyDiagnostic(root, b); e == nil {
		t.Fatal("tampering accepted")
	}
	if e := b.Output(MaxOutputBytes + 1); e == nil {
		t.Fatal("output cap ignored")
	}
}
func TestLimitsAndQualification(t *testing.T) {
	yes := true
	b := Bar{Open: N("10"), High: N("11"), Low: N("9"), RawClose: N("9"), Tick: N(".01"), LimitUp: N("11"), LimitDown: N("9"), LimitApplicable: &yes, LimitVerified: true}
	_, down, _ := Limits(&b)
	if down != "TRUE" {
		t.Fatal(down)
	}
	b.RawClose = N("10")
	_, down, _ = Limits(&b)
	if down != "FALSE" {
		t.Fatal(down)
	}
	b.RawClose = N("10.001")
	_, down, _ = Limits(&b)
	if down != "UNKNOWN" {
		t.Fatal(down)
	}
	s := row(map[string]any{"exchange": "SSE", "board": "MAIN", "security_type": "A_COMMON", "listed_on": "2020-01-01", "st": false, "suspended": false, "delisting_period": false})
	sessions := []string{}
	for i := 1; i <= 60; i++ {
		sessions = append(sessions, fmt.Sprintf("2026-%02d-%02d", 1+(i-1)/28, 1+(i-1)%28))
	}
	if reason := Qualify(s, sessions[59], sessions); reason != "" {
		t.Fatal(reason)
	}
	s["st"] = json.RawMessage("null")
	if reason := Qualify(s, sessions[59], sessions); reason != "UNKNOWN_HISTORICAL_STATUS" {
		t.Fatal(reason)
	}
}

// Every failure below happens after the connection has been acquired; failures
// after SELECT also hold a read snapshot. An immediate exclusive write proves
// the failed constructor did not strand that snapshot until GC.
func TestOpenHistoryFailureReleasesSnapshot(t *testing.T) {
	cases := []string{"missing_document", "raw_byte_tamper", "invalid_json", "duplicate_json_key", "expected_hash", "version", "invalid_partition_date", "negative_partition_count", "duplicate_partition", "invalid_receipt"}
	for _, kind := range cases {
		t.Run(kind, func(t *testing.T) {
			p, ref, hash, _ := historyFixture(t)
			db, e := sql.Open("sqlite", p)
			if e != nil {
				t.Fatal(e)
			}
			defer db.Close()
			var original []byte
			if e = db.QueryRow("SELECT payload FROM documents").Scan(&original); e != nil {
				t.Fatal(e)
			}
			raw := original
			var m Row
			_ = json.Unmarshal(raw, &m)
			switch kind {
			case "missing_document":
				ref = "history_missing"
			case "raw_byte_tamper":
				raw = append([]byte(" \n"), raw...)
			case "invalid_json":
				raw = []byte("{")
			case "duplicate_json_key":
				raw = []byte(`{"artifact_version":"CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1","artifact_version":"CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1"}`)
			case "expected_hash":
				hash = strings.Repeat("0", 64)
			case "version":
				m["artifact_version"] = json.RawMessage(`"BAD"`)
			case "invalid_partition_date", "negative_partition_count", "duplicate_partition":
				var parts []Row
				_ = json.Unmarshal(m["partitions"], &parts)
				if kind == "invalid_partition_date" {
					parts[0]["session"] = json.RawMessage(`"2026-02-30"`)
				}
				if kind == "negative_partition_count" {
					parts[0]["row_count"] = json.RawMessage(`-1`)
				}
				if kind == "duplicate_partition" {
					parts = append(parts, parts[0])
				}
				m["partitions"], _ = json.Marshal(parts)
			case "invalid_receipt":
				m["receipts"] = json.RawMessage(`[{"receipt_id":"TEST_FIXTURE"}]`)
			}
			if kind == "version" || strings.Contains(kind, "partition") || kind == "invalid_receipt" {
				raw, _ = json.Marshal(m)
				raw, _ = Canonical(raw)
			}
			if kind != "missing_document" && kind != "expected_hash" {
				stored := Hash(raw)
				if kind != "raw_byte_tamper" {
					hash = stored
					ref = "history_" + hash
				} else {
					stored = hash
				}
				if _, e = db.Exec("UPDATE documents SET payload=?,content_hash=?,document_id=?", string(raw), stored, ref); e != nil {
					t.Fatal(e)
				}
			}
			b, _ := NewBudget(context.Background(), 5*time.Second, nil)
			defer b.Close()
			if h, er := OpenHistory(p, ref, hash, b); er == nil {
				h.Close()
				t.Fatal("failure accepted", kind)
			}
			if _, e = db.Exec("PRAGMA busy_timeout=5"); e != nil {
				t.Fatal(e)
			}
			if _, e = db.Exec("BEGIN EXCLUSIVE"); e != nil {
				t.Fatal("failure retained read snapshot", e)
			}
			if _, e = db.Exec("ROLLBACK"); e != nil {
				t.Fatal(e)
			}
		})
	}
}
func TestHistoryCancelledEmptyWorkAndCallbackError(t *testing.T) {
	p, ref, hash, _ := historyFixture(t)
	b, _ := NewBudget(context.Background(), 5*time.Second, nil)
	defer b.Close()
	h, e := OpenHistory(p, ref, hash, b)
	if e != nil {
		t.Fatal(e)
	}
	defer h.Close()
	stop := errors.New("TEST_FIXTURE callback stop")
	if e = h.Bars("TEST_FIXTURE.SZ", "2026-01-05", "2026-01-05", func(Row) error { return stop }); !errors.Is(e, stop) {
		t.Fatal(e)
	}
	n := 0
	if e = h.Bars("TEST_FIXTURE.SZ", "2026-01-05", "2026-01-05", func(Row) error { n++; return nil }); e != nil || n != 1 {
		t.Fatal("callback error leaked rows", e, n)
	}
	b.Cancel()
	if e = h.Bars("MISSING", "1900-01-01", "1900-01-02", func(Row) error { return nil }); !errors.Is(e, context.Canceled) {
		t.Fatal("empty work ignores cancellation", e)
	}
	if e = h.VerifyIndex(); !errors.Is(e, context.Canceled) {
		t.Fatal(e)
	}
}
