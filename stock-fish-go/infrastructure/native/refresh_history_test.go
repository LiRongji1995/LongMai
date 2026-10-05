package native

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type refreshFixtureBudget struct{}

func (refreshFixtureBudget) Ctx() context.Context { return context.Background() }
func (refreshFixtureBudget) Check() error         { return nil }
func (refreshFixtureBudget) Read(int64) error     { return nil }
func (refreshFixtureBudget) Output(int64) error   { return nil }
func (refreshFixtureBudget) Expanded(int64) error { return nil }
func (refreshFixtureBudget) IndexRow() error      { return nil }
func (refreshFixtureBudget) Stage(string)         {}
func (refreshFixtureBudget) Processed(int)        {}
func (refreshFixtureBudget) Snapshot() c.Metrics  { return c.Metrics{} }

type refreshFixtureHistory struct {
	rows      []c.Row
	receipts  []c.Row
	hash      string
	badRaw    bool
	origin    string
	verifyErr error
}

func (h *refreshFixtureHistory) Identity() string   { return h.hash }
func (h *refreshFixtureHistory) Receipts() []c.Row  { return h.receipts }
func (h *refreshFixtureHistory) VerifyIndex() error { return h.verifyErr }
func (h *refreshFixtureHistory) Bars(id, start, end string, consume func(c.Row) error) error {
	for _, row := range h.rows {
		if row.Text("instrument_id") == id && start <= row.Text("session") && row.Text("session") <= end {
			if e := consume(row); e != nil {
				return e
			}
		}
	}
	return nil
}
func (h *refreshFixtureHistory) BarsWithIdentity(id, start, end string, consume func(c.Row, c.EvidenceIdentity) error) error {
	return h.Bars(id, start, end, func(row c.Row) error {
		raw, _ := json.Marshal(row)
		sha := c.Hash(raw)
		if h.badRaw {
			sha = "TEST_FIXTURE_UNBOUND"
		}
		origin := h.origin
		if origin == "" {
			origin = "HISTORY_ROW"
		}
		return consume(row, c.EvidenceIdentity{Origin: origin, ParentHash: h.hash, RawSHA256: sha, Locator: sha, RawBytes: int64(len(raw))})
	})
}

func refreshFixtureRow(t *testing.T, raw string, kind string) c.Row {
	t.Helper()
	row, e := ev.DecodeEvidenceRow(context.Background(), kind, []byte(raw))
	if e != nil {
		t.Fatal(e)
	}
	return row
}
func refreshFixtureBase(t *testing.T) (c.InputRecord, *refreshFixtureHistory) {
	t.Helper()
	hash := strings.Repeat("a", 64)
	receipt := refreshFixtureRow(t, `{"receipt_id":"TEST_FIXTURE_RAW","provider":"TEST_FIXTURE","dataset":"history","field_version":"TEST_FIXTURE_V1","content_hash":"`+strings.Repeat("b", 64)+`","row_count":2,"pagination_complete":true,"status":"COMPLETE","event_time":"2026-01-07T15:00:00+08:00","source_published_at":"2026-01-07T16:00:00+08:00","first_seen_at":"2026-01-07T16:00:00+08:00","ingested_at":"2026-01-07T16:00:00+08:00"}`, "receipt")
	bar := refreshFixtureRow(t, `{"instrument_id":"600000.SH","session":"2026-01-06","open":"10.00","high":"11.00","low":"9.00","close":"10.00","comparable_open":"10.00","comparable_high":"11.00","comparable_low":"9.00","comparable_close":"10.00","comparable_basis":"TEST_FIXTURE_BASIS","reference_prev_close":"10.00","amount_cny":"1000","volume_shares":"100","turnover_fraction":0.1,"quality":"VALID","source_refs":["TEST_FIXTURE_RAW"],"event_time":"2026-01-06T15:00:00+08:00","source_published_at":"2026-01-07T16:00:00+08:00","first_seen_at":"2026-01-07T16:00:00+08:00","ingested_at":"2026-01-07T16:00:00+08:00"}`, "bar")
	raw := []byte(`{"purpose":"TEST_FIXTURE","cutoff":"2026-01-07T20:00:00+08:00","input":{"coverage_type":"TEST_FIXTURE","observation_date":"2026-01-07","history_artifact_ref":"history_` + hash + `","history_artifact_hash":"` + hash + `","sessions":["2026-01-06","2026-01-07"],"bars":[],"receipts":[],"universe":{"snapshot_id":"TEST_FIXTURE_MASTER","complete":true,"expected_count":1,"source_refs":["TEST_FIXTURE_RAW"],"event_time":"2026-01-07T17:00:00+08:00","source_published_at":"2026-01-07T17:00:00+08:00","first_seen_at":"2026-01-07T17:00:00+08:00","ingested_at":"2026-01-07T17:00:00+08:00","securities":[{"instrument_id":"600000.SH","name":"TEST_FIXTURE","exchange":"SSE","board":"MAIN","security_type":"A_COMMON","listed_on":"2020-01-01","effective_from":"2026-01-06","effective_to":"2026-01-08","st":false,"suspended":false,"delisting_period":false,"source_refs":["TEST_FIXTURE_RAW"],"event_time":"2026-01-07T17:00:00+08:00","source_published_at":"2026-01-07T17:00:00+08:00","first_seen_at":"2026-01-07T17:00:00+08:00","ingested_at":"2026-01-07T17:00:00+08:00"}]},"themes":[],"funds":[]}}`)
	f, e := ev.DecodeFrozen(raw)
	if e != nil {
		t.Fatal(e)
	}
	return c.InputRecord{ID: "TEST_FIXTURE_BASE", Raw: raw, RawHash: c.Hash(raw), Observation: f.Input.Observation, Cutoff: f.Cutoff, Coverage: f.Input.Coverage}, &refreshFixtureHistory{rows: []c.Row{bar}, receipts: []c.Row{receipt}, hash: hash}
}

func TestRefreshHistoryTransportBindsSelectedRawAndRejectsUnbound(t *testing.T) {
	in, h := refreshFixtureBase(t)
	dir := t.TempDir()
	proof, e := ExportRefreshHistory(in, h, "2026-01-08", dir, refreshFixtureBudget{})
	if e != nil {
		t.Fatal(e)
	}
	if len(proof.Files) != 1 || len(proof.Files[0].RawSessions) != 1 {
		t.Fatalf("%+v", proof)
	}
	raw, e := os.ReadFile(filepath.Join(dir, proof.ManifestFile))
	if e != nil || c.Hash(raw) != proof.ManifestHash {
		t.Fatal("manifest transport identity", e)
	}
	file, e := os.Open(filepath.Join(dir, proof.Files[0].File))
	if e != nil {
		t.Fatal(e)
	}
	defer file.Close()
	reader, e := gzip.NewReader(file)
	if e != nil {
		t.Fatal(e)
	}
	defer reader.Close()
	var row refreshRow
	if e = json.NewDecoder(reader).Decode(&row); e != nil {
		t.Fatal(e)
	}
	if row.Identity.ParentHash != h.hash || row.Identity.Origin != "HISTORY_ROW" || !ev.ValidHash(row.Identity.RawSHA256) || row.Projection != c.RowHash(h.rows[0]) {
		t.Fatal(row)
	}
	h.badRaw = true
	if _, e = ExportRefreshHistory(in, h, "2026-01-08", t.TempDir(), refreshFixtureBudget{}); e == nil || !strings.Contains(e.Error(), "RAW_BINDING_INVALID") {
		t.Fatal("unbound original history reused", e)
	}
	h.badRaw = false
	h.origin = "TEST_FIXTURE_UNTRUSTED_SOURCE"
	if _, e = ExportRefreshHistory(in, h, "2026-01-08", t.TempDir(), refreshFixtureBudget{}); e == nil || !strings.Contains(e.Error(), "SOURCE_IDENTITY_UNKNOWN") {
		t.Fatal("untrusted source origin reused", e)
	}
	h.origin = ""
	h.verifyErr = errors.New("TEST_FIXTURE_INDEX_CORRUPT")
	if _, e = ExportRefreshHistory(in, h, "2026-01-08", t.TempDir(), refreshFixtureBudget{}); e == nil || e.Error() != h.verifyErr.Error() {
		t.Fatal(e)
	}
}

func TestRefreshHistoryFutureCalendarDoesNotDisplaceObservedWindow(t *testing.T) {
	in, h := refreshFixtureBase(t)
	var envelope map[string]json.RawMessage
	_ = json.Unmarshal(in.Raw, &envelope)
	var input map[string]json.RawMessage
	_ = json.Unmarshal(envelope["input"], &input)
	sessions := []string{"2026-01-06", "2026-01-07"}
	for i := 0; i < 320; i++ {
		sessions = append(sessions, time.Date(2026, 1, 8, 0, 0, 0, 0, time.UTC).AddDate(0, 0, i).Format("2006-01-02"))
	}
	input["sessions"], _ = json.Marshal(sessions)
	envelope["input"], _ = json.Marshal(input)
	in.Raw, _ = json.Marshal(envelope)
	in.RawHash = c.Hash(in.Raw)
	h.rows[0]["turnover_fraction"] = json.RawMessage(`0.06999999999999999999`)
	proof, e := ExportRefreshHistory(in, h, "2026-01-08", t.TempDir(), refreshFixtureBudget{})
	if e != nil {
		t.Fatal(e)
	}
	if len(proof.Sessions) != 2 || proof.Sessions[1] != in.Observation || proof.Files[0].Rows != 1 {
		t.Fatalf("future calendar displaced original history: %+v", proof)
	}
}

func TestComposeRefreshRejectsRevisionMaskingAndRetainsParent(t *testing.T) {
	in, h := refreshFixtureBase(t)
	proof, e := ExportRefreshHistory(in, h, "2026-01-08", t.TempDir(), refreshFixtureBudget{})
	if e != nil {
		t.Fatal(e)
	}
	var envelope map[string]json.RawMessage
	_ = json.Unmarshal(in.Raw, &envelope)
	var input map[string]json.RawMessage
	_ = json.Unmarshal(envelope["input"], &input)
	input["observation_date"] = json.RawMessage(`"2026-01-08"`)
	input["sessions"] = json.RawMessage(`["2026-01-06","2026-01-07","2026-01-08"]`)
	clock := time.Now().UTC().Add(time.Second).Format(time.RFC3339Nano)
	bar := h.rows[0]
	copy := c.Row{}
	for k, v := range bar {
		copy[k] = v
	}
	copy["session"] = json.RawMessage(`"2026-01-08"`)
	for _, field := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
		copy[field], _ = json.Marshal(clock)
	}
	input["bars"], _ = json.Marshal([]c.Row{copy})
	envelope["input"], _ = json.Marshal(input)
	envelope["cutoff"], _ = json.Marshal(clock)
	delta, _ := json.Marshal(envelope)
	composed, e := ComposeRefreshInput(in, delta, proof)
	if e != nil {
		t.Fatal(e)
	}
	var result map[string]json.RawMessage
	_ = json.Unmarshal(composed, &result)
	var parent map[string]string
	_ = json.Unmarshal(result["parent_input"], &parent)
	if parent["raw_sha256"] != in.RawHash || parent["external_id"] != in.ID {
		t.Fatal(parent)
	}
	copy["source_published_at"] = bar["source_published_at"]
	input["bars"], _ = json.Marshal([]c.Row{copy})
	envelope["input"], _ = json.Marshal(input)
	delta, _ = json.Marshal(envelope)
	if _, e = ComposeRefreshInput(in, delta, proof); e == nil || !strings.Contains(e.Error(), "REVISION_CLOCK") {
		t.Fatal("old comparable could mask a new revision", e)
	}
}
