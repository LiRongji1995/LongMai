package core

import (
	"context"
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestExactThresholds(t *testing.T) {
	for _, c := range []struct {
		v    string
		want State
	}{{"499999999.999999999999", Fail}, {"500000000", Pass}, {"500000000.000000000001", Pass}} {
		if s := Compare(N(c.v), N("500000000"), false, "AMOUNT").Status; s != c.want {
			t.Fatal(c, s)
		}
	}
	for _, v := range []string{"NaN", "Infinity", "true", "1/2", "1e999999999", ""} {
		if _, e := Parse(v); e == nil {
			t.Fatal(v)
		}
	}
	if s := Compare(Number{}, N("0"), false, "X").Status; s != Unknown {
		t.Fatal(s)
	}
	if s := Compare(N("0"), N("0"), true, "X").Status; s != Fail {
		t.Fatal(s)
	}
	if N(".0000005").Rounded(6).String() != "0" || N(".0000015").Rounded(6).String() != "1/500000" {
		t.Fatal("half even")
	}
}
func passFacts() Facts {
	return Facts{Returns: map[int]Number{1: N(".02"), 3: N(".04"), 5: N(".15"), 10: N(".2")}, RS: map[int]Number{1: N(".01"), 3: N(".03"), 5: N(".01")}, Momentum: "RISING", RecentTurnover: N("3"), PriorTurnover: N("2"), PriorReturn5: N(".05"), PriorReturn10: N(".18"), PriorPercentile10: N("90"), PriorHigh: N("10"), Close: N("11"), AmountRatio: N("1.2"), Path: Path{Available: true, CloseReturn: N(".02"), LowReturn: N("-.04"), Amplitude: N(".08"), Recovery: N(".06"), CLV: N(".6"), CloseLimitDown: "FALSE"}, Trend: Trend{Condition: C(Pass, ""), State: "STRONG_UPTREND", Days: N("2"), Distance: N(".02")}}
}
func TestEachStagePaths(t *testing.T) {
	for _, s := range Stages {
		f := passFacts()
		if s == "TREND_PULLBACK" {
			f.AmountRatio = N(".9")
		}
		if Identify(f)[s].Status != Pass {
			t.Fatal(s, Identify(f)[s])
		}
		switch s {
		case "START":
			f.AmountRatio = N("1.19")
		case "ACCELERATION":
			f.RecentTurnover = N("2")
		case "HIGH_LEVEL_REPAIR":
			f.Path.Recovery = N(".04999")
		case "TREND_PULLBACK":
			f.Trend.Days = N("4")
		}
		if Identify(f)[s].Status != Fail {
			t.Fatal("fail", s)
		}
		f = passFacts()
		if s == "TREND_PULLBACK" {
			f.AmountRatio = N(".9")
		}
		switch s {
		case "START":
			f.PriorHigh = Number{}
		case "ACCELERATION":
			f.RS[1] = Number{}
		case "HIGH_LEVEL_REPAIR":
			f.Path.Available = false
		case "TREND_PULLBACK":
			f.Trend.Condition = C(Unknown, "MISSING")
		}
		if Identify(f)[s].Status != Unknown {
			t.Fatal("unknown", s)
		}
	}
}
func TestDecisionIndependentAndShortCircuit(t *testing.T) {
	f := passFacts()
	a, e := Decide("TEST_FIXTURE", N("500000000"), "FALSE", Identify(f), C(Pass, ""), f.Trend.Condition, f.Trend.State, WeakToStrong(f.Path), nil)
	if e != nil || len(a.Matched) != 3 || a.CandidateStatus != "CORE_PASS" {
		t.Fatal(a, e)
	}
	b, e := Decide("TEST_FIXTURE", N("500000000"), "FALSE", Identify(f), C(Pass, ""), f.Trend.Condition, f.Trend.State, WeakToStrong(f.Path), []string{"HIGH_LEVEL_REPAIR", "TREND_PULLBACK", "ACCELERATION", "START"})
	if e != nil || strings.Join(a.Matched, ",") != strings.Join(b.Matched, ",") {
		t.Fatal("order changes selection")
	}
	for _, flag := range []string{"TRUE", "UNKNOWN", "NOT_APPLICABLE"} {
		d, _ := Decide("TEST_FIXTURE", N("500000000"), flag, Identify(f), C(Pass, ""), f.Trend.Condition, f.Trend.State, C(Pass, ""), nil)
		want := "CORE_UNKNOWN"
		if flag == "TRUE" {
			want = "CORE_FAIL"
		}
		if d.CandidateStatus != want {
			t.Fatal(flag, d)
		}
	}
	d, _ := Decide("TEST_FIXTURE", N("1"), "UNKNOWN", nil, Condition{}, Condition{}, "", Condition{}, nil)
	if d.CandidateStatus != "CORE_FAIL" {
		t.Fatal(d)
	}
	for _, c := range d.Setups {
		if c.Status != NotEvaluated {
			t.Fatal(c)
		}
	}
	if All(map[string]Condition{"a": C(NotImplemented, "")}).Status != NotImplemented {
		t.Fatal("unimplemented disguised")
	}
}
func row(v map[string]any) Row {
	b, _ := json.Marshal(v)
	var r Row
	_ = json.Unmarshal(b, &r)
	return r
}
func receipt() Row {
	return row(map[string]any{"receipt_id": "TEST_FIXTURE", "event_time": "2026-01-05T15:00:00+08:00", "source_published_at": "2026-01-05T15:00:00+08:00", "first_seen_at": "2026-01-05T15:00:00+08:00", "ingested_at": "2026-01-05T15:00:00+08:00", "status": "COMPLETE", "pagination_complete": true})
}
func evidence() Row {
	r := receipt()
	delete(r, "receipt_id")
	r["instrument_id"] = json.RawMessage(`"TEST_FIXTURE.SZ"`)
	r["session"] = json.RawMessage(`"2026-01-05"`)
	r["source_refs"] = json.RawMessage(`["TEST_FIXTURE"]`)
	return r
}
func TestEvidenceScopesAndPartial(t *testing.T) {
	p := receipt()
	r, _ := NewResolver([]Row{p}, "2026-01-05T20:00:00+08:00", "TEST_FIXTURE", "2026-01-05", []string{"2026-01-05"})
	s := Scope{Instrument: "TEST_FIXTURE.SZ", Session: "2026-01-05", RequireSession: true}
	if !r.Resolve(evidence(), s).Allowed {
		t.Fatal(r.Resolve(evidence(), s))
	}
	for k, v := range map[string]string{"instrument_id": `"OTHER"`, "session": `"2026-01-06"`, "source_refs": `["MISSING"]`, "source_published_at": `"2026-01-06T15:00:00+08:00"`} {
		x := evidence()
		x[k] = json.RawMessage(v)
		if r.Resolve(x, s).Allowed {
			t.Fatal(k)
		}
	}
	p["status"] = json.RawMessage(`"PARTIAL"`)
	if !r.Resolve(evidence(), s).Allowed {
		t.Fatal("partial row rejected")
	}
	s.Complete = true
	if r.Resolve(evidence(), s).Allowed {
		t.Fatal("partial upgraded")
	}
	p["status"] = json.RawMessage(`"COMPLETE"`)
	x := evidence()
	x["ingested_at"] = json.RawMessage(`"2026-01-06T15:00:00+08:00"`)
	if r.Resolve(x, s).Allowed {
		t.Fatal("future capture")
	}
	r.Coverage = "RECONSTRUCTED_HISTORICAL"
	if !r.Resolve(x, s).Allowed {
		t.Fatal("prior captured parent authorizes row")
	}
}
func TestHistoricalProofAndConflict(t *testing.T) {
	p := receipt()
	p["first_seen_at"] = json.RawMessage(`"2026-01-06T15:00:00+08:00"`)
	p["ingested_at"] = p["first_seen_at"]
	r, _ := NewResolver([]Row{p}, "2026-01-05T20:00:00+08:00", "RECONSTRUCTED_HISTORICAL", "2026-01-05", []string{"2026-01-05"})
	x := evidence()
	x["first_seen_at"] = p["first_seen_at"]
	x["ingested_at"] = p["ingested_at"]
	s := Scope{Session: "2026-01-05"}
	if r.Resolve(x, s).Allowed {
		t.Fatal("missing proof")
	}
	proof := map[string]any{"policy_id": "TEST_FIXTURE", "source_url": "https://example.invalid/TEST_FIXTURE", "evidence_hash": strings.Repeat("a", 64), "event_time_basis": "SOURCE_DATED_EVENT_RECORD", "publication_time_basis": "SOURCE_PUBLICATION_TIMESTAMP", "applies_from": "2026-01-05", "applies_to": "2026-01-05", "verified_at": "2026-01-06T15:00:00+08:00", "evidence_refs": []string{"TEST_FIXTURE"}}
	p["historical_time_proof"], _ = json.Marshal(proof)
	if !r.Resolve(x, s).Allowed {
		t.Fatal(r.Resolve(x, s))
	}
	proof["applies_to"] = "2026-01-04"
	p["historical_time_proof"], _ = json.Marshal(proof)
	if r.Resolve(x, s).Allowed {
		t.Fatal("proof out of scope")
	}
	a := evidence()
	b := evidence()
	b["value"] = json.RawMessage(`1`)
	if _, e := SelectRevision([]Row{a, b}); e == nil {
		t.Fatal("conflict accepted")
	}
}
func TestCanonical(t *testing.T) {
	got, e := Canonical([]byte(`{"z":null,"中文":"<>&\u2028","a":[1,1.0,true,"2026-01-05T00:00:00Z"]}`))
	want := "{\"a\":[1,1.0,true,\"2026-01-05T00:00:00Z\"],\"z\":null,\"中文\":\"<>&\u2028\"}"
	if e != nil || string(got) != want {
		t.Fatalf("%s %v", got, e)
	}
	if _, e := Canonical([]byte(`{"a":1,"a":2}`)); e == nil {
		t.Fatal("duplicate")
	}
}
func TestWindowAndCancellation(t *testing.T) {
	w := Window{Sessions: []string{"1", "2", "3"}, Bars: map[string]Bar{"1": {Close: N("1"), Basis: "a"}, "2": {Close: N("2"), Basis: "a"}, "3": {Close: N("3"), Basis: "a"}}}
	m, e := w.MA(context.Background(), 2, 3)
	if e != nil || m.String() != "2" {
		t.Fatal(m, e)
	}
	delete(w.Bars, "2")
	m, _ = w.MA(context.Background(), 2, 3)
	if m.Known() {
		t.Fatal("gap imputed")
	}
	if !w.Return(2, 2).Known() {
		t.Fatal("endpoint return intentionally requires endpoints only")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, e = w.MA(ctx, 2, 3); e == nil {
		t.Fatal("uncancellable computation")
	}
}
func TestBudgetAndSQLiteCancellation(t *testing.T) {
	b, _ := NewBudget(context.Background(), 5*time.Second, nil)
	defer b.Close()
	p := filepath.Join(t.TempDir(), "TEST_FIXTURE.sqlite3")
	db, e := sql.Open("sqlite", p)
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	if _, e = db.Exec("CREATE TABLE TEST_FIXTURE(x)"); e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	start := time.Now()
	var sum int64
	e = db.QueryRowContext(ctx, "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<1000000000) SELECT sum(n) FROM x").Scan(&sum)
	if e == nil || time.Since(start) > 2*time.Second {
		t.Fatal("driver cancellation", e, time.Since(start))
	}
	if e = b.Read(MaxSourceBytes + 1); e == nil {
		t.Fatal("source byte guard")
	}
}
func TestRSSAvailable(t *testing.T) {
	n, e := currentRSS()
	if e != nil {
		t.Log("RSS UNAVAILABLE", e)
		return
	}
	if n <= 0 {
		t.Fatal("invalid RSS", n)
	}
}
func TestOutputCancelledNoCompletion(t *testing.T) {
	root := t.TempDir()
	b, _ := NewBudget(context.Background(), time.Second, nil)
	b.cancel()
	defer b.Close()
	if e := SaveDiagnostic(root, Diagnostic{}, Manifest{}, b); e == nil {
		t.Fatal("cancel ignored")
	}
	if _, e := os.Stat(filepath.Join(root, "diagnostic-completion.json")); !os.IsNotExist(e) {
		t.Fatal("completion leaked")
	}
}
