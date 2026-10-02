package lifecycle

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWriterLoadsConfigurationUnderRootClaim(t *testing.T) {
	root, conf := isolatedFixture(t)
	instance := filepath.Join(root, "instance.json")
	lock := filepath.Join(root, "writer.lock")
	// Invalid configuration must not be read while another owner has the root
	// claim. The order matters because that owner may be publishing a restore.
	if e := os.WriteFile(instance, []byte("TEST_FIXTURE_INCOMPLETE_CONFIG"), 0600); e != nil {
		t.Fatal(e)
	}
	foreign := []byte("TEST_FIXTURE_FOREIGN_OWNER")
	if e := os.WriteFile(lock, foreign, 0600); e != nil {
		t.Fatal(e)
	}
	if _, _, e := AcquireWriterConfig(root, "TEST_FIXTURE_CONTENDER"); e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP_BUSY") {
		t.Fatal("configuration read before root ownership", e)
	}
	if raw, e := os.ReadFile(lock); e != nil || string(raw) != string(foreign) {
		t.Fatal("foreign root claim changed", e)
	}
	if e := os.Remove(lock); e != nil {
		t.Fatal(e)
	}
	if _, _, e := AcquireWriterConfig(root, "TEST_FIXTURE_INVALID"); e == nil {
		t.Fatal("invalid configuration accepted")
	}
	if _, e := os.Lstat(lock); !errors.Is(e, os.ErrNotExist) {
		t.Fatal("failed load leaked root claim", e)
	}
	if e := writeJSON(instance, conf); e != nil {
		t.Fatal(e)
	}
	// Model a completed restore publishing another target. A stale caller has
	// retained conf, but the acquisition must return and lock the new target.
	stale := conf
	conf.Target = filepath.Join(root, "TEST_FIXTURE_RESTORED.db")
	conf.ActiveDatabase = conf.Target
	conf.Mode = "GO_V2"
	conf.Revision++
	if e := writeJSON(instance, conf); e != nil {
		t.Fatal(e)
	}
	locked, release, e := AcquireWriterConfig(root, "TEST_FIXTURE_AFTER_RESTORE")
	if e != nil {
		t.Fatal(e)
	}
	defer release()
	if locked.Target != conf.Target || locked.ActiveDatabase != conf.Target || locked.Revision != conf.Revision {
		t.Fatal("stale configuration returned", locked)
	}
	if e = verifyOwnedWriter(root, locked.Target); e != nil {
		t.Fatal(e)
	}
	if _, e = os.Lstat(stale.Target + ".go-writer"); !errors.Is(e, os.ErrNotExist) {
		t.Fatal("stale database received lease", e)
	}
}

func TestLoadRejectsDifferentGoActiveDatabase(t *testing.T) {
	root, conf := isolatedFixture(t)
	conf.Mode = "GO_V2"
	conf.ActiveDatabase = conf.Source
	if e := writeJSON(filepath.Join(root, "instance.json"), conf); e != nil {
		t.Fatal(e)
	}
	if _, e := Load(root); e == nil || !strings.Contains(e.Error(), "GO_ACTIVE_DATABASE_TARGET_MISMATCH") {
		t.Fatal("Go mode allowed another active database", e)
	}
	if _, _, e := AcquireWriterConfig(root, "TEST_FIXTURE_MISMATCH"); e == nil {
		t.Fatal("writer accepted mismatched Go active database")
	}
	if _, e := os.Lstat(filepath.Join(root, "writer.lock")); !errors.Is(e, os.ErrNotExist) {
		t.Fatal("invalid config leaked lease", e)
	}
}

func TestDirectWriterCanonicalCreationAndDanglingSymlinks(t *testing.T) {
	root := t.TempDir()
	real := filepath.Join(root, "TEST_FIXTURE_REAL")
	if e := os.Mkdir(real, 0700); e != nil {
		t.Fatal(e)
	}
	alias := filepath.Join(root, "TEST_FIXTURE_ALIAS")
	if e := os.Symlink(real, alias); e != nil {
		t.Fatal(e)
	}
	newPath := filepath.Join(alias, "new-parent", "new.db")
	canonical, release, e := AcquireDatabaseWriter(newPath, "TEST_FIXTURE_NEW", true)
	if e != nil {
		t.Fatal(e)
	}
	expectedParent, e := filepath.EvalSymlinks(filepath.Join(real, "new-parent"))
	if e != nil || canonical != filepath.Join(expectedParent, "new.db") {
		t.Fatal("new path not canonical", canonical, e)
	}
	if _, _, e = AcquireDatabaseWriter(filepath.Join(real, "new-parent", "new.db"), "TEST_FIXTURE_COLLISION", true); e == nil || !strings.Contains(e.Error(), "WRITER_OWNERSHIP_BUSY") {
		t.Fatal("new target parent alias bypassed lease", e)
	}
	if e = release(); e != nil {
		t.Fatal(e)
	}
	for _, path := range []string{filepath.Join(root, "dangling.db"), filepath.Join(root, "dangling-parent")} {
		if e = os.Symlink(filepath.Join(root, "TEST_FIXTURE_MISSING"), path); e != nil {
			t.Fatal(e)
		}
	}
	for _, path := range []string{filepath.Join(root, "dangling.db"), filepath.Join(root, "dangling-parent", "new.db")} {
		if _, _, e = AcquireDatabaseWriter(path, "TEST_FIXTURE_DANGLING", true); e == nil {
			t.Fatal("dangling symlink accepted", path)
		}
	}
	if _, _, e = AcquireDatabaseWriter(filepath.Join(real, "missing.db"), "TEST_FIXTURE_EXISTING_REQUIRED", false); !errors.Is(e, os.ErrNotExist) {
		t.Fatal("missing database accepted without creation permission", e)
	}
}
