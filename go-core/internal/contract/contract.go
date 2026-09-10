package contract

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"sort"
)

const Version = "FEATURE_EVIDENCE_CONTRACT_V1"

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

func ValidateFact(f Fact) error {
	if f.ContractVersion != Version {
		return errors.New("unsupported fact contract_version")
	}
	if f.Instrument == "" || f.Session == "" || f.FieldGroup == "" || f.Field == "" {
		return errors.New("fact missing logical identity")
	}
	if !allowedStatuses[f.Status] {
		return errors.New("invalid fact status")
	}
	if f.Status != "KNOWN" && (f.Value == false || f.Value == float64(0)) {
		return errors.New("unknown fact must not be coerced to false or zero")
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
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var fact Fact
		if err := json.Unmarshal(line, &fact); err != nil {
			return nil, err
		}
		if err := ValidateFact(fact); err != nil {
			return nil, err
		}
		facts = append(facts, fact)
	}
	return facts, scanner.Err()
}

func WriteFactsJSONL(writer io.Writer, facts []Fact) error {
	for _, fact := range facts {
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
	type row struct {
		Key            string `json:"key"`
		ValueJSON      string `json:"value_json"`
		Status         string `json:"status"`
		ProvenanceHash string `json:"provenance_hash"`
	}
	rows := make([]row, 0, len(facts))
	for _, fact := range facts {
		if err := ValidateFact(fact); err != nil {
			return "", err
		}
		prov, err := json.Marshal(fact.Provenance)
		if err != nil {
			return "", err
		}
		provHash := sha256.Sum256(prov)
		rows = append(rows, row{
			Key:            fact.Instrument + "|" + fact.Session + "|" + fact.FieldGroup + "|" + fact.Field,
			ValueJSON:      fact.ValueJSON,
			Status:         fact.Status,
			ProvenanceHash: hex.EncodeToString(provHash[:]),
		})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Key < rows[j].Key })
	raw, err := json.Marshal(rows)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:]), nil
}
