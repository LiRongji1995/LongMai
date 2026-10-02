package ports

import (
	"context"
	"encoding/json"
)

// ResearchQueries presents saved native and legacy results without invoking a
// strategy, rewriting an old run, or requiring a Python interpreter.
type ResearchQueries interface {
	InputObservation(context.Context, string) (string, error)
	ResearchRuns(context.Context, int, int) ([]json.RawMessage, error)
	ResearchSummary(context.Context, string, string, string, int, int) (json.RawMessage, error)
	ResearchItem(context.Context, string, string) (json.RawMessage, error)
	ResearchReview(context.Context, string, bool) (json.RawMessage, error)
	ResearchExport(context.Context, string, string, string, string) ([]byte, string, error)
}
