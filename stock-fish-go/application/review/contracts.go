// Package review is an append-only consumer of saved decisions. It does not
// select stocks, train models, invoke providers, or promote production policy.
package review

import (
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"sort"
	"time"
)

const JournalVersion = "GO_REVIEW_JOURNAL_V1"
const MaxJournalBytes = 32 << 20

type Contract struct {
	Version            string `json:"version"`
	SignalAvailability string `json:"signal_availability"`
	Reference          string `json:"reference"`
	Basis              string `json:"price_basis"`
	Windows            []int  `json:"windows_exchange_sessions"`
	PrimaryWindow      int    `json:"primary_window"`
	Benchmark          string `json:"benchmark"`
	CostsBPS           []int  `json:"cost_sensitivity_bps"`
	Missing            string `json:"missing_policy"`
	Execution          string `json:"execution_policy"`
	Samples            string `json:"sample_policy"`
	Statistics         string `json:"statistics_policy"`
	MinSamples         int    `json:"minimum_samples"`
	MinDates           int    `json:"minimum_observation_dates"`
	MinBlocks          int    `json:"minimum_nonoverlapping_blocks"`
	EmbargoSessions    int    `json:"embargo_sessions"`
	FixtureOnly        bool   `json:"fixture_only"`
}

func DefaultContract() Contract {
	return Contract{Version: "GO_PRICE_REVIEW_V1", SignalAvailability: "VERIFIED_AVAILABLE_UPPER_BOUND; UNPROVEN_ORIGINAL_PUBLICATION_UNKNOWN; HISTORICAL_REPLAY_SEPARATE", Reference: "FROZEN_OBSERVATION_COMPARABLE_CLOSE", Basis: "EXACT_SAME_COMPARABLE_BASIS", Windows: []int{1, 3, 5, 10}, PrimaryWindow: 5, Benchmark: "FULL_REGISTERED_QUALIFIED_POOL_MEDIAN", CostsBPS: []int{0, 10, 30}, Missing: "NULL; NEVER_ZERO; IMMATURE_SEPARATE; APPEND_REVISIONS", Execution: "PRICE_OBSERVATION_ONLY; SUSPENSION_OR_UNKNOWN_CENSORED; NO_ASSUMED_FILLS", Samples: "ALL_EVALUATED_AND_QUALIFICATION_PARTITIONS; SECURITY_ONCE_PER_RUN", Statistics: "EXACT_EMPIRICAL_DISTRIBUTION; NO_IID_CONFIDENCE_OR_PORTFOLIO_METRICS", MinSamples: 20, MinDates: 5, MinBlocks: 3, EmbargoSessions: 10}
}

func (v Contract) Validate() error {
	d := DefaultContract()
	if v.Version == "" || v.SignalAvailability != d.SignalAvailability || v.Reference != d.Reference || v.Basis != d.Basis || v.Benchmark != d.Benchmark || v.Missing != d.Missing || v.Execution != d.Execution || v.Samples != d.Samples || v.Statistics != d.Statistics {
		return errors.New("REVIEW_CONTRACT_UNSUPPORTED_SEMANTICS")
	}
	if v.PrimaryWindow != 5 || len(v.Windows) != 4 {
		return errors.New("REVIEW_FIXED_WINDOWS_REQUIRED")
	}
	for i, n := range []int{1, 3, 5, 10} {
		if v.Windows[i] != n {
			return errors.New("REVIEW_FIXED_WINDOWS_REQUIRED")
		}
	}
	if len(v.CostsBPS) != 3 {
		return errors.New("REVIEW_COST_GRID_REQUIRED")
	}
	for i, n := range []int{0, 10, 30} {
		if v.CostsBPS[i] != n {
			return errors.New("REVIEW_COST_GRID_REQUIRED")
		}
	}
	if v.MinSamples < 1 || v.MinDates < 2 || v.MinBlocks < 2 || v.EmbargoSessions < 10 {
		return errors.New("REVIEW_MINIMUM_OR_EMBARGO_INVALID")
	}
	if !v.FixtureOnly && (v.MinSamples < 20 || v.MinDates < 5 || v.MinBlocks < 3) {
		return errors.New("REVIEW_REAL_MINIMUM_CANNOT_BE_LOWERED")
	}
	return nil
}

func Digest(v any) string { b, _ := json.Marshal(v); return c.Hash(b) }

type Record struct {
	Sequence int64           `json:"sequence"`
	Kind     string          `json:"kind"`
	ID       string          `json:"id"`
	Revision int             `json:"revision"`
	Payload  json.RawMessage `json:"payload"`
	Hash     string          `json:"sha256"`
}

func NewRecord(kind, id string, revision int, v any) (Record, error) {
	b, e := json.Marshal(v)
	if e != nil {
		return Record{}, e
	}
	return Record{Kind: kind, ID: id, Revision: revision, Payload: b, Hash: c.Hash(b)}, nil
}

type Signal struct {
	ID                      string          `json:"signal_id"`
	RunID                   string          `json:"run_id"`
	InputID                 string          `json:"input_id"`
	InputHash               string          `json:"input_sha256"`
	Instrument              string          `json:"instrument_id"`
	Strategy                string          `json:"strategy_id"`
	Decision                string          `json:"original_decision"`
	DecisionJSON            json.RawMessage `json:"original_condition_tree"`
	Matched                 []string        `json:"all_matched_setups"`
	Observation             string          `json:"observation_date"`
	Cutoff                  string          `json:"original_cutoff"`
	PublishedAt             string          `json:"actual_signal_published_at"`
	ObservedAvailableAt     string          `json:"verified_available_upper_bound"`
	HypotheticalAvailableAt string          `json:"hypothetical_available_at,omitempty"`
	PublicationLowerBound   string          `json:"publication_lower_bound"`
	RunCreatedAt            string          `json:"run_created_at"`
	AvailabilityBasis       string          `json:"availability_basis"`
	RegisteredAt            string          `json:"registered_at"`
	Mode                    string          `json:"evidence_mode"`
	Implementation          string          `json:"strategy_implementation_identity"`
	ConfigHash              string          `json:"strategy_config_identity"`
	ManifestHash            string          `json:"manifest_sha256"`
	ContractHash            string          `json:"evaluation_contract_sha256"`
	ContractVersion         string          `json:"evaluation_contract_version"`
	Qualified               bool            `json:"qualified"`
	QualificationStatus     string          `json:"qualification_status"`
	QualificationReason     string          `json:"qualification_reason,omitempty"`
	UniverseComplete        bool            `json:"original_universe_complete"`
	IdentityAbsent          int             `json:"original_missing_identity_count"`
	Reference               c.Number        `json:"frozen_reference_price"`
	Basis                   string          `json:"comparable_basis"`
	ReferenceHash           string          `json:"reference_evidence_sha256"`
	PoolComplete            bool            `json:"registered_qualified_pool_complete"`
	Reasons                 []string        `json:"reason_codes"`
}

type Outcome struct {
	Mode                    string              `json:"evidence_mode"`
	ActualPublishedAt       string              `json:"actual_signal_published_at"`
	ObservedAvailableAt     string              `json:"verified_available_upper_bound"`
	HypotheticalAvailableAt string              `json:"hypothetical_available_at,omitempty"`
	ID                      string              `json:"outcome_id"`
	SignalID                string              `json:"signal_id"`
	RunID                   string              `json:"run_id"`
	Instrument              string              `json:"instrument_id"`
	Window                  int                 `json:"window_exchange_sessions"`
	LabelEnd                string              `json:"label_end,omitempty"`
	LabelInput              string              `json:"label_input_id"`
	LabelHash               string              `json:"label_input_sha256"`
	LabelHistoryHash        string              `json:"label_history_sha256,omitempty"`
	LabelCutoff             string              `json:"label_cutoff"`
	Evaluator               string              `json:"evaluation_implementation_identity"`
	ContractHash            string              `json:"evaluation_contract_sha256"`
	Status                  string              `json:"maturity_status"`
	Return                  c.Number            `json:"price_reference_return"`
	Benchmark               c.Number            `json:"qualified_pool_median_return"`
	Excess                  c.Number            `json:"excess_price_return"`
	Costs                   map[string]c.Number `json:"hypothetical_cost_deduction_not_fills"`
	SessionStates           []SessionState      `json:"session_states"`
	Reasons                 []string            `json:"reason_codes"`
	SelectedEvidence        []string            `json:"selected_evidence_sha256"`
	Supersedes              string              `json:"supersedes_outcome_id,omitempty"`
	ExecutionStatus         string              `json:"execution_status"`
}
type SessionState struct {
	Session  string   `json:"session"`
	Status   string   `json:"status"`
	Evidence []string `json:"evidence_sha256"`
}

// LatestOutcomes selects revisions by parsed clocks, never SQL text sorting.
// A tied clock with different bytes is a conflict rather than a newer revision.
func LatestOutcomes(rows []Record, asof time.Time) (map[string]Outcome, error) {
	out := map[string]Outcome{}
	clocks := map[string]time.Time{}
	for _, r := range rows {
		if r.Kind != "OUTCOME" {
			continue
		}
		var v Outcome
		if e := json.Unmarshal(r.Payload, &v); e != nil {
			return nil, e
		}
		t, e := time.Parse(time.RFC3339Nano, v.LabelCutoff)
		if e != nil {
			return nil, e
		}
		if t.After(asof) {
			continue
		}
		key := v.SignalID + ":" + stringInt(v.Window)
		old, ok := out[key]
		if !ok || t.After(clocks[key]) {
			out[key] = v
			clocks[key] = t
		} else if t.Equal(clocks[key]) && v.ID != old.ID {
			return nil, errors.New("LABEL_REVISION_TIME_CONFLICT")
		}
	}
	return out, nil
}

func sortedUnique(v []string) []string {
	m := map[string]bool{}
	for _, s := range v {
		if s != "" {
			m[s] = true
		}
	}
	out := make([]string, 0, len(m))
	for s := range m {
		out = append(out, s)
	}
	sort.Strings(out)
	return out
}
