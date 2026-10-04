package sqlitev2

import "context"

// HistoryCaptureStatus inspects presence only. It does not select a history,
// validate its price basis, resolve historical eligibility or authorize reuse.
// A PUBLISHED Go history cannot be handed to Python's different writable store.
func (r *ReadOnly) HistoryCaptureStatus(ctx context.Context) (map[string]any, error) {
	var present bool
	err := r.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM partitions) OR EXISTS(SELECT 1 FROM snapshots WHERE history_ref IS NOT NULL AND history_ref<>'') OR EXISTS(SELECT 1 FROM legacy_records WHERE namespace='history_artifact_manifests')`).Scan(&present)
	if err != nil {
		return nil, err
	}
	status := "COLD_CAPTURE_EXPLICIT_OPT_IN_REQUIRED"
	if present {
		status = "BLOCKED_GO_V2_HISTORY_ADAPTER_UNAVAILABLE"
	}
	return map[string]any{"status": status, "history_present": present, "selection_status": "NOT_SELECTED", "adapter_implementation": "NOT_IMPLEMENTED", "validation_scope": "PRESENCE_ONLY_NOT_PRICE_BASIS_QUALIFICATION_CALENDAR_OR_REVISION_COMPATIBILITY", "policy": "NO_IMPLICIT_PRICE_BASIS_MERGE"}, nil
}
