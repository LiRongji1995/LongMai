package sqlitev2

import (
	"bytes"
	"compress/zlib"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
)

func (s *Store) verifyRelations(ctx context.Context, out *Verification) error {
	classificationRows, classificationErr := s.db.QueryContext(ctx, "SELECT name FROM source_inventory UNION ALL SELECT DISTINCT namespace FROM legacy_records")
	if classificationErr != nil {
		return classificationErr
	}
	for classificationRows.Next() {
		var name string
		if classificationErr = classificationRows.Scan(&name); classificationErr != nil {
			classificationRows.Close()
			return classificationErr
		}
		if !knownSourceTables[name] && !knownNamespaces[name] {
			classificationRows.Close()
			return fmt.Errorf("UNCLASSIFIED_SOURCE_STRUCTURE_PRESERVED: %s", name)
		}
	}
	classificationErr = classificationRows.Err()
	classificationRows.Close()
	if classificationErr != nil {
		return classificationErr
	}

	rows, e := s.db.QueryContext(ctx, "SELECT namespace,external_id FROM legacy_records WHERE namespace IN ('candidate_inputs','history_artifact_manifests','strategy_experiment_runs','strategy_experiment_item_details','daily_research_snapshots','strategy_experiment_summaries') ORDER BY namespace,external_id")
	if e != nil {
		return e
	}
	type key struct{ ns, id string }
	var keys []key
	for rows.Next() {
		var k key
		if e = rows.Scan(&k.ns, &k.id); e != nil {
			rows.Close()
			return e
		}
		keys = append(keys, k)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, k := range keys {
		raw, _, e := s.legacyDocument(ctx, k.ns, k.id)
		if e != nil {
			return e
		}
		o, e := object(raw)
		if e != nil {
			return e
		}
		if k.ns == "candidate_inputs" || k.ns == "history_artifact_manifests" {
			wantHeader := []any{k.ns, digest(raw), txt(o, "observation_date"), scalar(o["cutoff"]), scalar(o["history_artifact_ref"]), scalar(o["coverage_type"])}
			if e = s.verifyTypedValues(ctx, `SELECT s.namespace,e.raw_sha256,s.observation_date,s.cutoff,s.history_ref,s.coverage_type FROM snapshots s JOIN evidence_objects e ON e.id=s.evidence_id WHERE s.snapshot_id=?`, []any{k.id}, wantHeader); e != nil {
				return e
			}
			out.Checked["snapshot_headers"]++
			if k.ns == "history_artifact_manifests" {
				var expectedParts []map[string]json.RawMessage
				if e = json.Unmarshal(o["partitions"], &expectedParts); e != nil {
					return e
				}
				for _, p := range expectedParts {
					var count int64
					if e = json.Unmarshal(p["row_count"], &count); e != nil {
						return e
					}
					if e = s.verifyTypedValues(ctx, `SELECT sp.session,sp.row_count FROM snapshot_partitions sp JOIN partitions p ON p.id=sp.partition_id WHERE sp.snapshot_id=? AND p.partition_hash=?`, []any{k.id, txt(p, "partition_hash")}, []any{txt(p, "session"), count}); e != nil {
						return e
					}
					out.Checked["snapshot_partition_bindings"]++
				}
			}
			expected := map[string][]json.RawMessage{}
			for _, kind := range []string{"receipts", "bars", "universe", "securities", "funds", "themes", "sessions", "previous_emotions"} {
				members, err := snapshotMembers(o, kind)
				if err != nil {
					return err
				}
				expected[kind] = members
			}
			rows, e = s.db.QueryContext(ctx, "SELECT m.kind,m.ordinal,e.raw_sha256,m.instrument_id,m.session FROM snapshot_members m LEFT JOIN evidence_objects e ON e.id=m.evidence_id WHERE m.snapshot_id=? ORDER BY m.kind,m.ordinal", k.id)
			if e != nil {
				return e
			}
			count := 0
			for rows.Next() {
				var kind, hash string
				var ord int
				var iid, session any
				if e = rows.Scan(&kind, &ord, &hash, &iid, &session); e != nil {
					rows.Close()
					return e
				}
				a := expected[kind]
				if ord < 0 || ord >= len(a) || digest(a[ord]) != hash {
					rows.Close()
					return errors.New("SNAPSHOT_MEMBER_CONTENT_MISMATCH")
				}
				mo, _ := object(a[ord])
				if !equalProjection([]any{iid, session}, []any{scalar(mo["instrument_id"]), scalar(mo["session"])}) {
					rows.Close()
					return errors.New("SNAPSHOT_MEMBER_METADATA_MISMATCH")
				}
				count++
			}
			e = rows.Err()
			rows.Close()
			if e != nil {
				return e
			}
			want := 0
			for _, a := range expected {
				want += len(a)
			}
			if count != want {
				return errors.New("SNAPSHOT_MEMBER_SET_MISMATCH")
			}
			out.Checked["snapshot_members"] += int64(count)
			var receipts []json.RawMessage
			if len(o["receipts"]) > 0 {
				json.Unmarshal(o["receipts"], &receipts)
			}
			rows, e = s.db.QueryContext(ctx, "SELECT ordinal,receipt_id,content_hash,provider,dataset,status,event_time,source_published_at,first_seen_at,ingested_at,projection_hash FROM source_receipts WHERE snapshot_id=? ORDER BY ordinal", k.id)
			if e != nil {
				return e
			}
			rc := 0
			for rows.Next() {
				var ord int
				var hash string
				p := make([]any, 9)
				ptr := []any{&ord}
				for i := range p {
					ptr = append(ptr, &p[i])
				}
				ptr = append(ptr, &hash)
				if e = rows.Scan(ptr...); e != nil {
					rows.Close()
					return e
				}
				if ord >= len(receipts) || digest(receipts[ord]) != hash {
					rows.Close()
					return errors.New("RECEIPT_HASH_MISMATCH")
				}
				mo, _ := object(receipts[ord])
				want := []any{txt(mo, "receipt_id")}
				for _, field := range []string{"content_hash", "provider", "dataset", "status", "event_time", "source_published_at", "first_seen_at", "ingested_at"} {
					want = append(want, scalar(mo[field]))
				}
				if !equalProjection(p, want) {
					rows.Close()
					return errors.New("RECEIPT_PROJECTION_MISMATCH")
				}
				rc++
			}
			e = rows.Err()
			rows.Close()
			if e != nil {
				return e
			}
			if rc != len(receipts) {
				return errors.New("RECEIPT_SET_MISMATCH")
			}
			out.Checked["source_receipt_projections"] += int64(rc)
		}
		if k.ns == "strategy_experiment_runs" || k.ns == "daily_research_snapshots" || k.ns == "strategy_experiment_summaries" {
			want := []any{scalar(o["strategy_id"]), scalar(o["input_id"]), scalar(o["observation_date"]), scalar(o["decision_cutoff"]), digest(raw)}
			if e = s.verifyTypedValues(ctx, `SELECT r.strategy_id,r.input_id,r.observation_date,r.cutoff,e.raw_sha256 FROM legacy_result_headers r JOIN evidence_objects e ON e.id=r.evidence_id WHERE r.namespace=? AND r.legacy_id=?`, []any{k.ns, k.id}, want); e != nil {
				return e
			}
			out.Checked["legacy_result_header_projections"]++
		}
		if k.ns == "strategy_experiment_item_details" {
			if e = s.verifyTypedValues(ctx, `SELECT detail_id FROM legacy_result_items WHERE run_id=? AND instrument_id=? AND strategy_id=?`, []any{txt(o, "run_id"), txt(o, "instrument_id"), txt(o, "strategy_id")}, []any{k.id}); e != nil {
				return e
			}
			out.Checked["legacy_result_item_projections"]++
		}
		if k.ns == "strategy_experiment_item_details" && txt(o, "encoding") == "zlib+base64+canonical-json" {
			data, e := base64.StdEncoding.DecodeString(txt(o, "payload_b64"))
			if e != nil {
				return e
			}
			r, e := zlib.NewReader(bytes.NewReader(data))
			if e != nil {
				return e
			}
			var size int64
			if e = json.Unmarshal(o["raw_bytes"], &size); e != nil {
				r.Close()
				return e
			}
			if size < 0 || size > 128<<20 {
				r.Close()
				return errors.New("LEGACY_DETAIL_SIZE_INVALID")
			}
			expanded, e := io.ReadAll(io.LimitReader(contextReader{ctx, r}, size+1))
			r.Close()
			if e != nil {
				return e
			}
			if int64(len(expanded)) != size || digest(expanded) != txt(o, "raw_sha256") {
				return errors.New("LEGACY_COMPRESSED_DETAIL_HASH_MISMATCH")
			}
			out.Checked["legacy_compressed_details"]++
		}
		if k.ns == "strategy_experiment_runs" {
			var val any
			d := json.NewDecoder(bytes.NewReader(raw))
			d.UseNumber()
			if e = d.Decode(&val); e != nil {
				return e
			}
			var walk func(any) error
			walk = func(x any) error {
				switch v := x.(type) {
				case map[string]any:
					for field, w := range v {
						if target, ok := w.(string); ok && (field == "detail_ref" || field == "history_artifact_ref") {
							ns := "strategy_experiment_item_details"
							if field == "history_artifact_ref" {
								ns = "history_artifact_manifests"
							}
							var count int
							if e = s.db.QueryRowContext(ctx, "SELECT count(*) FROM legacy_records WHERE namespace=? AND external_id=?", ns, target).Scan(&count); e != nil {
								return e
							}
							if count != 1 {
								return fmt.Errorf("UNCLOSED_RESULT_REFERENCE: %s %s", field, target)
							}
							_, e = s.db.ExecContext(ctx, "INSERT INTO external_references VALUES(?,?,?,?,'RESOLVED') ON CONFLICT DO NOTHING", k.ns, k.id, field, target)
							if e != nil {
								return e
							}
							out.Checked["result_reference_edges"]++
						}
						if e := walk(w); e != nil {
							return e
						}
					}
				case []any:
					for _, w := range v {
						if e := walk(w); e != nil {
							return e
						}
					}
				}
				return nil
			}
			if e = walk(val); e != nil {
				return e
			}
		}
	}
	// Archive rows preserve all source columns, including mutable-job revisions and events.
	rows, e = s.db.QueryContext(ctx, "SELECT name FROM source_inventory WHERE classification!='DISCARD_REBUILDABLE_CACHE' AND name NOT IN ('documents','history_evidence_rows','history_partition_members') ORDER BY name")
	if e != nil {
		return e
	}
	var tables []string
	for rows.Next() {
		var n string
		if e = rows.Scan(&n); e != nil {
			rows.Close()
			return e
		}
		tables = append(tables, n)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, table := range tables {
		q := `SELECT rowid,* FROM frozen."` + strings.ReplaceAll(table, `"`, `""`) + `" ORDER BY rowid`
		rows, e = s.db.QueryContext(ctx, q)
		if e != nil {
			return e
		}
		cols, e := rows.Columns()
		if e != nil {
			rows.Close()
			return e
		}
		type ar struct {
			key, hash string
			job       []any
		}
		var expected []ar
		for rows.Next() {
			v := make([]any, len(cols))
			ptr := make([]any, len(cols))
			for i := range v {
				ptr[i] = &v[i]
			}
			if e = rows.Scan(ptr...); e != nil {
				rows.Close()
				return e
			}
			m := map[string]any{}
			for i, k := range cols[1:] {
				m[k] = v[i+1]
			}
			b, e := json.Marshal(m)
			if e != nil {
				rows.Close()
				return e
			}
			a := ar{key: fmt.Sprint(v[0]), hash: digest(b)}
			if table == "jobs" {
				a.job = []any{m["job_id"], m["revision"], m["state"], m["kind"], m["updated_at"], a.hash, int64(0)}
			}
			expected = append(expected, a)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		for _, a := range expected {
			var hash string
			if e = s.db.QueryRowContext(ctx, "SELECT e.raw_sha256 FROM archived_table_rows a JOIN evidence_objects e ON e.id=a.evidence_id WHERE a.table_name=? AND a.legacy_key=? AND a.revision=?", table, a.key, a.hash).Scan(&hash); e != nil {
				return e
			}
			if hash != a.hash {
				return errors.New("ARCHIVED_ROW_MISMATCH")
			}
			out.Checked["archived_source_rows"]++
			if a.job != nil {
				if e = s.verifyTypedValues(ctx, `SELECT j.job_id,j.revision,j.state,j.kind,j.updated_at,e.raw_sha256,j.executable FROM historical_jobs j JOIN evidence_objects e ON e.id=j.evidence_id WHERE j.job_id=? AND j.revision=?`, a.job[:2], a.job); e != nil {
					return e
				}
				out.Checked["historical_job_projections"]++
			}
		}
	}
	return nil
}

func (s *Store) verifyTypedValues(ctx context.Context, query string, args, want []any) error {
	actual := make([]any, len(want))
	ptr := make([]any, len(want))
	for i := range actual {
		ptr[i] = &actual[i]
	}
	if e := s.db.QueryRowContext(ctx, query, args...).Scan(ptr...); e != nil {
		return e
	}
	if !equalProjection(actual, want) {
		return errors.New("TYPED_RELATION_PROJECTION_MISMATCH")
	}
	return nil
}
