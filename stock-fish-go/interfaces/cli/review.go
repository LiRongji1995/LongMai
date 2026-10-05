package cli

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	review "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/review"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"io"
	"os"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"
)

func reviewHelp(w io.Writer, lab bool) error {
	text := "stockfish review init|contract|register|evaluate|list|summary|export --db PATH\nWrites: init, contract, register, evaluate. All inputs are already registered; no network.\nregister --run ID; evaluate --run ID --label-input ID; --contract GO_PRICE_REVIEW_V1\nlist/export --kind CONTRACT|SIGNAL|OUTCOME; summary --run ID --as-of RFC3339\nFormats: --format json|table|csv|markdown; --output NEW_PATH for atomic export.\n"
	if lab {
		text = "stockfish research-lab propose|compare|stability|transition|list|diagnose --db PATH\nWrites: propose, compare, stability, transition. No search, training, providers, or production promotion.\npropose --file candidate.json; compare/stability --candidate ID --baseline ID --key ATTEMPT --as-of RFC3339\ntransition --candidate ID --to OFFLINE_EVALUATED|REVIEW_REQUIRED|SHADOW_ELIGIBLE|SHADOW_OBSERVING|REJECTED|INSUFFICIENT_EVIDENCE --attempt ID --reason TEXT\nCandidate JSON predeclares identities, data cutoff, time_split_contract GO_PURGED_FORWARD_EVALUATION_V2, calendar input and fixed contract.\nStability needs predeclared stability_contract GO_INDEPENDENT_HOLDOUT_V1 and stability_start/end, separated by calendar embargo. Shadow states record research admission only and start no job.\nExplicit review init adds the versioned availability extension. Real proposals require Store-generated post-commit read proofs before the first evaluation session opens; old missing or late proofs remain blocked.\n"
	}
	_, err := io.WriteString(w, text)
	return err
}

// reviewLab routes query operations through a mode=ro adapter. Explicit writes
// acquire the existing authority lease and initialize only on `review init`.
func reviewLab(parent context.Context, args []string, out, log io.Writer, sourceHash string) (result error) {
	lab := args[0] == "research-lab"
	if len(args) < 2 || args[1] == "--help" || args[1] == "help" {
		return reviewHelp(out, lab)
	}
	command := args[1]
	read := (!lab && (command == "list" || command == "summary" || command == "export")) || (lab && (command == "list" || command == "diagnose"))
	write := (!lab && (command == "init" || command == "contract" || command == "register" || command == "evaluate")) || (lab && (command == "propose" || command == "compare" || command == "stability" || command == "transition"))
	if !read && !write {
		return errors.New("UNKNOWN_REVIEW_OR_RESEARCH_LAB_COMMAND")
	}
	f := flag.NewFlagSet(args[0]+" "+command, flag.ContinueOnError)
	f.SetOutput(log)
	db := f.String("db", "", "existing published SQLite V2 database (explicit)")
	runID := f.String("run", "", "saved run identity")
	labelID := f.String("label-input", "", "registered follow-up frozen input identity")
	version := f.String("contract", review.DefaultContract().Version, "registered evaluation contract version")
	file := f.String("file", "", "contract/candidate JSON; never price labels")
	kind := f.String("kind", "", "record kind filter")
	asOf := f.String("as-of", "", "explicit RFC3339 evaluation cutoff; defaults to current UTC")
	format := f.String("format", "json", "json, table, csv, markdown")
	dest := f.String("output", "", "new atomic export path")
	candidateID := f.String("candidate", "", "candidate ID")
	baselineID := f.String("baseline", "", "registered parent baseline ID")
	key := f.String("key", "", "unique experiment attempt key; reused key must have same request")
	to := f.String("to", "", "candidate target state; production disabled")
	attemptID := f.String("attempt", "", "saved evaluation attempt ID")
	reason := f.String("reason", "", "explicit transition reason")
	seconds := f.Int("budget-seconds", 60, "bounded operation deadline, 1..600 seconds")
	if err := f.Parse(args[2:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if f.NArg() != 0 {
		return errors.New("UNEXPECTED_POSITIONAL_ARGUMENTS")
	}
	if *db == "" {
		return errors.New("EXPLICIT_V2_DATABASE_REQUIRED")
	}
	if *seconds < 1 || *seconds > 600 {
		return errors.New("REVIEW_BUDGET_MUST_BE_1_TO_600_SECONDS")
	}
	if !c.Contains([]string{"json", "table", "csv", "markdown"}, *format) {
		return errors.New("REVIEW_FORMAT_UNSUPPORTED")
	}
	path, err := canonicalDatabasePath(*db)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	asof := now
	if *asOf != "" {
		asof, err = time.Parse(time.RFC3339Nano, *asOf)
		if err != nil || asof.After(now) {
			return errors.New("REVIEW_AS_OF_INVALID_OR_FUTURE")
		}
	}
	ctx, cancel := context.WithTimeout(parent, time.Duration(*seconds)*time.Second)
	defer cancel()
	var store *sqlitev2.Store
	if write {
		canonical, release, err := lifecycle.AcquireDatabaseWriter(path, fmt.Sprintf("stockfish-review-pid-%d", os.Getpid()), false)
		if err != nil {
			return err
		}
		defer func() { result = errors.Join(result, release()) }()
		path = canonical
		store, err = sqlitev2.Open(ctx, path, false)
		if err != nil {
			return err
		}
		defer store.Close()
		if command == "init" {
			if err = store.InitReview(ctx); err != nil {
				return err
			}
			r, _ := review.NewRecord("CONTRACT", review.DefaultContract().Version, 1, review.DefaultContract())
			n, err := store.AppendReview(ctx, []review.Record{r})
			if err != nil {
				return err
			}
			return emit(out, map[string]any{"schema": review.JournalVersion, "availability_schema": review.CandidateAvailabilityVersion, "records_added": n, "production_promotion": "DISABLED"})
		}
	}
	ro, err := sqlitev2.OpenReadOnly(ctx, path)
	if err != nil {
		return err
	}
	defer ro.Close()
	rows, err := ro.ReviewRecords(ctx)
	if err != nil {
		return err
	}
	var value any
	var postWriteErr error
	appendRecords := func(records []review.Record) error { _, err := store.AppendReview(ctx, records); return err }
	loadEvidence := func(id string, b ports.ResearchBudget) (c.InputRecord, *review.Evidence, error) {
		in, err := ro.ReviewInput(ctx, id)
		if err != nil {
			return in, nil, err
		}
		frozen, err := ev.DecodeFrozenContext(ctx, in.Raw)
		if err != nil {
			return in, nil, err
		}
		var h ports.HistoryReader
		if frozen.Input.HistoryRef != "" {
			history, err := ro.ReviewHistory(ctx, frozen.Input.HistoryRef, frozen.Input.HistoryHash, b)
			if err != nil {
				return in, nil, err
			}
			h = history
		}
		evidence, err := review.NewEvidence(ctx, frozen, h)
		return in, evidence, err
	}
	if !lab {
		switch command {
		case "contract":
			var v review.Contract
			if err = reviewJSON(*file, &v); err != nil {
				return err
			}
			if err = v.Validate(); err != nil {
				return err
			}
			r, _ := review.NewRecord("CONTRACT", v.Version, 1, v)
			if err = appendRecords([]review.Record{r}); err != nil {
				return err
			}
			value = v
		case "register":
			if *runID == "" {
				return errors.New("REVIEW_RUN_REQUIRED")
			}
			contract, err := review.ContractFrom(rows, *version)
			if err != nil {
				return err
			}
			existing, err := review.Signals(rows, *runID, review.Digest(contract))
			if err != nil {
				return err
			}
			if len(existing) > 0 {
				value = map[string]any{"run_id": *runID, "signals": len(existing), "created": false}
				break
			}
			b, err := resources.NewBudget(ctx, time.Duration(*seconds)*time.Second, nil)
			if err != nil {
				return err
			}
			defer b.Close()
			ctx = b.Ctx()
			run, err := ro.Saved(ctx, *runID, "", true)
			if err != nil {
				return err
			}
			in, evidence, err := loadEvidence(run.InputID, b)
			if err != nil {
				return err
			}
			publication, err := ro.ReviewAvailabilityLowerBound(ctx, run, in.Coverage)
			if err != nil {
				return err
			}
			signals, err := review.Register(ctx, run, in, evidence, contract, publication, time.Now().UTC())
			if err != nil {
				return err
			}
			records := make([]review.Record, 0, len(signals))
			for _, s := range signals {
				r, _ := review.NewRecord("SIGNAL", s.ID, 1, s)
				records = append(records, r)
			}
			if err = appendRecords(records); err != nil {
				return err
			}
			value = map[string]any{"run_id": *runID, "signals": len(signals), "created": true, "evaluation_contract_sha256": review.Digest(contract), "verification_scope": "FULL_SAVED_RUN_CLOSURE"}
		case "evaluate":
			if *runID == "" || *labelID == "" {
				return errors.New("REVIEW_RUN_AND_REGISTERED_LABEL_INPUT_REQUIRED")
			}
			contract, err := review.ContractFrom(rows, *version)
			if err != nil {
				return err
			}
			signals, err := review.Signals(rows, *runID, review.Digest(contract))
			if err != nil {
				return err
			}
			if len(signals) == 0 {
				return errors.New("REVIEW_SIGNALS_NOT_REGISTERED")
			}
			b, err := resources.NewBudget(ctx, time.Duration(*seconds)*time.Second, nil)
			if err != nil {
				return err
			}
			defer b.Close()
			ctx = b.Ctx()
			label, evidence, err := loadEvidence(*labelID, b)
			if err != nil {
				return err
			}
			labelTime, err := time.Parse(time.RFC3339Nano, label.Cutoff)
			if err != nil || labelTime.After(now) {
				return errors.New("REVIEW_LABEL_CUTOFF_INVALID_OR_FUTURE")
			}
			outcomes, err := review.Evaluate(ctx, signals, label, evidence, contract, sourceHash)
			if err != nil {
				return err
			}
			latest, err := review.LatestOutcomes(rows, now)
			if err != nil {
				return err
			}
			records := []review.Record{}
			known := map[string]bool{}
			for _, r := range rows {
				if r.Kind == "OUTCOME" {
					known[r.ID] = true
				}
			}
			for _, v := range outcomes {
				if known[v.ID] {
					continue
				}
				if old, ok := latest[v.SignalID+":"+strconv.Itoa(v.Window)]; ok {
					t, _ := time.Parse(time.RFC3339Nano, old.LabelCutoff)
					if !labelTime.After(t) {
						return errors.New("REVIEW_REVISION_REQUIRES_NEWER_ACTUAL_CUTOFF")
					}
					v.Supersedes = old.ID
				}
				r, _ := review.NewRecord("OUTCOME", v.ID, 1, v)
				records = append(records, r)
			}
			if err = appendRecords(records); err != nil {
				return err
			}
			value = map[string]any{"run_id": *runID, "label_input_id": *labelID, "outcome_records_added": len(records), "fixed_windows": contract.Windows, "execution_status": "NOT_SIMULATED; NOT_USER_ACCOUNT"}
		case "list", "export":
			value = filterReview(rows, *kind, *runID)
		case "summary":
			contract, err := review.ContractFrom(rows, *version)
			if err != nil {
				return err
			}
			signals, err := review.Signals(rows, *runID, review.Digest(contract))
			if err != nil {
				return err
			}
			latest, err := review.LatestOutcomes(rows, asof)
			if err != nil {
				return err
			}
			value = review.Summarize(signals, latest, contract)
		}
	} else {
		switch command {
		case "propose":
			var v review.Candidate
			if err = reviewJSON(*file, &v); err != nil {
				return err
			}
			// An identical saved proposal keeps its original registration clock.
			// Replaying it after evaluation starts does not register a new proposal.
			if old, found, e := savedCandidateProposal(rows, v); e != nil {
				return e
			} else if found {
				value, postWriteErr = completeCandidateProposal(ctx, store, rows, old)
				break
			}
			contract, err := review.ContractFrom(rows, v.ContractVersion)
			if err != nil {
				return err
			}
			in, err := ro.ReviewInput(ctx, v.CalendarInputID)
			if err != nil {
				return err
			}
			if in.RawHash != v.CalendarHash {
				return errors.New("CANDIDATE_CALENDAR_IDENTITY_MISMATCH")
			}
			frozen, err := ev.DecodeFrozenContext(ctx, in.Raw)
			if err != nil {
				return err
			}
			if v.FixtureOnly != (in.Coverage == "TEST_FIXTURE") {
				return errors.New("CANDIDATE_FIXTURE_REAL_DATA_MIX_FORBIDDEN")
			}
			if !v.FixtureOnly {
				inputCutoff, e := time.Parse(time.RFC3339Nano, in.Cutoff)
				allowed, e2 := time.Parse(time.RFC3339Nano, v.DataCutoff)
				if e != nil || e2 != nil || inputCutoff.After(allowed) {
					return errors.New("CANDIDATE_CALENDAR_INPUT_NOT_VISIBLE_BY_ALLOWED_CUTOFF")
				}
			}
			registeredAt := time.Now().UTC()
			v.CreatedAt = registeredAt.Format(time.RFC3339Nano)
			if err = v.Validate(contract, frozen.Input.Sessions, registeredAt); err != nil {
				return err
			}
			count := 0
			for _, r := range rows {
				if r.Kind == "CANDIDATE" {
					var old review.Candidate
					if err = json.Unmarshal(r.Payload, &old); err != nil {
						return err
					}
					if old.Role == "CANDIDATE" && old.Parent == v.Parent {
						count++
					}
				}
			}
			if v.Role == "CANDIDATE" {
				parent, err := candidateParentDeclaration(rows, v.Parent)
				if err != nil {
					return err
				}
				if parent.FixtureOnly != v.FixtureOnly || parent.Scope != v.Scope || parent.ContractHash != v.ContractHash || parent.TimeSplitContract != v.TimeSplitContract || parent.EvaluationStart != v.EvaluationStart || parent.EvaluationEnd != v.EvaluationEnd {
					return errors.New("CANDIDATE_PARENT_FIXED_SCOPE_OR_CONTRACT_MISMATCH")
				}
				if parent.StabilityContract != v.StabilityContract || parent.StabilityStart != v.StabilityStart || parent.StabilityEnd != v.StabilityEnd || (v.StabilityContract != "" && (parent.CalendarInputID != v.CalendarInputID || parent.CalendarHash != v.CalendarHash)) {
					return errors.New("CANDIDATE_PARENT_INDEPENDENT_STABILITY_DECLARATION_MISMATCH")
				}
				if count >= 2 {
					return errors.New("RESEARCH_CANDIDATE_LIMIT_TWO")
				}
			}
			r, _ := review.NewRecord("CANDIDATE", v.ID, 1, v)
			if err = appendRecords([]review.Record{r}); err != nil {
				return err
			}
			value, postWriteErr = completeCandidateProposal(ctx, store, append(append([]review.Record{}, rows...), r), v)
		case "compare", "stability":
			phase := review.PrimaryComparisonPhase
			if command == "stability" {
				phase = review.IndependentStabilityPhase
			}
			if !ev.ValidHash(sourceHash) {
				return errors.New("EXPERIMENT_BUILD_SHA256_IDENTITY_REQUIRED")
			}
			if *key == "" || *candidateID == "" || *baselineID == "" {
				return errors.New("EXPERIMENT_CANDIDATE_BASELINE_AND_KEY_REQUIRED")
			}
			for _, r := range rows {
				if r.Kind == "EVALUATION" && r.ID == *key {
					var old review.Evaluation
					if err = json.Unmarshal(r.Payload, &old); err != nil {
						return err
					}
					if old.CandidateID != *candidateID || old.BaselineID != *baselineID || (*asOf != "" && old.AsOf != asof.UTC().Format(time.RFC3339Nano)) {
						return errors.New("EXPERIMENT_ATTEMPT_KEY_CONFLICT")
					}
					oldPhase := old.Phase
					if oldPhase == "" {
						oldPhase = review.PrimaryComparisonPhase
					}
					if oldPhase != phase {
						return errors.New("EXPERIMENT_ATTEMPT_KEY_PHASE_CONFLICT")
					}
					value = old
					if old.Status == "ENGINEERING_ERROR" {
						postWriteErr = errors.New("SAVED_EXPERIMENT_ENGINEERING_ERROR: " + strings.Join(old.Reasons, "; "))
					}
				}
			}
			if value != nil {
				break
			}
			candidate, err := review.CandidateFrom(rows, *candidateID)
			if err != nil && !errors.Is(err, review.ErrPreregistrationPublicationUnproven) {
				return err
			}
			baseline, err := review.CandidateFrom(rows, *baselineID)
			if err != nil && !errors.Is(err, review.ErrPreregistrationPublicationUnproven) {
				return err
			}
			contract, err := review.ContractFrom(rows, candidate.ContractVersion)
			if err != nil {
				return err
			}
			var v review.Evaluation
			if command == "stability" {
				calendar, loadErr := ro.ReviewInput(ctx, candidate.CalendarInputID)
				v, err = review.Stability(rows, candidate, baseline, contract, calendar, asof, *key)
				err = errors.Join(err, loadErr)
			} else {
				v, err = review.Compare(rows, candidate, baseline, contract, asof, *key)
			}
			v.EvaluatorBuild = sourceHash
			if err != nil {
				v.Status = "ENGINEERING_ERROR"
				v.Reasons = append(v.Reasons, err.Error())
				postWriteErr = err
			}
			r, _ := review.NewRecord("EVALUATION", v.ID, 1, v)
			if err = appendRecords([]review.Record{r}); err != nil {
				return err
			}
			value = v
		case "transition":
			candidate, err := review.CandidateFrom(rows, *candidateID)
			if err != nil && !(errors.Is(err, review.ErrPreregistrationPublicationUnproven) && (*to == "REJECTED" || *to == "INSUFFICIENT_EVIDENCE")) {
				return err
			}
			v, err := review.TransitionCandidate(rows, candidate, *to, *attemptID, *reason, now)
			if err != nil {
				return err
			}
			revision := 1
			for _, r := range rows {
				if r.Kind == "TRANSITION" && r.ID == candidate.ID {
					revision++
				}
			}
			r, _ := review.NewRecord("TRANSITION", candidate.ID, revision, v)
			if err = appendRecords([]review.Record{r}); err != nil {
				return err
			}
			value = v
		case "list":
			value = filterReview(rows, *kind, "")
		case "diagnose":
			contract, err := review.ContractFrom(rows, *version)
			if err != nil {
				return err
			}
			signals, err := review.Signals(rows, *runID, review.Digest(contract))
			if err != nil {
				return err
			}
			latest, err := review.LatestOutcomes(rows, asof)
			if err != nil {
				return err
			}
			value = review.Diagnose(review.Summarize(signals, latest, contract))
		}
	}
	writeValue := func(w io.Writer) error { return reviewOutput(w, value, *format) }
	if *dest != "" {
		err = atomicExport(ctx, *dest, writeValue)
	} else {
		err = writeValue(out)
	}
	return errors.Join(err, postWriteErr)
}

func savedCandidateProposal(rows []review.Record, proposal review.Candidate) (review.Candidate, bool, error) {
	for _, r := range rows {
		if r.Kind != "CANDIDATE" || r.ID != proposal.ID {
			continue
		}
		var old review.Candidate
		if err := json.Unmarshal(r.Payload, &old); err != nil {
			return old, true, err
		}
		proposal.CreatedAt = old.CreatedAt
		if review.Digest(proposal) != review.Digest(old) {
			return old, true, errors.New("CANDIDATE_IMMUTABLE_PROPOSAL_CONFLICT")
		}
		return old, true, nil
	}
	return review.Candidate{}, false, nil
}

// A parent declaration can bind a child declaration without establishing
// pre-registration availability. Comparison and admission retain their proof
// requirement; no other parent read error is suppressed here.
func candidateParentDeclaration(rows []review.Record, id string) (review.Candidate, error) {
	parent, err := review.CandidateFrom(rows, id)
	if err != nil && !errors.Is(err, review.ErrPreregistrationPublicationUnproven) {
		return parent, err
	}
	if parent.Role != "BASELINE" {
		return parent, errors.New("CANDIDATE_PARENT_BASELINE_REQUIRED")
	}
	return parent, nil
}

func candidateProposalOutput(v review.Candidate, state string) map[string]any {
	availability := "EXPLICIT_TEST_FIXTURE; NOT_REAL_PREREGISTRATION"
	if !v.FixtureOnly {
		availability = review.ErrPreregistrationPublicationUnproven.Error()
	}
	return map[string]any{"candidate": v, "state": state, "preregistration_availability": availability}
}

type candidateAvailabilityWriter interface {
	ObserveCandidateAvailability(context.Context, string) (review.CandidateAvailability, bool, error)
}

func completeCandidateProposal(ctx context.Context, store candidateAvailabilityWriter, rows []review.Record, v review.Candidate) (map[string]any, error) {
	value := candidateProposalOutput(v, review.CandidateState(rows, v.ID))
	value["declaration_saved"] = true
	if v.FixtureOnly {
		value["operation_status"] = "SAVED_TEST_FIXTURE"
		value["proof_commit_status"] = "NOT_APPLICABLE_TEST_FIXTURE"
		return value, nil
	}
	p, created, err := store.ObserveCandidateAvailability(ctx, v.ID)
	if err != nil {
		value["operation_status"] = "PARTIAL_DECLARATION_SAVED"
		value["proof_commit_status"] = "FAILED_OR_UNPROVEN"
		value["reason"] = err.Error()
		return value, err
	}
	proof, _ := review.NewRecord(review.CandidateAvailabilityKind, v.ID, 1, p)
	value["proof_commit_status"] = "COMMITTED"
	value["proof_created"] = created
	value["preregistration_proof"] = p
	value["preregistration_proof_sha256"] = proof.Hash
	withProof := append([]review.Record{}, rows...)
	found := false
	for _, r := range withProof {
		if r.Kind == proof.Kind && r.ID == proof.ID {
			found = true
		}
	}
	if !found {
		withProof = append(withProof, proof)
	}
	if _, _, err = review.CandidateAvailabilityFrom(withProof, v.ID); err != nil {
		value["operation_status"] = "SAVED_ADMISSION_BLOCKED"
		value["preregistration_availability"] = err.Error()
		return value, err
	}
	value["operation_status"] = "SAVED_AND_VERIFIED_AVAILABLE"
	value["preregistration_availability"] = "VERIFIED_AVAILABLE_BEFORE_FIRST_EVALUATION_SESSION"
	return value, nil
}

func reviewJSON(path string, value any) error {
	if path == "" {
		return errors.New("REVIEW_EXPLICIT_JSON_FILE_REQUIRED")
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, 1<<20+1))
	if err != nil {
		return err
	}
	if len(raw) > 1<<20 {
		return errors.New("REVIEW_DOCUMENT_LIMIT")
	}
	d := json.NewDecoder(strings.NewReader(string(raw)))
	d.DisallowUnknownFields()
	if err = d.Decode(value); err != nil {
		return err
	}
	var extra any
	if err = d.Decode(&extra); !errors.Is(err, io.EOF) {
		return errors.New("REVIEW_TRAILING_JSON")
	}
	return nil
}

func filterReview(rows []review.Record, kind, run string) []review.Record {
	out := []review.Record{}
	for _, r := range rows {
		if kind != "" && r.Kind != kind {
			continue
		}
		if run != "" {
			var v struct {
				RunID string `json:"run_id"`
			}
			if json.Unmarshal(r.Payload, &v) != nil || v.RunID != run {
				continue
			}
		}
		out = append(out, r)
	}
	return out
}

// Non-JSON forms preserve full JSON per row rather than silently dropping
// condition trees, exact rational numbers, nulls, or revision links.
func reviewOutput(w io.Writer, value any, format string) error {
	if format == "json" {
		return emit(w, value)
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	var rows []json.RawMessage
	if len(raw) > 0 && raw[0] == '[' {
		if err = json.Unmarshal(raw, &rows); err != nil {
			return err
		}
	} else {
		rows = []json.RawMessage{raw}
	}
	if format == "csv" {
		c := csv.NewWriter(w)
		if err = c.Write([]string{"row", "complete_json"}); err != nil {
			return err
		}
		for i, row := range rows {
			if err = c.Write([]string{strconv.Itoa(i + 1), string(row)}); err != nil {
				return err
			}
		}
		c.Flush()
		return c.Error()
	}
	if format == "markdown" {
		if _, err = io.WriteString(w, "| row | complete JSON |\n| --- | --- |\n"); err != nil {
			return err
		}
		for i, row := range rows {
			if _, err = fmt.Fprintf(w, "| %d | %s |\n", i+1, strings.ReplaceAll(string(row), "|", "\\|")); err != nil {
				return err
			}
		}
		return nil
	}
	t := tabwriter.NewWriter(w, 0, 2, 2, ' ', 0)
	if _, err = io.WriteString(t, "ROW\tCOMPLETE JSON\n"); err != nil {
		return err
	}
	for i, row := range rows {
		if _, err = fmt.Fprintf(t, "%d\t%s\n", i+1, row); err != nil {
			return err
		}
	}
	return t.Flush()
}
