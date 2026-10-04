package daily

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/calendar"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type Saved struct {
	Target         string `json:"target_session"`
	TargetStatus   string `json:"target_derivation"`
	SelectionState string `json:"selection_state"`
	Protocol       string `json:"protocol"`
	History        bool   `json:"explicit_history"`
	Integrity      string `json:"integrity"`
	Total          int    `json:"selected_total"`
	Shown          int    `json:"shown"`
	Truncated      bool   `json:"truncated"`
	Run            c.Run  `json:"run"`
}

func savedOutput(ctx context.Context, b backend, id, instrument string, history, all bool, limit int, format, output string, out io.Writer) error {
	r, e := b.Saved(ctx, id, instrument, all)
	if e != nil {
		return e
	}
	v := Saved{Protocol: Protocol, History: history, Integrity: "VERIFIED_SAME_READ_TRANSACTION", Run: r, Total: len(r.Items)}
	var manifest struct {
		Observation string `json:"observation_date"`
		Target      string `json:"target_session"`
		Hash        string `json:"input_bytes_hash"`
	}
	_ = json.Unmarshal(r.Manifest, &manifest)
	ports.BindMetrics(ports.BudgetFromContext(ctx), r.ID, r.InputID, manifest.Hash, "")
	v.Target = manifest.Target
	v.TargetStatus = "SAVED_MANIFEST"
	if v.Target == "" {
		v.TargetStatus = "UNAVAILABLE_IN_FINITE_CALENDAR"
		if day, e := time.Parse(time.RFC3339, manifest.Observation+"T15:01:00+08:00"); e == nil {
			if plan, e := calendar.Official().Plan(day); e == nil {
				v.Target = plan["target_session"].(string)
				v.TargetStatus = "DERIVED_FROM_SAVED_OBSERVATION_OFFICIAL_CALENDAR"
			}
		}
	}
	var summary struct {
		Coverage struct {
			Evaluation string `json:"selection_evaluation_status"`
			Required   string `json:"required_data_coverage"`
		} `json:"coverage"`
	}
	_ = json.Unmarshal(r.Summary, &summary)
	v.SelectionState = "CONFIRMED_CANDIDATES"
	if len(r.Items) == 0 {
		v.SelectionState = "INSUFFICIENT_REQUIRED_DATA"
		if summary.Coverage.Evaluation == "EVALUABLE" {
			v.SelectionState = "LEGAL_ZERO_CANDIDATES"
		}
	} else if summary.Coverage.Evaluation != "EVALUABLE" {
		v.SelectionState = "PARTIAL_CANDIDATE_SUBSET"
	}
	if r.Strategy == c.R1 && len(r.Items) > 0 {
		v.SelectionState = "CORE_PASS_RESEARCH_SUBSET"
	}
	if all {
		v.SelectionState = "ALL_EVALUATED_SECURITIES"
	}
	if instrument != "" {
		v.SelectionState = "SAVED_INSTRUMENT_DETAIL"
	}
	if output != "" {
		limit = 0
	}
	if limit > 0 && len(r.Items) > limit {
		v.Run.Items = r.Items[:limit]
		v.Truncated = true
	}
	v.Shown = len(v.Run.Items)
	return withOutput(ctx, out, output, func(w io.Writer) error { return renderSaved(w, v, format, instrument != "") })
}
func textCell(value string) string {
	if strings.HasPrefix(value, "'") {
		return "'" + value
	}
	trimmed := strings.TrimLeft(value, " \t\r\n")
	if len(trimmed) > 0 && strings.ContainsAny(trimmed[:1], "=+-@") {
		return "'" + value
	}
	return value
}
func fields(item c.Item) (string, string, string) {
	var v struct {
		Fact struct {
			Name string `json:"name"`
		} `json:"fact"`
		Decision struct {
			Candidate string          `json:"candidate_status"`
			Status    string          `json:"decision_status"`
			Setups    json.RawMessage `json:"matched_setups"`
		} `json:"decision"`
	}
	_ = json.Unmarshal(item.Payload, &v)
	status := v.Decision.Candidate
	if status == "" {
		status = v.Decision.Status
	}
	return v.Fact.Name, status, string(v.Decision.Setups)
}
func renderSaved(w io.Writer, v Saved, format string, explain bool) error {
	switch format {
	case "json":
		return json.NewEncoder(w).Encode(v)
	case "csv":
		var manifest map[string]any
		_ = json.Unmarshal(v.Run.Manifest, &manifest)
		csvw := csv.NewWriter(w)
		_ = csvw.Write([]string{"run_id", "instrument_id_text", "name", "status", "all_matched_setups", "saved_detail_json", "input_id", "strategy_id", "observation_date", "cutoff", "target_session", "generated_at", "selection_state", "saved_summary_json"})
		for _, i := range v.Run.Items {
			name, status, setups := fields(i)
			if e := csvw.Write([]string{textCell(v.Run.ID), "'" + i.ID, textCell(name), textCell(status), textCell(setups), textCell(string(i.Payload)), textCell(v.Run.InputID), textCell(v.Run.Strategy), textCell(fmt.Sprint(manifest["observation_date"])), textCell(fmt.Sprint(manifest["cutoff"])), textCell(v.Target), textCell(v.Run.CreatedAt), textCell(v.SelectionState), textCell(string(v.Run.Summary))}); e != nil {
				return e
			}
		}
		csvw.Flush()
		return csvw.Error()
	case "table", "markdown", "md":
		var manifest map[string]any
		_ = json.Unmarshal(v.Run.Manifest, &manifest)
		if _, e := fmt.Fprintf(w, "run=%s strategy=%s history=%t integrity=%s\nobservation=%v cutoff=%v generated=%s availability=%s\nRESEARCH_ONLY · production_promotion=DISABLED · MARKET_EDGE=NOT_ESTABLISHED\nshown=%d selected_total=%d truncated=%t\n", v.Run.ID, v.Run.Strategy, v.History, v.Integrity, manifest["observation_date"], manifest["cutoff"], v.Run.CreatedAt, v.Run.CandidateAvailability, v.Shown, v.Total, v.Truncated); e != nil {
			return e
		}
		var summary map[string]json.RawMessage
		_ = json.Unmarshal(v.Run.Summary, &summary)
		if coverage, ok := summary["coverage"]; ok {
			if _, e := fmt.Fprintf(w, "saved_coverage=%s\n", coverage); e != nil {
				return e
			}
		}
		if v.Total == 0 {
			if _, e := io.WriteString(w, "No selected candidates in this saved run; consult saved coverage before interpreting an empty list.\n"); e != nil {
				return e
			}
		}
		if _, e := fmt.Fprintf(w, "target=%s target_basis=%s selection_state=%s\n", v.Target, v.TargetStatus, v.SelectionState); e != nil {
			return e
		}
		if format == "markdown" || format == "md" {
			if _, e := io.WriteString(w, "\n| Instrument | Name | Saved status | All setups |\n|---|---|---|---|\n"); e != nil {
				return e
			}
		}
		for _, i := range v.Run.Items {
			name, status, setups := fields(i)
			var line string
			if format == "table" {
				line = fmt.Sprintf("%s\t%s\t%s\t%s\n", i.ID, name, status, setups)
			} else {
				escape := func(s string) string { return strings.NewReplacer("|", "\\|", "\n", " ", "\r", " ").Replace(s) }
				line = fmt.Sprintf("| %s | %s | %s | %s |\n", escape(i.ID), escape(name), escape(status), escape(setups))
			}
			if _, e := io.WriteString(w, line); e != nil {
				return e
			}
			if explain {
				var detail any
				if e := json.Unmarshal(i.Payload, &detail); e != nil {
					return e
				}
				raw, e := json.MarshalIndent(detail, "", "  ")
				if e != nil {
					return e
				}
				if _, e = w.Write(append(raw, '\n')); e != nil {
					return e
				}
			}
		}
		return nil
	default:
		return errors.New("FORMAT_MUST_BE_TABLE_JSON_CSV_OR_MARKDOWN")
	}
}
func outputValue(ctx context.Context, out io.Writer, path, format string, v any) error {
	return withOutput(ctx, out, path, func(w io.Writer) error {
		if format != "json" && format != "table" && format != "markdown" && format != "md" {
			return errors.New("THIS_COMMAND_SUPPORTS_TABLE_JSON_MARKDOWN")
		}
		enc := json.NewEncoder(w)
		if format != "json" {
			enc.SetIndent("", "  ")
		}
		return enc.Encode(v)
	})
}
func withOutput(ctx context.Context, out io.Writer, path string, write func(io.Writer) error) error {
	if path == "" {
		return write(&limitedOutput{writer: contextOutput{ctx, out}})
	}
	absolute, e := filepath.Abs(path)
	if e != nil {
		return e
	}
	if _, e = os.Stat(absolute); e == nil {
		return errors.New("OUTPUT_EXISTS_NO_OVERWRITE")
	}
	f, e := os.CreateTemp(filepath.Dir(absolute), ".stockfish-export-*")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if e = write(&limitedOutput{writer: f}); e != nil {
		return e
	}
	if e = ctx.Err(); e != nil {
		return e
	}
	if e = f.Sync(); e != nil {
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	return os.Link(f.Name(), absolute)
}

type contextOutput struct {
	ctx    context.Context
	writer io.Writer
}

func (w contextOutput) Write(p []byte) (int, error) {
	if e := w.ctx.Err(); e != nil {
		return 0, e
	}
	if sink, ok := w.writer.(interface {
		WriteContext(context.Context, []byte) (int, error)
	}); ok {
		return sink.WriteContext(w.ctx, p)
	}
	return w.writer.Write(p)
}

type limitedOutput struct {
	writer io.Writer
	bytes  int64
}

func (w *limitedOutput) Write(p []byte) (int, error) {
	if w.bytes+int64(len(p)) > 128<<20 {
		return 0, errors.New("OUTPUT_BYTES_BUDGET_EXCEEDED")
	}
	n, e := w.writer.Write(p)
	w.bytes += int64(n)
	return n, e
}
