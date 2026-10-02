package sqlitev2

import "context"

type ObjectStats struct {
	Name         string `json:"name"`
	Bytes        int64  `json:"bytes"`
	PayloadBytes int64  `json:"payload_bytes"`
	UnusedBytes  int64  `json:"unused_bytes"`
	Cells        int64  `json:"cells"`
}
type DatabaseStats struct {
	SQLiteVersion string              `json:"sqlite_version"`
	Pages         int64               `json:"pages"`
	PageSize      int64               `json:"page_size"`
	FreePages     int64               `json:"free_pages"`
	Objects       []ObjectStats       `json:"objects"`
	Plans         map[string][]string `json:"query_plans"`
}

func (s *Store) Stats(ctx context.Context) (o DatabaseStats, err error) {
	if err = s.db.QueryRowContext(ctx, "SELECT sqlite_version()").Scan(&o.SQLiteVersion); err != nil {
		return
	}
	if err = s.db.QueryRowContext(ctx, "PRAGMA page_count").Scan(&o.Pages); err != nil {
		return
	}
	if err = s.db.QueryRowContext(ctx, "PRAGMA page_size").Scan(&o.PageSize); err != nil {
		return
	}
	if err = s.db.QueryRowContext(ctx, "PRAGMA freelist_count").Scan(&o.FreePages); err != nil {
		return
	}
	rows, e := s.db.QueryContext(ctx, "SELECT name,pgsize,payload,unused,ncell FROM dbstat WHERE aggregate=TRUE ORDER BY name")
	if e != nil {
		err = e
		return
	}
	for rows.Next() {
		var st ObjectStats
		if err = rows.Scan(&st.Name, &st.Bytes, &st.PayloadBytes, &st.UnusedBytes, &st.Cells); err != nil {
			rows.Close()
			return
		}
		o.Objects = append(o.Objects, st)
	}
	err = rows.Err()
	rows.Close()
	if err == nil {
		o.Plans, err = s.QueryPlans(ctx)
	}
	return
}
