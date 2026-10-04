package daily

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"reflect"
	"sort"
	"strings"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

type DiffItem struct {
	ID                         string `json:"instrument_id"`
	Change                     string `json:"change"`
	BeforeStatus               string `json:"before_status"`
	AfterStatus                string `json:"after_status"`
	BeforeSetups               string `json:"before_setups"`
	AfterSetups                string `json:"after_setups"`
	PriceFactsChanged          *bool  `json:"saved_price_facts_changed"`
	IdentityOrBenchmarkChanged *bool  `json:"saved_identity_or_relative_strength_changed"`
	EvidenceChanged            *bool  `json:"consumed_evidence_identity_changed"`
}
type SavedDiff struct {
	FromRun            string          `json:"from_run_id"`
	ToRun              string          `json:"to_run_id"`
	Verification       string          `json:"verification_scope"`
	EvidenceInspection string          `json:"evidence_inspection_scope"`
	FromImplementation json.RawMessage `json:"from_implementation"`
	ToImplementation   json.RawMessage `json:"to_implementation"`
	Factors            []string        `json:"observed_change_factors"`
	Attribution        string          `json:"attribution"`
	BeforeTotal        int             `json:"before_evaluated_total"`
	AfterTotal         int             `json:"after_evaluated_total"`
	Unchanged          int             `json:"unchanged_total"`
	OrderChanged       bool            `json:"member_order_changed"`
	Changes            []DiffItem      `json:"changes"`
}

func rawFields(raw json.RawMessage, keys ...string) []json.RawMessage {
	var object map[string]json.RawMessage
	_ = json.Unmarshal(raw, &object)
	fields := make([]json.RawMessage, len(keys))
	for i, key := range keys {
		fields[i] = object[key]
	}
	return fields
}
func observedFieldChange(before, after []json.RawMessage) *bool {
	unknown := false
	for i, left := range before {
		right := after[i]
		if len(left) == 0 || len(right) == 0 || string(left) == "null" || string(right) == "null" {
			unknown = true
			continue
		}
		if !reflect.DeepEqual(left, right) {
			changed := true
			return &changed
		}
	}
	if unknown {
		return nil
	}
	changed := false
	return &changed
}
func changeState(value *bool) string {
	if value == nil {
		return "UNKNOWN_MISSING_SAVED_FIELDS"
	}
	if *value {
		return "OBSERVED_CHANGED"
	}
	return "SAVED_FIELDS_EQUAL"
}
func implementationView(manifest map[string]json.RawMessage) json.RawMessage {
	v := map[string]json.RawMessage{}
	for _, key := range []string{"go_source_hash", "business_implementation_id", "facts_implementation_id", "config_hashes", "conversion_version", "daily_evidence_policy", "go_version"} {
		if value, ok := manifest[key]; ok {
			v[key] = value
		}
	}
	raw, _ := json.Marshal(v)
	return raw
}
func detailDiff(before, after c.Item) DiffItem {
	_, beforeStatus, beforeSetups := fields(before)
	_, afterStatus, afterSetups := fields(after)
	change := DiffItem{ID: after.ID, Change: "CHANGED", BeforeStatus: beforeStatus, AfterStatus: afterStatus, BeforeSetups: beforeSetups, AfterSetups: afterSetups}
	beforeFact := rawFields(before.Payload, "fact")[0]
	afterFact := rawFields(after.Payload, "fact")[0]
	beforeCalc := rawFields(beforeFact, "facts")[0]
	afterCalc := rawFields(afterFact, "facts")[0]
	change.PriceFactsChanged = observedFieldChange(append(rawFields(beforeFact, "amount_cny", "turnover_fraction", "CurrentMA20", "PriorMA20"), rawFields(beforeCalc, "Close", "Returns", "Path")...), append(rawFields(afterFact, "amount_cny", "turnover_fraction", "CurrentMA20", "PriorMA20"), rawFields(afterCalc, "Close", "Returns", "Path")...))
	change.IdentityOrBenchmarkChanged = observedFieldChange(append(rawFields(beforeFact, "name", "instrument_id"), rawFields(beforeCalc, "RS", "LegacyRS", "SectorRS1")...), append(rawFields(afterFact, "name", "instrument_id"), rawFields(afterCalc, "RS", "LegacyRS", "SectorRS1")...))
	change.EvidenceChanged = observedFieldChange(rawFields(beforeFact, "selected_evidence_hashes", "selected_evidence_bindings"), rawFields(afterFact, "selected_evidence_hashes", "selected_evidence_bindings"))
	return change
}

func compareSaved(ctx context.Context, before, after c.Run) (SavedDiff, error) {
	v := SavedDiff{FromRun: before.ID, ToRun: after.ID, Verification: "FULL_PUBLISHED_CLOSURE_PER_RUN", EvidenceInspection: "SAVED_RESULT_FIELDS_ONLY_RAW_INPUT_CATEGORIES_NOT_READ", BeforeTotal: len(before.Items), AfterTotal: len(after.Items), Factors: []string{}, Changes: []DiffItem{}}
	var left, right map[string]json.RawMessage
	if err := json.Unmarshal(before.Manifest, &left); err != nil {
		return v, err
	}
	if err := json.Unmarshal(after.Manifest, &right); err != nil {
		return v, err
	}
	v.FromImplementation, v.ToImplementation = implementationView(left), implementationView(right)
	changed := func(keys ...string) bool {
		for _, key := range keys {
			if !reflect.DeepEqual(left[key], right[key]) {
				return true
			}
		}
		return false
	}
	if changed("observation_date") {
		v.Factors = append(v.Factors, "OBSERVATION_SESSION_CHANGED_NEW_QUOTES_NOT_INDEPENDENTLY_ATTRIBUTED")
	}
	if changed("input_bytes_hash", "history_hash", "cutoff") {
		v.Factors = append(v.Factors, "INPUT_OR_HISTORY_OR_CUTOFF_CHANGED_LATE_OR_REVISED_EVIDENCE_NOT_SEPARATELY_PROVEN")
	}
	if changed("config_hashes") {
		v.Factors = append(v.Factors, "CONFIGURATION_IDENTITY_CHANGED")
	}
	if changed("business_implementation_id", "facts_implementation_id", "conversion_version", "daily_evidence_policy") || before.Strategy != after.Strategy {
		v.Factors = append(v.Factors, "BUSINESS_IMPLEMENTATION_OR_POLICY_CHANGED")
	}
	if changed("go_source_hash", "go_version") {
		v.Factors = append(v.Factors, "FULL_BUILD_PROVENANCE_CHANGED")
	}
	beforeByID := map[string]c.Item{}
	afterIDs := map[string]bool{}
	beforeOrder := []string{}
	afterOrder := []string{}
	for _, item := range before.Items {
		if _, duplicate := beforeByID[item.ID]; duplicate {
			return v, errors.New("DIFF_DUPLICATE_BEFORE_INSTRUMENT")
		}
		beforeByID[item.ID] = item
		beforeOrder = append(beforeOrder, item.ID)
	}
	price, identity, evidence := false, false, false
	for _, item := range after.Items {
		if err := ctx.Err(); err != nil {
			return v, err
		}
		if afterIDs[item.ID] {
			return v, errors.New("DIFF_DUPLICATE_AFTER_INSTRUMENT")
		}
		afterIDs[item.ID] = true
		afterOrder = append(afterOrder, item.ID)
		previous, exists := beforeByID[item.ID]
		if !exists {
			_, status, setups := fields(item)
			v.Changes = append(v.Changes, DiffItem{ID: item.ID, Change: "ADDED_TO_EVALUATED_SET", AfterStatus: status, AfterSetups: setups})
			continue
		}
		if reflect.DeepEqual(previous.Payload, item.Payload) {
			v.Unchanged++
			continue
		}
		change := detailDiff(previous, item)
		price = price || (change.PriceFactsChanged != nil && *change.PriceFactsChanged)
		identity = identity || (change.IdentityOrBenchmarkChanged != nil && *change.IdentityOrBenchmarkChanged)
		evidence = evidence || (change.EvidenceChanged != nil && *change.EvidenceChanged)
		v.Changes = append(v.Changes, change)
	}
	for _, item := range before.Items {
		if !afterIDs[item.ID] {
			_, status, setups := fields(item)
			v.Changes = append(v.Changes, DiffItem{ID: item.ID, Change: "REMOVED_FROM_EVALUATED_SET_REASON_NOT_INFERRED", BeforeStatus: status, BeforeSetups: setups})
		}
	}
	v.OrderChanged = !reflect.DeepEqual(beforeOrder, afterOrder)
	if price {
		v.Factors = append(v.Factors, "SAVED_PRICE_OR_VOLUME_FACTS_CHANGED")
	}
	if identity {
		v.Factors = append(v.Factors, "SAVED_IDENTITY_OR_RELATIVE_BENCHMARK_FACTS_CHANGED")
	}
	if evidence {
		v.Factors = append(v.Factors, "CONSUMED_EVIDENCE_IDENTITY_CHANGED")
	}
	if v.OrderChanged {
		v.Factors = append(v.Factors, "EVALUATED_MEMBERSHIP_OR_ORDER_CHANGED_QUALIFICATION_REASON_NOT_INFERRED")
	}
	v.Attribution = "NO_SINGLE_CAUSE_PROVEN_FROM_SAVED_RESULTS"
	if len(v.Factors) > 1 {
		v.Attribution = "MULTIPLE_CHANGES_NO_UNIQUE_CAUSAL_ATTRIBUTION"
	}
	if len(v.Changes) == 0 && !v.OrderChanged && len(v.Factors) == 0 {
		v.Attribution = "NO_SAVED_CHANGE"
	}
	sort.Slice(v.Changes, func(i, j int) bool { return v.Changes[i].ID < v.Changes[j].ID })
	return v, nil
}

func diffOutput(ctx context.Context, b backend, from, to, format, path string, out io.Writer) error {
	before, err := b.Saved(ctx, from, "", true)
	if err != nil {
		return err
	}
	after, err := b.Saved(ctx, to, "", true)
	if err != nil {
		return err
	}
	diff, err := compareSaved(ctx, before, after)
	if err != nil {
		return err
	}
	return withOutput(ctx, out, path, func(w io.Writer) error {
		switch format {
		case "json":
			return json.NewEncoder(w).Encode(diff)
		case "csv":
			writer := csv.NewWriter(w)
			if err := writer.Write([]string{"from_run_id", "to_run_id", "instrument_id_text", "change", "before_status", "after_status", "before_setups", "after_setups", "saved_price_facts_changed", "saved_identity_or_relative_strength_changed", "consumed_evidence_identity_changed", "attribution", "observed_change_factors"}); err != nil {
				return err
			}
			for _, item := range diff.Changes {
				if err := writer.Write([]string{textCell(from), textCell(to), "'" + item.ID, item.Change, textCell(item.BeforeStatus), textCell(item.AfterStatus), textCell(item.BeforeSetups), textCell(item.AfterSetups), changeState(item.PriceFactsChanged), changeState(item.IdentityOrBenchmarkChanged), changeState(item.EvidenceChanged), diff.Attribution, strings.Join(diff.Factors, ";")}); err != nil {
					return err
				}
			}
			writer.Flush()
			return writer.Error()
		case "table", "markdown", "md":
			if _, err := fmt.Fprintf(w, "保存结果差异：%s → %s\nverification_scope=%s\n评价集合：%d → %d；原样明细=%d；成员/顺序变化=%t\n归因边界：%s\n观察到的变化：%s\n新行情、晚到或修订、资格和基准变化均需原始证据进一步区分；这里不证明唯一原因。\n", from, to, diff.Verification, diff.BeforeTotal, diff.AfterTotal, diff.Unchanged, diff.OrderChanged, diff.Attribution, strings.Join(diff.Factors, "; ")); err != nil {
				return err
			}
			for _, item := range diff.Changes {
				if _, err := fmt.Fprintf(w, "%s %s：%s [%s] → %s [%s]；价格事实=%s；身份/相对基准事实=%s；证据身份=%s\n", item.ID, item.Change, item.BeforeStatus, item.BeforeSetups, item.AfterStatus, item.AfterSetups, changeState(item.PriceFactsChanged), changeState(item.IdentityOrBenchmarkChanged), changeState(item.EvidenceChanged)); err != nil {
					return err
				}
			}
			return nil
		default:
			return errors.New("FORMAT_MUST_BE_TABLE_JSON_CSV_OR_MARKDOWN")
		}
	})
}
