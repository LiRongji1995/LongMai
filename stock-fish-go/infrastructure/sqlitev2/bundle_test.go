package sqlitev2

import (
	"context"
	"encoding/json"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func bundleJSON(t *testing.T, v any) ([]byte, string) {
	t.Helper()
	raw, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		t.Fatal(e)
	}
	canonical, e := c.Canonical(raw)
	if e != nil {
		t.Fatal(e)
	}
	return raw, digest(canonical)
}
func bundleFixture(t *testing.T, indexed bool, dangling bool) string {
	t.Helper()
	root := t.TempDir()
	if e := os.Mkdir(filepath.Join(root, "objects"), 0700); e != nil {
		t.Fatal(e)
	}
	write := func(path string, raw []byte) {
		if e := os.WriteFile(filepath.Join(root, path), raw, 0600); e != nil {
			t.Fatal(e)
		}
	}
	manifest, mh := bundleJSON(t, map[string]any{"contract": "R1_EXPERIMENT_MANIFEST_V3", "coverage_type": "TEST_FIXTURE", "inputs": []string{"TEST_FIXTURE"}})
	leaf, lh := bundleJSON(t, map[string]any{"instrument_id": "TEST_FIXTURE_A", "source": "TEST_FIXTURE"})
	ref := lh
	if dangling {
		ref = strings.Repeat("a", 64)
	}
	detail, dh := bundleJSON(t, map[string]any{"manifest_hash": mh, "input_hash": "fixture-input", "observation_date": "2026-09-30", "source_hash": "fixture-source", "items": []any{map[string]any{"$r1_ref": ref}}})
	report := map[string]any{"contract": "R1_SHARED_RESULT_V3", "run_id": "r1_TEST_FIXTURE_bundle", "manifest_hash": mh, "results": []any{map[string]any{"input_hash": "fixture-input", "observation_date": "2026-09-30", "source_hash": "fixture-source", "detail": map[string]any{"$r1_ref": dh}}}}
	if indexed {
		a, ah := bundleJSON(t, map[string]json.RawMessage{lh: leaf})
		z, zh := bundleJSON(t, map[string]json.RawMessage{dh: detail})
		index, ih := bundleJSON(t, map[string]string{lh: ah, dh: zh})
		write("objects/"+ah+".json", a)
		write("objects/"+zh+".json", z)
		write("objects/index.json", index)
		report["storage"] = map[string]any{"index_hash": ih, "objects": 2}
	} else {
		write("objects/"+lh+".json", leaf)
		write("objects/"+dh+".json", detail)
	}
	result, rh := bundleJSON(t, report)
	completion, _ := bundleJSON(t, map[string]string{"manifest_hash": mh, "result_hash": rh})
	write("manifest.json", manifest)
	write("result.json", result)
	write("completion.json", completion)
	write("report.md", []byte("# TEST_FIXTURE archived report\n"))
	return root
}
func openBundleTest(t *testing.T) *Store {
	t.Helper()
	s, e := Open(context.Background(), filepath.Join(t.TempDir(), "v2.sqlite"), true)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.Close() })
	return s
}
func TestBundleRoundTripIndexedAndEarly(t *testing.T) {
	for _, indexed := range []bool{true, false} {
		t.Run(map[bool]string{true: "indexed", false: "early"}[indexed], func(t *testing.T) {
			ctx := context.Background()
			s := openBundleTest(t)
			root := bundleFixture(t, indexed, false)
			id, e := s.ImportBundle(ctx, root)
			if e != nil {
				t.Fatal(e)
			}
			if id != "r1_TEST_FIXTURE_bundle" {
				t.Fatal(id)
			}
			if e = s.VerifyBundle(ctx, id); e != nil {
				t.Fatal(e)
			}
			if _, e = s.ImportBundle(ctx, root); e != nil {
				t.Fatal("idempotent retry", e)
			}
			export := filepath.Join(t.TempDir(), "export")
			if e = s.ExportBundle(ctx, id, export); e != nil {
				t.Fatal(e)
			}
			a, _ := inventoryBundle(ctx, root)
			b, _ := inventoryBundle(ctx, export)
			aa, _ := json.Marshal(a)
			bb, _ := json.Marshal(b)
			if string(aa) != string(bb) {
				t.Fatal("raw bytes or inventory changed")
			}
			if e = s.ExportBundle(ctx, id, export); e == nil {
				t.Fatal("export overwrote existing root")
			}
			if _, e = s.db.ExecContext(ctx, "UPDATE bundle_files SET raw_hash=? WHERE bundle_id=?", strings.Repeat("a", 64), id); e == nil {
				t.Fatal("published raw evidence mutable")
			}
		})
	}
}
func TestBundleInterruptedImportResumesSameSource(t *testing.T) {
	ctx := context.Background()
	s := openBundleTest(t)
	root := bundleFixture(t, true, false)
	if e := s.InitBundleSchema(ctx); e != nil {
		t.Fatal(e)
	}
	_, e := s.db.ExecContext(ctx, `CREATE TEMP TRIGGER fixture_interrupt BEFORE INSERT ON bundle_files WHEN NEW.path='objects/index.json' BEGIN SELECT RAISE(ABORT,'TEST_FIXTURE_INTERRUPTION');END;`)
	if e != nil {
		t.Fatal(e)
	}
	id, e := s.ImportBundle(ctx, root)
	if e == nil {
		t.Fatal("interruption not injected")
	}
	var status string
	if e = s.db.QueryRowContext(ctx, "SELECT status FROM legacy_bundles WHERE bundle_id=?", id).Scan(&status); e != nil || status != "IMPORTING" {
		t.Fatal(status, e)
	}
	if e = s.VerifyBundle(ctx, id); e == nil {
		t.Fatal("partial bundle visible")
	}
	raw, e := os.ReadFile(filepath.Join(root, "report.md"))
	if e != nil {
		t.Fatal(e)
	}
	_ = os.WriteFile(filepath.Join(root, "report.md"), append(raw, ' '), 0600)
	if _, e = s.ImportBundle(ctx, root); e == nil || !strings.Contains(e.Error(), "SOURCE_IDENTITY_CONFLICT") {
		t.Fatal("changed frozen source accepted", e)
	}
	_ = os.WriteFile(filepath.Join(root, "report.md"), raw, 0600)
	_, _ = s.db.ExecContext(ctx, "DROP TRIGGER fixture_interrupt")
	if _, e = s.ImportBundle(ctx, root); e != nil {
		t.Fatal(e)
	}
}
func TestBundleMissingReferenceAndCorruption(t *testing.T) {
	ctx := context.Background()
	s := openBundleTest(t)
	root := bundleFixture(t, true, true)
	id, e := s.ImportBundle(ctx, root)
	if e == nil || !strings.Contains(e.Error(), "MISSING_RESULT_REFERENCE") {
		t.Fatal(e)
	}
	if e = s.VerifyBundle(ctx, id); e == nil {
		t.Fatal("broken reference published")
	}
	s2 := openBundleTest(t)
	root = bundleFixture(t, true, false)
	id, e = s2.ImportBundle(ctx, root)
	if e != nil {
		t.Fatal(e)
	}
	_, _ = s2.db.ExecContext(ctx, "DROP TRIGGER bundle_files_no_update")
	_, e = s2.db.ExecContext(ctx, "UPDATE bundle_files SET payload=X'00' WHERE bundle_id=? AND path='result.json'", id)
	if e != nil {
		t.Fatal(e)
	}
	if e = s2.VerifyBundle(ctx, id); e == nil {
		t.Fatal("raw blob corruption accepted")
	}
}
func TestBundleCanceledAndSymlinkRejected(t *testing.T) {
	s := openBundleTest(t)
	root := bundleFixture(t, true, false)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, e := s.ImportBundle(ctx, root); e == nil {
		t.Fatal("canceled import succeeded")
	}
	path := filepath.Join(root, "report.md")
	_ = os.Remove(path)
	if e := os.Symlink("manifest.json", path); e != nil {
		t.Skip("symlink unavailable", e)
	}
	if _, e := s.ImportBundle(context.Background(), root); e == nil {
		t.Fatal("symlink source accepted")
	}
}

func TestBundleUnknownContractAndObjectCountRejected(t *testing.T) {
	for _, mutation := range []string{"contract", "objects"} {
		t.Run(mutation, func(t *testing.T) {
			root := bundleFixture(t, true, false)
			raw, _ := os.ReadFile(filepath.Join(root, "result.json"))
			var report map[string]any
			if e := json.Unmarshal(raw, &report); e != nil {
				t.Fatal(e)
			}
			if mutation == "contract" {
				report["contract"] = "UNKNOWN_FIXTURE"
			} else {
				report["storage"].(map[string]any)["objects"] = 3
			}
			raw, rh := bundleJSON(t, report)
			if e := os.WriteFile(filepath.Join(root, "result.json"), raw, 0600); e != nil {
				t.Fatal(e)
			}
			complete, _ := bundleJSON(t, map[string]any{"manifest_hash": report["manifest_hash"], "result_hash": rh})
			if e := os.WriteFile(filepath.Join(root, "completion.json"), complete, 0600); e != nil {
				t.Fatal(e)
			}
			s := openBundleTest(t)
			if _, e := s.ImportBundle(context.Background(), root); e == nil {
				t.Fatal("invalid contract/count published")
			}
		})
	}
}
