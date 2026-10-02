package lifecycle

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

type writerOwner struct {
	Owner     string `json:"owner"`
	Token     string `json:"token"`
	PID       int    `json:"pid"`
	StartedAt string `json:"started_at"`
	Database  string `json:"database"`
}

// AcquireWriter returns ownership only after both exclusive filesystem claims
// succeed. Failure of the second claim rolls back the first; stale/foreign claims
// are never removed. The database directory is shared with CLI --db writers.
func AcquireWriter(root, owner string) (func() error, error) {
	_, release, e := AcquireWriterConfig(root, owner)
	return release, e
}

// AcquireWriterConfig reads configuration only while holding the instance
// claim. Callers must use the returned configuration for the entire operation:
// a preceding Restore may have replaced Target before this claim succeeded.
func AcquireWriterConfig(root, owner string) (Config, func() error, error) {
	var conf Config
	if owner == "" {
		return conf, nil, errors.New("WRITER_OWNER_REQUIRED")
	}
	path := filepath.Join(root, "writer.lock")
	f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		return conf, nil, fmt.Errorf("WRITER_OWNERSHIP_BUSY: %w", e)
	}
	owned, e := f.Stat()
	if e != nil {
		f.Close()
		return conf, nil, e
	}
	removeOwnClaim := func() {
		if current, err := os.Lstat(path); err == nil && os.SameFile(owned, current) {
			_ = os.Remove(path)
		}
	}
	conf, e = Load(root)
	if e != nil {
		f.Close()
		removeOwnClaim()
		return conf, nil, e
	}
	raw, _ := json.Marshal(writerOwner{Owner: owner, Token: randomID(), PID: os.Getpid(), StartedAt: now(), Database: conf.Target})
	if _, e = f.Write(raw); e != nil {
		f.Close()
		removeOwnClaim()
		return conf, nil, e
	}
	if e = f.Sync(); e != nil {
		f.Close()
		removeOwnClaim()
		return conf, nil, e
	}
	if e = f.Close(); e != nil {
		removeOwnClaim()
		return conf, nil, e
	}
	releaseDB, e := acquireDatabaseWriter(conf.Target, raw)
	if e != nil {
		if current, err := os.ReadFile(path); err == nil && string(current) == string(raw) {
			_ = os.Remove(path)
		}
		return conf, nil, e
	}
	return conf, func() error {
		current, e := os.ReadFile(path)
		if e != nil {
			return e
		}
		if string(current) != string(raw) {
			return errors.New("WRITER_LEASE_CHANGED")
		}
		if e = releaseDB(); e != nil {
			return e
		}
		return os.Remove(path)
	}, nil
}

// AcquireDatabaseWriter is the shared lease for direct database commands. Both
// existing file aliases and aliases of a new file's parent resolve to one lock.
// A dangling symlink is rejected even when creation was requested.
func AcquireDatabaseWriter(database, owner string, allowNew bool) (string, func() error, error) {
	if owner == "" {
		return "", nil, errors.New("WRITER_OWNER_REQUIRED")
	}
	canonical, e := canonicalWriterDatabase(database, allowNew)
	if e != nil {
		return "", nil, e
	}
	if allowNew {
		if e = os.MkdirAll(filepath.Dir(canonical), 0700); e != nil {
			return "", nil, e
		}
		// Resolve once more after directory creation; never claim an unresolved
		// spelling which could give another writer a distinct lock directory.
		if canonical, e = canonicalWriterDatabase(canonical, true); e != nil {
			return "", nil, e
		}
	}
	raw, _ := json.Marshal(writerOwner{Owner: owner, Token: randomID(), PID: os.Getpid(), StartedAt: now(), Database: canonical})
	release, e := acquireDatabaseWriter(canonical, raw)
	if e != nil {
		return "", nil, e
	}
	return canonical, release, nil
}

func canonicalWriterDatabase(database string, allowNew bool) (string, error) {
	if database == "" {
		return "", errors.New("DATABASE_PATH_REQUIRED")
	}
	abs, e := filepath.Abs(database)
	if e != nil {
		return "", e
	}
	st, e := os.Lstat(abs)
	if e == nil {
		resolved, err := filepath.EvalSymlinks(abs)
		if err != nil {
			return "", err
		}
		st, err = os.Stat(resolved)
		if err != nil {
			return "", err
		}
		if !st.Mode().IsRegular() {
			return "", errors.New("DATABASE_PATH_NOT_REGULAR")
		}
		return resolved, nil
	}
	if !allowNew || !errors.Is(e, os.ErrNotExist) {
		return "", e
	}
	// Lstat, rather than Stat, stops at dangling symlinks in the ancestor
	// chain. Such a name must never be reinterpreted as a new directory.
	parent := filepath.Dir(abs)
	missing := []string{filepath.Base(abs)}
	for {
		st, e = os.Lstat(parent)
		if e == nil {
			resolved, err := filepath.EvalSymlinks(parent)
			if err != nil {
				return "", err
			}
			st, err = os.Stat(resolved)
			if err != nil {
				return "", err
			}
			if !st.IsDir() {
				return "", errors.New("DATABASE_PARENT_NOT_DIRECTORY")
			}
			for i := len(missing) - 1; i >= 0; i-- {
				resolved = filepath.Join(resolved, missing[i])
			}
			return resolved, nil
		}
		if !errors.Is(e, os.ErrNotExist) {
			return "", e
		}
		next := filepath.Dir(parent)
		if next == parent {
			return "", e
		}
		missing = append(missing, filepath.Base(parent))
		parent = next
	}
}
func acquireDatabaseWriter(database string, raw []byte) (func() error, error) {
	lock := database + ".go-writer"
	if e := os.Mkdir(lock, 0700); e != nil {
		return nil, fmt.Errorf("WRITER_OWNERSHIP_BUSY %s: %w", lock, e)
	}
	owner := filepath.Join(lock, "owner.json")
	f, e := os.OpenFile(owner, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		os.Remove(lock)
		return nil, e
	}
	if _, e = f.Write(raw); e != nil {
		f.Close()
		os.Remove(owner)
		os.Remove(lock)
		return nil, e
	}
	if e = f.Sync(); e != nil {
		f.Close()
		os.Remove(owner)
		os.Remove(lock)
		return nil, e
	}
	if e = f.Close(); e != nil {
		os.Remove(owner)
		os.Remove(lock)
		return nil, e
	}
	return func() error {
		dir, e := os.Lstat(lock)
		if e != nil {
			return e
		}
		if !dir.IsDir() || dir.Mode()&os.ModeSymlink != 0 {
			return errors.New("DATABASE_WRITER_LEASE_CHANGED")
		}
		entry, e := os.Lstat(owner)
		if e != nil {
			return e
		}
		if !entry.Mode().IsRegular() {
			return errors.New("DATABASE_WRITER_LEASE_CHANGED")
		}
		current, e := os.ReadFile(owner)
		if e != nil {
			return e
		}
		if string(current) != string(raw) {
			return errors.New("DATABASE_WRITER_LEASE_CHANGED")
		}
		entries, e := os.ReadDir(lock)
		if e != nil {
			return e
		}
		if len(entries) != 1 || entries[0].Name() != "owner.json" {
			return errors.New("DATABASE_WRITER_LEASE_CONTENT_CHANGED")
		}
		if e = os.Remove(owner); e != nil {
			return e
		}
		return os.Remove(lock)
	}, nil
}
func verifyOwnedWriter(root, target string) error {
	raw, e := os.ReadFile(filepath.Join(root, "writer.lock"))
	if e != nil {
		return e
	}
	var rootOwner writerOwner
	if e = json.Unmarshal(raw, &rootOwner); e != nil {
		return e
	}
	if rootOwner.PID != os.Getpid() || rootOwner.Token == "" || rootOwner.Owner == "" || rootOwner.Database != target {
		return errors.New("WRITER_LEASE_CHANGED")
	}
	other, e := os.ReadFile(filepath.Join(target+".go-writer", "owner.json"))
	if e != nil {
		return e
	}
	if string(other) != string(raw) {
		return errors.New("DATABASE_WRITER_LEASE_CHANGED")
	}
	return nil
}
