package contract

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"os"
	"reflect"
	"regexp"
	"sort"
	"time"
)

const Version = "FEATURE_EVIDENCE_CONTRACT_V1"
const SchemaRevision = "FEATURE_EVIDENCE_SCHEMA_R16_R1"

var allowedStatuses = map[string]bool{
	"KNOWN":          true,
	"UNKNOWN":        true,
	"NOT_APPLICABLE": true,
	"UNVERIFIED":     true,
}

type Manifest struct {
	ContractVersion      string                 `json:"contract_version"`
	SnapshotID           string                 `json:"snapshot_id"`
	AsOfSession          string                 `json:"as_of_session"`
	DecisionCutoff       string                 `json:"decision_cutoff"`
	CreatedAt            string                 `json:"created_at"`
	UniverseHash         string                 `json:"universe_hash"`
	CalendarRevision     string                 `json:"calendar_revision"`
	SourcePolicyRevision string                 `json:"source_policy_revision"`
	FeatureSpecRevision  string                 `json:"feature_spec_revision"`
	SourceManifestHash   string                 `json:"source_manifest_hash"`
	ProvenanceRootHash   string                 `json:"provenance_root_hash"`
	BuilderVersion       string                 `json:"builder_version"`
	CoverageSummary      map[string]interface{} `json:"coverage_summary"`
	FactRowCount         int                    `json:"fact_row_count"`
}

type Fact struct {
	SchemaRevision    string                 `json:"schema_revision"`
	Reason            *string                `json:"reason"`
	ExactValue        map[string]string      `json:"exact_value,omitempty"`
	ContractVersion   string                 `json:"contract_version"`
	Instrument        string                 `json:"instrument"`
	Exchange          string                 `json:"exchange"`
	Session           string                 `json:"session"`
	Eligibility       string                 `json:"eligibility"`
	EligibilityReason *string                `json:"eligibility_reason"`
	FieldGroup        string                 `json:"field_group"`
	Field             string                 `json:"field"`
	Value             interface{}            `json:"value"`
	ValueJSON         string                 `json:"value_json"`
	Status            string                 `json:"status"`
	Unit              *string                `json:"unit"`
	Provenance        map[string]interface{} `json:"provenance"`
	SourceLabel       string                 `json:"source_label,omitempty"`
}

func ValidateManifest(manifest Manifest) error {
	if manifest.ContractVersion != Version {
		return errors.New("unsupported contract_version")
	}
	if manifest.SnapshotID == "" || manifest.AsOfSession == "" || manifest.DecisionCutoff == "" || manifest.CreatedAt == "" {
		return errors.New("manifest missing identity fields")
	}
	if manifest.FeatureSpecRevision == "" || manifest.SourceManifestHash == "" || manifest.ProvenanceRootHash == "" {
		return errors.New("manifest missing revision fields")
	}
	return nil
}

func VerifiedProvenance(prov map[string]interface{}) bool {
	if prov["status"] != "KNOWN" || prov["pit_status"] != "VISIBLE" {
		return false
	}
	refs := reflect.ValueOf(prov["source_refs"])
	if !refs.IsValid() || (refs.Kind() != reflect.Slice && refs.Kind() != reflect.Array) || refs.Len() == 0 {
		return false
	}
	for index := 0; index < refs.Len(); index++ {
		ref, ok := refs.Index(index).Interface().(string)
		if !ok || ref == "" {
			return false
		}
	}
	revision, _ := prov["source_revision_id"].(string)
	manifest, _ := prov["source_manifest_hash"].(string)
	if revision == "" && manifest == "" {
		return false
	}
	visible, ok := prov["visible_at"].(string)
	if !ok {
		return false
	}
	cutoff, ok := prov["decision_cutoff"].(string)
	if !ok {
		return false
	}
	v, e1 := time.Parse(time.RFC3339Nano, visible)
	c, e2 := time.Parse(time.RFC3339Nano, cutoff)
	return e1 == nil && e2 == nil && !v.After(c)
}

var decimalSyntax = regexp.MustCompile(`^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$`)

func numeric(value interface{}) (*big.Rat, error) {
	if _, ok := value.(bool); ok {
		return nil, fmt.Errorf("boolean is not numeric")
	}
	var text string
	switch n := value.(type) {
	case string:
		text = n
	case json.Number:
		text = string(n)
	default:
		raw, err := json.Marshal(value)
		if err != nil {
			return nil, err
		}
		text = string(raw)
	}
	if !decimalSyntax.MatchString(text) {
		return nil, errors.New("invalid JSON decimal syntax")
	}
	rat, ok := new(big.Rat).SetString(text)
	if !ok {
		return nil, fmt.Errorf("invalid finite numeric value")
	}
	return rat, nil
}
func roundEven(r *big.Rat, places int) *big.Rat {
	scale := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(places)), nil)
	n := new(big.Int).Mul(r.Num(), scale)
	negative := n.Sign() < 0
	n.Abs(n)
	q, remainder := new(big.Int), new(big.Int)
	q.QuoRem(n, r.Denom(), remainder)
	comparison := new(big.Int).Lsh(remainder, 1).Cmp(r.Denom())
	if comparison > 0 || comparison == 0 && q.Bit(0) == 1 {
		q.Add(q, big.NewInt(1))
	}
	if negative {
		q.Neg(q)
	}
	return new(big.Rat).SetFrac(q, scale)
}
func ValidateFact(f Fact) error {
	if f.ContractVersion != Version || f.SchemaRevision != SchemaRevision {
		return errors.New("unsupported fact contract/schema revision")
	}
	if f.Instrument == "" || f.FieldGroup == "" || f.Field == "" || f.Eligibility == "" {
		return errors.New("fact missing logical identity")
	}
	if _, err := time.Parse("2006-01-02", f.Session); err != nil {
		return err
	}
	if f.Exchange != "SSE" && f.Exchange != "SZSE" && f.Exchange != "BSE" && f.Exchange != "UNKNOWN" {
		return errors.New("invalid exchange")
	}
	if !allowedStatuses[f.Status] {
		return errors.New("invalid fact status")
	}
	if (f.Status == "UNKNOWN" || f.Status == "NOT_APPLICABLE") && f.Value != nil {
		return errors.New("unknown/not applicable must be null")
	}
	if f.Status == "KNOWN" && (f.Value == nil || !VerifiedProvenance(f.Provenance)) {
		return errors.New("known requires nonnull value and verified PIT provenance")
	}
	if f.Status != "KNOWN" && (f.Reason == nil || *f.Reason == "") {
		return errors.New("nonknown requires reason")
	}
	if f.Provenance == nil {
		return errors.New("missing provenance")
	}
	kind, unit := "", ""
	switch f.FieldGroup {
	case "PRICE":
		switch f.Field {
		case "open", "high", "low", "close", "prev_close", "comparable_price":
			kind = "number"
			unit = "CNY"
		case "price_basis":
			kind = "string"
		}
	case "RETURN":
		switch f.Field {
		case "ret1_pct", "ret3_pct", "ret5_pct", "ret10_pct", "ret20_pct":
			kind = "number"
			unit = "percent"
		}
	case "ROLLING":
		switch f.Field {
		case "MA20", "MA60", "MA120", "MA250":
			kind = "number"
			unit = "CNY"
		}
	case "ACTIVITY":
		switch f.Field {
		case "amount_cny":
			kind = "number"
			unit = "CNY"
		case "turnover_fraction":
			kind = "number"
			unit = "fraction"
		case "A":
			kind = "number"
			unit = "ratio"
		case "activity":
			kind = "string"
		}
	case "EVENT":
		switch f.Field {
		case "limit_event_status":
			kind = "string"
		case "close_limit_up", "touched_limit_up", "close_limit_down":
			kind = "boolean"
		}
	case "MARKET":
		switch f.Field {
		case "relative_strength_status", "market_context_state", "market_compatibility_status":
			kind = "string"
		case "RS_market_1d_pct":
			kind = "number"
			unit = "percent"
		}
	case "SECTOR":
		switch f.Field {
		case "sector_status":
			kind = "string"
		case "RS_sector_1d_pct":
			kind = "number"
			unit = "percent"
		}
	case "CAPITAL":
		switch f.Field {
		case "funds_status", "capital_evidence_status":
			kind = "string"
		}
	}
	if kind == "" {
		return errors.New("unsupported field")
	}
	if unit == "" && f.Unit != nil || unit != "" && (f.Unit == nil || *f.Unit != unit) {
		return errors.New("invalid unit")
	}
	var parsed interface{}
	decoder := json.NewDecoder(bytes.NewBufferString(f.ValueJSON))
	decoder.UseNumber()
	if err := decoder.Decode(&parsed); err != nil {
		return fmt.Errorf("invalid value_json: %w", err)
	}
	var trailing interface{}
	if err := decoder.Decode(&trailing); err != io.EOF {
		return errors.New("trailing value_json content")
	}
	if f.Value == nil {
		if parsed != nil {
			return errors.New("value/value_json conflict")
		}
	} else if kind == "number" {
		left, err := numeric(f.Value)
		if err != nil {
			return err
		}
		right, err := numeric(parsed)
		if err != nil {
			return err
		}
		if left.Cmp(right) != 0 {
			return errors.New("value/value_json conflict")
		}
		// JSON strings and numbers are different representations, though decimal
		// strings themselves are allowed as an exact input representation.
		_, leftString := f.Value.(string)
		_, rightString := parsed.(string)
		if leftString != rightString {
			return errors.New("value/value_json type conflict")
		}
		if (f.FieldGroup == "PRICE" || f.FieldGroup == "ROLLING") && left.Sign() <= 0 {
			return errors.New("non-positive price")
		}
		if f.ExactValue != nil {
			n, ok1 := new(big.Int).SetString(f.ExactValue["numerator"], 10)
			d, ok2 := new(big.Int).SetString(f.ExactValue["denominator"], 10)
			if !ok1 || !ok2 || d.Sign() <= 0 || len(f.ExactValue) != 2 {
				return errors.New("invalid exact rational")
			}
			r := new(big.Rat).SetFrac(n, d)
			if r.Num().String() != f.ExactValue["numerator"] || r.Denom().String() != f.ExactValue["denominator"] {
				return errors.New("noncanonical exact rational")
			}
			places := 6
			if f.FieldGroup == "PRICE" {
				places = 8
			}
			if f.FieldGroup == "RETURN" {
				places = 4
			}
			if roundEven(r, places).Cmp(left) != 0 {
				return errors.New("exact/display mismatch")
			}
		}
	} else {
		if kind == "string" {
			if _, ok := f.Value.(string); !ok {
				return errors.New("invalid string")
			}
		}
		if kind == "boolean" {
			if _, ok := f.Value.(bool); !ok {
				return errors.New("invalid boolean")
			}
		}
		if !reflect.DeepEqual(f.Value, parsed) {
			return errors.New("value/value_json conflict")
		}
	}
	if f.ExactValue != nil && (f.Value == nil || kind != "number") {
		return errors.New("exact requires numeric value")
	}
	return nil
}

func ReadFactsJSONL(path string) ([]Fact, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	return DecodeFactsJSONL(file)
}

func DecodeFactsJSONL(reader io.Reader) ([]Fact, error) {
	scanner := bufio.NewScanner(reader)
	buf := make([]byte, 1024*1024)
	scanner.Buffer(buf, 16*1024*1024)
	var facts []Fact
	seen := map[string]bool{}
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var fact Fact
		if err := DecodeJSONStrict(line, &fact); err != nil {
			return nil, err
		}
		if err := ValidateFact(fact); err != nil {
			return nil, err
		}
		key := fact.Instrument + "|" + fact.Session + "|" + fact.FieldGroup + "|" + fact.Field
		if seen[key] {
			return nil, errors.New("duplicate logical key")
		}
		seen[key] = true
		facts = append(facts, fact)
	}
	return facts, scanner.Err()
}

func WriteFactsJSONL(writer io.Writer, facts []Fact) error {
	seen := map[string]bool{}
	for _, fact := range facts {
		key := fact.Instrument + "|" + fact.Session + "|" + fact.FieldGroup + "|" + fact.Field
		if seen[key] {
			return errors.New("duplicate logical key")
		}
		seen[key] = true
		if err := ValidateFact(fact); err != nil {
			return err
		}
		raw, err := json.Marshal(fact)
		if err != nil {
			return err
		}
		if _, err := writer.Write(append(raw, '\n')); err != nil {
			return err
		}
	}
	return nil
}

func NormalizedHash(facts []Fact) (string, error) {
	var buffer bytes.Buffer
	if err := WriteFactsJSONL(&buffer, facts); err != nil {
		return "", err
	}
	rows := append([]Fact(nil), facts...)
	for i := range rows {
		rows[i].SourceLabel = ""
		raw, _ := json.Marshal(rows[i].Value)
		rows[i].ValueJSON = string(raw)
	}
	sort.Slice(rows, func(i, j int) bool {
		return rows[i].Instrument+rows[i].Session+rows[i].FieldGroup+rows[i].Field < rows[j].Instrument+rows[j].Session+rows[j].FieldGroup+rows[j].Field
	})
	raw, err := json.Marshal(rows)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:]), nil
}
