package sqlitev2

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
)

// FrozenSource owns a private, byte-verified main-file view. Normal SQLite
// writers can still change the original; they cannot change this independent
// copy. immutable is ONLY used on the private copy, never on the source.
// The protection assumes no malicious writer with access to our private 0700
// directory. SHA256 is the identity of the complete SQL-visible main-file state.
type FrozenSource struct {
	Identity  SnapshotIdentity
	DB        *sql.DB
	path, dir string
}

func (f *FrozenSource) URI() string { return URI(f.path, true) + "&immutable=1" }
func (f *FrozenSource) Close() error {
	e := f.DB.Close()
	r := os.RemoveAll(f.dir)
	if e != nil {
		return e
	}
	return r
}
func frozenSidecars(path string) error {
	for _, suffix := range []string{"-wal", "-journal"} {
		st, e := os.Lstat(path + suffix)
		if errors.Is(e, os.ErrNotExist) {
			continue
		}
		if e != nil {
			return e
		}
		if !st.Mode().IsRegular() || st.Size() != 0 {
			return errors.New("FROZEN_UNIDENTIFIED_SIDECAR: " + suffix)
		}
	}
	// SHM contains coordination data, not committed pages. A regular SHM with
	// absent/empty WAL is acceptable. Symlink/device sidecars are unexplained.
	st, e := os.Lstat(path + "-shm")
	if errors.Is(e, os.ErrNotExist) {
		return nil
	}
	if e != nil {
		return e
	}
	if !st.Mode().IsRegular() {
		return errors.New("FROZEN_INVALID_SHM")
	}
	return nil
}
func OpenFrozen(ctx context.Context, path string) (*FrozenSource, error) {
	return openFrozen(ctx, path, "")
}

// OpenFrozenAtHash consumes a replay checkpoint whose complete byte identity is
// already bound by the isolated lifecycle configuration. It does not mint or
// rewrite snapshot metadata. This hash-only view cannot publish a snapshot.
func OpenFrozenAtHash(ctx context.Context, path, expectedHash string) (*FrozenSource, error) {
	if !validBundleHash(expectedHash) {
		return nil, errors.New("FROZEN_EXPECTED_HASH_REQUIRED")
	}
	return openFrozen(ctx, path, expectedHash)
}

func openFrozen(ctx context.Context, path, expectedHash string) (out *FrozenSource, err error) {
	path, err = filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	canonical, e := filepath.EvalSymlinks(path)
	if e != nil {
		return nil, e
	}
	leaf, e := os.Lstat(path)
	if e != nil {
		return nil, e
	}
	if leaf.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("FROZEN_PATH_ALIAS")
	}
	path = canonical
	st, e := os.Lstat(path)
	if e != nil {
		return nil, e
	}
	if !st.Mode().IsRegular() {
		return nil, errors.New("FROZEN_NOT_REGULAR")
	}
	var raw []byte
	var ident SnapshotIdentity
	if expectedHash != "" {
		ident = SnapshotIdentity{Version: "ISOLATED_REPLAY_HASH_VIEW_V1", Snapshot: path, SHA256: expectedHash, Bytes: st.Size()}
	} else {
		raw, e = os.ReadFile(path + ".snapshot.json")
		if e != nil {
			return nil, e
		}
		if e = json.Unmarshal(raw, &ident); e != nil {
			return nil, e
		}
		if ident.Version != "SQLITE_ONLINE_BACKUP_V1" && ident.Version != "ISOLATED_APFS_CLONEFILE_V1" {
			return nil, errors.New("FROZEN_IDENTITY_VERSION")
		}
		bound, e := filepath.Abs(ident.Snapshot)
		if e != nil {
			return nil, e
		}
		bound, e = filepath.EvalSymlinks(bound)
		if e != nil {
			return nil, e
		}
		if bound != path || ident.Bytes != st.Size() || !validBundleHash(ident.SHA256) || ident.CreatedAt == "" || ident.SQLiteVersion == "" {
			return nil, errors.New("FROZEN_IDENTITY_METADATA_MISMATCH")
		}
	}
	if e = frozenSidecars(path); e != nil {
		return nil, e
	}
	if e = ctx.Err(); e != nil {
		return nil, e
	}
	dir, e := os.MkdirTemp(filepath.Dir(path), ".verified-frozen-")
	if e != nil {
		return nil, e
	}
	defer func() {
		if err != nil {
			os.RemoveAll(dir)
		}
	}()
	copyPath := filepath.Join(dir, "view.db")
	if e = copyFrozen(ctx, path, copyPath); e != nil {
		return nil, e
	}
	// Validate the consumed copy, not a hash calculated before copying.
	hash, e := FileHash(ctx, copyPath)
	if e != nil {
		return nil, e
	}
	if hash != ident.SHA256 {
		return nil, errors.New("FROZEN_SOURCE_CHANGED")
	}
	if e = frozenSidecars(path); e != nil {
		return nil, e
	}
	after, e := os.Stat(path)
	if e != nil {
		return nil, e
	}
	if !os.SameFile(st, after) || after.Size() != st.Size() {
		return nil, errors.New("FROZEN_SOURCE_REPLACED")
	}
	if expectedHash == "" {
		metadata, e := os.ReadFile(path + ".snapshot.json")
		if e != nil {
			return nil, e
		}
		if !bytes.Equal(raw, metadata) {
			return nil, errors.New("FROZEN_METADATA_CHANGED")
		}
	}
	db, e := sql.Open("sqlite", URI(copyPath, true)+"&immutable=1")
	if e != nil {
		return nil, e
	}
	db.SetMaxOpenConns(1)
	if e = db.PingContext(ctx); e != nil {
		db.Close()
		return nil, e
	}
	return &FrozenSource{Identity: ident, DB: db, path: copyPath, dir: dir}, nil
}
func streamFrozen(ctx context.Context, source, target string) error {
	in, e := os.Open(source)
	if e != nil {
		return e
	}
	defer in.Close()
	out, e := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	defer out.Close()
	buf := make([]byte, 1<<20)
	for {
		if e = ctx.Err(); e != nil {
			return e
		}
		if e = CheckResources(filepath.Dir(target)); e != nil {
			return e
		}
		n, r := in.Read(buf)
		if n > 0 {
			if _, e = out.Write(buf[:n]); e != nil {
				return e
			}
		}
		if r == io.EOF {
			break
		}
		if r != nil {
			return r
		}
	}
	return out.Sync()
}

// CopySnapshot materializes the already verified private view for recovery. It
// never reopens the original path; the new pathname gets its own metadata.
func (f *FrozenSource) CopySnapshot(ctx context.Context, target string) (SnapshotIdentity, error) {
	var out SnapshotIdentity
	if f.Identity.Version == "ISOLATED_REPLAY_HASH_VIEW_V1" {
		return out, errors.New("HASH_VIEW_HAS_NO_CAPTURE_METADATA")
	}
	if _, e := os.Lstat(target); !errors.Is(e, os.ErrNotExist) {
		return out, errors.New("SNAPSHOT_TARGET_MUST_NOT_EXIST")
	}
	if e := copyFrozen(ctx, f.path, target); e != nil {
		return out, e
	}
	h, e := FileHash(ctx, target)
	if e != nil {
		return out, e
	}
	if h != f.Identity.SHA256 {
		return out, errors.New("FROZEN_COPY_CHANGED")
	}
	out = f.Identity
	out.Source = f.Identity.Snapshot
	out.Snapshot = target
	out.SourceOpenMode = "VERIFIED_PRIVATE_MAIN_VIEW_V1; independent copy; immutable SQL view"
	raw, e := json.MarshalIndent(out, "", "  ")
	if e != nil {
		return out, e
	}
	return out, os.WriteFile(target+".snapshot.json", raw, 0600)
}

type frozenContextKey struct{}

func acquireFrozen(ctx context.Context, path string) (*FrozenSource, func(), error) {
	if f, ok := ctx.Value(frozenContextKey{}).(*FrozenSource); ok {
		p, e := filepath.Abs(path)
		if e != nil {
			return nil, nil, e
		}
		p, e = filepath.EvalSymlinks(p)
		if e != nil {
			return nil, nil, e
		}
		original, e := filepath.Abs(f.Identity.Snapshot)
		if e != nil {
			return nil, nil, e
		}
		original, e = filepath.EvalSymlinks(original)
		if e != nil {
			return nil, nil, e
		}
		if p != original {
			return nil, nil, errors.New("FROZEN_CONTEXT_SOURCE_MISMATCH")
		}
		return f, func() {}, nil
	}
	f, e := OpenFrozen(ctx, path)
	if e != nil {
		return nil, nil, e
	}
	return f, func() { f.Close() }, nil
}
