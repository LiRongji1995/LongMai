package sqlitev2

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
)

const VerificationVersion = "V2_FULL_VERIFICATION_2_PRIVATE_SOURCE_LOCKED_TARGET"

var guardedTables = []string{"evidence_objects", "legacy_records", "history_rows", "partitions", "partition_members", "snapshots", "snapshot_members", "snapshot_partitions", "source_receipts", "legacy_result_headers", "legacy_result_items", "archived_table_rows", "historical_jobs"}

func guardDDL(table string) string {
	return fmt.Sprintf("CREATE TRIGGER %s_verification_insert BEFORE INSERT ON %s WHEN (SELECT status FROM migration_state WHERE id=1) IN ('VERIFIED','PUBLISHED') BEGIN SELECT RAISE(ABORT,'verified authority closed to inserts'); END", table, table)
}
func initVerificationGuard(ctx context.Context, db *sql.DB) error {
	tx, e := db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	if _, e = tx.ExecContext(ctx, "CREATE TABLE IF NOT EXISTS verification_guard_versions(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL)"); e != nil {
		return e
	}
	ddl := ""
	for _, table := range guardedTables {
		ddl += guardDDL(table) + ";"
	}
	var hash string
	e = tx.QueryRowContext(ctx, "SELECT checksum FROM verification_guard_versions WHERE version=1").Scan(&hash)
	if e == sql.ErrNoRows {
		if _, e = tx.ExecContext(ctx, ddl); e != nil {
			return e
		}
		if _, e = tx.ExecContext(ctx, "INSERT INTO verification_guard_versions VALUES(1,?)", digest([]byte(ddl))); e != nil {
			return e
		}
		// Old verification did not prove this protection boundary. Preserve its
		// source/checkpoints but require a new full verification before publication.
		if _, e = tx.ExecContext(ctx, "UPDATE migration_state SET status='MIGRATING',verification=NULL WHERE status IN ('VERIFIED','PUBLISHED')"); e != nil {
			return e
		}
	} else if e != nil {
		return e
	} else if hash != digest([]byte(ddl)) {
		return errors.New("VERIFICATION_GUARD_CHECKSUM")
	}
	for _, table := range guardedTables {
		var actual string
		if e = tx.QueryRowContext(ctx, "SELECT sql FROM sqlite_master WHERE name=?", table+"_verification_insert").Scan(&actual); e != nil {
			return e
		}
		if actual != guardDDL(table) {
			return errors.New("VERIFICATION_GUARD_DDL_CHANGED")
		}
	}
	return tx.Commit()
}
