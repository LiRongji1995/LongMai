package sqlitev2

import (
	"context"
	"path/filepath"
	"testing"
)

func TestManagedHashViewDoesNotMintCaptureMetadata(t *testing.T) {
	_, source, _ := fixture(t)
	ctx := context.Background()
	identity, e := SnapshotCheck(ctx, source)
	if e != nil {
		t.Fatal(e)
	}
	view, e := OpenFrozenAtHash(ctx, source, identity.SHA256)
	if e != nil {
		t.Fatal(e)
	}
	defer view.Close()
	var n int
	if e = view.DB.QueryRow("SELECT count(*) FROM documents").Scan(&n); e != nil || n == 0 {
		t.Fatal(n, e)
	}
	if _, e = view.CopySnapshot(ctx, filepath.Join(t.TempDir(), "TEST_FIXTURE-unproven-capture.db")); e == nil {
		t.Fatal("hash-only view invented capture metadata")
	}
	if _, e = OpenFrozenAtHash(ctx, source, ""); e == nil {
		t.Fatal("missing checkpoint hash accepted")
	}
}
