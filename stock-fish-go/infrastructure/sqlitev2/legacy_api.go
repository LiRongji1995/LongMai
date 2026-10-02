package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"errors"
	"strings"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

func decodeMap(raw []byte) (map[string]json.RawMessage, error) {
	var m map[string]json.RawMessage
	e := json.Unmarshal(raw, &m)
	if e == nil && m == nil {
		e = errors.New("RESULT_OBJECT_REQUIRED")
	}
	return m, e
}
func textField(m map[string]json.RawMessage, k string) string {
	var s string
	_ = json.Unmarshal(m[k], &s)
	return s
}
func setField(m map[string]json.RawMessage, k string, v any) { m[k], _ = json.Marshal(v) }
func canonicalBytes(raw []byte) ([]byte, error)              { return contracts.Canonical(raw) }
func hashObject(v any) (string, error) {
	b, e := json.Marshal(v)
	if e != nil {
		return "", e
	}
	b, e = canonicalBytes(b)
	if e != nil {
		return "", e
	}
	return digest(b), nil
}
func nativeHeader(run contracts.Run) (json.RawMessage, error) {
	m, e := decodeMap(run.Summary)
	if e != nil {
		return nil, e
	}
	setField(m, "run_id", run.ID)
	setField(m, "input_id", run.InputID)
	setField(m, "strategy_id", run.Strategy)
	setField(m, "generated_at", run.CreatedAt)
	setField(m, "implementation_version", "STOCKFISH_GO_RUN_V2")
	setField(m, "candidate_availability", run.CandidateAvailability)
	if status, ok := m["run_status"]; ok {
		m["status"] = status
	}
	setField(m, "parameter_status", "UNVALIDATED")
	setField(m, "parameter_status_label_zh", "尚未验证")
	setField(m, "market_edge", "NOT_ESTABLISHED")
	return json.Marshal(m)
}
func (r *AppRepository) ResearchRuns(ctx context.Context, limit, offset int) ([]json.RawMessage, error) {
	if r.store == nil {
		return nil, errors.New("READ_ONLY_RESEARCH_REPOSITORY_REQUIRED")
	}
	if e := r.store.Ready(ctx); e != nil {
		return nil, e
	}
	if limit < 1 || limit > 1000 || offset < 0 {
		return nil, errors.New("INVALID_RESULT_PAGE")
	}
	rows, e := r.db.QueryContext(ctx, `SELECT id,kind FROM (SELECT external_id AS id,'legacy' AS kind,created_at FROM legacy_records WHERE namespace='strategy_experiment_runs' UNION ALL SELECT run_id AS id,'native' AS kind,created_at FROM app_runs WHERE state='PUBLISHED') ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?`, limit, offset)
	if e != nil {
		return nil, e
	}
	type key struct{ id, kind string }
	keys := []key{}
	for rows.Next() {
		var k key
		if e = rows.Scan(&k.id, &k.kind); e != nil {
			rows.Close()
			return nil, e
		}
		keys = append(keys, k)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	out := make([]json.RawMessage, 0, len(keys))
	for _, k := range keys {
		var raw []byte
		if k.kind == "native" {
			run, e := r.Run(ctx, k.id)
			if e != nil {
				return nil, e
			}
			raw, e = nativeHeader(run)
			if e != nil {
				return nil, e
			}
		} else {
			raw, _, e = r.store.LegacyDocument(ctx, "strategy_experiment_summaries", k.id)
			if errors.Is(e, sql.ErrNoRows) {
				raw, _ = json.Marshal(map[string]any{"run_id": k.id, "status": "PROJECTION_REBUILD_REQUIRED"})
			} else if e != nil {
				return nil, e
			}
		}
		out = append(out, json.RawMessage(raw))
	}
	return out, ctx.Err()
}
func checkSelection(selection string) error {
	if selection != "all" && selection != "focus" && selection != "unselected" && selection != "core_pass" {
		return errors.New("UNKNOWN_SELECTION")
	}
	return nil
}
func checkNativeSelection(strategy, selection string) error {
	if e := checkSelection(selection); e != nil {
		return e
	}
	if selection == "core_pass" && strategy != contracts.R1 {
		return errors.New("CORE_PASS_SELECTION_REQUIRES_NATIVE_R1")
	}
	if strategy == contracts.R1 && selection != "all" && selection != "core_pass" {
		return errors.New("R1_CORE_PASS_IS_NOT_V2_FOCUS; USE_ALL_OR_CORE_PASS_SELECTION")
	}
	return nil
}

// InputObservation reads only immutable input metadata; the worker validates raw
// input bytes when executing the request through the shared application path.
func (r *AppRepository) InputObservation(ctx context.Context, id string) (string, error) {
	if r.store == nil {
		return "", errors.New("READ_ONLY_RESEARCH_REPOSITORY_REQUIRED")
	}
	if e := r.store.Ready(ctx); e != nil {
		return "", e
	}
	var observation string
	e := r.db.QueryRowContext(ctx, "SELECT observation FROM app_inputs WHERE input_id=?", id).Scan(&observation)
	return observation, e
}
func (r *AppRepository) ResearchSummary(ctx context.Context, id, strategy, selection string, page, size int) (json.RawMessage, error) {
	if r.store == nil {
		return nil, errors.New("READ_ONLY_RESEARCH_REPOSITORY_REQUIRED")
	}
	if e := r.store.Ready(ctx); e != nil {
		return nil, e
	}
	if page < 1 || page > 1000000 || size < 1 || size > 500 {
		return nil, errors.New("INVALID_SUMMARY_PAGE")
	}
	if selection == "" {
		selection = "all"
	}
	if e := checkSelection(selection); e != nil {
		return nil, e
	}
	run, e := r.Run(ctx, id)
	if e == nil {
		return r.nativeSummary(ctx, run, strategy, selection, page, size)
	}
	if !errors.Is(e, sql.ErrNoRows) {
		return nil, e
	}
	if selection == "core_pass" {
		return nil, errors.New("CORE_PASS_SELECTION_REQUIRES_NATIVE_R1")
	}
	if strategy == "" {
		strategy = "PV_OBSERVATION_V1"
	}
	raw, _, e := r.store.LegacyDocument(ctx, "strategy_experiment_summaries", id)
	if e != nil {
		return nil, e
	}
	m, e := decodeMap(raw)
	if e != nil {
		return nil, e
	}
	var results map[string]map[string]json.RawMessage
	var selections map[string]map[string]int
	if e = json.Unmarshal(m["results"], &results); e != nil {
		return nil, e
	}
	result, ok := results[strategy]
	if !ok {
		return nil, errors.New("SAVED_STRATEGY_NOT_PRESENT")
	}
	if e = json.Unmarshal(m["selections"], &selections); e != nil {
		return nil, e
	}
	counts, ok := selections[strategy]
	if !ok {
		return nil, errors.New("SAVED_SELECTION_COUNTS_MISSING")
	}
	total, ok := counts[selection]
	if !ok || total < 0 {
		return nil, errors.New("SAVED_SELECTION_COUNT_INVALID")
	}
	start := (page - 1) * size
	stop := page * size
	if stop > total {
		stop = total
	}
	items := []json.RawMessage{}
	if start < stop {
		for chunk := start / 50; chunk <= (stop-1)/50; chunk++ {
			hash, e := hashObject(map[string]any{"strategy_id": strategy, "selection": selection, "chunk": chunk})
			if e != nil {
				return nil, e
			}
			data, _, e := r.store.LegacyDocument(ctx, "strategy_experiment_summary_pages", id+"_"+hash)
			if e != nil {
				return nil, e
			}
			p, e := decodeMap(data)
			if e != nil {
				return nil, e
			}
			if textField(p, "run_id") != id || textField(p, "source_run_hash") != textField(m, "source_run_hash") {
				return nil, errors.New("STRATEGY_SUMMARY_PROJECTION_MISMATCH")
			}
			var rows []json.RawMessage
			if e = json.Unmarshal(p["items"], &rows); e != nil {
				return nil, e
			}
			a := start - chunk*50
			if a < 0 {
				a = 0
			}
			b := stop - chunk*50
			if b > 50 {
				b = 50
			}
			if b > len(rows) {
				return nil, errors.New("STRATEGY_SUMMARY_PAGE_TRUNCATED")
			}
			items = append(items, rows[a:b]...)
		}
	}
	for _, key := range []string{"status", "counts", "data_scope_status", "coverage_scope", "context_scope_status", "context_coverage_scope"} {
		value, ok := result[key]
		if !ok {
			value = json.RawMessage("null")
		}
		m[key] = value
	}
	for k, v := range map[string]any{"strategy_id": strategy, "selection": selection, "filtered_total": total, "page": page, "page_size": size, "has_next": stop < total, "items": items} {
		setField(m, k, v)
	}
	return json.Marshal(m)
}
func (r *AppRepository) nativeSummary(ctx context.Context, run contracts.Run, strategy, selection string, page, size int) (json.RawMessage, error) {
	if strategy != "" && strategy != run.Strategy {
		return nil, errors.New("SAVED_STRATEGY_NOT_PRESENT")
	}
	if e := checkNativeSelection(run.Strategy, selection); e != nil {
		return nil, e
	}
	var expected, projections, details int
	if e := r.db.QueryRowContext(ctx, `SELECT item_count,(SELECT count(*) FROM app_item_summaries WHERE run_id=?),(SELECT count(*) FROM app_items WHERE run_id=?) FROM app_runs WHERE run_id=? AND state='PUBLISHED'`, run.ID, run.ID, run.ID).Scan(&expected, &projections, &details); e != nil {
		return nil, e
	}
	if expected != projections || expected != details {
		return nil, errors.New("NATIVE_SUMMARY_MEMBERSHIP_MISMATCH")
	}
	where := "run_id=?"
	args := []any{run.ID}
	if selection != "all" {
		if selection == "core_pass" {
			where += " AND decision_status='CORE_PASS'"
		} else if selection == "focus" {
			where += " AND decision_status='FOCUS'"
		} else {
			where += " AND decision_status!='FOCUS'"
		}
	}
	var total int
	if e := r.db.QueryRowContext(ctx, "SELECT count(*) FROM app_item_summaries WHERE "+where, args...).Scan(&total); e != nil {
		return nil, e
	}
	args = append(args, size, (page-1)*size)
	rows, e := r.db.QueryContext(ctx, "SELECT payload,raw_hash,source_hash,projection_version,instrument_id,(SELECT raw_hash FROM app_items i WHERE i.run_id=app_item_summaries.run_id AND i.instrument_id=app_item_summaries.instrument_id) FROM app_item_summaries WHERE "+where+" ORDER BY display_rank,ordinal LIMIT ? OFFSET ?", args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	items := []json.RawMessage{}
	for rows.Next() {
		var raw []byte
		var h, source, version, iid, detailHash string
		if e = rows.Scan(&raw, &h, &source, &version, &iid, &detailHash); e != nil {
			return nil, e
		}
		if digest(raw) != h || source != detailHash || version != "GO_RESULT_SUMMARY_V1" {
			return nil, errors.New("NATIVE_SUMMARY_HASH_MISMATCH")
		}
		m, e := decodeMap(raw)
		if e != nil {
			return nil, e
		}
		if textField(m, "instrument_id") != iid {
			return nil, errors.New("NATIVE_SUMMARY_IDENTITY_MISMATCH")
		}
		items = append(items, json.RawMessage(raw))
	}
	if e = rows.Err(); e != nil {
		return nil, e
	}
	raw, e := nativeHeader(run)
	if e != nil {
		return nil, e
	}
	m, _ := decodeMap(raw)
	for k, v := range map[string]any{"strategy_id": run.Strategy, "selection": selection, "filtered_total": total, "page": page, "page_size": size, "has_next": page*size < total, "items": items} {
		setField(m, k, v)
	}
	return json.Marshal(m)
}
func (r *AppRepository) expandLegacy(ctx context.Context, run, strategy string, row json.RawMessage) (json.RawMessage, error) {
	m, e := decodeMap(row)
	if e != nil {
		return nil, e
	}
	ref := textField(m, "detail_ref")
	if ref == "" {
		return row, nil
	}
	iid := textField(m, "instrument_id")
	document, _, e := r.store.LegacyDocument(ctx, "strategy_experiment_item_details", ref)
	if e != nil {
		return nil, e
	}
	d, e := decodeMap(document)
	if e != nil {
		return nil, e
	}
	if textField(d, "run_id") != run || textField(d, "instrument_id") != iid || textField(d, "strategy_id") != strategy {
		return nil, errors.New("LEGACY_DETAIL_IDENTITY_MISMATCH")
	}
	var size int64
	if e = json.Unmarshal(d["raw_bytes"], &size); e != nil {
		return nil, e
	}
	h, e := hashObject(map[string]any{"sha256": textField(d, "raw_sha256"), "bytes": size})
	if e != nil {
		return nil, e
	}
	if textField(m, "detail_hash") != "" && textField(m, "detail_hash") != h {
		return nil, errors.New("LEGACY_DETAIL_REF_HASH_MISMATCH")
	}
	expanded, e := r.store.LegacyDetail(ctx, run, iid, strategy)
	if e != nil {
		return nil, e
	}
	if int64(len(expanded)) != size || digest(expanded) != textField(d, "raw_sha256") {
		return nil, errors.New("LEGACY_DETAIL_INDEX_REF_MISMATCH")
	}
	return expanded, ctx.Err()
}
func (r *AppRepository) ResearchItem(ctx context.Context, id, iid string) (json.RawMessage, error) {
	if r.store == nil {
		return nil, errors.New("READ_ONLY_RESEARCH_REPOSITORY_REQUIRED")
	}
	if e := r.store.Ready(ctx); e != nil {
		return nil, e
	}
	item, e := r.Item(ctx, id, iid)
	if e == nil {
		run, err := r.Run(ctx, id)
		if err != nil {
			return nil, err
		}
		flat, err := nativeItem(item)
		if err != nil {
			return nil, err
		}
		return json.Marshal(map[string]any{"run_id": id, "instrument_id": iid, "strategy_results": map[string]any{run.Strategy: []json.RawMessage{flat}}, "saved_native_detail": item.Payload, "implementation_version": "STOCKFISH_GO_RUN_V2", "market_edge": "NOT_ESTABLISHED", "outcomes": []any{}, "tracking": []any{}, "outcome_status": "NOT_EVALUATED"})
	}
	if !errors.Is(e, sql.ErrNoRows) {
		return nil, e
	}
	raw, _, e := r.store.LegacyDocument(ctx, "strategy_experiment_runs", id)
	if e != nil {
		return nil, e
	}
	m, e := decodeMap(raw)
	if e != nil {
		return nil, e
	}
	var results map[string]struct {
		Items []json.RawMessage `json:"items"`
	}
	if e = json.Unmarshal(m["results"], &results); e != nil {
		return nil, e
	}
	matches := map[string][]json.RawMessage{}
	for sid, result := range results {
		for _, row := range result.Items {
			v, e := decodeMap(row)
			if e != nil {
				return nil, e
			}
			if textField(v, "instrument_id") == iid {
				full, e := r.expandLegacy(ctx, id, sid, row)
				if e != nil {
					return nil, e
				}
				matches[sid] = append(matches[sid], full)
			}
		}
	}
	policy, focus := primaryFocus(m)
	var focusMatch json.RawMessage
	var focusRows []json.RawMessage
	_ = json.Unmarshal(focus["items"], &focusRows)
	for _, row := range focusRows {
		v, _ := decodeMap(row)
		if textField(v, "instrument_id") == iid {
			focusMatch = row
			break
		}
	}
	if len(matches) == 0 && focusMatch == nil {
		return nil, sql.ErrNoRows
	}
	outcomes, e := r.legacyMatching(ctx, "strategy_trading_session_observations", id, iid, 0)
	if e != nil {
		return nil, e
	}
	tracking, e := r.legacyMatching(ctx, "research_episode_observations", id, iid, 100)
	if e != nil {
		return nil, e
	}
	return json.Marshal(map[string]any{"run_id": id, "instrument_id": iid, "observation_date": m["observation_date"], "strategy_results": matches, "focus_selection": focusMatch, "selection_policy": policy, "market_edge": "NOT_ESTABLISHED", "outcomes": outcomes, "tracking": tracking})
}
func primaryFocus(run map[string]json.RawMessage) (string, map[string]json.RawMessage) {
	var selections map[string]map[string]json.RawMessage
	_ = json.Unmarshal(run["selection_results"], &selections)
	for _, key := range []string{"STAGE_AWARE_MOMENTUM_V2_FOCUS", "PV_FOCUS_PREVIEW_V0.1"} {
		if s, ok := selections[key]; ok {
			return key, s
		}
	}
	return "", map[string]json.RawMessage{}
}
func focusIDs(run map[string]json.RawMessage, strategy string, rows []json.RawMessage) map[string]bool {
	ids := map[string]bool{}
	if strategy == contracts.V2 {
		for _, raw := range rows {
			m, _ := decodeMap(raw)
			if textField(m, "decision_status") == "FOCUS" {
				ids[textField(m, "instrument_id")] = true
			}
		}
		if len(ids) > 0 {
			return ids
		}
	}
	var selections map[string]struct {
		IDs []string `json:"selected_ids"`
	}
	_ = json.Unmarshal(run["selection_results"], &selections)
	for _, key := range []string{"STAGE_AWARE_MOMENTUM_V2_FOCUS", "PV_FOCUS_PREVIEW_V0.1"} {
		if s, ok := selections[key]; ok {
			for _, id := range s.IDs {
				ids[id] = true
			}
			break
		}
	}
	return ids
}
func (r *AppRepository) ResearchExport(ctx context.Context, id, strategy, selection, format string) ([]byte, string, error) {
	if r.store == nil {
		return nil, "", errors.New("READ_ONLY_RESEARCH_REPOSITORY_REQUIRED")
	}
	if e := r.store.Ready(ctx); e != nil {
		return nil, "", e
	}
	if format == "" {
		format = "json"
	}
	if format != "json" && format != "csv" {
		return nil, "", errors.New("UNSUPPORTED_EXPORT_FORMAT")
	}
	filtered := strategy != "" || selection != ""
	if selection == "" {
		selection = "all"
	}
	if e := checkSelection(selection); e != nil {
		return nil, "", e
	}
	if run, err := r.Run(ctx, id); err == nil {
		return r.nativeExport(ctx, run, strategy, selection, format)
	} else if !errors.Is(err, sql.ErrNoRows) {
		return nil, "", err
	}
	if selection == "core_pass" {
		return nil, "", errors.New("CORE_PASS_SELECTION_REQUIRES_NATIVE_R1")
	}
	raw, _, e := r.store.LegacyDocument(ctx, "strategy_experiment_runs", id)
	if e != nil {
		return nil, "", e
	}
	if format == "json" && !filtered {
		return raw, "application/json", nil
	}
	if strategy == "" {
		strategy = "PV_OBSERVATION_V1"
	}
	run, e := decodeMap(raw)
	if e != nil {
		return nil, "", e
	}
	var results map[string]map[string]json.RawMessage
	if e = json.Unmarshal(run["results"], &results); e != nil {
		return nil, "", e
	}
	var baselines map[string]map[string]json.RawMessage
	if v := run["evaluation_baselines"]; len(v) > 0 {
		if e = json.Unmarshal(v, &baselines); e != nil {
			return nil, "", e
		}
		for k, v := range baselines {
			results[k] = v
		}
	}
	result, ok := results[strategy]
	if !ok {
		for k, v := range results {
			sid := textField(v, "canonical_strategy_id")
			if sid == "" {
				sid = textField(v, "strategy_id")
			}
			if sid == "" {
				sid = k
			}
			if sid == strategy {
				if result != nil {
					return nil, "", errors.New("AMBIGUOUS_CANONICAL_STRATEGY")
				}
				result = v
			}
		}
	}
	if result == nil {
		return nil, "", errors.New("SAVED_STRATEGY_NOT_PRESENT")
	}
	var all []json.RawMessage
	if e = json.Unmarshal(result["items"], &all); e != nil {
		return nil, "", e
	}
	_, primary := primaryFocus(run)
	var retained bool
	_ = json.Unmarshal(result["full_eligible_pool_retained"], &retained)
	v2 := textField(result, "strategy_id") == contracts.V2 || retained
	focus := focusIDs(run, strategy, all)
	ranked := []json.RawMessage{}
	selected := []json.RawMessage{}
	rankedIDs := map[string]bool{}
	pvByID := map[string]map[string]json.RawMessage{}
	if !v2 {
		var pv []json.RawMessage
		_ = json.Unmarshal(results["PV_OBSERVATION_V1"]["items"], &pv)
		for _, raw := range pv {
			m, e := decodeMap(raw)
			if e != nil {
				return nil, "", e
			}
			pvByID[textField(m, "instrument_id")] = m
		}
	}
	for _, row := range all {
		if e = ctx.Err(); e != nil {
			return nil, "", e
		}
		m, e := decodeMap(row)
		if e != nil {
			return nil, "", e
		}
		iid := textField(m, "instrument_id")
		if v2 {
			full, e := r.expandLegacy(ctx, id, strategy, row)
			if e != nil {
				return nil, "", e
			}
			expanded, e := decodeMap(full)
			if e != nil {
				return nil, "", e
			}
			isFocus := textField(expanded, "decision_status") == "FOCUS"
			if isFocus {
				ranked = append(ranked, full)
				rankedIDs[iid] = true
			}
			if selection == "all" || isFocus == (selection == "focus") {
				selected = append(selected, full)
			}
		} else {
			if iid == "" || rankedIDs[iid] || len(m["rank"]) == 0 || string(m["rank"]) == "null" {
				continue
			}
			if strategy == "DNA_RERANK_V1" || strategy == "THEME_CONTEXT_V1" || strategy == "DNA_REVERSAL_V1" || strategy == "THEME_LIMITUP_V1" {
				status := textField(m, "status")
				if status != "" && status != "EVALUATED" && status != "COMPLETE" {
					continue
				}
			}
			merged := map[string]json.RawMessage{}
			for k, v := range pvByID[iid] {
				merged[k] = v
			}
			for k, v := range m {
				merged[k] = v
			}
			value, e := json.Marshal(merged)
			if e != nil {
				return nil, "", e
			}
			ranked = append(ranked, value)
			rankedIDs[iid] = true
			if selection == "all" || focus[iid] == (selection == "focus") {
				selected = append(selected, value)
			}
		}
	}
	if v2 {
		focus = focusIDs(run, contracts.V2, all)
	}
	if format == "csv" {
		return exportCSV(run, result, primary, id, strategy, selected, focus), "text/csv; charset=utf-8", nil
	}
	diagnostics := []json.RawMessage{}
	for _, row := range all {
		m, e := decodeMap(row)
		if e != nil {
			return nil, "", e
		}
		iid := textField(m, "instrument_id")
		if !rankedIDs[iid] && (selection == "all" || focus[iid] == (selection == "focus")) {
			diagnostics = append(diagnostics, row)
		}
	}
	sourceHash := textField(run, "content_hash")
	if sourceHash == "" {
		sourceHash, e = hashObject(run)
		if e != nil {
			return nil, "", e
		}
	}
	setField(result, "items", selected)
	setField(result, "coverage_diagnostics", diagnostics)
	setField(run, "results", map[string]any{strategy: result})
	delete(run, "evaluation_baselines")
	delete(run, "content_hash")
	setField(run, "export_view", map[string]any{"strategy_id": strategy, "selection": selection, "source_content_hash": sourceHash, "payload_role": "SAVED_RANKING_SELECTION_VIEW", "ranked_total": len(ranked), "filtered_total": len(selected)})
	h, e := hashObject(run)
	if e != nil {
		return nil, "", e
	}
	setField(run, "view_content_hash", h)
	out, e := json.Marshal(run)
	return out, "application/json", e
}

var csvColumns = strings.Split("run_id,observation_date,strategy_id,instrument_id,rank,S_PV,S_DNA,S_THEME,S_PV_REFERENCE,reference_score,reference_rank,A,R,H,status,technical_status,theme_status,capital_status,hard_requirements_status,formal_status,primary_stage,alternate_stages,liquidity_status,sector_status,funds_status,funds_grade,decision_status,watch_reasons,setup_rank,display_order,rank_semantics,trend_state,market_context_state,market_compatibility_status,same_theme_context_status,behavioral_confirmation_status,critical_counterevidence_status,capital_evidence_status,relative_strength_status,ret1_pct,ret3_pct,ret5_pct,ret10_pct,ret20_pct,CLV,amplitude_pct,low_return_pct,high_return_pct,close_return_pct,recovery_points_pct,low_to_close_rebound_pct,RS_market_1d_pct,RS_sector_1d_pct,shape_type,amount_cny,turnover_fraction,reason,reason_codes,selected_focus,focus_reason_codes,close_location,scope_label,parameter_status,parameter_status_label_zh", ",")

func exportCSV(run, result, primary map[string]json.RawMessage, id, strategy string, rows []json.RawMessage, focus map[string]bool) []byte {
	var out strings.Builder
	out.WriteRune('\ufeff')
	writer := csv.NewWriter(&out)
	writer.UseCRLF = true
	columns := csvColumns
	if strategy == contracts.R1 && textField(run, "implementation_version") == "STOCKFISH_GO_RUN_V2" {
		columns = append(append([]string{}, csvColumns...), "candidate_status")
	}
	focusRows := map[string]map[string]json.RawMessage{}
	var focusItems []json.RawMessage
	_ = json.Unmarshal(primary["items"], &focusItems)
	for _, raw := range focusItems {
		m, _ := decodeMap(raw)
		focusRows[textField(m, "instrument_id")] = m
	}
	_ = writer.Write(columns)
	for _, raw := range rows {
		m, _ := decodeMap(raw)
		setField(m, "run_id", id)
		setField(m, "strategy_id", strategy)
		m["observation_date"] = run["observation_date"]
		m["parameter_status"] = run["parameter_status"]
		m["scope_label"] = result["scope_label"]
		setField(m, "selected_focus", focus[textField(m, "instrument_id")])
		focusRow := focusRows[textField(m, "instrument_id")]
		m["focus_reason_codes"] = focusRow["reason_codes"]
		m["close_location"] = focusRow["close_location"]
		values := make([]string, len(columns))
		for i, k := range columns {
			v := m[k]
			if len(v) == 0 || string(v) == "null" {
				continue
			}
			var s string
			isText := false
			if json.Unmarshal(v, &s) == nil {
				isText = true
				values[i] = s
			} else {
				var a []string
				if json.Unmarshal(v, &a) == nil {
					values[i] = strings.Join(a, "|")
					isText = true
				} else if string(v) == "true" {
					values[i] = "True"
				} else if string(v) == "false" {
					values[i] = "False"
				} else {
					values[i] = string(v)
				}
			}
			if isText && strings.ContainsAny(values[i][:min(1, len(values[i]))], "=+-@") {
				values[i] = "'" + values[i]
			}
		}
		_ = writer.Write(values)
	}
	writer.Flush()
	return []byte(out.String())
}

// Only a bounded metadata page is retained while archived observations are
// decoded and checked. Unknown namespaces remain accessible through legacy_records.
func (r *AppRepository) legacyMatching(ctx context.Context, ns, run, iid string, latest int) ([]json.RawMessage, error) {
	out := []json.RawMessage{}
	for offset := 0; ; offset += 100 {
		rows, e := r.db.QueryContext(ctx, "SELECT external_id FROM legacy_records WHERE namespace=? ORDER BY created_at,external_id LIMIT 100 OFFSET ?", ns, offset)
		if e != nil {
			return nil, e
		}
		keys := []string{}
		for rows.Next() {
			var id string
			if e = rows.Scan(&id); e != nil {
				rows.Close()
				return nil, e
			}
			keys = append(keys, id)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return nil, e
		}
		for _, key := range keys {
			raw, _, e := r.store.LegacyDocument(ctx, ns, key)
			if e != nil {
				return nil, e
			}
			m, e := decodeMap(raw)
			if e != nil {
				return nil, e
			}
			if textField(m, "run_id") == run && (iid == "" || textField(m, "instrument_id") == iid) {
				out = append(out, json.RawMessage(raw))
				if latest > 0 && len(out) > latest {
					out = out[1:]
				}
				if len(out) > 10000 {
					return nil, errors.New("LEGACY_OBSERVATIONS_TOO_LARGE; USE_PAGED_LEGACY_EXPORT")
				}
			}
		}
		if len(keys) < 100 {
			return out, ctx.Err()
		}
	}
}
func (r *AppRepository) ResearchReview(ctx context.Context, id string, full bool) (json.RawMessage, error) {
	if r.store == nil {
		return nil, errors.New("READ_ONLY_RESEARCH_REPOSITORY_REQUIRED")
	}
	if e := r.store.Ready(ctx); e != nil {
		return nil, e
	}
	indexes, e := r.legacyMatching(ctx, "strategy_research_review_indexes", id, "", 0)
	if e != nil {
		return nil, e
	}
	var chosen map[string]json.RawMessage
	for _, raw := range indexes {
		m, e := decodeMap(raw)
		if e != nil {
			return nil, e
		}
		if chosen == nil || textField(m, "evaluated_at") >= textField(chosen, "evaluated_at") {
			chosen = m
		}
	}
	if chosen != nil {
		ns := "strategy_research_review_summaries"
		if full {
			ns = "strategy_research_reviews"
		}
		raw, _, e := r.store.LegacyDocument(ctx, ns, textField(chosen, "review_id"))
		return raw, e
	}
	raw, _, e := r.store.LegacyDocument(ctx, "strategy_experiment_summaries", id)
	if errors.Is(e, sql.ErrNoRows) {
		run, err := r.Run(ctx, id)
		if err != nil {
			return nil, err
		}
		raw = run.Summary
	} else if e != nil {
		return nil, e
	}
	m, e := decodeMap(raw)
	if e != nil {
		return nil, e
	}
	return json.Marshal(map[string]any{"schema_version": "R05_RESEARCH_FEEDBACK_V2", "run_id": id, "observation_date": m["observation_date"], "status": "PENDING_MARKET_VALIDATION", "market_edge": "NOT_ESTABLISHED", "rolling_windows": map[string]any{}, "counts": map[string]int{"mature": 0, "outcome_observations": 0}, "reason_codes": []string{"OUTCOME_OR_TRACKING_REVIEW_NOT_RUN"}, "full_report_available": false})
}

func nativeItem(item contracts.Item) (json.RawMessage, error) {
	envelope, e := decodeMap(item.Payload)
	if e != nil {
		return nil, e
	}
	m := map[string]json.RawMessage{}
	if v := envelope["decision"]; len(v) > 0 {
		if e = json.Unmarshal(v, &m); e != nil {
			return nil, e
		}
	}
	var fact map[string]json.RawMessage
	if v := envelope["fact"]; len(v) > 0 {
		if e = json.Unmarshal(v, &fact); e != nil {
			return nil, e
		}
	}
	for k, v := range fact {
		if _, ok := m[k]; !ok {
			m[k] = v
		}
	}
	setField(m, "instrument_id", item.ID)
	m["saved_native_detail"] = item.Payload
	setField(m, "implementation_version", "GO_NATIVE_SAVED_ITEM_V1")
	return json.Marshal(m)
}
func (r *AppRepository) nativeExport(ctx context.Context, run contracts.Run, strategy, selection, format string) ([]byte, string, error) {
	if strategy != "" && strategy != run.Strategy {
		return nil, "", errors.New("SAVED_STRATEGY_NOT_PRESENT")
	}
	if e := checkNativeSelection(run.Strategy, selection); e != nil {
		return nil, "", e
	}
	if e := r.VerifyRun(ctx, run.ID); e != nil {
		return nil, "", e
	}
	selected := []json.RawMessage{}
	focus := map[string]bool{}
	var selectedBytes int
	for page := 1; ; page++ {
		raw, e := r.nativeSummary(ctx, run, run.Strategy, selection, page, 50)
		if e != nil {
			return nil, "", e
		}
		var summary struct {
			Items []json.RawMessage `json:"items"`
			Next  bool              `json:"has_next"`
		}
		if e = json.Unmarshal(raw, &summary); e != nil {
			return nil, "", e
		}
		for _, row := range summary.Items {
			m, e := decodeMap(row)
			if e != nil {
				return nil, "", e
			}
			iid := textField(m, "instrument_id")
			item, e := r.Item(ctx, run.ID, iid)
			if e != nil {
				return nil, "", e
			}
			flat, e := nativeItem(item)
			if e != nil {
				return nil, "", e
			}
			selectedBytes += len(flat)
			if selectedBytes > 64<<20 {
				return nil, "", errors.New("FILTERED_EXPORT_LIMIT_64_MIB; USE_COMPLETE_STREAMING_JSON_OR_PAGED_SUMMARY")
			}
			selected = append(selected, flat)
			if textField(m, "decision_status") == "FOCUS" {
				focus[iid] = true
			}
		}
		if !summary.Next {
			break
		}
	}
	raw, e := nativeHeader(run)
	if e != nil {
		return nil, "", e
	}
	header, e := decodeMap(raw)
	if e != nil {
		return nil, "", e
	}
	status, totalField := "FOCUS", "focus_total"
	if run.Strategy == contracts.R1 {
		status, totalField = "CORE_PASS", "core_pass_total"
	}
	var selectedStatusTotal int
	if e = r.db.QueryRowContext(ctx, "SELECT count(*) FROM app_item_summaries WHERE run_id=? AND decision_status=?", run.ID, status).Scan(&selectedStatusTotal); e != nil {
		return nil, "", e
	}
	result := map[string]json.RawMessage{}
	setField(result, "strategy_id", run.Strategy)
	setField(result, "items", selected)
	setField(result, "scope_label", "SAVED_GO_NATIVE_RESULT_VIEW")
	if format == "csv" {
		return exportCSV(header, result, map[string]json.RawMessage{}, run.ID, run.Strategy, selected, focus), "text/csv; charset=utf-8", nil
	}
	setField(header, "results", map[string]any{run.Strategy: result})
	setField(header, "export_view", map[string]any{"strategy_id": run.Strategy, "selection": selection, "source_manifest": run.Manifest, "payload_role": "SAVED_GO_NATIVE_SELECTION_VIEW", totalField: selectedStatusTotal, "filtered_total": len(selected)})
	h, e := hashObject(header)
	if e != nil {
		return nil, "", e
	}
	setField(header, "view_content_hash", h)
	raw, e = json.Marshal(header)
	return raw, "application/json", e
}
