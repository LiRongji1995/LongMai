package ports

import (
	"context"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	f "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
)

type ResearchBudget interface {
	Ctx() context.Context
	Check() error
	Read(int64) error
	Output(int64) error
	Expanded(int64) error
	IndexRow() error
	Stage(string)
	Processed(int)
	Snapshot() c.Metrics
}
type HistoryReader interface {
	VerifyIndex() error
	Bars(string, string, string, func(c.Row) error) error
	Receipts() []c.Row
	Identity() string
}
type FactsCache interface {
	GetFact(context.Context, string) (f.StockFacts, bool, error)
	PutFact(context.Context, string, f.StockFacts) error
}
type HistoryIdentityReader interface {
	BarsWithIdentity(string, string, string, func(c.Row, c.EvidenceIdentity) error) error
}

type budgetContextKey struct{}
type progressContextKey struct{}
type ProgressReporter func(string, int) error

func WithProgressReporter(ctx context.Context, report ProgressReporter) context.Context {
	return context.WithValue(ctx, progressContextKey{}, report)
}
func ProgressFromContext(ctx context.Context) ProgressReporter {
	report, _ := ctx.Value(progressContextKey{}).(ProgressReporter)
	return report
}

func WithBudget(ctx context.Context, b ResearchBudget) context.Context {
	return context.WithValue(ctx, budgetContextKey{}, b)
}
func BudgetFromContext(ctx context.Context) ResearchBudget {
	b, _ := ctx.Value(budgetContextKey{}).(ResearchBudget)
	return b
}

// Observe records explicitly measured components; components may be nested and
// must not be summed as disjoint wall-clock stages.
func Observe(b ResearchBudget, label string, seconds float64) {
	if p, ok := b.(interface{ Observe(string, float64) }); ok {
		p.Observe(label, seconds)
	}
}
func CacheRead(b ResearchBudget, n int64) {
	if p, ok := b.(interface{ CacheRead(int64) }); ok {
		p.CacheRead(n)
	}
}
func HistoryPayload(b ResearchBudget, n int64, unique bool) {
	if p, ok := b.(interface{ HistoryPayload(int64, bool) }); ok {
		p.HistoryPayload(n, unique)
	}
}

// CacheObservation measures logical payload occupancy, never physical disk IO.
// A nil occupancy explicitly means it could not be measured.
func CacheObservation(b ResearchBudget, outcome string, occupancy *int64) {
	if p, ok := b.(interface{ CacheObservation(string, *int64) }); ok {
		p.CacheObservation(outcome, occupancy)
	}
}
func BindMetrics(b ResearchBudget, run, input, hash, source string) {
	if p, ok := b.(interface {
		BindMetrics(string, string, string, string)
	}); ok {
		p.BindMetrics(run, input, hash, source)
	}
}
