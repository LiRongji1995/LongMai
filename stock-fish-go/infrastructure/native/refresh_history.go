package native

import (
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
)

const RefreshHistoryContract = "GO_VERIFIED_REFRESH_HISTORY_V1"

type RefreshHistoryFile struct {
	Instrument  string   `json:"instrument_id"`
	File        string   `json:"file"`
	SHA256      string   `json:"sha256"`
	Bytes       int64    `json:"bytes"`
	Rows        int      `json:"rows"`
	RawSessions []string `json:"verified_raw_sessions"`
}

// RefreshHistoryProof is a bounded transport of the selected window, not a
// replacement history database. The parent retains and later reopens the exact
// immutable input and history referenced here when composing the new input.
type RefreshHistoryProof struct {
	Contract     string               `json:"contract"`
	InputID      string               `json:"input_id"`
	InputHash    string               `json:"input_sha256"`
	HistoryRef   string               `json:"history_artifact_ref"`
	HistoryHash  string               `json:"history_artifact_hash"`
	Observation  string               `json:"observation_date"`
	Cutoff       string               `json:"base_cutoff"`
	VerifiedAt   string               `json:"verified_at"`
	Coverage     string               `json:"coverage_type"`
	Sessions     []string             `json:"sessions"`
	Receipts     []c.Row              `json:"receipts"`
	Files        []RefreshHistoryFile `json:"files"`
	ManifestFile string               `json:"-"`
	ManifestHash string               `json:"-"`
}

type refreshRow struct {
	Row        c.Row              `json:"row"`
	Projection string             `json:"projection_sha256"`
	Identity   c.EvidenceIdentity `json:"identity"`
}

type refreshWriter struct {
	writer io.Writer
	budget ports.ResearchBudget
	bytes  int64
}

func (w *refreshWriter) Write(p []byte) (int, error) {
	if e := w.budget.Output(int64(len(p))); e != nil {
		return 0, e
	}
	n, e := w.writer.Write(p)
	w.bytes += int64(n)
	return n, e
}

// ExportRefreshHistory uses precisely the research resolver and revision
// selector, with actual source clocks at this prospective build clock. Every
// history row has already passed its storage adapter's raw/projection checks.
// Complete required windows are never truncated to fit the output budget.
func ExportRefreshHistory(in c.InputRecord, history ports.HistoryReader, target, dir string, b ports.ResearchBudget) (RefreshHistoryProof, error) {
	var proof RefreshHistoryProof
	if b == nil {
		return proof, errors.New("REFRESH_HISTORY_BUDGET_REQUIRED")
	}
	if len(in.Raw) > 64<<20 {
		return proof, errors.New("FROZEN_INPUT_SIZE_LIMIT")
	}
	if e := b.Read(int64(len(in.Raw))); e != nil {
		return proof, e
	}
	if c.Hash(in.Raw) != in.RawHash {
		return proof, errors.New("INPUT_BYTES_CORRUPT")
	}
	f, e := ev.DecodeFrozenContext(b.Ctx(), in.Raw)
	if e != nil {
		return proof, e
	}
	if f.Input.Observation != in.Observation || f.Cutoff != in.Cutoff {
		return proof, errors.New("REFRESH_BASE_METADATA_MISMATCH")
	}
	if _, e = time.Parse("2006-01-02", target); e != nil || target <= f.Input.Observation {
		return proof, errors.New("REFRESH_BASE_MUST_PRECEDE_OBSERVATION")
	}
	if f.Input.Coverage != "PROSPECTIVE_CAPTURED" && f.Input.Coverage != "TEST_FIXTURE" {
		return proof, errors.New("REFRESH_BASE_PROSPECTIVE_COVERAGE_REQUIRED")
	}
	verified := time.Now().UTC()
	oldCutoff, e := time.Parse(time.RFC3339Nano, f.Cutoff)
	if e != nil || oldCutoff.After(verified) {
		return proof, errors.New("REFRESH_BASE_FUTURE_CUTOFF")
	}
	if history == nil || history.Identity() != f.Input.HistoryHash || f.Input.HistoryRef == "" {
		return proof, errors.New("REFRESH_AUTHENTICATED_HISTORY_REQUIRED")
	}
	reader, ok := history.(ports.HistoryIdentityReader)
	if !ok {
		return proof, errors.New("AUTHENTICATED_HISTORY_IDENTITIES_REQUIRED")
	}
	if e = history.VerifyIndex(); e != nil {
		return proof, e
	}
	parents := map[string]c.Row{}
	for _, r := range append(append([]c.Row{}, history.Receipts()...), f.Input.Receipts...) {
		id := r.Text("receipt_id")
		if old, ok := parents[id]; ok && c.RowHash(old) != c.RowHash(r) {
			return proof, errors.New("HISTORY_RECEIPT_IDENTITY_CONFLICT")
		}
		for _, field := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
			stamp, err := time.Parse(time.RFC3339Nano, r.Text(field))
			if err != nil || stamp.After(verified) {
				return proof, errors.New("REFRESH_FUTURE_OR_INVALID_PARENT_CLOCK")
			}
		}
		parents[id] = r
	}
	ids := []string{}
	for id := range parents {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	receipts := make([]c.Row, 0, len(ids))
	for _, id := range ids {
		receipts = append(receipts, parents[id])
	}
	resolver, e := ev.NewResolverContext(b.Ctx(), receipts, verified.Format(time.RFC3339Nano), f.Input.Coverage, f.Input.Observation, f.Input.Sessions)
	if e != nil {
		return proof, e
	}
	var securities []c.Row
	if e = json.Unmarshal(f.Input.Universe["securities"], &securities); e != nil {
		return proof, e
	}
	universe := map[string]bool{}
	for _, state := range securities {
		if id := state.Text("instrument_id"); id != "" {
			universe[id] = true
		}
	}
	ids = ids[:0]
	for id := range universe {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	if len(ids) == 0 || len(f.Input.Sessions) == 0 {
		return proof, errors.New("REFRESH_BASE_EMPTY_SCOPE")
	}
	// A legal frozen calendar can include announced future sessions. The
	// research window counts only sessions at or before its observation.
	sessions := make([]string, 0, len(f.Input.Sessions))
	for _, session := range f.Input.Sessions {
		if session <= f.Input.Observation {
			sessions = append(sessions, session)
		}
	}
	if len(sessions) == 0 || sessions[len(sessions)-1] != f.Input.Observation {
		return proof, errors.New("REFRESH_BASE_OBSERVATION_CALENDAR_REQUIRED")
	}
	if len(sessions) > 315 {
		sessions = sessions[len(sessions)-315:]
	}
	bridgeDir := filepath.Join(dir, "history-bridge")
	if e = os.Mkdir(bridgeDir, 0700); e != nil {
		return proof, e
	}
	proof = RefreshHistoryProof{Contract: RefreshHistoryContract, InputID: in.ID, InputHash: in.RawHash, HistoryRef: f.Input.HistoryRef, HistoryHash: f.Input.HistoryHash, Observation: f.Input.Observation, Cutoff: f.Cutoff, VerifiedAt: verified.Format(time.RFC3339Nano), Coverage: f.Input.Coverage, Sessions: sessions, Receipts: receipts, Files: []RefreshHistoryFile{}}
	inline := map[string][]refreshRow{}
	for i, row := range f.Input.Bars {
		if row.Text("session") > f.Input.Observation {
			return proof, errors.New("REFRESH_BASE_FUTURE_INLINE_BAR")
		}
		inline[row.Text("instrument_id")] = append(inline[row.Text("instrument_id")], refreshRow{row, c.RowHash(row), c.EvidenceIdentity{Origin: "FROZEN_INPUT", ParentHash: in.RawHash, Locator: fmt.Sprintf("/input/bars/%d", i)}})
	}
	for _, id := range ids {
		if e = b.Check(); e != nil {
			return proof, e
		}
		groups := map[string][]refreshRow{}
		consume := func(row c.Row, identity c.EvidenceIdentity) error {
			if e := b.Check(); e != nil {
				return e
			}
			if identity.Origin != "HISTORY_ROW" && identity.Origin != "FROZEN_INPUT" {
				return errors.New("REFRESH_HISTORY_SOURCE_IDENTITY_UNKNOWN")
			}
			if identity.Origin == "HISTORY_ROW" && (!ev.ValidHash(identity.RawSHA256) || identity.ParentHash != f.Input.HistoryHash) {
				return errors.New("REFRESH_HISTORY_RAW_BINDING_INVALID")
			}
			if identity.Origin == "FROZEN_INPUT" {
				index, err := strconv.Atoi(strings.TrimPrefix(identity.Locator, "/input/bars/"))
				if identity.ParentHash != in.RawHash || err != nil || index < 0 || index >= len(f.Input.Bars) || identity.Locator != fmt.Sprintf("/input/bars/%d", index) || c.RowHash(f.Input.Bars[index]) != c.RowHash(row) {
					return errors.New("REFRESH_INLINE_RAW_BINDING_INVALID")
				}
			}
			for _, field := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
				stamp, err := time.Parse(time.RFC3339Nano, row.Text(field))
				if err != nil || stamp.After(verified) {
					return errors.New("REFRESH_FUTURE_OR_INVALID_HISTORY_ROW_CLOCK")
				}
			}
			if row.Text("quality") == "INVALID" {
				return nil
			}
			if !resolver.Resolve(row, ev.Scope{Instrument: id, Session: row.Text("session"), RequireSession: true}).Allowed {
				return nil
			}
			groups[row.Text("session")] = append(groups[row.Text("session")], refreshRow{row, c.RowHash(row), identity})
			return nil
		}
		if e = reader.BarsWithIdentity(id, sessions[0], f.Input.Observation, consume); e != nil {
			return proof, e
		}
		for _, r := range inline[id] {
			if r.Row.Text("session") >= sessions[0] {
				if e = consume(r.Row, r.Identity); e != nil {
					return proof, e
				}
			}
		}
		fileName := c.Hash([]byte(id)) + ".jsonl.gz"
		file, e := os.OpenFile(filepath.Join(bridgeDir, fileName), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if e != nil {
			return proof, e
		}
		hash := sha256.New()
		writer := &refreshWriter{writer: io.MultiWriter(file, hash), budget: b}
		compressed := gzip.NewWriter(writer)
		encoder := json.NewEncoder(compressed)
		descriptor := RefreshHistoryFile{Instrument: id, File: "history-bridge/" + fileName, RawSessions: []string{}}
		writeErr := func() error {
			for _, session := range sessions {
				rows := groups[session]
				if len(rows) == 0 {
					continue
				}
				values := make([]c.Row, len(rows))
				for i := range rows {
					values[i] = rows[i].Row
				}
				chosen, err := ev.SelectRevisionIndex(values)
				if err != nil {
					return err
				}
				selected := rows[chosen]
				if _, err = facts.ParseBar(selected.Row); err != nil {
					return err
				}
				if err = encoder.Encode(selected); err != nil {
					return err
				}
				descriptor.Rows++
				complete := selected.Row.Text("quality") == "VALID"
				for _, field := range []string{"open", "high", "low", "close", "reference_prev_close", "amount_cny", "volume_shares", "turnover_fraction"} {
					complete = complete && selected.Row.Num(field).Known()
				}
				if complete {
					descriptor.RawSessions = append(descriptor.RawSessions, session)
				}
			}
			return nil
		}()
		writeErr = errors.Join(writeErr, compressed.Close(), file.Sync(), file.Close())
		if writeErr != nil {
			return proof, writeErr
		}
		descriptor.Bytes = writer.bytes
		descriptor.SHA256 = hex.EncodeToString(hash.Sum(nil))
		proof.Files = append(proof.Files, descriptor)
	}
	raw, e := json.Marshal(proof)
	if e != nil {
		return proof, e
	}
	if e = b.Output(int64(len(raw))); e != nil {
		return proof, e
	}
	proof.ManifestFile = "history-bridge/manifest.json"
	proof.ManifestHash = c.Hash(raw)
	file, e := os.OpenFile(filepath.Join(dir, proof.ManifestFile), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		return proof, e
	}
	_, e = file.Write(raw)
	e = errors.Join(e, file.Sync(), file.Close())
	return proof, e
}

// ComposeRefreshInput preserves original decimal lexemes and source clocks.
// The Python worker emits only new observations; Go adds retained inline rows
// and exact parents, retains the authenticated history reference, and validates
// the final envelope through the same evidence boundary as ordinary research.
func ComposeRefreshInput(base c.InputRecord, delta []byte, proof RefreshHistoryProof) ([]byte, error) {
	if base.RawHash != proof.InputHash || c.Hash(base.Raw) != proof.InputHash {
		return nil, errors.New("REFRESH_BASE_CHANGED")
	}
	before, e := ev.DecodeFrozen(base.Raw)
	if e != nil {
		return nil, e
	}
	after, e := ev.DecodeFrozen(delta)
	if e != nil {
		return nil, e
	}
	if proof.Contract != RefreshHistoryContract || before.Input.HistoryRef != proof.HistoryRef || before.Input.HistoryHash != proof.HistoryHash || after.Input.Coverage != before.Input.Coverage {
		return nil, errors.New("REFRESH_PROOF_OR_COVERAGE_IDENTITY_MISMATCH")
	}
	if after.Input.Observation <= before.Input.Observation || after.Input.HistoryRef != proof.HistoryRef || after.Input.HistoryHash != proof.HistoryHash {
		return nil, errors.New("REFRESH_HISTORY_LINEAGE_MISMATCH")
	}
	newClock, e := time.Parse(time.RFC3339Nano, after.Cutoff)
	if e != nil {
		return nil, e
	}
	verified, e := time.Parse(time.RFC3339Nano, proof.VerifiedAt)
	if e != nil || newClock.Before(verified) {
		return nil, errors.New("REFRESH_CUTOFF_PRECEDES_BASE_VERIFICATION")
	}
	for _, row := range after.Input.Bars {
		published, err := time.Parse(time.RFC3339Nano, row.Text("source_published_at"))
		if err != nil || !published.After(verified) {
			return nil, errors.New("REFRESH_NEW_BAR_REVISION_CLOCK_NOT_AFTER_BASE")
		}
		for _, field := range []string{"first_seen_at", "ingested_at"} {
			stamp, err := time.Parse(time.RFC3339Nano, row.Text(field))
			if err != nil || stamp.Before(verified) {
				return nil, errors.New("REFRESH_NEW_BAR_CAPTURE_CLOCK_NOT_AFTER_BASE")
			}
		}
	}
	var oldEnvelope, newEnvelope map[string]json.RawMessage
	if e = json.Unmarshal(base.Raw, &oldEnvelope); e != nil {
		return nil, e
	}
	if e = json.Unmarshal(delta, &newEnvelope); e != nil {
		return nil, e
	}
	var oldInput, newInput map[string]json.RawMessage
	_ = json.Unmarshal(oldEnvelope["input"], &oldInput)
	_ = json.Unmarshal(newEnvelope["input"], &newInput)
	for _, field := range []string{"bars", "receipts"} {
		var oldRows, newRows []json.RawMessage
		_ = json.Unmarshal(oldInput[field], &oldRows)
		_ = json.Unmarshal(newInput[field], &newRows)
		if field == "receipts" {
			known := map[string]string{}
			for _, r := range append(append([]json.RawMessage{}, oldRows...), newRows...) {
				var row c.Row
				if e = json.Unmarshal(r, &row); e != nil {
					return nil, e
				}
				id := row.Text("receipt_id")
				canonical, err := c.Canonical(r)
				if err != nil {
					return nil, err
				}
				if h, ok := known[id]; ok && h != c.Hash(canonical) {
					return nil, errors.New("REFRESH_PARENT_RECEIPT_CONFLICT")
				}
				known[id] = c.Hash(canonical)
			}
		}
		newInput[field], e = json.Marshal(append(oldRows, newRows...))
		if e != nil {
			return nil, e
		}
	}
	newEnvelope["input"], e = json.Marshal(newInput)
	if e != nil {
		return nil, e
	}
	newEnvelope["parent_input"], _ = json.Marshal(map[string]string{"namespace": "app_inputs", "external_id": base.ID, "raw_sha256": base.RawHash})
	newEnvelope["supplement"], _ = json.Marshal(map[string]any{"actual_capture_clock_preserved": true, "added_bar_revisions": len(after.Input.Bars), "normalization_report": "capture/capture-report.json"})
	raw, e := json.Marshal(newEnvelope)
	if e != nil {
		return nil, e
	}
	if len(raw) > 64<<20 {
		return nil, errors.New("FROZEN_INPUT_SIZE_LIMIT")
	}
	_, e = ev.DecodeFrozen(raw)
	return raw, e
}
