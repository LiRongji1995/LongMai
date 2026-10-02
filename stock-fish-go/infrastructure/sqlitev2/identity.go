package sqlitev2

import (
	"context"
	"database/sql"
	"errors"
)

// PublishedResearchIdentity is the per-evaluation authority gate. The broader
// ResearchIdentity remains available for health and unpublished diagnostics.
func (s *Store) PublishedResearchIdentity(ctx context.Context) (map[string]string, error) {
	if err := s.Ready(ctx); err != nil {
		return nil, err
	}
	return s.ResearchIdentity(ctx)
}

// ResearchIdentity binds a result to the migration and actual engine, without
// introducing a private filesystem path into its portable identity.
func (s *Store) ResearchIdentity(ctx context.Context) (map[string]string, error) {
	out := map[string]string{}
	var source, status, verification, checksum, version string
	e := s.db.QueryRowContext(ctx, "SELECT source_sha256,status,coalesce(verification,'') FROM migration_state WHERE id=1").Scan(&source, &status, &verification)
	if errors.Is(e, sql.ErrNoRows) {
		return out, nil
	}
	if e != nil {
		return nil, e
	}
	if e = s.db.QueryRowContext(ctx, "SELECT checksum FROM schema_versions WHERE version=?", SchemaVersion).Scan(&checksum); e != nil {
		return nil, e
	}
	if e = s.db.QueryRowContext(ctx, "SELECT sqlite_version()").Scan(&version); e != nil {
		return nil, e
	}
	out["frozen_source_sha256"] = source
	out["migration_status"] = status
	out["migration_verification_sha256"] = digest([]byte(verification))
	out["schema_sha256"] = checksum
	out["application_schema_sha256"] = appHash([]byte(appDDL))
	out["sqlite_version"] = version
	if status == "PUBLISHED" {
		projection, e := s.BatchProjectionIdentity(ctx)
		if e != nil {
			return nil, e
		}
		for key, value := range projection {
			out["batch_projection_"+key] = value
		}
	}
	return out, nil
}
