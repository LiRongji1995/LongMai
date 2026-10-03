package sqlitev2

import (
	"context"
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"path/filepath"
	"strings"
	"testing"
)

const batchFixtureRaw = `{
 "coverage_type":"TEST_FIXTURE", "observation_date":"2026-01-02",
 "themes":[{"theme_id":"TEST_THEME","name":"TEST_FIXTURE","theme_type":"EVENT","definition_version":"TEST_FIXTURE","effective_from":"2026-01-01","effective_to":null,"member_ids":["600001.SH","600002.SH","600001.SH"],"source_refs":["TEST_RECEIPT"]}],
 "funds":[{"theme_id":"TEST_THEME","instrument_id":"600002.SH","session":"2026-01-02","source_layer":"PUBLIC_EXCHANGE_LHB","seat_id":"TEST_SEAT","buy_amount_cny":"+0012345678901234567890.00100","sell_amount_cny":1.2300e+8,"net_amount_cny":null,"window_start":"2026-01-01","window_end":"2026-01-02","expected_scope":["600001.SH","600002.SH"],"source_refs":["TEST_RECEIPT"]}],
 "receipts":[{"receipt_id":"TEST_RECEIPT","provider":"TEST_FIXTURE","dataset":"funds","content_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","field_version":"TEST_FIXTURE","status":"COMPLETE","row_count":1,"pagination_complete":true,"instrument_ids":["600001.SH","600002.SH"],"business_sessions":["2026-01-01","2026-01-02"],"event_time":"2026-01-02T00:00:00Z","source_published_at":"2026-01-02T00:00:00Z","first_seen_at":"2026-01-02T00:00:00Z","ingested_at":"2026-01-02T00:00:00Z","source_type":null,"parameters":{"page":1,"limit":"020","enabled":true,"absent":null},"historical_time_proof":{"policy_id":"TEST_FIXTURE","source_url":"https://example.invalid/TEST_FIXTURE","evidence_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","applies_from":"2026-01-01","applies_to":"2026-01-02","verified_at":"2026-01-02T00:00:00Z","event_time_basis":"SOURCE_DATED_EVENT_RECORD","publication_time_basis":"SOURCE_PUBLICATION_TIMESTAMP","evidence_refs":["TEST_PROOF"]}}]
}`

func newBatchFixture(t *testing.T, snapshots int) *Store {
	t.Helper()
	ctx := context.Background()
	s, e := Open(ctx, filepath.Join(t.TempDir(), "TEST_FIXTURE_batch.sqlite3"), true)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.Close() })
	if e = s.InitBatchProjectionSchema(ctx); e != nil {
		t.Fatal(e)
	}
	if _, e = s.db.ExecContext(ctx, "INSERT INTO migration_state VALUES(1,'TEST_FIXTURE','MIGRATING','{}','TEST_FIXTURE')"); e != nil {
		t.Fatal(e)
	}
	for i := 0; i < snapshots; i++ {
		tx, e := s.db.BeginTx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		id := "TEST_FIXTURE_" + strings.Repeat("x", i+1)
		e = putDocument(ctx, tx, "candidate_inputs", id, []byte(batchFixtureRaw), digest([]byte(batchFixtureRaw)), "TEST_FIXTURE")
		if e != nil {
			tx.Rollback()
			t.Fatal(e)
		}
		if e = tx.Commit(); e != nil {
			t.Fatal(e)
		}
	}
	// Projection-only fixture: construct authority before sealing its test baseline.
	if _, e = s.db.Exec("UPDATE migration_state SET status='VERIFIED' WHERE id=1"); e != nil {
		t.Fatal(e)
	}
	return s
}
func batchInput(id string, raw []byte) c.InputRecord {
	return c.InputRecord{ID: id, Raw: raw, RawHash: digest(raw), Observation: "2026-01-02", Cutoff: "2026-01-02T08:00:00Z", Coverage: "TEST_FIXTURE"}
}
func TestBatchProjectionBackfillResumeExactAndNewInput(t *testing.T) {
	ctx := context.Background()
	s := newBatchFixture(t, 3)
	r, e := s.BackfillBatchProjections(ctx, BatchProjectionOptions{BatchSize: 1, MaxBatches: 1})
	if e != nil || r.Status != "INTERRUPTED_CHECKPOINTED" || r.Owners != 1 {
		t.Fatal(r, e)
	}
	if e = s.BatchProjectionsReady(ctx); e == nil {
		t.Fatal("partial projection exposed")
	}
	var n int
	s.db.QueryRow("SELECT count(*) FROM batch_projection_publications").Scan(&n)
	if n != 0 {
		t.Fatal("partial global publication")
	}
	r, e = s.BackfillBatchProjections(ctx, BatchProjectionOptions{BatchSize: 1, MaxBatches: 1})
	if e != nil || r.Status != "INTERRUPTED_CHECKPOINTED" || r.Owners != 2 {
		t.Fatal("resume did not advance", r, e)
	}
	r, e = s.BackfillBatchProjections(ctx, BatchProjectionOptions{BatchSize: 1})
	if e != nil || r.Owners != 3 || r.Records != 9 {
		t.Fatal(r, e)
	}
	if e = s.BatchProjectionsReady(ctx); e != nil {
		t.Fatal(e)
	}
	verified, e := s.VerifyBatchProjections(ctx)
	if e != nil || verified.ManifestHash != r.ManifestHash {
		t.Fatal(verified, e)
	}
	var buy, sell, presence, proof string
	var net any
	if e = s.db.QueryRow("SELECT f.buy_amount_cny,f.sell_amount_cny,f.net_amount_cny,r.presence_json FROM batch_fund_rows f JOIN batch_projection_records r ON r.record_id=f.record_id LIMIT 1").Scan(&buy, &sell, &net, &presence); e != nil {
		t.Fatal(e)
	}
	if buy != "+0012345678901234567890.00100" || sell != "1.2300e+8" || net != nil || !strings.Contains(presence, `"net_amount_cny":"NULL"`) || !strings.Contains(presence, `"amount_cny":"ABSENT"`) {
		t.Fatal(buy, sell, net, presence)
	}
	s.db.QueryRow("SELECT proof_policy_id FROM batch_receipt_scopes LIMIT 1").Scan(&proof)
	if proof != "TEST_FIXTURE" {
		t.Fatal(proof)
	}
	raw := []byte(`{"purpose":"TEST_FIXTURE","input":` + batchFixtureRaw + `,"cutoff":"2026-01-02T08:00:00Z"}`)
	repo := NewAppRepository(s)
	in := batchInput("TEST_NATIVE", raw)
	if e = repo.PutInput(ctx, in); e != nil {
		t.Fatal(e)
	}
	if e = repo.PutInput(ctx, in); e != nil {
		t.Fatal("idempotent", e)
	}
	v, e := s.VerifyBatchProjections(ctx)
	if e != nil || v.Owners != 4 || v.Records != 12 || v.ManifestHash == r.ManifestHash {
		t.Fatal(v, e)
	}
	var pointer string
	var evidenceID any
	if e = s.db.QueryRow("SELECT json_pointer,evidence_id FROM batch_projection_records WHERE owner_kind='APP_INPUT' AND kind='funds'").Scan(&pointer, &evidenceID); e != nil {
		t.Fatal(e)
	}
	if pointer != "/input/funds/0" || evidenceID != nil {
		t.Fatal(pointer, evidenceID)
	}
	// The same decoder produces the exact same typed content for both origins.
	var hashes int
	if e = s.db.QueryRow("SELECT count(DISTINCT projection_hash) FROM batch_projection_records WHERE kind='funds'").Scan(&hashes); e != nil || hashes != 1 {
		t.Fatal(hashes, e)
	}
	saved, e := repo.Input(ctx, in.ID)
	if e != nil || string(saved.Raw) != string(raw) {
		t.Fatal("raw input changed", e)
	}
	old := v.ManifestHash
	s.Close()
	again, e := Open(ctx, s.Path, false)
	if e != nil {
		t.Fatal(e)
	}
	defer again.Close()
	v, e = again.VerifyBatchProjections(ctx)
	if e != nil || v.ManifestHash != old {
		t.Fatal(v, e)
	}
}
func TestBatchProjectionInputAtomicFailureAndBackfillCancellation(t *testing.T) {
	ctx := context.Background()
	s := newBatchFixture(t, 0)
	repo := NewAppRepository(s)
	if _, e := s.db.Exec(`CREATE TRIGGER TEST_FIXTURE_abort_fund BEFORE INSERT ON batch_fund_rows BEGIN SELECT RAISE(ABORT,'TEST_FIXTURE interrupted projection'); END`); e != nil {
		t.Fatal(e)
	}
	if e := repo.PutInput(ctx, batchInput("TEST_ATOMIC", []byte(batchFixtureRaw))); e == nil {
		t.Fatal("injected failure ignored")
	}
	for _, table := range []string{"app_inputs", "app_change_ledger", "batch_projection_owners", "batch_projection_records", "batch_theme_headers", "batch_theme_members"} {
		var n int
		if e := s.db.QueryRow("SELECT count(*) FROM " + table).Scan(&n); e != nil || n != 0 {
			t.Fatal(table, n, e)
		}
	}
	if _, e := s.db.Exec("DROP TRIGGER TEST_FIXTURE_abort_fund"); e != nil {
		t.Fatal(e)
	}
	if e := repo.PutInput(ctx, batchInput("TEST_ATOMIC", []byte(batchFixtureRaw))); e != nil {
		t.Fatal(e)
	}
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	if _, e := s.BackfillBatchProjections(cancelled, BatchProjectionOptions{}); !errors.Is(e, context.Canceled) {
		t.Fatal(e)
	}
	if _, e := s.VerifyBatchProjections(ctx); e != nil {
		t.Fatal(e)
	}
}
func TestBatchProjectionTamperingAndCoverage(t *testing.T) {
	cases := []struct{ name, table, sql string }{
		{"money", "batch_fund_rows", "UPDATE batch_fund_rows SET buy_amount_cny='9'"},
		{"theme_member", "batch_theme_members", "UPDATE batch_theme_members SET instrument_id='TEST_OTHER' WHERE ordinal=0"},
		{"scope", "batch_receipt_relations", "UPDATE batch_receipt_relations SET value_text='TEST_OTHER' WHERE field='instrument_ids' AND ordinal=0"},
		{"proof", "batch_receipt_scopes", "UPDATE batch_receipt_scopes SET proof_policy_id='TEST_OTHER'"},
		{"parameter", "batch_receipt_parameters", "UPDATE batch_receipt_parameters SET value_text='9' WHERE name='page'"},
		{"binding", "batch_projection_records", "UPDATE batch_projection_records SET parent_raw_hash='TEST_OTHER'"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			s := newBatchFixture(t, 1)
			if _, e := s.BackfillBatchProjections(ctx, BatchProjectionOptions{}); e != nil {
				t.Fatal(e)
			}
			if _, e := s.db.Exec(tc.sql); e == nil {
				t.Fatal("immutability not enforced")
			}
			var trigger string
			name := tc.table + "_no_UPDATE"
			if e := s.db.QueryRow("SELECT sql FROM sqlite_master WHERE name=?", name).Scan(&trigger); e != nil {
				t.Fatal(e)
			}
			if _, e := s.db.Exec("DROP TRIGGER " + name); e != nil {
				t.Fatal(e)
			}
			if _, e := s.db.Exec(tc.sql); e != nil {
				t.Fatal(e)
			}
			if _, e := s.db.Exec(trigger); e != nil {
				t.Fatal(e)
			}
			if _, e := s.VerifyBatchProjections(ctx); e == nil {
				t.Fatal("typed tampering accepted")
			}
		})
	}
	t.Run("new_owner_missing", func(t *testing.T) {
		ctx := context.Background()
		s := newBatchFixture(t, 1)
		if _, e := s.BackfillBatchProjections(ctx, BatchProjectionOptions{}); e != nil {
			t.Fatal(e)
		}
		// Explicit test migration opens the authority before adding an owner.
		if _, e := s.db.Exec("UPDATE migration_state SET status='MIGRATING' WHERE id=1"); e != nil {
			t.Fatal(e)
		}
		tx, _ := s.db.BeginTx(ctx, nil)
		if e := putDocument(ctx, tx, "candidate_inputs", "TEST_ADDED", []byte(batchFixtureRaw), digest([]byte(batchFixtureRaw)), "TEST_FIXTURE"); e != nil {
			tx.Rollback()
			t.Fatal(e)
		}
		if e := tx.Commit(); e != nil {
			t.Fatal(e)
		}
		if e := s.BatchProjectionsReady(ctx); e == nil {
			t.Fatal("new source omitted from ready")
		}
	})
	t.Run("missing_trigger", func(t *testing.T) {
		s := newBatchFixture(t, 0)
		if _, e := s.db.Exec("DROP TRIGGER batch_fund_rows_no_UPDATE"); e != nil {
			t.Fatal(e)
		}
		if e := s.InitBatchProjectionSchema(context.Background()); e == nil {
			t.Fatal("missing trigger silently repaired")
		}
	})
}
func TestBatchProjectionKnownFieldTypesAndNulls(t *testing.T) {
	ctx := context.Background()
	s := newBatchFixture(t, 0)
	r := NewAppRepository(s)
	for _, raw := range []string{`{"receipts":[{"pagination_complete":"true"}]}`, `{"themes":[{"member_ids":[1]}]}`, `{"receipts":[{"parameters":{"bad":{}}}]}`} {
		if e := r.PutInput(ctx, batchInput("TEST_BAD", []byte(raw))); e == nil {
			t.Fatal("invalid typed field accepted", raw)
		}
	}
	raw := []byte(`{"themes":[],"funds":null,"receipts":[]}`)
	if e := r.PutInput(ctx, batchInput("TEST_EMPTY", raw)); e != nil {
		t.Fatal(e)
	}
	v, e := s.VerifyBatchProjections(ctx)
	if e != nil || v.Owners != 1 || v.Records != 0 {
		t.Fatal(v, e)
	}
	var manifest []byte
	if e = s.db.QueryRow("SELECT manifest FROM batch_projection_owners").Scan(&manifest); e != nil || !json.Valid(manifest) {
		t.Fatal(e)
	}
}

func TestBatchProjectionPreservesInvalidOpenFundFields(t *testing.T) {
	ctx := context.Background()
	s := newBatchFixture(t, 0)
	r := NewAppRepository(s)
	raw := []byte(`{"funds":[{"buy_amount_cny":true,"sell_amount_cny":"not available","source_refs":{},"instrument_id":12}]}`)
	if e := r.PutInput(ctx, batchInput("TEST_OPEN_FUNDS", raw)); e != nil {
		t.Fatal(e)
	}
	var money any
	var presence string
	if e := s.db.QueryRow("SELECT f.buy_amount_cny,r.presence_json FROM batch_fund_rows f JOIN batch_projection_records r ON r.record_id=f.record_id").Scan(&money, &presence); e != nil {
		t.Fatal(e)
	}
	if money != nil || !strings.Contains(presence, `"buy_amount_cny":"INVALID_DECIMAL"`) || !strings.Contains(presence, `"source_refs":"INVALID_STRINGS"`) {
		t.Fatal(money, presence)
	}
	if _, e := s.VerifyBatchProjections(ctx); e != nil {
		t.Fatal(e)
	}
}

func TestBatchProjectionInterruptedOwnerTransactionResumes(t *testing.T) {
	ctx := context.Background()
	s := newBatchFixture(t, 3)
	if _, e := s.db.Exec(`CREATE TRIGGER TEST_FIXTURE_abort_owner BEFORE INSERT ON batch_projection_records WHEN NEW.owner_id='TEST_FIXTURE_xx' AND NEW.kind='funds' BEGIN SELECT RAISE(ABORT,'TEST_FIXTURE crash in owner'); END`); e != nil {
		t.Fatal(e)
	}
	if _, e := s.BackfillBatchProjections(ctx, BatchProjectionOptions{}); e == nil {
		t.Fatal("backfill failure ignored")
	}
	var owners, partial, published int
	s.db.QueryRow("SELECT count(*) FROM batch_projection_owners").Scan(&owners)
	s.db.QueryRow("SELECT count(*) FROM batch_projection_records WHERE owner_id='TEST_FIXTURE_xx'").Scan(&partial)
	s.db.QueryRow("SELECT count(*) FROM batch_projection_publications").Scan(&published)
	if owners != 1 || partial != 0 || published != 0 {
		t.Fatal(owners, partial, published)
	}
	if _, e := s.db.Exec("DROP TRIGGER TEST_FIXTURE_abort_owner"); e != nil {
		t.Fatal(e)
	}
	if _, e := s.BackfillBatchProjections(ctx, BatchProjectionOptions{}); e != nil {
		t.Fatal(e)
	}
	if _, e := s.VerifyBatchProjections(ctx); e != nil {
		t.Fatal(e)
	}
}
