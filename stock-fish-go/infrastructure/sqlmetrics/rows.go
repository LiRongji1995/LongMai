// Package sqlmetrics measures application-observed SQLite call time. These are
// SQL preparation/execution/row-conversion durations, not physical disk I/O.
package sqlmetrics

import (
	"context"
	"database/sql"
	"time"
)

type Queryer interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}
type Rows struct {
	rows    *sql.Rows
	elapsed *time.Duration
}

func Query(ctx context.Context, q Queryer, elapsed *time.Duration, statement string, args ...any) (*Rows, error) {
	start := time.Now()
	r, e := q.QueryContext(ctx, statement, args...)
	*elapsed += time.Since(start)
	if e != nil {
		return nil, e
	}
	return &Rows{r, elapsed}, nil
}
func (r *Rows) Next() bool {
	start := time.Now()
	ok := r.rows.Next()
	*r.elapsed += time.Since(start)
	return ok
}
func (r *Rows) Scan(args ...any) error {
	start := time.Now()
	e := r.rows.Scan(args...)
	*r.elapsed += time.Since(start)
	return e
}
func (r *Rows) Err() error   { return r.rows.Err() }
func (r *Rows) Close() error { return r.rows.Close() }
