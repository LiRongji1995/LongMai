// Package ports defines only the boundaries used by the application service.
package ports

import (
	"context"
	"encoding/json"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"time"
)

type Repository interface {
	Ready(context.Context) (c.Readiness, error)
	PutInput(context.Context, c.InputRecord) error
	Input(context.Context, string) (c.InputRecord, error)
	InputExists(context.Context, string) error
	SaveRun(context.Context, c.Run) error
	VerifyRun(context.Context, string) error
	Run(context.Context, string) (c.Run, error)
	Runs(context.Context, int, int) ([]c.Run, error)
	Items(context.Context, string, int, int) ([]c.Item, error)
	Item(context.Context, string, string) (c.Item, error)
	Submit(context.Context, c.Job) (c.Job, bool, error)
	Job(context.Context, string) (c.Job, error)
	Jobs(context.Context, string) ([]c.Job, error)
	Transition(context.Context, string, string, string, string, int) (c.Job, error)
	Events(context.Context, string) ([]c.JobEvent, error)
	Legacy(context.Context, string, string) (json.RawMessage, error)
}

type Engine interface {
	Start(context.Context, int) (context.Context, func(), error)
	Validate([]byte) (c.InputRecord, error)
	Evaluate(context.Context, c.InputRecord, c.ResearchRequest) (c.Run, error)
}

type Clock interface{ Now() time.Time }
type ProgressRepository interface {
	ReportProgress(context.Context, string, string, int) error
}
type RecoveryRepository interface {
	AbandonUnpublishedRuns(context.Context) ([]string, error)
	VerifyAbandonedRun(context.Context, string) error
}
type Calendar interface {
	Plan(time.Time) (map[string]any, error)
}
