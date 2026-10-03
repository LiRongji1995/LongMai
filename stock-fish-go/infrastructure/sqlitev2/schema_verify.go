package sqlitev2

import (
	"context"
	"database/sql"
	"fmt"
)

// VerifySchema checks the real table/index/trigger definitions, not merely a
// checksum stored in an otherwise mutable metadata row. Application extension
// tables are checked by their owner and are allowed alongside this authority DDL.
func (s *Store) VerifySchema(ctx context.Context) error { return VerifySchemaDB(ctx, s.db) }
func VerifySchemaDB(ctx context.Context, db *sql.DB) error {
	expected, e := sql.Open("sqlite", ":memory:")
	if e != nil {
		return e
	}
	defer expected.Close()
	expected.SetMaxOpenConns(1)
	if _, e = expected.ExecContext(ctx, schema); e != nil {
		return e
	}
	for _, t := range []string{"evidence_objects", "legacy_records", "history_rows", "partitions", "partition_members", "snapshots", "snapshot_members", "snapshot_partitions", "source_receipts", "legacy_result_headers", "legacy_result_items", "archived_table_rows", "historical_jobs"} {
		for _, op := range []string{"UPDATE", "DELETE"} {
			if _, e = expected.ExecContext(ctx, fmt.Sprintf("CREATE TRIGGER %s_no_%s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT,'immutable evidence'); END", t, op, op, t)); e != nil {
				return e
			}
		}
	}
	for _, table := range guardedTables {
		if _, e = expected.ExecContext(ctx, guardDDL(table)); e != nil {
			return e
		}
	}
	rows, e := expected.QueryContext(ctx, "SELECT name,type,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")
	if e != nil {
		return e
	}
	defer rows.Close()
	for rows.Next() {
		var name, kind, want, actual string
		if e = rows.Scan(&name, &kind, &want); e != nil {
			return e
		}
		if e = db.QueryRowContext(ctx, "SELECT sql FROM sqlite_master WHERE name=? AND type=?", name, kind).Scan(&actual); e != nil {
			return fmt.Errorf("SCHEMA_OBJECT_MISSING %s: %w", name, e)
		}
		if actual != want {
			return fmt.Errorf("SCHEMA_OBJECT_CHANGED: %s", name)
		}
	}
	return rows.Err()
}
