package lifecycle

import (
	"context"
	"encoding/json"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
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
	return raw, c.Hash(canonical)
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

func TestBundleOnlyCutoverExportBlockAndRestore(t *testing.T) {
	ctx := context.Background()
	root, conf := isolatedFixture(t)
	if _, e := Cutover(ctx, root); e != nil {
		t.Fatal(e)
	}
	s, e := sqlitev2.Open(ctx, conf.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	external := bundleFixture(t, true, false)
	id, e := s.ImportBundle(ctx, external)
	if e != nil {
		t.Fatal(e)
	}
	s.Close()
	if e = os.Rename(external, external+"-unavailable"); e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { os.RemoveAll(external + "-unavailable") })
	out, e := Rollback(ctx, root)
	if e != nil || out.Status != "BLOCKED_UNREPRESENTABLE" {
		t.Fatal(out, e)
	}
	manifest, e := VerifyExport(ctx, root, out.Export)
	if e != nil {
		t.Fatal(e)
	}
	if manifest.Changes != 1 || len(manifest.IncompatibleKinds) != 1 || manifest.IncompatibleKinds[0] != sqlitev2.BundleChangeKind {
		t.Fatal(manifest)
	}
	restored, e := Restore(ctx, root, out.Export)
	if e != nil {
		t.Fatal(e)
	}
	s, e = sqlitev2.Open(ctx, restored.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	if e = s.VerifyBundle(ctx, id); e != nil {
		t.Fatal(e)
	}
	if e = s.ExportBundle(ctx, id, filepath.Join(root, "restored-bundle")); e != nil {
		t.Fatal(e)
	}
	if e = verifyAuthorityLedger(ctx, s.DB()); e != nil {
		t.Fatal(e)
	}
}
