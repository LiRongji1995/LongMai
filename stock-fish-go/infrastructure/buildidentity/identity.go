// Package buildidentity binds reuse to the audited business source manifest
// produced by tools/build.py. Full build provenance remains a separate identity.
package buildidentity

import (
	"encoding/hex"
	"encoding/json"
	"reflect"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
)

var businessHash = "UNRECORDED_BUSINESS_IMPLEMENTATION"
var factsHash = "UNRECORDED_FACTS_IMPLEMENTATION"

const Policy = "FOUR_CLOCK_SCOPE_REVISION_V2"

func Current() string { return businessHash }
func Facts() string   { return factsHash }
func Recorded(s string) bool {
	decoded, err := hex.DecodeString(s)
	return err == nil && len(decoded) == 32 && len(s) == 64
}

// Matches never upgrades a historical manifest. Unknown legacy builds require
// explicit historical queries; no automatic compatibility registrations exist.
func Matches(raw []byte, source string, config map[string]string) bool {
	return MatchesIdentity(raw, source, config, Current())
}

// MatchesIdentity permits isolated fixture identities without mutable globals.
// Production callers use Matches and its linker-bound manifest identity.
func MatchesIdentity(raw []byte, source string, config map[string]string, business string) bool {
	var m struct {
		Source     string            `json:"go_source_hash"`
		Business   string            `json:"business_implementation_id"`
		Config     map[string]string `json:"config_hashes"`
		Conversion string            `json:"conversion_version"`
		Policy     string            `json:"daily_evidence_policy"`
	}
	if json.Unmarshal(raw, &m) != nil {
		return false
	}
	return Recorded(source) && Recorded(m.Source) && Recorded(business) &&
		m.Business == business && m.Conversion == evidence.InputContractVersion &&
		m.Policy == Policy && reflect.DeepEqual(m.Config, config)
}
