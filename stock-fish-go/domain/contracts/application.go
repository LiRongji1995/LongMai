// Package contracts contains transport-independent application identities.
package contracts

import "encoding/json"

const (
	R1            = "STAGE_AWARE_RESEARCH_R1"
	V2            = "STAGE_AWARE_MOMENTUM_V2"
	SchemaVersion = 1
)

type InputRecord struct {
	ID          string `json:"input_id"`
	Raw         []byte `json:"-"`
	RawHash     string `json:"raw_sha256"`
	Observation string `json:"observation_date"`
	Cutoff      string `json:"cutoff"`
	Coverage    string `json:"coverage_type"`
}

type ResearchRequest struct {
	TargetSession  string `json:"target_session,omitempty"`
	ConfigIdentity string `json:"config_identity,omitempty"`
	Implementation string `json:"implementation_identity,omitempty"`
	EvidencePolicy string `json:"evidence_policy,omitempty"`
	InputID        string `json:"input_id"`
	Strategy       string `json:"strategy_id"`
	BudgetSeconds  int    `json:"budget_seconds"`
	IdempotencyKey string `json:"idempotency_key"`
}

type Run struct {
	JobID                 string          `json:"job_id,omitempty"`
	ID                    string          `json:"run_id"`
	InputID               string          `json:"input_id"`
	Strategy              string          `json:"strategy_id"`
	CreatedAt             string          `json:"created_at"`
	ImplementationStatus  string          `json:"implementation_status"`
	CandidateAvailability string          `json:"candidate_availability"`
	Manifest              json.RawMessage `json:"manifest"`
	Summary               json.RawMessage `json:"summary"`
	Items                 []Item          `json:"items,omitempty"`
}

type Item struct {
	ID      string          `json:"instrument_id"`
	Payload json.RawMessage `json:"detail"`
}

type Job struct {
	ID        string          `json:"job_id"`
	Key       string          `json:"idempotency_key"`
	Request   ResearchRequest `json:"request"`
	State     string          `json:"state"`
	Revision  int             `json:"revision"`
	Progress  int             `json:"progress"`
	RunID     string          `json:"run_id,omitempty"`
	Error     string          `json:"error,omitempty"`
	ParentID  string          `json:"parent_job_id,omitempty"`
	CreatedAt string          `json:"created_at"`
	UpdatedAt string          `json:"updated_at"`
}

type JobEvent struct {
	Sequence int64  `json:"sequence"`
	JobID    string `json:"job_id"`
	State    string `json:"state"`
	Reason   string `json:"reason"`
	At       string `json:"at"`
}

type Readiness struct {
	Healthy         bool   `json:"healthy"`
	Ready           bool   `json:"ready"`
	Schema          int    `json:"schema_version"`
	Migration       string `json:"migration_status"`
	DefaultStrategy string `json:"default_strategy"`
	Scheduler       string `json:"scheduler"`
	UnfinishedJobs  int    `json:"unfinished_jobs"`
	Reason          string `json:"reason,omitempty"`
}
