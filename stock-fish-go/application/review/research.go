package review

import (
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"sort"
	"time"
)

type Candidate struct {
	ID                  string   `json:"candidate_id"`
	Role                string   `json:"role"`
	Parent              string   `json:"parent_baseline_id"`
	Hypothesis          string   `json:"hypothesis"`
	Change              string   `json:"versioned_change"`
	Scope               string   `json:"applicable_scope"`
	Implementation      string   `json:"implementation_identity"`
	ConfigHash          string   `json:"config_identity"`
	DataCutoff          string   `json:"allowed_data_cutoff"`
	SelectionEnd        string   `json:"selection_data_end"`
	EvaluationStart     string   `json:"evaluation_start"`
	EvaluationEnd       string   `json:"evaluation_end"`
	CalendarInputID     string   `json:"calendar_input_id"`
	CalendarHash        string   `json:"calendar_input_sha256"`
	ContractVersion     string   `json:"evaluation_contract_version"`
	ContractHash        string   `json:"evaluation_contract_sha256"`
	PrimaryMetric       string   `json:"primary_metric"`
	SecondaryMetrics    []string `json:"exploratory_metrics"`
	FixtureOnly         bool     `json:"fixture_only"`
	CreatedAt           string   `json:"registered_at"`
	ProductionPromotion string   `json:"production_promotion"`
}

func (v Candidate) Validate(contract Contract, calendar []string, now time.Time) error {
	if err := contract.Validate(); err != nil {
		return err
	}
	if v.ID == "" || v.Hypothesis == "" || v.Change == "" || v.Scope == "" || v.Implementation == "" || v.ConfigHash == "" || v.CalendarInputID == "" || v.CalendarHash == "" {
		return errors.New("CANDIDATE_REQUIRED_IDENTITY_OR_HYPOTHESIS_MISSING")
	}
	for _, hash := range []string{v.Implementation, v.ConfigHash, v.CalendarHash, v.ContractHash} {
		if !ev.ValidHash(hash) {
			return errors.New("CANDIDATE_SHA256_IDENTITY_INVALID")
		}
	}
	if (v.Role != "BASELINE" && v.Role != "CANDIDATE") || (v.Role == "CANDIDATE" && v.Parent == "") {
		return errors.New("CANDIDATE_PARENT_BASELINE_REQUIRED")
	}
	if v.ContractHash != Digest(contract) || v.ContractVersion != contract.Version || v.PrimaryMetric != "PRIMARY_WINDOW_EXCESS_MEDIAN" || v.ProductionPromotion != "DISABLED" {
		return errors.New("CANDIDATE_FIXED_CONTRACT_OR_PROMOTION_INVALID")
	}
	if len(v.SecondaryMetrics) != 3 || v.SecondaryMetrics[0] != "ALL_FIXED_WINDOWS" || v.SecondaryMetrics[1] != "NEGATIVE_TAIL" || v.SecondaryMetrics[2] != "FIXED_COST_SENSITIVITY" {
		return errors.New("CANDIDATE_FIXED_EXPLORATORY_METRICS_REQUIRED")
	}
	cutoff, err := time.Parse(time.RFC3339Nano, v.DataCutoff)
	if err != nil || cutoff.After(now) {
		return errors.New("CANDIDATE_DATA_CUTOFF_INVALID")
	}
	for _, d := range []string{v.SelectionEnd, v.EvaluationStart, v.EvaluationEnd} {
		if _, err := time.Parse("2006-01-02", d); err != nil {
			return errors.New("CANDIDATE_DATE_INVALID")
		}
	}
	if v.SelectionEnd > cutoff.In(sh).Format("2006-01-02") || v.SelectionEnd >= v.EvaluationStart || v.EvaluationStart >= v.EvaluationEnd {
		return errors.New("CANDIDATE_FUTURE_DATA_OR_TIME_SPLIT_OVERLAP")
	}
	start, _ := closeTime(v.EvaluationStart)
	if !cutoff.Before(start) {
		return errors.New("CANDIDATE_DATA_CUTOFF_AFTER_EVALUATION_START")
	}
	gap := 0
	selectionFound, startFound := false, false
	for _, d := range calendar {
		if d == v.SelectionEnd {
			selectionFound = true
		}
		if d == v.EvaluationStart {
			startFound = true
		}
		if d > v.SelectionEnd && d < v.EvaluationStart {
			gap++
		}
	}
	if !selectionFound || !startFound || gap < contract.EmbargoSessions {
		return errors.New("CANDIDATE_PURGE_EMBARGO_UNPROVEN")
	}
	if contract.FixtureOnly && !v.FixtureOnly {
		return errors.New("CANDIDATE_FIXTURE_CONTRACT_REAL_DATA_FORBIDDEN")
	}
	if !v.FixtureOnly && (!now.Before(start) || !cutoff.Before(start)) {
		return errors.New("CANDIDATE_PREREGISTRATION_REQUIRED")
	}
	return nil
}

type Distribution struct {
	N        int      `json:"n"`
	Minimum  c.Number `json:"minimum"`
	P10      c.Number `json:"p10"`
	Median   c.Number `json:"median"`
	Maximum  c.Number `json:"maximum"`
	Negative int      `json:"negative_count"`
	Positive int      `json:"positive_count"`
}

func distribution(values []c.Number) Distribution {
	v := append([]c.Number{}, values...)
	sort.Slice(v, func(i, j int) bool { return v[i].Cmp(v[j]) < 0 })
	d := Distribution{N: len(v)}
	if len(v) == 0 {
		return d
	}
	d.Minimum = v[0]
	d.Maximum = v[len(v)-1]
	d.P10 = v[(len(v)-1)/10]
	d.Median = Median(v)
	for _, x := range v {
		if x.Cmp(c.N("0")) < 0 {
			d.Negative++
		}
		if x.Cmp(c.N("0")) > 0 {
			d.Positive++
		}
	}
	return d
}

type Group struct {
	Window          int                     `json:"window_exchange_sessions"`
	Group           string                  `json:"group"`
	Mode            string                  `json:"evidence_mode"`
	Samples         int                     `json:"signal_count"`
	Mature          int                     `json:"mature_valid_count"`
	Immature        int                     `json:"immature_count"`
	Missing         int                     `json:"missing_or_censored_count"`
	Dates           []string                `json:"observation_dates"`
	Blocks          int                     `json:"nonoverlapping_time_blocks"`
	Return          Distribution            `json:"price_return_distribution"`
	Excess          Distribution            `json:"excess_distribution"`
	CostSensitivity map[string]Distribution `json:"hypothetical_cost_deduction_not_fills"`
	Status          string                  `json:"evidence_status"`
	Notes           []string                `json:"uncertainty"`
}

func selected(s Signal) bool {
	return s.Decision == "CORE_PASS" || s.Decision == "FOCUS" || s.Decision == "WATCH"
}

// Summarize keeps every fixed window and every original decision partition.
// Setup groups overlap; ALL_QUALIFIED and SELECTED never duplicate a security.
func Summarize(signals []Signal, outcomes map[string]Outcome, contract Contract) []Group {
	type acc struct {
		g              Group
		values, excess []c.Number
		cost           map[string][]c.Number
		dates          map[string]bool
		endByDate      map[string]string
	}
	groups := map[string]*acc{}
	for _, s := range signals {
		for _, h := range contract.Windows {
			names := []string{s.Decision}
			if s.Qualified {
				names = append(names, "ALL_QUALIFIED")
			}
			if selected(s) {
				names = append(names, "SELECTED")
			}
			for _, setup := range s.Matched {
				names = append(names, "SETUP:"+setup)
			}
			for _, name := range sortedUnique(names) {
				key := s.Mode + ":" + stringInt(h) + ":" + name
				a := groups[key]
				if a == nil {
					a = &acc{g: Group{Window: h, Group: name, Mode: s.Mode, CostSensitivity: map[string]Distribution{}, Status: "INSUFFICIENT_EVIDENCE", Notes: []string{"SAME_DAY_AND_REPEATED_SECURITY_DEPENDENCE; NO_IID_CONFIDENCE", "PRICE_OBSERVATION; NO_USER_ACCOUNT_OR_SUCCESS_PROBABILITY"}}, cost: map[string][]c.Number{}, dates: map[string]bool{}, endByDate: map[string]string{}}
					groups[key] = a
				}
				a.g.Samples++
				o, ok := outcomes[s.ID+":"+stringInt(h)]
				if !ok {
					a.g.Missing++
					continue
				}
				if o.Status == "NOT_MATURED" {
					a.g.Immature++
					continue
				}
				if o.Status != "MATURE" || !o.Return.Known() {
					a.g.Missing++
					continue
				}
				a.g.Mature++
				a.values = append(a.values, o.Return)
				if o.Excess.Known() {
					a.excess = append(a.excess, o.Excess)
				}
				for k, v := range o.Costs {
					if v.Known() {
						a.cost[k] = append(a.cost[k], v)
					}
				}
				a.dates[s.Observation] = true
				if o.LabelEnd > a.endByDate[s.Observation] {
					a.endByDate[s.Observation] = o.LabelEnd
				}
			}
		}
	}
	keys := make([]string, 0, len(groups))
	for k := range groups {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := []Group{}
	for _, key := range keys {
		a := groups[key]
		a.g.Return = distribution(a.values)
		a.g.Excess = distribution(a.excess)
		for k, v := range a.cost {
			a.g.CostSensitivity[k] = distribution(v)
		}
		for d := range a.dates {
			a.g.Dates = append(a.g.Dates, d)
		}
		sort.Strings(a.g.Dates)
		end := ""
		for _, d := range a.g.Dates {
			if d > end {
				a.g.Blocks++
				end = a.endByDate[d]
			}
		}
		if a.g.Mature >= contract.MinSamples && len(a.g.Dates) >= contract.MinDates && a.g.Blocks >= contract.MinBlocks && a.g.Excess.N == a.g.Mature {
			a.g.Status = "DESCRIPTIVE_EVIDENCE_ONLY"
		}
		out = append(out, a.g)
	}
	return out
}

type Evaluation struct {
	EvaluatorBuild      string   `json:"evaluation_build_sha256"`
	ID                  string   `json:"attempt_id"`
	CandidateID         string   `json:"candidate_id"`
	BaselineID          string   `json:"baseline_id"`
	Attempt             int      `json:"attempt_number"`
	AsOf                string   `json:"as_of"`
	ContractHash        string   `json:"contract_sha256"`
	Status              string   `json:"admission_status"`
	Reasons             []string `json:"reason_codes"`
	CandidateGroups     []Group  `json:"candidate_groups"`
	BaselineGroups      []Group  `json:"baseline_groups"`
	InputRecordsHash    string   `json:"consumed_journal_sha256"`
	ProductionPromotion string   `json:"production_promotion"`
	MarketEdge          string   `json:"market_edge"`
}

func CandidateFrom(records []Record, id string) (Candidate, error) {
	for _, r := range records {
		if r.Kind == "CANDIDATE" && r.ID == id {
			var v Candidate
			err := json.Unmarshal(r.Payload, &v)
			return v, err
		}
	}
	return Candidate{}, errors.New("CANDIDATE_NOT_REGISTERED")
}

// Compare consumes already registered outputs only. Every call becomes a saved
// attempt, including failed and insufficient attempts; no search is invoked.
func Compare(records []Record, candidate, baseline Candidate, contract Contract, asof time.Time, key string) (Evaluation, error) {
	result := Evaluation{ID: key, CandidateID: candidate.ID, BaselineID: baseline.ID, AsOf: asof.UTC().Format(time.RFC3339Nano), ContractHash: Digest(contract), Status: "INSUFFICIENT_EVIDENCE", Reasons: []string{}, InputRecordsHash: Digest(records), ProductionPromotion: "DISABLED", MarketEdge: "NOT_ESTABLISHED"}
	if key == "" {
		return result, errors.New("EXPERIMENT_ATTEMPT_KEY_REQUIRED")
	}
	for _, hash := range []string{candidate.Implementation, candidate.ConfigHash, candidate.ContractHash, baseline.Implementation, baseline.ConfigHash, baseline.ContractHash} {
		if !ev.ValidHash(hash) {
			return result, errors.New("EXPERIMENT_SHA256_IDENTITY_INVALID")
		}
	}
	if candidate.Parent != baseline.ID || baseline.Role != "BASELINE" || candidate.ContractHash != baseline.ContractHash || candidate.ContractHash != Digest(contract) || candidate.EvaluationStart != baseline.EvaluationStart || candidate.EvaluationEnd != baseline.EvaluationEnd {
		return result, errors.New("EXPERIMENT_BASELINE_OR_CONTRACT_MISMATCH")
	}
	result.Attempt = 1
	for _, r := range records {
		if r.Kind == "EVALUATION" {
			var v Evaluation
			if err := json.Unmarshal(r.Payload, &v); err != nil {
				return result, err
			}
			if v.CandidateID == candidate.ID {
				result.Attempt++
			}
		}
	}
	latest, err := LatestOutcomes(records, asof)
	if err != nil {
		return result, err
	}
	signals, err := Signals(records, "", Digest(contract))
	if err != nil {
		return result, err
	}
	filter := func(v Candidate) ([]Signal, error) {
		out := []Signal{}
		seen := map[string]bool{}
		for _, s := range signals {
			if s.Observation < v.EvaluationStart || s.Observation >= v.EvaluationEnd {
				continue
			}
			if s.Implementation != v.Implementation || s.ConfigHash != v.ConfigHash {
				continue
			}
			if v.FixtureOnly != (s.Mode == "TEST_FIXTURE") {
				continue
			}
			if s.Mode == "HISTORICAL_REPLAY" {
				continue
			}
			key := s.Observation + ":" + s.Instrument
			if seen[key] {
				return nil, errors.New("EXPERIMENT_DUPLICATE_OBSERVATION_SECURITY; EXPLICIT_RUN_SELECTION_REQUIRED")
			}
			seen[key] = true
			out = append(out, s)
		}
		return out, nil
	}
	cs, err := filter(candidate)
	if err != nil {
		return result, err
	}
	bs, err := filter(baseline)
	if err != nil {
		return result, err
	}
	result.CandidateGroups = Summarize(cs, latest, contract)
	result.BaselineGroups = Summarize(bs, latest, contract)
	// Pair by observation/instrument; a different universe is a mixed change and
	// cannot pass this fixed comparison contract. Unmatured windows are excluded.
	byID := func(ss []Signal) map[string]Signal {
		m := map[string]Signal{}
		for _, s := range ss {
			m[s.Observation+":"+s.Instrument] = s
		}
		return m
	}
	cm, bm := byID(cs), byID(bs)
	paired := len(cm) == len(bm) && len(cm) > 0
	for k := range cm {
		if _, ok := bm[k]; !ok {
			paired = false
		}
	}
	if !paired {
		result.Reasons = append(result.Reasons, "EVALUATION_POPULATIONS_NOT_PAIRED")
	}
	for _, s := range append(append([]Signal{}, cs...), bs...) {
		o, ok := latest[s.ID+":"+stringInt(contract.PrimaryWindow)]
		if !ok || o.Status != "MATURE" || o.LabelEnd >= candidate.EvaluationEnd {
			result.Reasons = append(result.Reasons, "LABEL_IMMATURE_MISSING_OR_CROSSES_EVALUATION_BOUNDARY")
		}
	}
	primary := func(groups []Group) *Group {
		for i := range groups {
			g := &groups[i]
			if g.Window == contract.PrimaryWindow && g.Group == "SELECTED" {
				return g
			}
		}
		return nil
	}
	cg, bg := primary(result.CandidateGroups), primary(result.BaselineGroups)
	if cg == nil || bg == nil || cg.Status != "DESCRIPTIVE_EVIDENCE_ONLY" || bg.Status != "DESCRIPTIVE_EVIDENCE_ONLY" {
		result.Reasons = append(result.Reasons, "PREDECLARED_SAMPLE_DATE_BLOCK_MINIMUM_NOT_MET")
	}
	if len(result.Reasons) == 0 {
		if cg.Excess.Median.Cmp(bg.Excess.Median) <= 0 {
			result.Status = "REJECTED"
			result.Reasons = append(result.Reasons, "PRIMARY_METRIC_NO_IMPROVEMENT")
		} else if cg.Return.P10.Cmp(bg.Return.P10) < 0 {
			result.Status = "REJECTED"
			result.Reasons = append(result.Reasons, "NEGATIVE_TAIL_WORSE_THAN_BASELINE")
		} else {
			result.Status = "REVIEW_REQUIRED"
			result.Reasons = append(result.Reasons, "INDEPENDENT_STABILITY_AND_HUMAN_REVIEW_REQUIRED")
		}
	}
	result.Reasons = sortedUnique(result.Reasons)
	return result, nil
}

type Transition struct {
	CandidateID         string `json:"candidate_id"`
	From                string `json:"from"`
	To                  string `json:"to"`
	EvaluationID        string `json:"evaluation_attempt_id,omitempty"`
	Reason              string `json:"reason"`
	At                  string `json:"at"`
	ProductionPromotion string `json:"production_promotion"`
}

func CandidateState(records []Record, id string) string {
	state := "PROPOSED"
	for _, r := range records {
		if r.Kind == "TRANSITION" && r.ID == id {
			var v Transition
			if json.Unmarshal(r.Payload, &v) == nil {
				state = v.To
			}
		}
	}
	return state
}

func TransitionCandidate(records []Record, v Candidate, to, attempt, reason string, now time.Time) (Transition, error) {
	from := CandidateState(records, v.ID)
	event := Transition{CandidateID: v.ID, From: from, To: to, EvaluationID: attempt, Reason: reason, At: now.UTC().Format(time.RFC3339Nano), ProductionPromotion: "DISABLED"}
	if reason == "" {
		return event, errors.New("CANDIDATE_TRANSITION_REASON_REQUIRED")
	}
	if to == "PRODUCTION" || to == "PROMOTED" || to == "ACTIVE" {
		return event, errors.New("PRODUCTION_PROMOTION_DISABLED")
	}
	allowed := map[string][]string{"PROPOSED": {"OFFLINE_EVALUATED", "INSUFFICIENT_EVIDENCE", "REJECTED"}, "OFFLINE_EVALUATED": {"REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE", "REJECTED"}, "REVIEW_REQUIRED": {"REJECTED", "INSUFFICIENT_EVIDENCE"}, "SHADOW_ELIGIBLE": {"SHADOW_OBSERVING", "REJECTED"}, "SHADOW_OBSERVING": {"REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE", "REJECTED"}, "INSUFFICIENT_EVIDENCE": {"OFFLINE_EVALUATED", "REJECTED"}, "REJECTED": {}}
	if !c.Contains(allowed[from], to) {
		return event, errors.New("CANDIDATE_TRANSITION_NOT_ALLOWED")
	}
	if to == "SHADOW_ELIGIBLE" || to == "SHADOW_OBSERVING" {
		return event, errors.New("INDEPENDENT_STABILITY_EVIDENCE_NOT_IMPLEMENTED; SHADOW_ADMISSION_BLOCKED")
	}
	if to == "OFFLINE_EVALUATED" || to == "REVIEW_REQUIRED" {
		var evaluation *Evaluation
		for _, r := range records {
			if r.Kind == "EVALUATION" && r.ID == attempt {
				var x Evaluation
				if err := json.Unmarshal(r.Payload, &x); err != nil {
					return event, err
				}
				evaluation = &x
			}
		}
		if evaluation == nil || evaluation.CandidateID != v.ID {
			return event, errors.New("CANDIDATE_EVALUATION_ATTEMPT_REQUIRED")
		}
		if to == "REVIEW_REQUIRED" && evaluation.Status != "REVIEW_REQUIRED" {
			return event, errors.New("CANDIDATE_EVIDENCE_INSUFFICIENT_FOR_REVIEW")
		}
	}
	return event, nil
}

type Diagnosis struct {
	Category    string   `json:"category"`
	Status      string   `json:"status"`
	Basis       []string `json:"basis"`
	Samples     int      `json:"signal_count"`
	Missing     int      `json:"missing_count"`
	Uncertainty string   `json:"uncertainty"`
}

func Diagnose(groups []Group) []Diagnosis {
	d := []Diagnosis{{Category: "DATA_OR_ENGINEERING", Status: "INSUFFICIENT_EVIDENCE", Basis: []string{}, Uncertainty: "Coverage loss does not prove strategy failure."}, {Category: "INPUT_DISTRIBUTION", Status: "NOT_EVALUATED", Basis: []string{"NO_PREDECLARED_FEATURE_DISTRIBUTION_REFERENCE"}, Uncertainty: "Price outcomes do not identify input distribution change."}, {Category: "MATURE_PERFORMANCE", Status: "INSUFFICIENT_EVIDENCE", Basis: []string{}, Uncertainty: "Descriptive association; no causal or stock success probability claim."}}
	for _, g := range groups {
		if g.Window == 5 && g.Group == "SELECTED" {
			d[0].Samples += g.Samples
			d[0].Missing += g.Missing
			d[2].Samples += g.Mature
			if g.Missing > 0 {
				d[0].Status = "OBSERVED_GAP"
				d[0].Basis = append(d[0].Basis, "MISSING_OR_CENSORED_LABELS")
			}
			if g.Status == "DESCRIPTIVE_EVIDENCE_ONLY" {
				d[2].Status = "DESCRIPTIVE_EVIDENCE_ONLY"
				d[2].Basis = append(d[2].Basis, "FIXED_PRIMARY_WINDOW_DISTRIBUTION_AVAILABLE")
			}
		}
	}
	return d
}
