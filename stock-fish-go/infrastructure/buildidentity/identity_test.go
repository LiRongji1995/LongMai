package buildidentity

import (
	"encoding/json"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"strings"
	"testing"
)

func TestCompatibilityRequiresEveryBusinessBoundary(t *testing.T) {
	source, business := strings.Repeat("a", 64), strings.Repeat("b", 64)
	config := map[string]string{"frozen": strings.Repeat("c", 64)}
	base := map[string]any{"go_source_hash": source, "business_implementation_id": business, "config_hashes": config, "conversion_version": evidence.InputContractVersion, "daily_evidence_policy": Policy}
	check := func(m map[string]any) bool {
		raw, _ := json.Marshal(m)
		return MatchesIdentity(raw, strings.Repeat("d", 64), config, business)
	}
	if !check(base) {
		t.Fatal("different full build with identical business should reuse")
	}
	for key, value := range map[string]any{"go_source_hash": "", "business_implementation_id": "", "conversion_version": "OTHER", "daily_evidence_policy": "OTHER", "config_hashes": map[string]string{"frozen": "OTHER"}} {
		m := map[string]any{}
		for k, v := range base {
			m[k] = v
		}
		m[key] = value
		if check(m) {
			t.Fatalf("unsafe compatibility for changed %s", key)
		}
	}
	raw, _ := json.Marshal(base)
	if MatchesIdentity(raw, "UNRECORDED", config, business) || MatchesIdentity(raw, source, config, "UNRECORDED") || MatchesIdentity(raw, source, config, strings.Repeat("e", 64)) {
		t.Fatal("unknown or changed build reused")
	}
}
