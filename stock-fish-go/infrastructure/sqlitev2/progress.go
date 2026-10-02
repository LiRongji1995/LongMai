package sqlitev2

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
)

// ReportProgress records a bounded worker checkpoint and its immutable event in
// one transaction. A late callback never revives a cancelled or completed job.
func (r *AppRepository) ReportProgress(ctx context.Context, id, stage string, processed int) error {
	if stage == "" || processed < 0 {
		return errors.New("INVALID_JOB_PROGRESS")
	}
	tx, e := r.db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	j, e := appScanJob(tx.QueryRowContext(ctx, "SELECT "+appJobColumns+" FROM app_jobs WHERE job_id=?", id))
	if e != nil {
		return e
	}
	if j.State != "RUNNING" {
		return nil
	}
	if processed < j.Progress {
		return errors.New("JOB_PROGRESS_REGRESSION")
	}
	j.Revision++
	j.Progress = processed
	j.UpdatedAt = appNow()
	reasonRaw, _ := json.Marshal(map[string]any{"stage": stage, "processed_instruments": processed})
	reason := string(reasonRaw)
	if _, e = tx.ExecContext(ctx, "UPDATE app_jobs SET revision=?,progress=?,updated_at=? WHERE job_id=?", j.Revision, processed, j.UpdatedAt, id); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, "INSERT INTO app_job_events(job_id,revision,state,reason,event_at) VALUES(?,?,'RUNNING',?,?)", id, j.Revision, reason, j.UpdatedAt); e != nil {
		return e
	}
	raw, _ := json.Marshal(map[string]any{"job_id": id, "revision": j.Revision, "state": "RUNNING", "reason": reason, "event_at": j.UpdatedAt})
	if e = appLedger(ctx, tx, "JOB_EVENT", fmt.Sprintf("%s:%d", id, j.Revision), appHash(raw)); e != nil {
		return e
	}
	return tx.Commit()
}
