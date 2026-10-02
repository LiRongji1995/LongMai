package sqlitev2

import "context"

func ExpectedSchemaChecksum() string { return digest([]byte(schema)) }

// QueryPlans returns SQLite's actual chosen plans; a scan during full integrity
// verification is intentional and is not suppressed or relabelled as an index hit.
func (s *Store) QueryPlans(ctx context.Context) (map[string][]string, error) {
	out := map[string][]string{}
	queries := map[string]string{
		"history_window":        `SELECT h.row_hash,h.payload,h.close FROM history_rows h JOIN partition_members m ON m.row_id=h.id JOIN partitions p ON p.id=m.partition_id WHERE p.partition_hash IN ('first','second') AND h.kind='bar' AND h.instrument_id='600001.SH' AND h.session>='2026-01-01' AND h.session<='2026-09-30' ORDER BY h.session,h.row_hash`,
		"typed_snapshot_window": `SELECT h.row_hash,h.close,h.amount_cny FROM history_rows h JOIN partition_members m ON m.row_id=h.id JOIN snapshot_partitions sp ON sp.partition_id=m.partition_id WHERE sp.snapshot_id='test' AND h.instrument_id='600001.SH' AND h.session>='2026-01-01' AND h.session<='2026-09-30' AND h.kind='bar' ORDER BY h.session,h.row_hash`,
		"security_window":       `SELECT id,close,amount_cny,comparable_close FROM history_rows WHERE instrument_id='600001.SH' AND session BETWEEN '2026-01-01' AND '2026-09-30' AND kind='bar' ORDER BY session`,
		"session_cross_section": `SELECT instrument_id,close,amount_cny FROM history_rows WHERE session='2026-09-30' AND kind='bar' ORDER BY instrument_id`,
		"partition_validation":  `SELECT h.row_hash,h.kind,h.instrument_id,h.session FROM partitions p JOIN partition_members m ON m.partition_id=p.id LEFT JOIN history_rows h ON h.id=m.row_id WHERE p.partition_hash='test' ORDER BY h.kind,h.instrument_id,h.row_hash`,
		"legacy_result_list":    `SELECT legacy_id,strategy_id,input_id,observation_date FROM legacy_result_headers WHERE namespace='strategy_experiment_runs' ORDER BY observation_date DESC LIMIT 50`,
		"snapshot_member":       `SELECT evidence_id FROM snapshot_members WHERE snapshot_id='test' AND instrument_id='600001.SH' AND kind='bars' ORDER BY session`,
	}
	for name, q := range queries {
		rows, e := s.db.QueryContext(ctx, "EXPLAIN QUERY PLAN "+q)
		if e != nil {
			return nil, e
		}
		for rows.Next() {
			var id, parent, unused int
			var detail string
			if e = rows.Scan(&id, &parent, &unused, &detail); e != nil {
				rows.Close()
				return nil, e
			}
			out[name] = append(out[name], detail)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return nil, e
		}
	}
	return out, nil
}
