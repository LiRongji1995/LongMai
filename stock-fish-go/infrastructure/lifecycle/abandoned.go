package lifecycle

import (
	"context"
	"database/sql"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
)

// An abandoned prefix remains immutable evidence. Verify every disposition
// before subtracting it from unresolved PREPARING work; no timestamp/flag bypass.
func verifyAbandonedRuns(ctx context.Context, db *sql.DB) (int, error) {
	verified := 0
	repo := sqlitev2.NewReadOnlyAppRepository(db)
	for offset := 0; ; offset += 100 {
		rows, e := db.QueryContext(ctx, "SELECT entity_id FROM app_change_ledger WHERE kind='ABANDONED_RUN' ORDER BY entity_id LIMIT 100 OFFSET ?", offset)
		if e != nil {
			return 0, e
		}
		ids := []string{}
		for rows.Next() {
			var id string
			if e = rows.Scan(&id); e != nil {
				rows.Close()
				return 0, e
			}
			ids = append(ids, id)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return 0, e
		}
		for _, id := range ids {
			if e = repo.VerifyAbandonedRun(ctx, id); e != nil {
				return 0, e
			}
			verified++
		}
		if len(ids) < 100 {
			return verified, ctx.Err()
		}
	}
}
