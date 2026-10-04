package research

import (
	"encoding/json"
	"sort"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

// factCacheKeyWithIdentity runs only after index/raw validation, current qualification,
// source visibility and revision selection. It reuses stock-local arithmetic;
// current evidence bindings/rejections and all population benchmarks, ranks,
// backgrounds and decisions are reconstructed on every invocation.
//
// Cutoff and enclosing input/history hashes are not arithmetic dependencies
// after re-resolution. Each selected raw row/projection and its current parent
// receipt is instead bound independently, so an unrelated late receipt need
// not invalidate all securities. A historical raw-byte revision still changes
// the key even if its decoded projection happens to remain equal.
func factCacheKeyWithIdentity(algorithm string, frozen Frozen, inputHash, instrument string, state Row, sessions []string, selected []Row, bindings []c.SelectedEvidenceBinding, resolver *Resolver) (string, string) {
	parents := map[string]string{}
	rows := make([]string, 0, len(selected))
	for _, row := range append(append([]Row{}, selected...), state) {
		for _, ref := range row.Strings("source_refs") {
			parents[ref] = RowHash(resolver.Receipts[ref])
		}
	}
	for _, row := range selected {
		rows = append(rows, RowHash(row))
	}
	sort.Strings(rows)
	raw := make([]string, 0, len(bindings))
	for _, binding := range bindings {
		if binding.Identity.RawSHA256 != "" {
			raw = append(raw, binding.ProjectionHash+":"+binding.Identity.RawSHA256)
		}
	}
	sort.Strings(raw)
	identity := map[string]any{
		"contract":  "RESOLVED_STOCK_FACT_ARITHMETIC_DEPENDENCIES_V1",
		"algorithm": algorithm, "instrument": instrument, "security_state": RowHash(state),
		"observation": frozen.Input.Observation, "sessions": sessions,
		"coverage_policy": frozen.Input.Coverage, "selected_rows": rows,
		"selected_raw_bytes": raw, "consumed_receipts": parents,
	}
	scope := "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT"
	// Builds lacking an audited calculation identity retain the old same-input
	// boundary. They cannot claim cross-input compatibility merely by version.
	if !validHash(algorithm) {
		identity["input"] = inputHash
		identity["history"] = frozen.Input.HistoryHash
		identity["cutoff"] = frozen.Cutoff
		scope = "SAME_INPUT_ONLY_UNRECORDED_ALGORITHM"
	}
	encoded, _ := json.Marshal(identity)
	return Hash(encoded), scope
}
