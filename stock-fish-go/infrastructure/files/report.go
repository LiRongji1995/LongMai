package files

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
	StrategyID               string            `json:"strategy_id"`
	SchemaVersion            string            `json:"database_schema_version,omitempty"`
	ConversionVersion        string            `json:"conversion_version,omitempty"`
	ImplementationAcceptance string            `json:"implementation_acceptance"`
	Contract                 string            `json:"contract"`
	Baseline                 string            `json:"baseline"`
	SourceHash               string            `json:"go_source_hash"`
	BinaryHash               string            `json:"binary_hash"`
	ConfigHashes             map[string]string `json:"config_hashes"`
	InputHash                string            `json:"input_bytes_hash"`
	HistoryHash              string            `json:"history_hash"`
	Observation              string            `json:"observation_date"`
	Cutoff                   string            `json:"cutoff"`
	BudgetSeconds            float64           `json:"budget_seconds"`
	ProductRole              string            `json:"product_role"`
	Promotion                string            `json:"production_promotion"`
	MarketEdge               string            `json:"market_edge"`
}
type Completion struct {
	ImplementationAcceptance string            `json:"implementation_acceptance"`
	Contract                 string            `json:"contract"`
	ResearchComplete         bool              `json:"research_complete"`
	Files                    map[string]string `json:"files"`
}
type budgetWriter struct {
	w io.Writer
	b Budget
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
func writeJSON(path string, value any, b Budget) (string, error) {
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
func SaveDiagnostic(root string, d Diagnostic, m Manifest, b Budget) error {
	return saveOutput(root, d, m, b, false)
}
func SaveResearch(root string, d Diagnostic, m Manifest, b Budget) error {
	if d.RunStatus != "SUCCEEDED" || len(d.Blockers) > 0 {
		return errCode("RESEARCH_COMPUTATION_INCOMPLETE")
	}
	return saveOutput(root, d, m, b, true)
}
func saveOutput(root string, d Diagnostic, m Manifest, b Budget, full bool) error {
	b.Stage("save_and_verify")
	c := Completion{Contract: "GO_R1_DIAGNOSTIC_COMPLETION_V1", ResearchComplete: false, Files: map[string]string{}}
	completionFile := "diagnostic-completion.json"
	if full {
		c.Contract = "GO_RESEARCH_COMPLETION_V2"
		c.ResearchComplete = true
		c.ImplementationAcceptance = d.ImplementationAcceptance
		completionFile = "completion.json"
	}
	candidates := []any{}
	for _, row := range d.Decisions {
		if row.CandidateStatus == "CORE_PASS" {
			candidates = append(candidates, row)
		}
	}
	for _, row := range d.V2 {
		if row.Status == "FOCUS" {
			candidates = append(candidates, row)
		}
	}
	detailHash, e := writeDetails(root, d, b)
	if e != nil {
		return e
	}
	c.Files["details.ndjson.gz"] = detailHash
	d.DetailFile = "details.ndjson.gz"
	d.DetailCount = len(d.Facts)
	d.Facts = nil
	d.Decisions = nil
	d.V2 = nil
	d.Metrics = b.Snapshot()
	candidateOutput := map[string]any{"candidate_availability": "UNAVAILABLE", "reason": "INCOMPLETE_IMPLEMENTATION", "candidates": nil}
	if full {
		candidateOutput = map[string]any{"candidate_availability": d.CandidateAvailability, "coverage": d.Coverage, "candidates": candidates}
	}
	values := map[string]any{"manifest.json": m, "result.json": d, "candidates.json": candidateOutput}
	for _, n := range []string{"manifest.json", "result.json", "candidates.json"} {
		h, e := writeJSON(filepath.Join(root, n), values[n], b)
		if e != nil {
			return e
		}
		c.Files[n] = h
	}
	text := "# R1 Go migration diagnostic\n\nResearch is INCOMPLETE_IMPLEMENTATION. Candidate list is UNAVAILABLE.\n\n" + "Observation: " + d.Observation + "; cutoff: " + d.Cutoff + ".\n\nBlockers:\n\n- " + strings.Join(d.Blockers, "\n- ") + "\n\nDiagnostic decisions are not an accepted R1 candidate list.\n"
	if full {
		text = "# StockFish Go 研究结果\n\n策略：" + d.StrategyID + "；观察日：" + d.Observation + "；资料截止：" + d.Cutoff + "。\n\n执行：" + d.RunStatus + "；候选：" + d.CandidateAvailability + "；总体：" + d.Coverage.Universe + "；选择可判定：" + d.Coverage.Selection + "；背景：" + d.Coverage.Background + "。\n\n实现验收：" + d.ImplementationAcceptance + "。参数尚未验证；MARKET_EDGE=NOT_ESTABLISHED。\n\n候选数：" + fmt.Sprint(len(candidates)) + "。逐股条件、全部形态、关键理由和证据引用见 details.ndjson.gz。\n"
	}
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
	if _, e := writeJSON(filepath.Join(root, completionFile), c, b); e != nil {
		return e
	}
	return b.Check()
}
func VerifyDiagnostic(root string, b Budget) (Completion, error) {
	return verifyOutput(root, b, false)
}
func VerifyResearch(root string, b Budget) (Completion, error) { return verifyOutput(root, b, true) }
func verifyOutput(root string, b Budget, full bool) (Completion, error) {
	var c Completion
	completionFile := "diagnostic-completion.json"
	if full {
		completionFile = "completion.json"
	}
	raw, e := ReadBounded(filepath.Join(root, completionFile), 1<<20, b)
	if e != nil {
		return c, e
	}
	if e = json.Unmarshal(raw, &c); e != nil {
		return c, e
	}
	if len(c.Files) != 5 || (!full && (c.Contract != "GO_R1_DIAGNOSTIC_COMPLETION_V1" || c.ResearchComplete)) || (full && (c.Contract != "GO_RESEARCH_COMPLETION_V2" || !c.ResearchComplete)) {
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
	if m.InputHash != d.InputBytesHash || m.HistoryHash != d.HistoryHash || m.Cutoff != d.Cutoff || m.Observation != d.Observation || (full && d.RunStatus != "SUCCEEDED") || (!full && (d.CandidateAvailability != "UNAVAILABLE" || d.RunStatus != "INCOMPLETE_IMPLEMENTATION")) {
		return c, errCode("RESULT_IDENTITY_CONFLICT")
	}
	if e = verifyDetails(root, d, b); e != nil {
		return c, e
	}
	return c, b.Check()
}
