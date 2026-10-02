package sqlitev2

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"
)

// BenchmarkControlledIndexEvidence is deliberately opt-in: ordinary go test does
// not run it. All database paths are testing-owned TempDir paths, never supplied
// by an environment variable. The only external path is an exclusive report file.
//
//	STOCKFISH_INDEX_BENCHMARK_REPORT=/absolute/private/report.json go test \
//	  ./infrastructure/sqlitev2 -run '^$' -bench '^BenchmarkControlledIndexEvidence$' \
//	  -benchtime=1x -count=1
func BenchmarkControlledIndexEvidence(b *testing.B) {
	if b.N != 1 {
		b.Fatal("controlled evidence requires -benchtime=1x")
	}
	destination := os.Getenv("STOCKFISH_INDEX_BENCHMARK_REPORT")
	if destination == "" || !filepath.IsAbs(destination) {
		b.Fatal("absolute STOCKFISH_INDEX_BENCHMARK_REPORT is required")
	}
	if _, e := os.Lstat(destination); !os.IsNotExist(e) {
		b.Fatal("refuse replacing existing report", e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	started := time.Now()
	history, summaries := indexFixtureRows()
	report := indexReport{
		Version: "TEST_FIXTURE_CONTROLLED_INDEX_BENCHMARK_V1", Purpose: "TEST_FIXTURE; NOT_REAL_MARKET_DATA; NOT_PRODUCTION_INDEX_CHANGE",
		StartedAt: started.UTC().Format(time.RFC3339Nano), GoVersion: runtime.Version(), Platform: runtime.GOOS + "/" + runtime.GOARCH,
		AuthoritySchemaHash: ExpectedSchemaChecksum(), ApplicationSchemaHash: digest([]byte(appDDL)),
		HistoryRows: len(history), SummaryRows: len(summaries), DetailRows: len(summaries), ReadRepetitions: 15,
		Controls: []string{"three independent temporary databases; identical schema except named fixture indexes", "300 securities x 100 synthetic dates; 4 saved-result-shaped runs x 5000 summary/detail rows; no strategy executed", "runs stay PREPARING and migration is unpublished: fixture must never be mistaken for published research", "WAL, synchronous FULL, one connection and 16 MiB SQLite cache per database", "insert timing includes prepared INSERT execution and transaction commit; generated arguments and DDL setup are outside timer", "read timing includes prepare/query/scan/rows close; canonical row serialization and SHA256 are outside timer", "one untimed warm-up per query/variant; measured variant order rotates each round; OS caches are not flushed", "no cold-start or hardware-isolated latency claim; repeated read measurements on small synthetic data only", "fixture covering indexes are experimental comparisons and are never added to authority/application schema", "same SQL and arguments in every variant; ordered complete result hash checked on every repetition"},
	}
	variants := []indexVariant{{Name: "without_corresponding_indexes"}, {Name: "current_production_indexes"}, {Name: "fixture_covering_indexes"}}
	stores := make([]*Store, len(variants))
	for i := range variants {
		path := filepath.Join(b.TempDir(), "TEST_FIXTURE_INDEX_"+variants[i].Name+".db")
		store, e := Open(ctx, path, true)
		if e != nil {
			b.Fatal(e)
		}
		stores[i] = store
		b.Cleanup(func() { store.Close() })
		if _, e = store.db.ExecContext(ctx, "PRAGMA cache_size=-16384"); e != nil {
			b.Fatal(e)
		}
		if i != 1 {
			if _, e = store.db.ExecContext(ctx, "DROP INDEX history_security_window; DROP INDEX history_session_cross_section; DROP INDEX app_summary_selection"); e != nil {
				b.Fatal(e)
			}
		}
		if i == 2 {
			for _, ddl := range fixtureCoveringDDL {
				if _, e = store.db.ExecContext(ctx, ddl); e != nil {
					b.Fatal(e)
				}
			}
		}
		variants[i].Indexes, e = indexStats(ctx, store.db)
		if e != nil {
			b.Fatal(e)
		}
		timing, e := insertIndexFixture(ctx, store.db, history, summaries)
		if e != nil {
			b.Fatal(e)
		}
		variants[i].Insert = timing
		if _, e = store.db.ExecContext(ctx, "ANALYZE; PRAGMA wal_checkpoint(TRUNCATE)"); e != nil {
			b.Fatal(e)
		}
		variants[i].Indexes, e = indexStats(ctx, store.db)
		if e != nil {
			b.Fatal(e)
		}
		if e = store.db.QueryRowContext(ctx, "SELECT sqlite_version()").Scan(&report.SQLiteVersion); e != nil {
			b.Fatal(e)
		}
		if e = store.db.QueryRowContext(ctx, "PRAGMA page_size").Scan(&variants[i].PageSize); e != nil {
			b.Fatal(e)
		}
		if e = store.db.QueryRowContext(ctx, "PRAGMA page_count").Scan(&variants[i].PageCount); e != nil {
			b.Fatal(e)
		}
		if e = store.db.QueryRowContext(ctx, "SELECT coalesce(sum(pgsize),0) FROM dbstat WHERE name IN (SELECT name FROM sqlite_master WHERE type='index')").Scan(&variants[i].AllIndexBytes); e != nil {
			b.Fatal(e)
		}
		variants[i].Queries = map[string]indexQueryResult{}
	}
	for _, query := range indexQueries() {
		var reference string
		var expectedCount int
		for i := range variants {
			plan, vm, e := indexExplain(ctx, stores[i].db, query)
			if e != nil {
				b.Fatal(e)
			}
			hash, n, _, e := indexRead(ctx, stores[i].db, query)
			if e != nil {
				b.Fatal(e)
			}
			if i == 0 {
				reference, expectedCount = hash, n
			} else if hash != reference || n != expectedCount {
				b.Fatal("result changed with index", query.Name)
			}
			q := indexQueryResult{SQL: query.SQL, Arguments: query.Args, QueryPlan: plan, ExplainVM: vm, ResultSHA256: hash, Rows: n, ReadSeconds: []float64{}}
			for _, step := range plan {
				if strings.Contains(step.Detail, "USING COVERING INDEX") {
					q.CoveringPlanSteps = append(q.CoveringPlanSteps, step.Detail)
				}
			}
			variants[i].Queries[query.Name] = q
		}
		for round := 0; round < report.ReadRepetitions; round++ {
			for offset := 0; offset < len(variants); offset++ {
				i := (round + offset) % len(variants)
				hash, n, duration, e := indexRead(ctx, stores[i].db, query)
				if e != nil {
					b.Fatal(e)
				}
				if hash != reference || n != expectedCount {
					b.Fatal("read result drift", query.Name, variants[i].Name, round)
				}
				q := variants[i].Queries[query.Name]
				q.ReadSeconds = append(q.ReadSeconds, duration.Seconds())
				variants[i].Queries[query.Name] = q
			}
		}
		baseline := variants[0].Queries[query.Name].ReadSeconds
		baseMedian := indexPercentile(baseline, 0.5)
		for i := range variants {
			q := variants[i].Queries[query.Name]
			q.MedianSeconds = indexPercentile(q.ReadSeconds, 0.5)
			q.P95Seconds = indexPercentile(q.ReadSeconds, 0.95)
			q.SpeedupVsNoIndex = baseMedian / q.MedianSeconds
			variants[i].Queries[query.Name] = q
		}
	}
	for i := range variants {
		var integrity string
		if e := stores[i].db.QueryRowContext(ctx, "PRAGMA integrity_check").Scan(&integrity); e != nil || integrity != "ok" {
			b.Fatal("fixture integrity", integrity, e)
		}
		variants[i].Integrity = integrity
		variants[i].InsertRatioVsNoIndex = variants[i].Insert.TotalSeconds / variants[0].Insert.TotalSeconds
	}
	report.Variants = variants
	report.TotalSeconds = time.Since(started).Seconds()
	report.CompletedAt = time.Now().UTC().Format(time.RFC3339Nano)
	raw, e := json.MarshalIndent(report, "", "  ")
	if e != nil {
		b.Fatal(e)
	}
	if e = os.MkdirAll(filepath.Dir(destination), 0700); e != nil {
		b.Fatal(e)
	}
	file, e := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		b.Fatal(e)
	}
	if _, e = file.Write(append(raw, '\n')); e != nil {
		file.Close()
		b.Fatal(e)
	}
	if e = file.Sync(); e != nil {
		file.Close()
		b.Fatal(e)
	}
	if e = file.Close(); e != nil {
		b.Fatal(e)
	}
	b.ReportMetric(float64(len(history)+len(summaries)), "fixture-rows")
	b.ReportMetric(report.TotalSeconds, "controlled-seconds")
	b.Logf("TEST_FIXTURE controlled evidence written to %s", destination)
}

type indexReport struct {
	Version               string         `json:"version"`
	Purpose               string         `json:"purpose"`
	StartedAt             string         `json:"started_at"`
	CompletedAt           string         `json:"completed_at"`
	GoVersion             string         `json:"go_version"`
	Platform              string         `json:"platform"`
	SQLiteVersion         string         `json:"sqlite_version"`
	AuthoritySchemaHash   string         `json:"authority_schema_sha256"`
	ApplicationSchemaHash string         `json:"application_schema_sha256"`
	HistoryRows           int            `json:"history_rows"`
	SummaryRows           int            `json:"summary_rows"`
	DetailRows            int            `json:"detail_rows"`
	ReadRepetitions       int            `json:"read_repetitions"`
	Controls              []string       `json:"controls"`
	Variants              []indexVariant `json:"variants"`
	TotalSeconds          float64        `json:"total_seconds"`
}
type indexVariant struct {
	Name                 string                      `json:"name"`
	Insert               indexWrite                  `json:"insert"`
	InsertRatioVsNoIndex float64                     `json:"insert_ratio_vs_no_index"`
	Indexes              []indexSize                 `json:"corresponding_indexes"`
	AllIndexBytes        int64                       `json:"all_index_dbstat_bytes"`
	PageSize             int                         `json:"page_size"`
	PageCount            int64                       `json:"page_count"`
	Queries              map[string]indexQueryResult `json:"queries"`
	Integrity            string                      `json:"integrity"`
}
type indexWrite struct {
	BeginSeconds   float64 `json:"begin_seconds"`
	HistorySeconds float64 `json:"history_insert_seconds"`
	ResultSeconds  float64 `json:"result_insert_seconds"`
	CommitSeconds  float64 `json:"commit_seconds"`
	TotalSeconds   float64 `json:"total_seconds"`
}
type indexSize struct {
	Name  string `json:"name"`
	DDL   string `json:"ddl"`
	Bytes int64  `json:"dbstat_bytes"`
	Pages int64  `json:"dbstat_pages"`
}
type indexPlan struct {
	ID     int    `json:"id"`
	Parent int    `json:"parent"`
	Detail string `json:"detail"`
}
type indexOpcode struct {
	Address int    `json:"address"`
	Opcode  string `json:"opcode"`
	P1      int    `json:"p1"`
	P2      int    `json:"p2"`
	P3      int    `json:"p3"`
	P4      any    `json:"p4"`
	P5      int    `json:"p5"`
	Comment any    `json:"comment"`
}
type indexQueryResult struct {
	SQL               string        `json:"sql"`
	Arguments         []any         `json:"arguments"`
	QueryPlan         []indexPlan   `json:"explain_query_plan"`
	ExplainVM         []indexOpcode `json:"explain_vm"`
	CoveringPlanSteps []string      `json:"covering_plan_steps"`
	ResultSHA256      string        `json:"result_sha256"`
	Rows              int           `json:"rows"`
	ReadSeconds       []float64     `json:"read_seconds"`
	MedianSeconds     float64       `json:"median_seconds"`
	P95Seconds        float64       `json:"p95_seconds"`
	SpeedupVsNoIndex  float64       `json:"median_speedup_vs_no_index"`
}
type indexQuery struct {
	Name, SQL string
	Args      []any
}

var fixtureCoveringDDL = []string{
	"CREATE INDEX TEST_FIXTURE_cover_security ON history_rows(instrument_id,session,kind,id,close,amount_cny,comparable_close)",
	"CREATE INDEX TEST_FIXTURE_cover_session ON history_rows(session,kind,instrument_id,id,close,amount_cny)",
	"CREATE INDEX TEST_FIXTURE_cover_summary ON app_item_summaries(run_id,decision_status,display_rank,ordinal,payload,raw_hash,source_hash,projection_version,instrument_id)",
	"CREATE INDEX TEST_FIXTURE_cover_detail_hash ON app_items(run_id,instrument_id,raw_hash)",
}

func indexQueries() []indexQuery {
	return []indexQuery{
		{"security_window", `SELECT id,close,amount_cny,comparable_close FROM history_rows WHERE instrument_id=? AND session BETWEEN ? AND ? AND kind='bar' ORDER BY session`, []any{"TEST000150.SZ", "2026-01-20", "2026-03-10"}},
		{"session_cross_section", `SELECT instrument_id,close,amount_cny FROM history_rows WHERE session=? AND kind='bar' ORDER BY instrument_id`, []any{"2026-03-10"}},
		{"summary_selection", `SELECT payload,raw_hash,source_hash,projection_version,instrument_id,(SELECT raw_hash FROM app_items i WHERE i.run_id=app_item_summaries.run_id AND i.instrument_id=app_item_summaries.instrument_id) FROM app_item_summaries WHERE run_id=? AND decision_status='CORE_PASS' ORDER BY display_rank,ordinal LIMIT ? OFFSET ?`, []any{"TEST_FIXTURE_RUN_2", 50, 50}},
	}
}

type indexHistoryRow struct {
	hash, iid, session string
	payload            []byte
	close, amount      string
}
type indexSummaryRow struct {
	run, iid, status        string
	ordinal                 int
	detail, summary         []byte
	detailHash, summaryHash string
}

func indexFixtureRows() ([]indexHistoryRow, []indexSummaryRow) {
	history := make([]indexHistoryRow, 0, 30000)
	origin := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for day := 0; day < 100; day++ {
		session := origin.AddDate(0, 0, day).Format("2006-01-02")
		for security := 0; security < 300; security++ {
			iid := fmt.Sprintf("TEST%06d.SZ", security)
			close := fmt.Sprintf("%d.%04d", 10+security%50, day*31%10000)
			amount := fmt.Sprintf("%d.00", 500000000+day*1000+security)
			payload, _ := json.Marshal(map[string]any{"purpose": "TEST_FIXTURE_INDEX_BENCHMARK", "instrument_id": iid, "session": session, "close": close, "amount_cny": amount})
			history = append(history, indexHistoryRow{digest(payload), iid, session, payload, close, amount})
		}
	}
	summaries := make([]indexSummaryRow, 0, 20000)
	for i := 0; i < 20000; i++ {
		run := fmt.Sprintf("TEST_FIXTURE_RUN_%d", i%4)
		iid := fmt.Sprintf("TEST%06d.SZ", i/4)
		ordinal := i / 4
		status := "FAIL"
		if i%5 == 0 {
			status = "CORE_PASS"
		} else if i%5 == 1 {
			status = "UNKNOWN"
		}
		detail, _ := json.Marshal(map[string]any{"purpose": "TEST_FIXTURE_INDEX_BENCHMARK", "instrument_id": iid, "candidate_status": status})
		summary, _ := json.Marshal(map[string]any{"instrument_id": iid, "candidate_status": status, "rank": ordinal + 1, "amount_cny": "500000000.00"})
		summaries = append(summaries, indexSummaryRow{run, iid, status, ordinal, detail, summary, digest(detail), digest(summary)})
	}
	return history, summaries
}
func insertIndexFixture(ctx context.Context, db *sql.DB, history []indexHistoryRow, summaries []indexSummaryRow) (out indexWrite, err error) {
	// Prepare before the measured transaction so preparation is not reported as
	// per-row write/index overhead. A single durable commit is common to all arms.
	hs, e := db.PrepareContext(ctx, `INSERT INTO history_rows(row_hash,kind,instrument_id,session,raw_size,codec,payload,projection_version,projection_hash,close,amount_cny,comparable_close) VALUES(?,'bar',?,?,?,'raw',?,?,?,?,?,?)`)
	if e != nil {
		return out, e
	}
	defer hs.Close()
	ds, e := db.PrepareContext(ctx, `INSERT INTO app_items VALUES(?,?,?,?,?,?)`)
	if e != nil {
		return out, e
	}
	defer ds.Close()
	ss, e := db.PrepareContext(ctx, `INSERT INTO app_item_summaries VALUES(?,?,?,?,?,?,?,?,?)`)
	if e != nil {
		return out, e
	}
	defer ss.Close()
	started := time.Now()
	tx, e := db.BeginTx(ctx, nil)
	if e != nil {
		return out, e
	}
	defer tx.Rollback()
	out.BeginSeconds = time.Since(started).Seconds()
	if _, e = tx.ExecContext(ctx, "INSERT INTO app_inputs VALUES('TEST_FIXTURE_INPUT',?,?,'2026-01-01','2026-01-01T00:00:00Z','TEST_FIXTURE','TEST_FIXTURE')", digest([]byte("TEST_FIXTURE")), []byte("TEST_FIXTURE")); e != nil {
		return out, e
	}
	for i := 0; i < 4; i++ {
		if _, e = tx.ExecContext(ctx, `INSERT INTO app_runs VALUES(?,'TEST_FIXTURE_INPUT','STAGE_AWARE_RESEARCH_R1','2026-01-01','TEST_FIXTURE_ONLY','ABSENT',?,?,?,?, 'PREPARING',5000,NULL)`, fmt.Sprintf("TEST_FIXTURE_RUN_%d", i), []byte(`{}`), digest([]byte(`{}`)), []byte(`{}`), digest([]byte(`{}`))); e != nil {
			return out, e
		}
	}
	htx, dtx, stx := tx.StmtContext(ctx, hs), tx.StmtContext(ctx, ds), tx.StmtContext(ctx, ss)
	stage := time.Now()
	for _, r := range history {
		if _, e = htx.ExecContext(ctx, r.hash, r.iid, r.session, len(r.payload), r.payload, ProjectionVersion, r.hash, r.close, r.amount, r.close); e != nil {
			return out, e
		}
	}
	out.HistorySeconds = time.Since(stage).Seconds()
	stage = time.Now()
	for _, r := range summaries {
		if _, e = dtx.ExecContext(ctx, r.run, r.iid, r.ordinal, r.detail, r.detailHash, len(r.detail)); e != nil {
			return out, e
		}
		if _, e = stx.ExecContext(ctx, r.run, r.iid, r.ordinal, r.ordinal+1, r.status, r.summary, r.summaryHash, r.detailHash, "GO_RESULT_SUMMARY_V1"); e != nil {
			return out, e
		}
	}
	out.ResultSeconds = time.Since(stage).Seconds()
	stage = time.Now()
	if e = tx.Commit(); e != nil {
		return out, e
	}
	out.CommitSeconds = time.Since(stage).Seconds()
	out.TotalSeconds = time.Since(started).Seconds()
	return out, nil
}
func indexStats(ctx context.Context, db *sql.DB) ([]indexSize, error) {
	rows, e := db.QueryContext(ctx, `SELECT name,sql FROM sqlite_master WHERE type='index' AND (name IN ('history_security_window','history_session_cross_section','app_summary_selection') OR name LIKE 'TEST_FIXTURE_cover_%') ORDER BY name`)
	if e != nil {
		return nil, e
	}
	out := []indexSize{}
	for rows.Next() {
		var s indexSize
		if e = rows.Scan(&s.Name, &s.DDL); e != nil {
			rows.Close()
			return nil, e
		}
		out = append(out, s)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	for i := range out {
		if e = db.QueryRowContext(ctx, "SELECT coalesce(sum(pgsize),0),count(*) FROM dbstat WHERE name=?", out[i].Name).Scan(&out[i].Bytes, &out[i].Pages); e != nil {
			return nil, e
		}
	}
	return out, nil
}
func indexExplain(ctx context.Context, db *sql.DB, q indexQuery) ([]indexPlan, []indexOpcode, error) {
	rows, e := db.QueryContext(ctx, "EXPLAIN QUERY PLAN "+q.SQL, q.Args...)
	if e != nil {
		return nil, nil, e
	}
	plan := []indexPlan{}
	for rows.Next() {
		var p indexPlan
		var unused int
		if e = rows.Scan(&p.ID, &p.Parent, &unused, &p.Detail); e != nil {
			rows.Close()
			return nil, nil, e
		}
		plan = append(plan, p)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, nil, e
	}
	rows, e = db.QueryContext(ctx, "EXPLAIN "+q.SQL, q.Args...)
	if e != nil {
		return nil, nil, e
	}
	vm := []indexOpcode{}
	for rows.Next() {
		var v indexOpcode
		if e = rows.Scan(&v.Address, &v.Opcode, &v.P1, &v.P2, &v.P3, &v.P4, &v.P5, &v.Comment); e != nil {
			rows.Close()
			return nil, nil, e
		}
		vm = append(vm, v)
	}
	e = rows.Err()
	rows.Close()
	return plan, vm, e
}
func indexRead(ctx context.Context, db *sql.DB, q indexQuery) (string, int, time.Duration, error) {
	started := time.Now()
	rows, e := db.QueryContext(ctx, q.SQL, q.Args...)
	if e != nil {
		return "", 0, 0, e
	}
	columns, e := rows.Columns()
	if e != nil {
		rows.Close()
		return "", 0, 0, e
	}
	data := [][]any{}
	for rows.Next() {
		values := make([]any, len(columns))
		pointers := make([]any, len(columns))
		for i := range values {
			pointers[i] = &values[i]
		}
		if e = rows.Scan(pointers...); e != nil {
			rows.Close()
			return "", 0, 0, e
		}
		data = append(data, values)
	}
	e = rows.Err()
	rows.Close()
	elapsed := time.Since(started)
	if e != nil {
		return "", 0, 0, e
	}
	hash := sha256.New()
	for _, row := range data {
		raw, e := json.Marshal(row)
		if e != nil {
			return "", 0, 0, e
		}
		hash.Write(raw)
		hash.Write([]byte{'\n'})
	}
	return hex.EncodeToString(hash.Sum(nil)), len(data), elapsed, nil
}
func indexPercentile(values []float64, fraction float64) float64 {
	ordered := append([]float64{}, values...)
	sort.Float64s(ordered)
	index := int(fraction*float64(len(ordered)-1) + 0.5)
	return ordered[index]
}
