package sqlitev2

import (
	"context"
	"database/sql"
	"errors"
)

// Run publication checks authority in the publication transaction, even when a
// caller bypasses the application queue. Missing projections are never repaired
// implicitly by saving a result.
func requirePublishedForRun(ctx context.Context, tx *sql.Tx) error {
	var status string
	if err := tx.QueryRowContext(ctx, "SELECT status FROM migration_state WHERE id=1").Scan(&status); err != nil {
		return err
	}
	if status != "PUBLISHED" {
		return errors.New("MIGRATION_NOT_PUBLISHED")
	}
	_, err := batchCurrentIdentity(ctx, tx)
	return err
}
