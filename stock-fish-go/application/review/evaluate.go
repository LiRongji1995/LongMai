package review

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/shared"
	"sort"
	"strconv"
	"strings"
	"time"
)

func stringInt(n int) string { return strconv.Itoa(n) }

var sh = time.FixedZone("Asia/Shanghai", 8*3600)

func closeTime(day string) (time.Time, error) {
	return time.Parse(time.RFC3339Nano, day+"T15:00:00+08:00")
}

type Evidence struct {
	Frozen     c.Frozen
	History    ports.HistoryReader
	Resolver   *ev.Resolver
	Securities []c.Row
}

// NewEvidence uses the same four-clock parent-receipt policy and authenticated
// history port as research. It does not add a label upload/trust assertion path.
func NewEvidence(ctx context.Context, f c.Frozen, h ports.HistoryReader) (*Evidence, error) {
	if err := ev.ValidateFrozenContext(ctx, &f); err != nil {
		return nil, err
	}
	receipts := append([]c.Row{}, f.Input.Receipts...)
	if f.Input.HistoryRef != "" && h == nil {
		return nil, errors.New("REVIEW_AUTHENTICATED_HISTORY_REQUIRED")
	}
	if h != nil {
		if h.Identity() != f.Input.HistoryHash {
			return nil, errors.New("REVIEW_HISTORY_IDENTITY_MISMATCH")
		}
		if _, ok := h.(ports.HistoryIdentityReader); !ok {
			return nil, errors.New("REVIEW_AUTHENTICATED_HISTORY_REQUIRED")
		}
		if err := h.VerifyIndex(); err != nil {
			return nil, err
		}
		receipts = append(h.Receipts(), receipts...)
	}
	r, err := ev.NewResolverContext(ctx, receipts, f.Cutoff, f.Input.Coverage, f.Input.Observation, f.Input.Sessions)
	if err != nil {
		return nil, err
	}
	out := &Evidence{Frozen: f, History: h, Resolver: r}
	if err = json.Unmarshal(f.Input.Universe["securities"], &out.Securities); err != nil {
		return nil, err
	}
	return out, nil
}

func (e *Evidence) bar(iid, session string) (c.Row, []string, error) {
	visible := []c.Row{}
	reasons := []string{}
	consume := func(row c.Row) error {
		if row.Text("instrument_id") != iid || row.Text("session") != session {
			return nil
		}
		proof := e.Resolver.Resolve(row, ev.Scope{Instrument: iid, Session: session, RequireSession: true, Fields: []string{"comparable_close", "comparable_basis"}})
		if proof.Allowed {
			visible = append(visible, row)
		} else {
			reasons = append(reasons, proof.Reason)
		}
		return nil
	}
	for _, row := range e.Frozen.Input.Bars {
		if err := consume(row); err != nil {
			return nil, nil, err
		}
	}
	if e.History != nil {
		if err := e.History.Bars(iid, session, session, consume); err != nil {
			return nil, nil, err
		}
	}
	row, err := ev.SelectRevision(visible)
	if err != nil {
		return nil, []string{err.Error()}, nil
	}
	if row == nil {
		return nil, append(sortedUnique(reasons), "PRICE_OBSERVATION_UNAVAILABLE"), nil
	}
	return row, nil, nil
}

func (e *Evidence) security(iid, session string) (c.Row, []string) {
	outer := c.Row{}
	for k, v := range e.Frozen.Input.Universe {
		if k != "securities" {
			outer[k] = v
		}
	}
	p := e.Resolver.Resolve(outer, ev.Scope{Complete: true})
	if !p.Allowed {
		return nil, []string{p.Reason}
	}
	if p := outer.Bool("complete"); p == nil || !*p {
		return nil, []string{"SECURITY_UNIVERSE_EVIDENCE_INCOMPLETE"}
	}
	visible := []c.Row{}
	reasons := []string{}
	for _, row := range e.Securities {
		if row.Text("instrument_id") != iid {
			continue
		}
		proof := e.Resolver.Resolve(row, ev.Scope{Instrument: iid, Session: session, Complete: true})
		if proof.Allowed {
			visible = append(visible, row)
		} else {
			reasons = append(reasons, proof.Reason)
		}
	}
	row, err := ev.SelectRevision(visible)
	if err != nil {
		return nil, []string{err.Error()}
	}
	if row == nil {
		return nil, append(sortedUnique(reasons), "DATED_SECURITY_STATUS_UNAVAILABLE")
	}
	return row, nil
}

func validPrice(row c.Row) bool {
	b, err := facts.ParseBar(row)
	if err != nil || b.Quality != "VALID" || !b.Close.Positive() {
		return false
	}
	for _, v := range []c.Number{b.Open, b.High, b.Low, b.RawClose} {
		if !v.Positive() || !v.Div(b.Tick).IsInt() {
			return false
		}
	}
	return b.Low.Cmp(b.Open) <= 0 && b.Low.Cmp(b.RawClose) <= 0 && b.High.Cmp(b.Open) >= 0 && b.High.Cmp(b.RawClose) >= 0
}

// Register preserves the complete evaluated set and the independent known and
// unknown qualification partitions. Missing identities remain a run-level gap.
func Register(ctx context.Context, run c.Run, in c.InputRecord, e *Evidence, contract Contract, availableAt string, now time.Time) ([]Signal, error) {
	if err := contract.Validate(); err != nil {
		return nil, err
	}
	if run.InputID != in.ID || !ev.ValidHash(in.RawHash) || in.RawHash != c.Hash(in.Raw) {
		return nil, errors.New("REVIEW_SIGNAL_INPUT_IDENTITY_MISMATCH")
	}
	decoded, err := ev.DecodeFrozenContext(ctx, in.Raw)
	if err != nil {
		return nil, err
	}
	if Digest(decoded) != Digest(e.Frozen) {
		return nil, errors.New("REVIEW_SIGNAL_EVIDENCE_INPUT_MISMATCH")
	}
	if in.Observation != decoded.Input.Observation || in.Cutoff != decoded.Cutoff || in.Coverage != decoded.Input.Coverage {
		return nil, errors.New("REVIEW_SIGNAL_REGISTERED_METADATA_MISMATCH")
	}
	var manifest c.Row
	if err := json.Unmarshal(run.Manifest, &manifest); err != nil {
		return nil, err
	}
	if manifest.Text("input_bytes_hash") != in.RawHash || manifest.Text("cutoff") != in.Cutoff {
		return nil, errors.New("REVIEW_MANIFEST_INPUT_BINDING_REQUIRED")
	}
	implementation := manifest.Text("go_source_hash")
	if !ev.ValidHash(implementation) || strings.HasPrefix(implementation, "UNRECORDED") || len(manifest["config_hashes"]) == 0 || manifest.Text("conversion_version") == "" {
		return nil, errors.New("REVIEW_STRATEGY_IDENTITY_REQUIRED")
	}
	var configs map[string]string
	if err = json.Unmarshal(manifest["config_hashes"], &configs); err != nil || len(configs) == 0 {
		return nil, errors.New("REVIEW_CONFIG_HASHES_REQUIRED")
	}
	for _, hash := range configs {
		if !ev.ValidHash(hash) {
			return nil, errors.New("REVIEW_CONFIG_HASH_INVALID")
		}
	}
	created, err := time.Parse(time.RFC3339Nano, run.CreatedAt)
	if err != nil || created.After(now) {
		return nil, errors.New("REVIEW_RUN_CREATION_INVALID")
	}
	cutoff, err := time.Parse(time.RFC3339Nano, in.Cutoff)
	if err != nil || created.Before(cutoff) {
		return nil, errors.New("REVIEW_CREATION_PRECEDES_INPUT")
	}
	if availableAt != "" {
		publication, err := time.Parse(time.RFC3339Nano, availableAt)
		if err != nil || publication.After(now) || publication.Before(created) {
			return nil, errors.New("REVIEW_PUBLICATION_LOWER_BOUND_INVALID")
		}
	}
	var summary struct {
		Qualification c.Qualification `json:"qualification"`
	}
	if err = json.Unmarshal(run.Summary, &summary); err != nil {
		return nil, err
	}
	q := summary.Qualification
	qualified := map[string]bool{}
	for _, id := range q.Eligible {
		if _, exists := q.Excluded[id]; exists {
			return nil, errors.New("REVIEW_QUALIFICATION_PARTITIONS_OVERLAP")
		}
		if _, exists := q.Unknown[id]; exists {
			return nil, errors.New("REVIEW_QUALIFICATION_PARTITIONS_OVERLAP")
		}
		qualified[id] = true
	}
	for id := range q.Excluded {
		if _, exists := q.Unknown[id]; exists {
			return nil, errors.New("REVIEW_QUALIFICATION_PARTITIONS_OVERLAP")
		}
	}
	items := map[string]c.Item{}
	for _, item := range run.Items {
		if _, ok := items[item.ID]; ok {
			return nil, errors.New("REVIEW_DUPLICATE_SECURITY")
		}
		items[item.ID] = item
	}
	for id := range q.Excluded {
		if _, ok := items[id]; !ok {
			items[id] = c.Item{ID: id}
		}
	}
	for id := range q.Unknown {
		if _, ok := items[id]; !ok {
			items[id] = c.Item{ID: id}
		}
	}
	for id := range qualified {
		if _, ok := items[id]; !ok {
			return nil, errors.New("REVIEW_EVALUATED_SET_INCOMPLETE")
		}
	}
	ids := make([]string, 0, len(items))
	for id := range items {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := make([]Signal, 0, len(ids))
	for _, id := range ids {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		item := items[id]
		s := Signal{RunID: run.ID, InputID: in.ID, InputHash: in.RawHash, Instrument: id, Strategy: run.Strategy, Matched: []string{}, Observation: in.Observation, Cutoff: in.Cutoff, RunCreatedAt: run.CreatedAt, PublicationLowerBound: availableAt, ObservedAvailableAt: now.UTC().Format(time.RFC3339Nano), AvailabilityBasis: "VERIFIED_SAVED_READ_UPPER_BOUND; ORIGINAL_PUBLICATION_UNKNOWN", RegisteredAt: now.UTC().Format(time.RFC3339Nano), Mode: "HISTORICAL_REPLAY", Implementation: implementation, ConfigHash: c.Hash(manifest["config_hashes"]), ManifestHash: c.Hash(run.Manifest), ContractHash: Digest(contract), ContractVersion: contract.Version, Qualified: qualified[id], PoolComplete: q.UniverseComplete && q.Absent == 0 && len(q.Unknown) == 0, Reasons: []string{}}
		s.UniverseComplete = q.UniverseComplete
		s.IdentityAbsent = q.Absent
		s.QualificationStatus = "QUALIFIED"
		if in.Coverage == "TEST_FIXTURE" {
			s.Mode = "TEST_FIXTURE"
			s.PublishedAt = availableAt
			s.AvailabilityBasis = "EXPLICIT_TEST_FIXTURE"
		} else if in.Coverage == "PROSPECTIVE_CAPTURED" && now.In(sh).Format("2006-01-02") == in.Observation {
			s.Mode = "PROSPECTIVE_VERIFIED_AVAILABLE"
		}
		if s.Mode == "HISTORICAL_REPLAY" {
			s.HypotheticalAvailableAt = s.Cutoff
		}
		if contract.FixtureOnly && s.Mode != "TEST_FIXTURE" {
			return nil, errors.New("FIXTURE_CONTRACT_REAL_DATA_FORBIDDEN")
		}
		if !s.Qualified {
			s.QualificationReason = q.Excluded[id]
			s.QualificationStatus = "KNOWN_EXCLUDED"
			s.Decision = "QUALIFICATION_EXCLUDED"
			if reason, ok := q.Unknown[id]; ok {
				s.QualificationReason = reason
				s.QualificationStatus = "UNKNOWN"
				s.Decision = "QUALIFICATION_UNKNOWN"
			} else if s.QualificationReason == "" {
				s.QualificationStatus = "UNKNOWN"
				s.QualificationReason = "QUALIFICATION_NOT_RECORDED"
				s.Decision = "QUALIFICATION_UNKNOWN"
				s.PoolComplete = false
			}
			s.Reasons = append(s.Reasons, s.QualificationReason)
		}
		if len(item.Payload) > 0 {
			var payload struct {
				Decision c.Row `json:"decision"`
			}
			if err = json.Unmarshal(item.Payload, &payload); err != nil {
				return nil, err
			}
			s.DecisionJSON = payload.Decision["candidate_status"]
			s.Decision = payload.Decision.Text("candidate_status")
			if s.Decision == "" {
				s.Decision = payload.Decision.Text("decision_status")
			}
			s.DecisionJSON, _ = json.Marshal(payload.Decision)
			var matchedReasons []string
			s.Matched, matchedReasons, err = savedMatchedStages(run.Strategy, payload.Decision)
			if err != nil {
				return nil, err
			}
			s.Reasons = append(s.Reasons, matchedReasons...)
		}
		if s.Decision == "" {
			s.Decision = "UNKNOWN"
			s.Reasons = append(s.Reasons, "ORIGINAL_DECISION_UNAVAILABLE")
		}
		row, reasons, err := e.bar(id, in.Observation)
		if err != nil {
			return nil, err
		}
		s.Reasons = append(s.Reasons, reasons...)
		if row != nil && validPrice(row) {
			s.Reference = row.Num("comparable_close")
			s.Basis = row.Text("comparable_basis")
			s.ReferenceHash = c.RowHash(row)
		} else {
			s.Reasons = append(s.Reasons, "FROZEN_REFERENCE_UNAVAILABLE")
		}
		s.Reasons = sortedUnique(s.Reasons)
		s.ID = "signal_" + Digest([]string{run.ID, id, s.ContractHash})
		out = append(out, s)
	}
	return out, nil
}

func Evaluate(ctx context.Context, signals []Signal, label c.InputRecord, e *Evidence, contract Contract, implementation string) ([]Outcome, error) {
	if err := contract.Validate(); err != nil {
		return nil, err
	}
	if !ev.ValidHash(implementation) || strings.HasPrefix(implementation, "UNRECORDED") {
		return nil, errors.New("REVIEW_EVALUATOR_BUILD_IDENTITY_REQUIRED")
	}
	if label.RawHash != c.Hash(label.Raw) || label.Cutoff != e.Frozen.Cutoff {
		return nil, errors.New("REVIEW_LABEL_INPUT_IDENTITY_MISMATCH")
	}
	decoded, err := ev.DecodeFrozenContext(ctx, label.Raw)
	if err != nil {
		return nil, err
	}
	if Digest(decoded) != Digest(e.Frozen) {
		return nil, errors.New("REVIEW_LABEL_EVIDENCE_INPUT_MISMATCH")
	}
	if label.Observation != decoded.Input.Observation || label.Cutoff != decoded.Cutoff || label.Coverage != decoded.Input.Coverage {
		return nil, errors.New("REVIEW_LABEL_REGISTERED_METADATA_MISMATCH")
	}
	cutoff, err := time.Parse(time.RFC3339Nano, label.Cutoff)
	if err != nil {
		return nil, err
	}
	out := []Outcome{}
	for _, s := range signals {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !ev.ValidHash(s.InputHash) || !ev.ValidHash(s.Implementation) || !ev.ValidHash(s.ConfigHash) || s.ContractHash != Digest(contract) {
			return nil, errors.New("REVIEW_SIGNAL_OR_CONTRACT_BINDING_MISMATCH")
		}
		if (s.Mode == "TEST_FIXTURE") != (label.Coverage == "TEST_FIXTURE") {
			return nil, errors.New("REVIEW_FIXTURE_REAL_DATA_MIX_FORBIDDEN")
		}
		availability := s.ObservedAvailableAt
		if s.Mode == "TEST_FIXTURE" {
			availability = s.PublishedAt
		}
		if s.Mode == "HISTORICAL_REPLAY" {
			availability = s.HypotheticalAvailableAt
		}
		available, err := time.Parse(time.RFC3339Nano, availability)
		if err != nil {
			return nil, err
		}
		if cutoff.Before(available) {
			return nil, errors.New("REVIEW_LABEL_PRECEDES_SIGNAL_AVAILABILITY_OR_REPLAY_ASSUMPTION")
		}
		index := sort.SearchStrings(e.Frozen.Input.Sessions, s.Observation)
		if index >= len(e.Frozen.Input.Sessions) || e.Frozen.Input.Sessions[index] != s.Observation {
			return nil, errors.New("REVIEW_ORIGINAL_SESSION_ABSENT")
		}
		future := e.Frozen.Input.Sessions[index+1:]
		ref, refReasons, err := e.bar(s.Instrument, s.Observation)
		if err != nil {
			return nil, err
		}
		if ref == nil || !s.Reference.Positive() || !validPrice(ref) {
			refReasons = append(refReasons, "REFERENCE_EVIDENCE_UNAVAILABLE")
		} else if ref.Text("comparable_basis") != s.Basis || ref.Num("comparable_close").Cmp(s.Reference) != 0 {
			refReasons = append(refReasons, "REFERENCE_REVISION_MISMATCH")
		}
		for _, horizon := range contract.Windows {
			o := Outcome{SignalID: s.ID, RunID: s.RunID, Instrument: s.Instrument, Window: horizon, LabelInput: label.ID, LabelHash: label.RawHash, LabelHistoryHash: e.Frozen.Input.HistoryHash, LabelCutoff: label.Cutoff, Evaluator: implementation, ContractHash: s.ContractHash, Status: "NOT_MATURED", Costs: map[string]c.Number{}, SessionStates: []SessionState{}, Reasons: []string{}, SelectedEvidence: []string{}, ExecutionStatus: "NOT_SIMULATED; NOT_USER_ACCOUNT"}
			o.Mode = s.Mode
			o.ActualPublishedAt = s.PublishedAt
			o.ObservedAvailableAt = s.ObservedAvailableAt
			o.HypotheticalAvailableAt = s.HypotheticalAvailableAt
			o.ID = "outcome_" + Digest([]string{s.ID, stringInt(horizon), label.RawHash, implementation})
			if !s.Qualified {
				o.Status = "QUALIFICATION_EXCLUDED"
				if qualification(s) == "UNKNOWN" {
					o.Status = "QUALIFICATION_UNKNOWN"
				}
				o.Reasons = append(o.Reasons, s.QualificationReason)
				out = append(out, o)
				continue
			}
			if len(future) < horizon {
				o.Reasons = append(o.Reasons, "CALENDAR_HORIZON_UNAVAILABLE")
				out = append(out, o)
				continue
			}
			o.LabelEnd = future[horizon-1]
			endTime, err := closeTime(o.LabelEnd)
			if err != nil {
				return nil, err
			}
			if endTime.After(cutoff) {
				o.Reasons = append(o.Reasons, "SESSION_NOT_MATURED")
				out = append(out, o)
				continue
			}
			if s.Mode == "PROSPECTIVE_VERIFIED_AVAILABLE" {
				first, _ := time.Parse(time.RFC3339Nano, future[0]+"T09:30:00+08:00")
				if !available.Before(first) {
					o.Status = "UNKNOWN"
					o.Reasons = append(o.Reasons, "SIGNAL_NOT_AVAILABLE_BEFORE_WINDOW")
					out = append(out, o)
					continue
				}
			}
			o.Status = "MATURE"
			o.Reasons = append(o.Reasons, refReasons...)
			var last c.Row
			for _, session := range future[:horizon] {
				state := SessionState{Session: session, Status: "OBSERVED", Evidence: []string{}}
				security, reasons := e.security(s.Instrument, session)
				if security == nil || security.Bool("suspended") == nil {
					state.Status = "SECURITY_STATUS_UNKNOWN"
				} else if *security.Bool("suspended") {
					state.Status = "SUSPENDED"
					reasons = append(reasons, "VERIFIED_SUSPENSION")
				} else if security.Text("delisted_on") != "" && security.Text("delisted_on") <= session {
					state.Status = "DELISTED"
					reasons = append(reasons, "DELISTED")
				}
				if security != nil {
					state.Evidence = append(state.Evidence, c.RowHash(security))
				}
				bar, barReasons, err := e.bar(s.Instrument, session)
				if err != nil {
					return nil, err
				}
				if bar == nil {
					state.Status = "MISSING_PRICE"
				} else {
					state.Evidence = append(state.Evidence, c.RowHash(bar))
					if !validPrice(bar) {
						state.Status = "INVALID_PRICE"
					} else if bar.Text("comparable_basis") != s.Basis {
						state.Status = "COMPARABLE_BASIS_MISMATCH"
					}
				}
				reasons = append(reasons, barReasons...)
				if state.Status != "OBSERVED" {
					reasons = append(reasons, state.Status)
				}
				o.Reasons = append(o.Reasons, reasons...)
				o.SessionStates = append(o.SessionStates, state)
				o.SelectedEvidence = append(o.SelectedEvidence, state.Evidence...)
				last = bar
			}
			if len(o.Reasons) > 0 {
				o.Status = "UNKNOWN"
			} else {
				o.Return = last.Num("comparable_close").Div(s.Reference).Sub(c.N("1"))
				for _, bps := range contract.CostsBPS {
					o.Costs[stringInt(bps)] = o.Return.Sub(c.Count(bps).Div(c.N("10000")))
				}
			}
			o.Reasons = sortedUnique(o.Reasons)
			o.SelectedEvidence = sortedUnique(append(o.SelectedEvidence, s.ReferenceHash))
			out = append(out, o)
		}
	}
	// Full qualified set, including failed decisions, forms the reference pool.
	// Any missing member leaves this benchmark unknown; no smaller substitute.
	for _, h := range contract.Windows {
		byRun := map[string][]int{}
		for i, o := range out {
			if o.Window == h {
				byRun[o.RunID] = append(byRun[o.RunID], i)
			}
		}
		signalByID := map[string]Signal{}
		for _, s := range signals {
			signalByID[s.ID] = s
		}
		for _, indexes := range byRun {
			values := []c.Number{}
			complete := true
			for _, i := range indexes {
				o := out[i]
				signal, ok := signalByID[o.SignalID]
				if !ok {
					complete = false
					continue
				}
				if !signal.PoolComplete {
					complete = false
				}
				if !signal.Qualified {
					continue
				}
				if o.Status != "MATURE" || !o.Return.Known() {
					complete = false
				} else {
					values = append(values, o.Return)
				}
			}
			if complete && len(values) > 0 {
				median := Median(values)
				for _, i := range indexes {
					if out[i].Return.Known() {
						out[i].Benchmark = median
						out[i].Excess = out[i].Return.Sub(median)
					}
				}
			} else {
				for _, i := range indexes {
					out[i].Reasons = sortedUnique(append(out[i].Reasons, "FULL_QUALIFIED_BENCHMARK_UNAVAILABLE"))
				}
			}
		}
	}
	return out, nil
}

// The saved primary/alternate identities express V2's final stage selection.
// A PASS in an unrelated detector is not an additional final matched stage.
func savedMatchedStages(strategy string, decision c.Row) ([]string, []string, error) {
	if strategy == c.R1 {
		var matched []string
		if raw, ok := decision["matched_setups"]; ok {
			if err := json.Unmarshal(raw, &matched); err != nil {
				return nil, nil, errors.New("REVIEW_R1_MATCHED_SETUPS_INVALID")
			}
		}
		return matched, nil, nil
	}
	if strategy != c.V2 {
		return nil, nil, errors.New("REVIEW_UNKNOWN_SAVED_STRATEGY")
	}
	declared := []string{}
	reasons := []string{}
	if raw, ok := decision["primary_stage"]; !ok {
		reasons = append(reasons, "V2_PRIMARY_STAGE_IDENTITY_UNAVAILABLE")
	} else if !ev.IsNull(raw) {
		var primary string
		if err := json.Unmarshal(raw, &primary); err != nil {
			return nil, nil, errors.New("REVIEW_V2_PRIMARY_STAGE_INVALID")
		}
		if primary != "" {
			declared = append(declared, primary)
		}
	}
	var alternate []string
	if raw, ok := decision["alternate_stages"]; ok && !ev.IsNull(raw) {
		if err := json.Unmarshal(raw, &alternate); err != nil {
			return nil, nil, errors.New("REVIEW_V2_ALTERNATE_STAGES_INVALID")
		}
	} else if !ok {
		reasons = append(reasons, "V2_ALTERNATE_STAGE_IDENTITIES_UNAVAILABLE")
	}
	declared = append(declared, alternate...)
	var stages map[string]c.Condition
	if raw, ok := decision["stage_candidates"]; ok && !ev.IsNull(raw) {
		if err := json.Unmarshal(raw, &stages); err != nil {
			return nil, nil, errors.New("REVIEW_V2_STAGE_TREE_INVALID")
		}
	}
	matched := []string{}
	seen := map[string]bool{}
	for _, stage := range declared {
		if stage == "" || seen[stage] {
			continue
		}
		seen[stage] = true
		if !c.Contains(shared.Stages, stage) {
			reasons = append(reasons, "DECLARED_STAGE_IDENTITY_OUTSIDE_FROZEN_SET:"+stage)
			continue
		}
		condition, ok := stages[stage]
		if ok && condition.Status == c.Pass {
			matched = append(matched, stage)
		} else {
			reasons = append(reasons, "DECLARED_STAGE_NOT_CONFIRMED_PASS:"+stage)
		}
	}
	status := decision.Text("candidate_status")
	if status == "" {
		status = decision.Text("decision_status")
	}
	if len(matched) == 0 {
		if status == "UNKNOWN" {
			reasons = append(reasons, "V2_FINAL_STAGE_SELECTION_UNKNOWN")
		} else if status == "FOCUS" || status == "WATCH" {
			reasons = append(reasons, "V2_SELECTED_DECISION_WITHOUT_CONFIRMED_STAGE")
		}
	}
	if len(matched) == 0 && len(reasons) > 0 {
		matched = nil
	}
	return matched, reasons, nil
}

func Median(values []c.Number) c.Number {
	v := append([]c.Number{}, values...)
	sort.Slice(v, func(i, j int) bool { return v[i].Cmp(v[j]) < 0 })
	if len(v) == 0 {
		return c.Number{}
	}
	n := len(v)
	if n%2 == 1 {
		return v[n/2]
	}
	return v[n/2-1].Add(v[n/2]).Div(c.N("2"))
}

func Signals(rows []Record, runID, contractHash string) ([]Signal, error) {
	out := []Signal{}
	for _, r := range rows {
		if r.Kind != "SIGNAL" {
			continue
		}
		var s Signal
		if err := json.Unmarshal(r.Payload, &s); err != nil {
			return nil, err
		}
		if (runID == "" || s.RunID == runID) && (contractHash == "" || s.ContractHash == contractHash) {
			out = append(out, s)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out, nil
}

func ContractFrom(rows []Record, version string) (Contract, error) {
	for _, r := range rows {
		if r.Kind == "CONTRACT" && r.ID == version {
			var v Contract
			err := json.Unmarshal(r.Payload, &v)
			if err == nil {
				err = v.Validate()
			}
			return v, err
		}
	}
	return Contract{}, fmt.Errorf("REVIEW_CONTRACT_NOT_REGISTERED: %s", version)
}
