package core

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

type Manifest struct {
	Contract      string            `json:"contract"`
	Baseline      string            `json:"baseline"`
	SourceHash    string            `json:"go_source_hash"`
	BinaryHash    string            `json:"binary_hash"`
	ConfigHashes  map[string]string `json:"config_hashes"`
	InputHash     string            `json:"input_bytes_hash"`
	HistoryHash   string            `json:"history_hash"`
	Observation   string            `json:"observation_date"`
	Cutoff        string            `json:"cutoff"`
	BudgetSeconds float64           `json:"budget_seconds"`
	ProductRole   string            `json:"product_role"`
	Promotion     string            `json:"production_promotion"`
	MarketEdge    string            `json:"market_edge"`
}
type Completion struct {
	Contract         string            `json:"contract"`
	ResearchComplete bool              `json:"research_complete"`
	Files            map[string]string `json:"files"`
}
type budgetWriter struct {
	w io.Writer
	b *Budget
}

func (w budgetWriter) Write(p []byte) (int, error) {
	for offset := 0; offset < len(p); {
		end := offset + (64 << 10)
		if end > len(p) {
			end = len(p)
		}
		if e := w.b.Output(int64(end - offset)); e != nil {
			return offset, e
		}
		n, e := w.w.Write(p[offset:end])
		offset += n
		if e != nil {
			return offset, e
		}
		if n == 0 {
			return offset, io.ErrShortWrite
		}
	}
	return len(p), nil
}
func writeJSON(path string, value any, b *Budget) (string, error) {
	if e := b.Check(); e != nil {
		return "", e
	}
	tmp, e := os.CreateTemp(filepath.Dir(path), ".unpublished-")
	if e != nil {
		return "", e
	}
	name := tmp.Name()
	defer os.Remove(name)
	h := sha256.New()
	e = json.NewEncoder(budgetWriter{io.MultiWriter(tmp, h), b}).Encode(value)
	if e == nil {
		e = tmp.Sync()
	}
	ce := tmp.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return "", e
	}
	if e = b.Check(); e != nil {
		return "", e
	}
	if _, e = os.Lstat(path); !os.IsNotExist(e) {
		return "", errCode("OUTPUT_EXISTS")
	}
	if e = os.Rename(name, path); e != nil {
		return "", e
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// Output root must be created exclusively by the caller. There is deliberately
// no research completion.json until the migration acceptance blockers are closed.
func SaveDiagnostic(root string, d Diagnostic, m Manifest, b *Budget) error {
	b.Stage("save_and_verify")
	c := Completion{Contract: "GO_R1_DIAGNOSTIC_COMPLETION_V1", ResearchComplete: false, Files: map[string]string{}}
	detailHash, e := writeDetails(root, d, b)
	if e != nil {
		return e
	}
	c.Files["details.ndjson.gz"] = detailHash
	d.DetailFile = "details.ndjson.gz"
	d.DetailCount = len(d.Facts)
	d.Facts = nil
	d.Decisions = nil
	d.Metrics = b.Snapshot()
	values := map[string]any{"manifest.json": m, "result.json": d, "candidates.json": map[string]any{"candidate_availability": "UNAVAILABLE", "reason": "INCOMPLETE_IMPLEMENTATION", "candidates": nil}}
	for _, n := range []string{"manifest.json", "result.json", "candidates.json"} {
		h, e := writeJSON(filepath.Join(root, n), values[n], b)
		if e != nil {
			return e
		}
		c.Files[n] = h
	}
	text := "# R1 Go migration diagnostic\n\nResearch is INCOMPLETE_IMPLEMENTATION. Candidate list is UNAVAILABLE.\n\n" + "Observation: " + d.Observation + "; cutoff: " + d.Cutoff + ".\n\nBlockers:\n\n- " + strings.Join(d.Blockers, "\n- ") + "\n\nDiagnostic decisions are not an accepted R1 candidate list.\n"
	p := filepath.Join(root, "report.md")
	if e := b.Output(int64(len(text))); e != nil {
		return e
	}
	if e := os.WriteFile(p, []byte(text), 0600); e != nil {
		return e
	}
	c.Files["report.md"] = Hash([]byte(text))
	for n, h := range c.Files {
		raw, e := ReadBounded(filepath.Join(root, n), MaxOutputBytes, b)
		if e != nil {
			return e
		}
		if Hash(raw) != h {
			return errCode("OUTPUT_VERIFICATION_FAILED")
		}
	}
	if e := verifyDetails(root, d, b); e != nil {
		return e
	}
	if _, e := writeJSON(filepath.Join(root, "diagnostic-completion.json"), c, b); e != nil {
		return e
	}
	return b.Check()
}
func VerifyDiagnostic(root string, b *Budget) (Completion, error) {
	var c Completion
	raw, e := ReadBounded(filepath.Join(root, "diagnostic-completion.json"), 1<<20, b)
	if e != nil {
		return c, e
	}
	if e = json.Unmarshal(raw, &c); e != nil {
		return c, e
	}
	if c.Contract != "GO_R1_DIAGNOSTIC_COMPLETION_V1" || c.ResearchComplete || len(c.Files) != 5 {
		return c, errCode("INVALID_DIAGNOSTIC_COMPLETION")
	}
	for _, n := range []string{"manifest.json", "result.json", "candidates.json", "report.md", "details.ndjson.gz"} {
		h, ok := c.Files[n]
		if !ok {
			return c, errCode("MISSING_OUTPUT_REFERENCE")
		}
		p := filepath.Join(root, n)
		st, e := os.Lstat(p)
		if e != nil {
			return c, e
		}
		if !st.Mode().IsRegular() {
			return c, errCode("NONREGULAR_OUTPUT")
		}
		v, e := ReadBounded(p, MaxOutputBytes, b)
		if e != nil {
			return c, e
		}
		if Hash(v) != h {
			return c, fmt.Errorf("OUTPUT_HASH_MISMATCH: %s", n)
		}
	}
	var m Manifest
	v, e := ReadBounded(filepath.Join(root, "manifest.json"), 1<<20, b)
	if e != nil {
		return c, e
	}
	if e = json.Unmarshal(v, &m); e != nil {
		return c, e
	}
	var d Diagnostic
	v, e = ReadBounded(filepath.Join(root, "result.json"), MaxOutputBytes, b)
	if e != nil {
		return c, e
	}
	if e = json.Unmarshal(v, &d); e != nil {
		return c, e
	}
	if m.InputHash != d.InputBytesHash || m.HistoryHash != d.HistoryHash || m.Cutoff != d.Cutoff || m.Observation != d.Observation || d.CandidateAvailability != "UNAVAILABLE" || d.RunStatus != "INCOMPLETE_IMPLEMENTATION" {
		return c, errCode("RESULT_IDENTITY_CONFLICT")
	}
	if e = verifyDetails(root, d, b); e != nil {
		return c, e
	}
	return c, b.Check()
}
