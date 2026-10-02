package sqlitev2

import (
	"encoding/json"
	"fmt"
	"regexp"
)

var decimalText = regexp.MustCompile(`^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$`)

// Projection columns intentionally use exact decimal text, but invalid JSON
// types must not become indistinguishable from genuine booleans or strings.
func validateProjectionTypes(o map[string]json.RawMessage) error {
	for _, k := range []string{"open", "high", "low", "close", "amount_cny", "volume_shares", "turnover_fraction", "comparable_open", "comparable_high", "comparable_low", "comparable_close", "limit_up", "limit_down", "reference_prev_close"} {
		v := o[k]
		if len(v) == 0 || string(v) == "null" {
			continue
		}
		s := fmt.Sprint(scalar(v))
		if !decimalText.MatchString(s) {
			return fmt.Errorf("INVALID_EXACT_DECIMAL_PROJECTION: %s", k)
		}
	}
	for _, k := range []string{"limit_verified", "limit_applicable", "st", "suspended", "delisting_period"} {
		v := o[k]
		if len(v) == 0 || string(v) == "null" {
			continue
		}
		if string(v) != "true" && string(v) != "false" {
			return fmt.Errorf("INVALID_BOOLEAN_PROJECTION: %s", k)
		}
	}
	for _, k := range []string{"comparable_basis", "quality", "event_time", "source_published_at", "first_seen_at", "ingested_at", "effective_from", "effective_to", "listed_on", "exchange", "board", "security_type"} {
		v := o[k]
		if len(v) == 0 || string(v) == "null" {
			continue
		}
		var s string
		if e := json.Unmarshal(v, &s); e != nil {
			return fmt.Errorf("INVALID_STRING_PROJECTION: %s", k)
		}
	}
	if v := o["source_refs"]; len(v) > 0 && string(v) != "null" {
		var refs []string
		if e := json.Unmarshal(v, &refs); e != nil {
			return fmt.Errorf("INVALID_SOURCE_REFERENCES_PROJECTION: %w", e)
		}
	}
	return nil
}
