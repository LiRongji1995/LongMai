package sqlitev2

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	sqlite "modernc.org/sqlite"
	"os"
	"path/filepath"
	"time"
)

type SnapshotIdentity struct {
	Version        string `json:"version"`
	Source         string `json:"source"`
	Snapshot       string `json:"snapshot"`
	SHA256         string `json:"sha256"`
	Bytes          int64  `json:"bytes"`
	CreatedAt      string `json:"created_at"`
	SQLiteVersion  string `json:"sqlite_version"`
	SourceOpenMode string `json:"source_open_mode"`
}

func FileHash(ctx context.Context, path string) (string, error) {
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	h := sha256.New()
	buf := make([]byte, 1<<20)
	for {
		if e = ctx.Err(); e != nil {
			return "", e
		}
		n, e := f.Read(buf)
		if n > 0 {
			h.Write(buf[:n])
		}
		if e == io.EOF {
			break
		}
		if e != nil {
			return "", e
		}
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func Snapshot(ctx context.Context, source, target string) (out SnapshotIdentity, err error) {
	if _, e := os.Stat(target); !errors.Is(e, os.ErrNotExist) {
		return out, errors.New("SNAPSHOT_TARGET_MUST_NOT_EXIST")
	}
	if e := os.MkdirAll(filepath.Dir(target), 0700); e != nil {
		return out, e
	}
	f, createErr := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0600)
	if createErr != nil {
		return out, createErr
	}
	if createErr = f.Close(); createErr != nil {
		return out, createErr
	}
	db, e := sql.Open("sqlite", URI(source, true))
	if e != nil {
		return out, e
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	conn, e := db.Conn(ctx)
	if e != nil {
		return out, e
	}
	defer conn.Close()
	var v string
	if e = conn.QueryRowContext(ctx, "SELECT sqlite_version()").Scan(&v); e != nil {
		return out, e
	}
	// The SQLite online backup API reads a consistent snapshot including committed WAL.
	e = conn.Raw(func(c any) error {
		b, e := c.(interface {
			NewBackup(string) (*sqlite.Backup, error)
		}).NewBackup(target)
		if e != nil {
			return e
		}
		defer func() {
			f := b.Finish()
			if err == nil && f != nil {
				err = f
			}
		}()
		for {
			if e = ctx.Err(); e != nil {
				return e
			}
			if e = CheckResources(filepath.Dir(target)); e != nil {
				return e
			}
			more, e := b.Step(2048)
			if e != nil {
				return e
			}
			if !more {
				return nil
			}
		}
	})
	if e != nil {
		return out, e
	}
	if err != nil {
		return out, err
	}
	h, e := FileHash(ctx, target)
	if e != nil {
		return out, e
	}
	st, e := os.Stat(target)
	if e != nil {
		return out, e
	}
	out = SnapshotIdentity{"SQLITE_ONLINE_BACKUP_V1", source, target, h, st.Size(), time.Now().UTC().Format(time.RFC3339Nano), v, "mode=ro; no source PRAGMA mutation"}
	raw, _ := json.MarshalIndent(out, "", "  ")
	if e = os.WriteFile(target+".snapshot.json", raw, 0600); e != nil {
		return out, e
	}
	return out, nil
}
func SnapshotCheck(ctx context.Context, path string) (SnapshotIdentity, error) {
	f, e := OpenFrozen(ctx, path)
	if e != nil {
		return SnapshotIdentity{}, e
	}
	ident := f.Identity
	return ident, f.Close()
}

type InventoryTable struct {
	Name      string `json:"name"`
	Schema    string `json:"schema"`
	Count     int64  `json:"records"`
	Bytes     int64  `json:"bytes"`
	Treatment string `json:"treatment"`
}
type Inventory struct {
	SQLiteVersion              string `json:"sqlite_version"`
	Pages, FreePages, PageSize int64
	Tables                     []InventoryTable `json:"tables"`
	Namespaces                 []InventoryTable `json:"namespaces"`
	Objects                    map[string]int64 `json:"object_bytes"`
}

func InventorySource(ctx context.Context, path string) (o Inventory, err error) {
	db, e := sql.Open("sqlite", URI(path, true))
	if e != nil {
		return o, e
	}
	defer db.Close()
	db.SetMaxOpenConns(2)
	db.QueryRowContext(ctx, "SELECT sqlite_version()").Scan(&o.SQLiteVersion)
	db.QueryRowContext(ctx, "PRAGMA page_count").Scan(&o.Pages)
	db.QueryRowContext(ctx, "PRAGMA freelist_count").Scan(&o.FreePages)
	db.QueryRowContext(ctx, "PRAGMA page_size").Scan(&o.PageSize)
	rows, e := db.QueryContext(ctx, "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
	if e != nil {
		return o, e
	}
	for rows.Next() {
		var t InventoryTable
		if e = rows.Scan(&t.Name, &t.Schema); e != nil {
			rows.Close()
			return o, e
		}
		t.Treatment = "IMMUTABLE_ARCHIVE"
		switch t.Name {
		case "documents":
			t.Treatment = "RAW_BYTES_AND_TYPED_SNAPSHOTS_RESULTS_RECEIPTS"
		case "history_evidence_rows":
			t.Treatment = "RAW_BYTES_AND_TYPED_EXACT_PROJECTIONS"
		case "history_partition_members":
			t.Treatment = "EXACT_MEMBER_RELATION"
		case "jobs", "job_events":
			t.Treatment = "HISTORICAL_NONEXECUTABLE"
		case "yaogu_derived_features_v2":
			t.Treatment = "DISCARD_REBUILDABLE_CACHE"
		}
		o.Tables = append(o.Tables, t)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return o, e
	}
	for i := range o.Tables {
		e = db.QueryRowContext(ctx, `SELECT count(*) FROM "`+o.Tables[i].Name+`"`).Scan(&o.Tables[i].Count)
		if e != nil {
			return o, e
		}
	}
	rows, e = db.QueryContext(ctx, "SELECT namespace,count(*),sum(length(CAST(payload AS BLOB))) FROM documents GROUP BY namespace")
	if e != nil {
		return o, e
	}
	for rows.Next() {
		var t InventoryTable
		if e = rows.Scan(&t.Name, &t.Count, &t.Bytes); e != nil {
			rows.Close()
			return o, e
		}
		t.Treatment = "PRESERVE_RAW_BYTES_WITH_IDENTITY_MAPPING"
		o.Namespaces = append(o.Namespaces, t)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return o, e
	}
	rows, e = db.QueryContext(ctx, "SELECT name,sum(pgsize) FROM dbstat GROUP BY name")
	if e != nil {
		return o, e
	}
	o.Objects = map[string]int64{}
	for rows.Next() {
		var n string
		var b int64
		if e = rows.Scan(&n, &b); e != nil {
			rows.Close()
			return o, e
		}
		o.Objects[n] = b
	}
	err = rows.Err()
	rows.Close()
	for i := range o.Tables {
		o.Tables[i].Bytes = o.Objects[o.Tables[i].Name]
	}
	return
}
