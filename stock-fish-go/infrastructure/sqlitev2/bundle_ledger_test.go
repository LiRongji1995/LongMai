package sqlitev2

import (
	"context"
	"testing"
)

func TestBundlePublicationLedgerAtomic(t *testing.T) {
	ctx := context.Background()
	s := openBundleTest(t)
	if e := InitApplicationSchema(ctx, s.db); e != nil {
		t.Fatal(e)
	}
	id, e := s.ImportBundle(ctx, bundleFixture(t, true, false))
	if e != nil {
		t.Fatal(e)
	}
	var n int
	if e = s.db.QueryRow("SELECT count(*) FROM app_change_ledger WHERE kind='BUNDLE_V1' AND entity_id=?", id).Scan(&n); e != nil {
		t.Fatal(e)
	}
	if n != 1 {
		t.Fatal("BUG: published bundle has no authoritative ledger entry", n)
	}
}
func TestBundlePublicationRollbackAndIdempotence(t *testing.T) {
	ctx := context.Background()
	s := openBundleTest(t)
	root := bundleFixture(t, true, false)
	if e := InitApplicationSchema(ctx, s.db); e != nil {
		t.Fatal(e)
	}
	if _, e := s.db.Exec(`CREATE TEMP TRIGGER fixture_fail_ledger BEFORE INSERT ON app_change_ledger WHEN NEW.kind='BUNDLE_V1' BEGIN SELECT RAISE(ABORT,'TEST_FIXTURE ledger failure');END`); e != nil {
		t.Fatal(e)
	}
	id, e := s.ImportBundle(ctx, root)
	if e == nil {
		t.Fatal("injected failure ignored")
	}
	var status string
	if e = s.db.QueryRow("SELECT status FROM legacy_bundles WHERE bundle_id=?", id).Scan(&status); e != nil || status != "IMPORTING" {
		t.Fatal(status, e)
	}
	if e = VerifyBundleLedger(ctx, s.db); e == nil {
		t.Fatal("unfinished import passed gate")
	}
	if _, e = s.db.Exec("DROP TRIGGER fixture_fail_ledger"); e != nil {
		t.Fatal(e)
	}
	for i := 0; i < 2; i++ {
		if _, e = s.ImportBundle(ctx, root); e != nil {
			t.Fatal(e)
		}
	}
	var n int
	if e = s.db.QueryRow("SELECT count(*) FROM app_change_ledger WHERE kind='BUNDLE_V1'").Scan(&n); e != nil || n != 1 {
		t.Fatal(n, e)
	}
	if e = VerifyBundleLedger(ctx, s.db); e != nil {
		t.Fatal(e)
	}
	// Test-only bypass of immutability simulates corruption, not repair.
	if _, e = s.db.Exec("DROP TRIGGER app_ledger_no_delete; DELETE FROM app_change_ledger WHERE kind='BUNDLE_V1'"); e != nil {
		t.Fatal(e)
	}
	if e = VerifyBundleLedger(ctx, s.db); e == nil {
		t.Fatal("missing legacy ledger passed")
	}
	if _, e = s.ImportBundle(ctx, root); e == nil {
		t.Fatal("retry invented historical ledger")
	}
}
