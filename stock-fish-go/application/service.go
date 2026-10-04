package application

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"io"
	"sync"
	"time"
)

type wallClock struct{}

func (wallClock) Now() time.Time { return time.Now().UTC() }

type Service struct {
	Store    ports.Repository
	Engine   ports.Engine
	Clock    ports.Clock
	Calendar ports.Calendar
	mu       sync.Mutex
	active   map[string]context.CancelFunc
	worker   sync.Mutex
}

func New(store ports.Repository, engine ports.Engine) *Service {
	return &Service{Store: store, Engine: engine, Clock: wallClock{}, active: map[string]context.CancelFunc{}}
}
func ID(prefix string) string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return prefix + hex.EncodeToString(b[:])
}
func (s *Service) now() string { return s.Clock.Now().UTC().Format(time.RFC3339Nano) }

func (s *Service) Import(ctx context.Context, raw []byte) (c.InputRecord, error) {
	if err := ctx.Err(); err != nil {
		return c.InputRecord{}, err
	}
	in, err := s.Engine.Validate(raw)
	if err != nil {
		return in, err
	}
	err = s.Store.PutInput(ctx, in)
	return in, err
}
func validateRequest(r *c.ResearchRequest) error {
	if r.InputID == "" || r.IdempotencyKey == "" {
		return errors.New("INPUT_AND_IDEMPOTENCY_KEY_REQUIRED")
	}
	if r.Strategy == "" {
		r.Strategy = c.V2
	}
	if r.Strategy != c.V2 && r.Strategy != c.R1 {
		return errors.New("UNSUPPORTED_STRATEGY")
	}
	if r.BudgetSeconds == 0 {
		r.BudgetSeconds = 300
	}
	if r.BudgetSeconds < 1 || r.BudgetSeconds > 1800 {
		return errors.New("BUDGET_MUST_BE_1_TO_1800_SECONDS")
	}
	return nil
}
func (s *Service) Submit(ctx context.Context, r c.ResearchRequest) (c.Job, bool, error) {
	if err := validateRequest(&r); err != nil {
		return c.Job{}, false, err
	}
	ready, err := s.Store.Ready(ctx)
	if err != nil {
		return c.Job{}, false, err
	}
	if !ready.Ready {
		return c.Job{}, false, fmt.Errorf("DATABASE_NOT_READY: %s", ready.Reason)
	}
	if err = s.Store.InputExists(ctx, r.InputID); err != nil {
		return c.Job{}, false, err
	}
	j := c.Job{ID: ID("job_"), Key: r.IdempotencyKey, Request: r, State: "QUEUED", CreatedAt: s.now(), UpdatedAt: s.now()}
	return s.Store.Submit(ctx, j)
}

// Execute owns the single application worker. HTTP requests enqueue only; a
// process-owned worker supplies ctx so disconnects cannot abandon task state.
func (s *Service) Execute(parent context.Context, id string) (c.Job, error) {
	s.worker.Lock()
	defer s.worker.Unlock()
	j, err := s.Store.Job(parent, id)
	if err != nil {
		return j, err
	}
	if j.State != "QUEUED" {
		return j, fmt.Errorf("JOB_NOT_QUEUED: %s", j.State)
	}
	ctx, cancel := context.WithTimeout(parent, time.Duration(j.Request.BudgetSeconds)*time.Second)
	defer cancel()
	// A queued request is not a readiness reservation. Recheck after acquiring
	// the worker, before any engine work or transition to RUNNING.
	ready, err := s.Store.Ready(ctx)
	if err != nil {
		return j, err
	}
	if !ready.Ready {
		return j, fmt.Errorf("DATABASE_NOT_READY: %s", ready.Reason)
	}
	s.mu.Lock()
	s.active[id] = cancel
	s.mu.Unlock()
	defer func() { cancel(); s.mu.Lock(); delete(s.active, id); s.mu.Unlock() }()
	j, err = s.Store.Transition(ctx, id, "RUNNING", "WORKER_STARTED", "", 0)
	if err != nil {
		return j, err
	}
	if progress, ok := s.Store.(ports.ProgressRepository); ok {
		lastStage, lastCount, lastAt := "", 0, time.Now()
		ctx = ports.WithProgressReporter(ctx, func(stage string, count int) error {
			if stage == lastStage && count-lastCount < 100 && time.Since(lastAt) < 5*time.Second {
				return nil
			}
			if e := progress.ReportProgress(ctx, id, stage, count); e != nil {
				return e
			}
			lastStage, lastCount, lastAt = stage, count, time.Now()
			return nil
		})
	}
	workCtx, release, resourceErr := s.Engine.Start(ctx, j.Request.BudgetSeconds)
	if resourceErr != nil {
		cleanup, done := context.WithTimeout(context.Background(), 5*time.Second)
		defer done()
		current, readErr := s.Store.Job(cleanup, id)
		if readErr == nil && current.State == "CANCELLED" {
			return current, resourceErr
		}
		failed, saveErr := s.Store.Transition(cleanup, id, "FAILED", resourceErr.Error(), "", 0)
		return failed, errors.Join(resourceErr, saveErr)
	}
	defer release()
	ctx = workCtx
	in, err := s.Store.Input(ctx, j.Request.InputID)
	var run c.Run
	if err == nil {
		run, err = s.Engine.Evaluate(ctx, in, j.Request)
	}
	if err == nil {
		if run.ID == "" {
			run.ID = ID("go_run_")
		}
		run.JobID = j.ID
		run.InputID = in.ID
		run.Strategy = j.Request.Strategy
		run.CreatedAt = s.now()
		ports.BindMetrics(ports.BudgetFromContext(ctx), run.ID, in.ID, in.RawHash, "")
		err = s.Store.SaveRun(ctx, run)
	}
	if err != nil {
		// Resource enforcement cancels SQL through context. Preserve the actual
		// budget cause instead of misclassifying that cancellation as user intent.
		if b := ports.BudgetFromContext(ctx); b != nil {
			if cause := b.Check(); cause != nil && !errors.Is(cause, context.Canceled) && !errors.Is(cause, context.DeadlineExceeded) {
				err = fmt.Errorf("%w; operation: %v", cause, err)
			}
		}
		state := "FAILED"
		if errors.Is(err, context.Canceled) {
			state = "CANCELLED"
		}
		if errors.Is(err, context.DeadlineExceeded) {
			state = "FAILED"
		}
		// A bounded cleanup context records an obtained failure after the work
		// deadline. It never publishes a result or continues strategy work.
		cleanup, done := context.WithTimeout(context.Background(), 5*time.Second)
		defer done()
		current, readErr := s.Store.Job(cleanup, id)
		if readErr == nil && current.State == "CANCELLED" {
			return current, err
		}
		if readErr == nil {
			j.Progress = current.Progress
		}
		failure := map[string]any{"error": err.Error(), "attempted_run_id": run.ID, "input_id": j.Request.InputID, "input_bytes_hash": in.RawHash, "observation_date": in.Observation, "cutoff": in.Cutoff, "strategy_id": j.Request.Strategy, "budget_seconds": j.Request.BudgetSeconds}
		if json.Valid(run.Manifest) {
			failure["obtained_manifest"] = run.Manifest
		}
		if b := ports.BudgetFromContext(ctx); b != nil {
			failure["metrics"] = b.Snapshot()
		}
		failureRaw, _ := json.Marshal(failure)
		failed, saveErr := s.Store.Transition(cleanup, id, state, string(failureRaw), "", j.Progress)
		if saveErr != nil {
			return failed, errors.Join(err, saveErr)
		}
		return failed, err
	}
	return s.Store.Job(ctx, id)
}

func (s *Service) Cancel(ctx context.Context, id string) (c.Job, error) {
	j, err := s.Store.Job(ctx, id)
	if err != nil {
		return j, err
	}
	if j.State != "QUEUED" && j.State != "RUNNING" {
		return j, errors.New("JOB_NOT_CANCELLABLE")
	}
	j, err = s.Store.Transition(ctx, id, "CANCELLED", "EXPLICIT_CANCELLATION", "", j.Progress)
	if err != nil {
		return j, err
	}
	s.mu.Lock()
	if fn := s.active[id]; fn != nil {
		fn()
	}
	s.mu.Unlock()
	return j, nil
}

// Recover is explicit, audited and never executes an imported legacy task.
func (s *Service) Recover(ctx context.Context) ([]c.Job, error) {
	s.mu.Lock()
	n := len(s.active)
	s.mu.Unlock()
	if n > 0 {
		return nil, errors.New("ACTIVE_WORKER_RECOVERY_FORBIDDEN")
	}
	jobs, err := s.Store.Jobs(ctx, "")
	if err != nil {
		return nil, err
	}
	out := []c.Job{}
	for _, j := range jobs {
		if j.State == "RUNNING" || j.State == "QUEUED" {
			j, err = s.Store.Transition(ctx, j.ID, "INTERRUPTED", "EXPLICIT_RESTART_RECONCILIATION", "", j.Progress)
			if err != nil {
				return out, err
			}
			out = append(out, j)
		}
	}
	if recovery, ok := s.Store.(ports.RecoveryRepository); ok {
		if _, err = recovery.AbandonUnpublishedRuns(ctx); err != nil {
			return out, err
		}
	}
	return out, nil
}
func (s *Service) Resume(ctx context.Context, id, key string) (c.Job, bool, error) {
	j, err := s.Store.Job(ctx, id)
	if err != nil {
		return j, false, err
	}
	if j.State != "INTERRUPTED" && j.State != "FAILED" && j.State != "CANCELLED" {
		return j, false, errors.New("JOB_NOT_RESUMABLE")
	}
	ready, err := s.Store.Ready(ctx)
	if err != nil {
		return j, false, err
	}
	if !ready.Ready {
		return j, false, errors.New("DATABASE_NOT_READY")
	}
	if err = s.Store.InputExists(ctx, j.Request.InputID); err != nil {
		return j, false, err
	}
	r := j.Request
	r.IdempotencyKey = key
	if err = validateRequest(&r); err != nil {
		return j, false, err
	}
	n := c.Job{ID: ID("job_"), Key: key, Request: r, State: "QUEUED", ParentID: j.ID, CreatedAt: s.now(), UpdatedAt: s.now()}
	return s.Store.Submit(ctx, n)
}
func (s *Service) Export(ctx context.Context, id string) (json.RawMessage, error) {
	var out bytes.Buffer
	if e := s.ExportTo(ctx, id, &out); e != nil {
		return nil, e
	}
	return out.Bytes(), nil
}

// ExportTo streams the exact saved envelope and ordered records. No strategy is
// invoked by any query or export operation.
func (s *Service) ExportTo(ctx context.Context, id string, w io.Writer) error {
	if e := s.Store.VerifyRun(ctx, id); e != nil {
		return e
	}
	r, e := s.Store.Run(ctx, id)
	if e != nil {
		return e
	}
	r.Items = nil
	header, e := json.Marshal(r)
	if e != nil {
		return e
	}
	if _, e = w.Write(header[:len(header)-1]); e != nil {
		return e
	}
	if _, e = io.WriteString(w, ",\"items\":["); e != nil {
		return e
	}
	first := true
	for offset := 0; ; offset += 100 {
		items, err := s.Store.Items(ctx, id, 100, offset)
		if err != nil {
			return err
		}
		for _, item := range items {
			if e = ctx.Err(); e != nil {
				return e
			}
			if !first {
				if _, e = io.WriteString(w, ","); e != nil {
					return e
				}
			}
			first = false
			raw, err := json.Marshal(item)
			if err != nil {
				return err
			}
			if _, err = w.Write(raw); err != nil {
				return err
			}
		}
		if len(items) < 100 {
			break
		}
	}
	_, e = io.WriteString(w, "]}\n")
	return e
}

// A scheduler plan is side-effect free. Isolated instances remain paused until
// a distinct production activation is authorized; legacy queued work is inert.
func (s *Service) SchedulePlan(ctx context.Context) (map[string]any, error) {
	r, e := s.Store.Ready(ctx)
	if e != nil {
		return nil, e
	}
	plan := map[string]any{"state": "PAUSED", "default_strategy": c.V2, "external_effects": false, "database_ready": r.Ready}
	if s.Calendar == nil {
		plan["calendar_status"] = "UNAVAILABLE"
		return plan, nil
	}
	dates, e := s.Calendar.Plan(s.Clock.Now())
	if e != nil {
		return nil, e
	}
	for k, v := range dates {
		plan[k] = v
	}
	plan["calendar_status"] = "VERIFIED_FINITE_COVERAGE"
	return plan, nil
}
