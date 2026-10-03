package sqlitev2

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Bundle schema is an additive, checksummed extension. Imported Python research
// objects remain immutable evidence; no Python interpreter or old DB is used.
const bundleSchemaVersion = 1
const bundleSchema = `
CREATE TABLE IF NOT EXISTS bundle_schema_versions(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_bundles(bundle_id TEXT PRIMARY KEY,legacy_run_id TEXT NOT NULL UNIQUE,raw_identity_hash TEXT NOT NULL CHECK(length(raw_identity_hash)=64),manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64),result_hash TEXT NOT NULL CHECK(length(result_hash)=64),inventory BLOB NOT NULL,status TEXT NOT NULL CHECK(status IN('IMPORTING','PUBLISHED')),created_at TEXT NOT NULL,published_at TEXT);
CREATE TABLE IF NOT EXISTS bundle_files(bundle_id TEXT NOT NULL REFERENCES legacy_bundles(bundle_id),path TEXT NOT NULL,raw_hash TEXT NOT NULL CHECK(length(raw_hash)=64),canonical_hash TEXT NOT NULL,raw_size INTEGER NOT NULL CHECK(raw_size>=0),codec TEXT NOT NULL CHECK(codec IN('raw','deflate')),payload BLOB NOT NULL,PRIMARY KEY(bundle_id,path)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS bundle_objects(bundle_id TEXT NOT NULL,object_hash TEXT NOT NULL CHECK(length(object_hash)=64),path TEXT NOT NULL,PRIMARY KEY(bundle_id,object_hash),FOREIGN KEY(bundle_id,path) REFERENCES bundle_files(bundle_id,path)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS bundle_object_file ON bundle_objects(bundle_id,path,object_hash);
CREATE TRIGGER IF NOT EXISTS bundle_files_no_update BEFORE UPDATE ON bundle_files BEGIN SELECT RAISE(ABORT,'immutable bundle file');END;
CREATE TRIGGER IF NOT EXISTS bundle_files_no_delete BEFORE DELETE ON bundle_files BEGIN SELECT RAISE(ABORT,'immutable bundle file');END;
CREATE TRIGGER IF NOT EXISTS bundle_objects_no_update BEFORE UPDATE ON bundle_objects BEGIN SELECT RAISE(ABORT,'immutable bundle object');END;
CREATE TRIGGER IF NOT EXISTS bundle_objects_no_delete BEFORE DELETE ON bundle_objects BEGIN SELECT RAISE(ABORT,'immutable bundle object');END;
CREATE TRIGGER IF NOT EXISTS bundle_files_published BEFORE INSERT ON bundle_files WHEN (SELECT status FROM legacy_bundles WHERE bundle_id=NEW.bundle_id)!='IMPORTING' BEGIN SELECT RAISE(ABORT,'published bundle immutable');END;
CREATE TRIGGER IF NOT EXISTS bundle_objects_published BEFORE INSERT ON bundle_objects WHEN (SELECT status FROM legacy_bundles WHERE bundle_id=NEW.bundle_id)!='IMPORTING' BEGIN SELECT RAISE(ABORT,'published bundle immutable');END;
CREATE TRIGGER IF NOT EXISTS bundles_no_delete BEFORE DELETE ON legacy_bundles BEGIN SELECT RAISE(ABORT,'immutable bundle identity');END;
CREATE TRIGGER IF NOT EXISTS bundles_guard_update BEFORE UPDATE ON legacy_bundles WHEN OLD.status='PUBLISHED' OR NEW.bundle_id!=OLD.bundle_id OR NEW.legacy_run_id!=OLD.legacy_run_id OR NEW.raw_identity_hash!=OLD.raw_identity_hash OR NEW.inventory!=OLD.inventory OR NEW.manifest_hash!=OLD.manifest_hash OR NEW.result_hash!=OLD.result_hash OR NEW.status!='PUBLISHED' BEGIN SELECT RAISE(ABORT,'immutable bundle identity');END;
`
const maxBundleFile int64 = 64 << 20

type bundleFile struct {
	Path string `json:"path"`
	Hash string `json:"raw_sha256"`
	Size int64  `json:"bytes"`
}
type bundleHeader struct {
	ID, Run, Identity, Manifest, Result, Status string
	Inventory                                   []bundleFile
}
type bundleDocument struct {
	header          bundleHeader
	report          map[string]json.RawMessage
	roots           map[string][4]string
	index           map[string]string
	hasIndex        bool
	declaredObjects *int
}

func (s *Store) InitBundleSchema(ctx context.Context) error {
	tx, e := s.db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	if _, e = tx.ExecContext(ctx, bundleSchema); e != nil {
		return e
	}
	var hash string
	e = tx.QueryRowContext(ctx, "SELECT checksum FROM bundle_schema_versions WHERE version=?", bundleSchemaVersion).Scan(&hash)
	if e == sql.ErrNoRows {
		_, e = tx.ExecContext(ctx, "INSERT INTO bundle_schema_versions VALUES(?,?,?)", bundleSchemaVersion, digest([]byte(bundleSchema)), time.Now().UTC().Format(time.RFC3339Nano))
	} else if e == nil && hash != digest([]byte(bundleSchema)) {
		return errors.New("BUNDLE_SCHEMA_CHECKSUM_MISMATCH")
	}
	if e != nil {
		return e
	}
	return tx.Commit()
}
func validBundleHash(h string) bool {
	if len(h) != 64 {
		return false
	}
	for _, v := range h {
		if !strings.ContainsRune("0123456789abcdef", v) {
			return false
		}
	}
	return true
}
func validBundlePath(p string) bool {
	if p == "" || strings.Contains(p, "\\") || filepath.IsAbs(p) || filepath.ToSlash(filepath.Clean(p)) != p {
		return false
	}
	parts := strings.Split(p, "/")
	if len(parts) == 1 {
		return p != "." && p != ".." && !strings.HasPrefix(p, ".")
	}
	return len(parts) == 2 && parts[0] == "objects" && (parts[1] == "index.json" || (strings.HasSuffix(parts[1], ".json") && validBundleHash(strings.TrimSuffix(parts[1], ".json"))))
}
func bundleRead(ctx context.Context, root, path string) ([]byte, error) {
	if !validBundlePath(path) {
		return nil, errors.New("INVALID_BUNDLE_PATH")
	}
	full := filepath.Join(root, filepath.FromSlash(path))
	st, e := os.Lstat(full)
	if e != nil {
		return nil, e
	}
	if !st.Mode().IsRegular() {
		return nil, errors.New("BUNDLE_NONREGULAR_FILE")
	}
	if st.Size() > maxBundleFile {
		return nil, errors.New("RECOVERABLE_RESULT_SHARD_BYTE_BUDGET")
	}
	f, e := os.Open(full)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	out := make([]byte, 0, st.Size())
	buf := make([]byte, 64<<10)
	for {
		if e = ctx.Err(); e != nil {
			return nil, e
		}
		n, er := f.Read(buf)
		if n > 0 {
			out = append(out, buf[:n]...)
			if int64(len(out)) > maxBundleFile {
				return nil, errors.New("RECOVERABLE_RESULT_SHARD_BYTE_BUDGET")
			}
		}
		if er == io.EOF {
			break
		}
		if er != nil {
			return nil, er
		}
	}
	return out, nil
}
func inventoryBundle(ctx context.Context, root string) ([]bundleFile, error) {
	st, e := os.Lstat(root)
	if e != nil {
		return nil, e
	}
	if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("BUNDLE_ROOT_MUST_BE_REAL_DIRECTORY")
	}
	entries, e := os.ReadDir(root)
	if e != nil {
		return nil, e
	}
	paths := []string{}
	for _, entry := range entries {
		if e = ctx.Err(); e != nil {
			return nil, e
		}
		if entry.Name() == "objects" {
			st, e := os.Lstat(filepath.Join(root, "objects"))
			if e != nil || !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
				return nil, errors.New("BUNDLE_OBJECT_DIRECTORY_INVALID")
			}
			objects, e := os.ReadDir(filepath.Join(root, "objects"))
			if e != nil {
				return nil, e
			}
			for _, o := range objects {
				path := "objects/" + o.Name()
				if o.IsDir() || !validBundlePath(path) {
					return nil, errors.New("BUNDLE_UNRECOGNIZED_OBJECT_FILE")
				}
				paths = append(paths, path)
			}
		} else {
			if entry.IsDir() || !validBundlePath(entry.Name()) {
				return nil, errors.New("BUNDLE_UNRECOGNIZED_FILE")
			}
			paths = append(paths, entry.Name())
		}
	}
	sort.Strings(paths)
	out := []bundleFile{}
	for _, path := range paths {
		if e = ctx.Err(); e != nil {
			return nil, e
		}
		full := filepath.Join(root, filepath.FromSlash(path))
		st, e := os.Lstat(full)
		if e != nil {
			return nil, e
		}
		if !st.Mode().IsRegular() || st.Size() > maxBundleFile {
			return nil, errors.New("BUNDLE_FILE_TYPE_OR_SIZE_INVALID")
		}
		f, e := os.Open(full)
		if e != nil {
			return nil, e
		}
		h := sha256.New()
		buf := make([]byte, 64<<10)
		var total int64
		for {
			if e = ctx.Err(); e != nil {
				f.Close()
				return nil, e
			}
			n, er := f.Read(buf)
			if n > 0 {
				total += int64(n)
				if total > maxBundleFile {
					f.Close()
					return nil, errors.New("BUNDLE_FILE_SIZE_CHANGED")
				}
				_, _ = h.Write(buf[:n])
			}
			if er == io.EOF {
				break
			}
			if er != nil {
				f.Close()
				return nil, er
			}
		}
		if e = f.Close(); e != nil {
			return nil, e
		}
		out = append(out, bundleFile{path, hex.EncodeToString(h.Sum(nil)), total})
	}
	return out, nil
}
func bundleCanonical(ctx context.Context, raw []byte) (string, error) {
	v, e := c.CanonicalContext(ctx, raw)
	if e != nil {
		return "", e
	}
	return digest(v), nil
}
func bundleObject(raw []byte) (map[string]json.RawMessage, error) {
	var v map[string]json.RawMessage
	e := json.Unmarshal(raw, &v)
	if e != nil || v == nil {
		return nil, errors.New("BUNDLE_JSON_OBJECT_REQUIRED")
	}
	return v, nil
}
func bundleText(v map[string]json.RawMessage, k string) string {
	var s string
	_ = json.Unmarshal(v[k], &s)
	return s
}
func bundleRefs(ctx context.Context, raw []byte, visit func(string) error) error {
	if e := ctx.Err(); e != nil {
		return e
	}
	var v any
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	if e := dec.Decode(&v); e != nil {
		return e
	}
	var walk func(any, int) error
	walk = func(v any, depth int) error {
		if e := ctx.Err(); e != nil {
			return e
		}
		if depth > 256 {
			return errors.New("BUNDLE_REFERENCE_DEPTH")
		}
		switch m := v.(type) {
		case map[string]any:
			if r, ok := m["$r1_ref"]; ok {
				if len(m) != 1 {
					return errors.New("INVALID_RESULT_REFERENCE")
				}
				key, ok := r.(string)
				if !ok || !validBundleHash(key) {
					return errors.New("INVALID_RESULT_REFERENCE")
				}
				return visit(key)
			}
			if value, ok := m["$r1_list"]; ok {
				if _, isList := value.([]any); len(m) != 1 || !isList {
					return errors.New("INVALID_RESULT_LIST_REFERENCE")
				}
			}
			for _, child := range m {
				if e := walk(child, depth+1); e != nil {
					return e
				}
			}
		case []any:
			for _, child := range m {
				if e := walk(child, depth+1); e != nil {
					return e
				}
			}
		}
		return nil
	}
	return walk(v, 0)
}
func parseBundle(ctx context.Context, inventory []bundleFile, read func(string) ([]byte, error)) (*bundleDocument, error) {
	doc := &bundleDocument{roots: map[string][4]string{}, index: map[string]string{}}
	invraw, _ := json.Marshal(inventory)
	doc.header.Identity = digest(invraw)
	doc.header.Inventory = inventory
	present := map[string]bundleFile{}
	for _, f := range inventory {
		if !validBundlePath(f.Path) || !validBundleHash(f.Hash) || f.Size < 0 {
			return nil, errors.New("BUNDLE_INVENTORY_INVALID")
		}
		if _, ok := present[f.Path]; ok {
			return nil, errors.New("BUNDLE_DUPLICATE_FILE")
		}
		present[f.Path] = f
	}
	verified := func(path string) ([]byte, error) {
		meta, ok := present[path]
		if !ok {
			return nil, fmt.Errorf("BUNDLE_REQUIRED_FILE_MISSING: %s", path)
		}
		raw, e := read(path)
		if e != nil {
			return nil, e
		}
		if int64(len(raw)) != meta.Size || digest(raw) != meta.Hash {
			return nil, fmt.Errorf("BUNDLE_RAW_BYTES_CHANGED: %s", path)
		}
		return raw, nil
	}
	manifest, e := verified("manifest.json")
	if e != nil {
		return nil, e
	}
	report, e := verified("result.json")
	if e != nil {
		return nil, e
	}
	completion, e := verified("completion.json")
	if e != nil {
		return nil, e
	}
	mh, e := bundleCanonical(ctx, manifest)
	if e != nil {
		return nil, e
	}
	rh, e := bundleCanonical(ctx, report)
	if e != nil {
		return nil, e
	}
	if _, e = bundleCanonical(ctx, completion); e != nil {
		return nil, e
	}
	comp, e := bundleObject(completion)
	if e != nil {
		return nil, e
	}
	if len(comp) != 2 || bundleText(comp, "manifest_hash") != mh || bundleText(comp, "result_hash") != rh {
		return nil, errors.New("BUNDLE_COMPLETION_IDENTITY_MISMATCH")
	}
	r, e := bundleObject(report)
	if e != nil {
		return nil, e
	}
	m, e := bundleObject(manifest)
	if e != nil {
		return nil, e
	}
	if contract := bundleText(m, "contract"); contract != "R1_EXPERIMENT_MANIFEST_V2" && contract != "R1_EXPERIMENT_MANIFEST_V3" {
		return nil, errors.New("UNSUPPORTED_BUNDLE_MANIFEST_CONTRACT")
	}
	if contract := bundleText(r, "contract"); contract != "R1_SHARED_RESULT_V2" && contract != "R1_SHARED_RESULT_V3" {
		return nil, errors.New("UNSUPPORTED_BUNDLE_RESULT_CONTRACT")
	}
	if bundleText(r, "manifest_hash") != mh {
		return nil, errors.New("BUNDLE_MANIFEST_IDENTITY_MISMATCH")
	}
	run := bundleText(r, "run_id")
	if run == "" || strings.ContainsAny(run, "/\\\x00") {
		return nil, errors.New("BUNDLE_RUN_ID_REQUIRED")
	}
	doc.header.ID = run
	doc.header.Run = run
	doc.header.Manifest = mh
	doc.header.Result = rh
	doc.report = r
	if storageRaw, ok := r["storage"]; ok {
		storage, er := bundleObject(storageRaw)
		if er != nil {
			return nil, er
		}
		if count, ok := storage["objects"]; ok {
			var n int
			if er = json.Unmarshal(count, &n); er != nil || n < 0 {
				return nil, errors.New("INVALID_BUNDLE_OBJECT_COUNT")
			}
			doc.declaredObjects = &n
		}
	}
	var results []map[string]json.RawMessage
	if e = json.Unmarshal(r["results"], &results); e != nil || len(results) == 0 {
		return nil, errors.New("BUNDLE_RESULTS_REQUIRED")
	}
	for _, result := range results {
		detail, e := bundleObject(result["detail"])
		if e != nil || len(detail) != 1 {
			return nil, errors.New("INVALID_RESULT_ROOT_REFERENCE")
		}
		key := bundleText(detail, "$r1_ref")
		if !validBundleHash(key) {
			return nil, errors.New("INVALID_RESULT_ROOT_REFERENCE")
		}
		if _, ok := doc.roots[key]; ok {
			return nil, errors.New("DUPLICATE_RESULT_ROOT_REFERENCE")
		}
		doc.roots[key] = [4]string{mh, bundleText(result, "input_hash"), bundleText(result, "observation_date"), bundleText(result, "source_hash")}
	}
	if _, ok := present["objects/index.json"]; ok {
		doc.hasIndex = true
		raw, e := verified("objects/index.json")
		if e != nil {
			return nil, e
		}
		hash, e := bundleCanonical(ctx, raw)
		if e != nil {
			return nil, e
		}
		storage, e := bundleObject(r["storage"])
		if e != nil {
			return nil, e
		}
		if hash != bundleText(storage, "index_hash") {
			return nil, errors.New("RESULT_INDEX_HASH_MISMATCH")
		}
		if e = json.Unmarshal(raw, &doc.index); e != nil {
			return nil, e
		}
		for key, shard := range doc.index {
			if !validBundleHash(key) || !validBundleHash(shard) {
				return nil, errors.New("INVALID_RESULT_REFERENCE")
			}
		}
	}
	return doc, nil
}
func checkBundleRoot(doc *bundleDocument, key string, raw []byte) error {
	if expected, ok := doc.roots[key]; ok {
		v, e := bundleObject(raw)
		if e != nil {
			return e
		}
		got := [4]string{bundleText(v, "manifest_hash"), bundleText(v, "input_hash"), bundleText(v, "observation_date"), bundleText(v, "source_hash")}
		if got != expected {
			return errors.New("RESULT_DETAIL_IDENTITY_CONFLICT")
		}
	}
	return nil
}
func walkBundle(ctx context.Context, doc *bundleDocument, read func(string) ([]byte, error), object func(string, string) error) error {
	raw, _ := json.Marshal(doc.report)
	seen := map[string]bool{}
	paths := map[string]bool{}
	for _, f := range doc.header.Inventory {
		paths[f.Path] = true
	}
	if doc.hasIndex {
		check := func(key string) error {
			if _, ok := doc.index[key]; !ok {
				return errors.New("MISSING_RESULT_REFERENCE")
			}
			return nil
		}
		if e := bundleRefs(ctx, raw, check); e != nil {
			return e
		}
		shards := map[string]bool{}
		for _, shard := range doc.index {
			shards[shard] = true
		}
		names := []string{}
		for shard := range shards {
			names = append(names, shard)
		}
		sort.Strings(names)
		for _, shard := range names {
			path := "objects/" + shard + ".json"
			raw, e := read(path)
			if e != nil {
				return e
			}
			hash, e := bundleCanonical(ctx, raw)
			if e != nil {
				return e
			}
			if hash != shard {
				return errors.New("RESULT_SHARD_HASH_MISMATCH")
			}
			values, e := bundleObject(raw)
			if e != nil {
				return e
			}
			for key, value := range values {
				if e = ctx.Err(); e != nil {
					return e
				}
				hash, e := bundleCanonical(ctx, value)
				if e != nil {
					return e
				}
				if doc.index[key] != shard || hash != key {
					return errors.New("RESULT_OBJECT_HASH_MISMATCH")
				}
				if e = checkBundleRoot(doc, key, value); e != nil {
					return e
				}
				if e = bundleRefs(ctx, value, check); e != nil {
					return e
				}
				seen[key] = true
				if object != nil {
					if e = object(key, path); e != nil {
						return e
					}
				}
			}
		}
		if len(seen) != len(doc.index) {
			return errors.New("INCOMPLETE_RESULT_OBJECT_INDEX")
		}
		for path := range paths {
			if strings.HasPrefix(path, "objects/") && path != "objects/index.json" && !shards[strings.TrimSuffix(strings.TrimPrefix(path, "objects/"), ".json")] {
				return errors.New("UNINDEXED_RESULT_SHARD")
			}
		}
	} else {
		pending := []string{}
		add := func(key string) error { pending = append(pending, key); return nil }
		if e := bundleRefs(ctx, raw, add); e != nil {
			return e
		}
		for len(pending) > 0 {
			key := pending[len(pending)-1]
			pending = pending[:len(pending)-1]
			if seen[key] {
				continue
			}
			path := "objects/" + key + ".json"
			value, e := read(path)
			if e != nil {
				return e
			}
			hash, e := bundleCanonical(ctx, value)
			if e != nil {
				return e
			}
			if hash != key {
				return errors.New("RESULT_OBJECT_HASH_MISMATCH")
			}
			if e = checkBundleRoot(doc, key, value); e != nil {
				return e
			}
			if e = bundleRefs(ctx, value, add); e != nil {
				return e
			}
			seen[key] = true
			doc.index[key] = key
			if object != nil {
				if e = object(key, path); e != nil {
					return e
				}
			}
		}
		for path := range paths {
			if strings.HasPrefix(path, "objects/") && !seen[strings.TrimSuffix(strings.TrimPrefix(path, "objects/"), ".json")] {
				return errors.New("UNREFERENCED_RESULT_OBJECT")
			}
		}
	}
	for root := range doc.roots {
		if !seen[root] {
			return errors.New("RESULT_ROOT_NOT_FOUND")
		}
	}
	if doc.declaredObjects != nil && *doc.declaredObjects != len(seen) {
		return errors.New("BUNDLE_DECLARED_OBJECT_COUNT_MISMATCH")
	}
	return nil
}
func (s *Store) ImportBundle(ctx context.Context, root string) (string, error) {
	if e := InitApplicationSchema(ctx, s.db); e != nil {
		return "", e
	}
	if e := s.InitBundleSchema(ctx); e != nil {
		return "", e
	}
	inventory, e := inventoryBundle(ctx, root)
	if e != nil {
		return "", e
	}
	inv := map[string]bundleFile{}
	for _, f := range inventory {
		inv[f.Path] = f
	}
	read := func(path string) ([]byte, error) {
		raw, e := bundleRead(ctx, root, path)
		if e != nil {
			return nil, e
		}
		f, ok := inv[path]
		if !ok || int64(len(raw)) != f.Size || digest(raw) != f.Hash {
			return nil, errors.New("BUNDLE_SOURCE_CHANGED_DURING_IMPORT")
		}
		return raw, nil
	}
	doc, e := parseBundle(ctx, inventory, read)
	if e != nil {
		return "", e
	}
	id := doc.header.ID
	invraw, _ := json.Marshal(inventory)
	tx, e := s.db.BeginTx(ctx, nil)
	if e != nil {
		return id, e
	}
	var prior, status string
	e = tx.QueryRowContext(ctx, "SELECT raw_identity_hash,status FROM legacy_bundles WHERE bundle_id=?", id).Scan(&prior, &status)
	if e == sql.ErrNoRows {
		_, e = tx.ExecContext(ctx, "INSERT INTO legacy_bundles(bundle_id,legacy_run_id,raw_identity_hash,manifest_hash,result_hash,inventory,status,created_at) VALUES(?,?,?,?,?,?,'IMPORTING',?)", id, doc.header.Run, doc.header.Identity, doc.header.Manifest, doc.header.Result, invraw, time.Now().UTC().Format(time.RFC3339Nano))
	} else if e == nil && prior != doc.header.Identity {
		e = errors.New("BUNDLE_SOURCE_IDENTITY_CONFLICT")
	}
	if e != nil {
		tx.Rollback()
		return id, e
	}
	if e = tx.Commit(); e != nil {
		return id, e
	}
	if status == "PUBLISHED" {
		if e = s.VerifyBundle(ctx, id); e != nil {
			return id, e
		}
		var hash string
		if e = s.db.QueryRowContext(ctx, "SELECT content_hash FROM app_change_ledger WHERE kind=? AND entity_id=?", BundleChangeKind, id).Scan(&hash); e != nil {
			return id, errors.New("UNRECONCILED_BUNDLE_LEDGER")
		}
		return id, VerifyBundleChange(ctx, s.db, id, hash)
	}
	for _, file := range inventory {
		raw, e := read(file.Path)
		if e != nil {
			return id, e
		}
		canonical := ""
		if strings.HasSuffix(file.Path, ".json") {
			canonical, e = bundleCanonical(ctx, raw)
			if e != nil {
				return id, e
			}
		}
		codec, payload := encode(raw)
		tx, e := s.db.BeginTx(ctx, nil)
		if e != nil {
			return id, e
		}
		var old string
		e = tx.QueryRowContext(ctx, "SELECT raw_hash FROM bundle_files WHERE bundle_id=? AND path=?", id, file.Path).Scan(&old)
		if e == sql.ErrNoRows {
			_, e = tx.ExecContext(ctx, "INSERT INTO bundle_files VALUES(?,?,?,?,?,?,?)", id, file.Path, file.Hash, canonical, file.Size, codec, payload)
		} else if e == nil && old != file.Hash {
			e = errors.New("BUNDLE_FILE_IDENTITY_CONFLICT")
		}
		if e != nil {
			tx.Rollback()
			return id, e
		}
		if e = tx.Commit(); e != nil {
			return id, e
		}
	}
	// One shard is decoded at a time; object mappings are committed per shard.
	mappings := map[string][]string{}
	if e = walkBundle(ctx, doc, read, func(key, path string) error { mappings[path] = append(mappings[path], key); return nil }); e != nil {
		return id, e
	}
	paths := []string{}
	for path := range mappings {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	for _, path := range paths {
		tx, e := s.db.BeginTx(ctx, nil)
		if e != nil {
			return id, e
		}
		for _, key := range mappings[path] {
			var old string
			e = tx.QueryRowContext(ctx, "SELECT path FROM bundle_objects WHERE bundle_id=? AND object_hash=?", id, key).Scan(&old)
			if e == sql.ErrNoRows {
				_, e = tx.ExecContext(ctx, "INSERT INTO bundle_objects VALUES(?,?,?)", id, key, path)
			} else if e == nil && old != path {
				e = errors.New("BUNDLE_OBJECT_IDENTITY_CONFLICT")
			}
			if e != nil {
				tx.Rollback()
				return id, e
			}
		}
		if e = tx.Commit(); e != nil {
			return id, e
		}
	}
	tx, e = s.db.BeginTx(ctx, nil)
	if e != nil {
		return id, e
	}
	defer tx.Rollback()
	reader := bundleReader{tx}
	if e = reader.verifyBundle(ctx, id, false); e != nil {
		return id, e
	}
	h, e := reader.bundleHeader(ctx, id)
	if e != nil {
		return id, e
	}
	result, e := tx.ExecContext(ctx, "UPDATE legacy_bundles SET status='PUBLISHED',published_at=? WHERE bundle_id=? AND status='IMPORTING'", time.Now().UTC().Format(time.RFC3339Nano), id)
	if e != nil {
		return id, e
	}
	n, e := result.RowsAffected()
	if e != nil || n != 1 {
		return id, errors.New("BUNDLE_PUBLICATION_CONFLICT")
	}
	if e = appLedger(ctx, tx, BundleChangeKind, id, bundleChangeHash(h)); e != nil {
		return id, e
	}
	return id, tx.Commit()
}

func (s bundleReader) bundleHeader(ctx context.Context, id string) (bundleHeader, error) {
	h := bundleHeader{ID: id}
	var raw []byte
	e := s.db.QueryRowContext(ctx, "SELECT legacy_run_id,raw_identity_hash,manifest_hash,result_hash,inventory,status FROM legacy_bundles WHERE bundle_id=?", id).Scan(&h.Run, &h.Identity, &h.Manifest, &h.Result, &raw, &h.Status)
	if e != nil {
		return h, e
	}
	if digest(raw) != h.Identity {
		return h, errors.New("BUNDLE_INVENTORY_HASH_MISMATCH")
	}
	e = json.Unmarshal(raw, &h.Inventory)
	return h, e
}
func (s bundleReader) bundleFile(ctx context.Context, id, path string) ([]byte, error) {
	if !validBundlePath(path) {
		return nil, errors.New("INVALID_BUNDLE_PATH")
	}
	var payload []byte
	var codec, hash, canonical string
	var size int64
	e := s.db.QueryRowContext(ctx, "SELECT raw_hash,canonical_hash,raw_size,codec,payload FROM bundle_files WHERE bundle_id=? AND path=?", id, path).Scan(&hash, &canonical, &size, &codec, &payload)
	if e != nil {
		return nil, e
	}
	if size > maxBundleFile {
		return nil, errors.New("BUNDLE_FILE_SIZE_INVALID")
	}
	raw, e := decodeContext(ctx, codec, payload, size, hash)
	if e != nil {
		return nil, e
	}
	if strings.HasSuffix(path, ".json") {
		got, e := bundleCanonical(ctx, raw)
		if e != nil {
			return nil, e
		}
		if got != canonical {
			return nil, errors.New("BUNDLE_CANONICAL_PROJECTION_CORRUPT")
		}
	} else if canonical != "" {
		return nil, errors.New("BUNDLE_UNEXPECTED_CANONICAL_PROJECTION")
	}
	return raw, nil
}
func (s *Store) VerifyBundle(ctx context.Context, id string) error {
	return (bundleReader{s.db}).verifyBundle(ctx, id, true)
}
func (s bundleReader) verifyBundle(ctx context.Context, id string, published bool) error {
	h, e := s.bundleHeader(ctx, id)
	if e != nil {
		return e
	}
	if published && h.Status != "PUBLISHED" {
		return errors.New("BUNDLE_NOT_PUBLISHED")
	}
	read := func(path string) ([]byte, error) { return s.bundleFile(ctx, id, path) }
	doc, e := parseBundle(ctx, h.Inventory, read)
	if e != nil {
		return e
	}
	if doc.header.ID != id || doc.header.Run != h.Run || doc.header.Manifest != h.Manifest || doc.header.Result != h.Result {
		return errors.New("BUNDLE_HEADER_IDENTITY_CONFLICT")
	}
	var n int
	if e = s.db.QueryRowContext(ctx, "SELECT count(*) FROM bundle_files WHERE bundle_id=?", id).Scan(&n); e != nil {
		return e
	}
	if n != len(h.Inventory) {
		return errors.New("BUNDLE_FILE_MEMBERSHIP_MISMATCH")
	}
	for _, file := range h.Inventory {
		raw, e := read(file.Path)
		if e != nil {
			return e
		}
		if int64(len(raw)) != file.Size || digest(raw) != file.Hash {
			return errors.New("BUNDLE_RAW_BYTES_MISMATCH")
		}
	}
	expected := map[string]string{}
	if e = walkBundle(ctx, doc, read, func(key, path string) error { expected[key] = path; return nil }); e != nil {
		return e
	}
	rows, e := s.db.QueryContext(ctx, "SELECT object_hash,path FROM bundle_objects WHERE bundle_id=? ORDER BY object_hash", id)
	if e != nil {
		return e
	}
	defer rows.Close()
	seen := 0
	for rows.Next() {
		if e = ctx.Err(); e != nil {
			return e
		}
		var key, path string
		if e = rows.Scan(&key, &path); e != nil {
			return e
		}
		if expected[key] != path {
			return errors.New("BUNDLE_OBJECT_MAPPING_MISMATCH")
		}
		seen++
	}
	if e = rows.Err(); e != nil {
		return e
	}
	if seen != len(expected) {
		return errors.New("BUNDLE_OBJECT_MEMBERSHIP_MISMATCH")
	}
	return nil
}
func (s *Store) ExportBundle(ctx context.Context, id, newRoot string) error {
	if e := s.VerifyBundle(ctx, id); e != nil {
		return e
	}
	h, e := s.bundleHeader(ctx, id)
	if e != nil {
		return e
	}
	if e = os.Mkdir(newRoot, 0700); e != nil {
		return e
	}
	if e = os.Mkdir(filepath.Join(newRoot, "objects"), 0700); e != nil {
		return e
	}
	write := func(file bundleFile) error {
		if e = ctx.Err(); e != nil {
			return e
		}
		raw, e := s.bundleFile(ctx, id, file.Path)
		if e != nil {
			return e
		}
		path := filepath.Join(newRoot, filepath.FromSlash(file.Path))
		f, e := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if e != nil {
			return e
		}
		for len(raw) > 0 {
			if e = ctx.Err(); e != nil {
				f.Close()
				return e
			}
			n := len(raw)
			if n > 64<<10 {
				n = 64 << 10
			}
			if _, e = f.Write(raw[:n]); e != nil {
				f.Close()
				return e
			}
			raw = raw[n:]
		}
		e = f.Sync()
		ce := f.Close()
		if e != nil {
			return e
		}
		return ce
	}
	var completion bundleFile
	for _, file := range h.Inventory {
		if file.Path == "completion.json" {
			completion = file
			continue
		}
		if e = write(file); e != nil {
			return e
		}
	}
	if completion.Path == "" {
		return errors.New("BUNDLE_COMPLETION_MISSING")
	}
	return write(completion)
}

// BundleChangeKind is a versioned authoritative change, never a replayable input.
const BundleChangeKind = "BUNDLE_V1"

type bundleQuery interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}
type bundleReader struct{ db bundleQuery }

func (s *Store) bundleHeader(ctx context.Context, id string) (bundleHeader, error) {
	return (bundleReader{s.db}).bundleHeader(ctx, id)
}
func (s *Store) bundleFile(ctx context.Context, id, path string) ([]byte, error) {
	return (bundleReader{s.db}).bundleFile(ctx, id, path)
}
func bundleChangeHash(h bundleHeader) string {
	raw, _ := json.Marshal([]string{BundleChangeKind, h.ID, h.Run, h.Identity, h.Manifest, h.Result})
	return digest(raw)
}
func VerifyBundleChange(ctx context.Context, db *sql.DB, id, hash string) error {
	r := bundleReader{db}
	h, e := r.bundleHeader(ctx, id)
	if e != nil {
		return e
	}
	if bundleChangeHash(h) != hash {
		return errors.New("BUNDLE_LEDGER_IDENTITY_MISMATCH")
	}
	return r.verifyBundle(ctx, id, true)
}

// Unledgered legacy bundles are quarantined by the lifecycle gate. Never infer
// a past cutover time or silently backfill an earlier ledger sequence.
func VerifyBundleLedger(ctx context.Context, db *sql.DB) error {
	var exists int
	if e := db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='legacy_bundles'").Scan(&exists); e != nil {
		return e
	}
	if exists == 0 {
		return nil
	}
	rows, e := db.QueryContext(ctx, "SELECT bundle_id,status FROM legacy_bundles ORDER BY bundle_id")
	if e != nil {
		return e
	}
	var ids []string
	for rows.Next() {
		var id, status string
		if e = rows.Scan(&id, &status); e != nil {
			rows.Close()
			return e
		}
		if status != "PUBLISHED" {
			rows.Close()
			return errors.New("UNFINISHED_BUNDLE_REQUIRES_EXPLICIT_RECOVERY")
		}
		ids = append(ids, id)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, id := range ids {
		var hash string
		if e = db.QueryRowContext(ctx, "SELECT content_hash FROM app_change_ledger WHERE kind=? AND entity_id=?", BundleChangeKind, id).Scan(&hash); e != nil {
			return fmt.Errorf("UNRECONCILED_BUNDLE_LEDGER: %s: %w", id, e)
		}
		if e = VerifyBundleChange(ctx, db, id, hash); e != nil {
			return e
		}
	}
	return nil
}
