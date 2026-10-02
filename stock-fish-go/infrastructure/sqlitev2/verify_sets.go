package sqlitev2

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
)

// Exact sets are compact identities, never complete payloads. The allocation
// ceiling is checked before reading, including conservative Go-map overhead.
// A source identity must match exactly once on the target. Equal counts plus
// the duplicate bitmap prove the reverse inclusion as well; no probabilistic
// commutative checksum or database unique-index assumption is used.
type compactIdentity struct {
	hash                      [32]byte
	kind, instrument, session uint32
}
type identityStrings struct {
	ids    map[string]uint32
	values []string
	bytes  int64
}

func (d *identityStrings) intern(s string) (uint32, error) {
	if v, ok := d.ids[s]; ok {
		return v, nil
	}
	d.bytes += int64(len(s) + 128)
	if d.bytes > 16<<20 {
		return 0, errors.New("IDENTITY_STRING_BUDGET_EXCEEDED")
	}
	n := uint32(len(d.values))
	d.ids[s] = n
	d.values = append(d.values, s)
	return n, nil
}
func identityHash(s string) (h [32]byte, e error) {
	if len(s) != 64 {
		return h, errors.New("INVALID_IDENTITY_HASH")
	}
	_, e = hex.Decode(h[:], []byte(s))
	if e == nil && hex.EncodeToString(h[:]) != s {
		e = errors.New("NONCANONICAL_IDENTITY_HASH")
	}
	return
}
func bitMark(bits []byte, i int) bool {
	mask := byte(1 << uint(i&7))
	if bits[i>>3]&mask != 0 {
		return false
	}
	bits[i>>3] |= mask
	return true
}
func (s *Store) verifyExactSets(ctx context.Context, opt Options, out *Verification) error {
	var nh, nm int64
	if e := s.db.QueryRowContext(ctx, "SELECT count(*) FROM frozen.history_evidence_rows").Scan(&nh); e != nil {
		return e
	}
	if e := s.db.QueryRowContext(ctx, "SELECT count(*) FROM frozen.history_partition_members").Scan(&nm); e != nil {
		return e
	}
	if nh < 0 || nm < 0 || nh > 3_000_000 || nm > 8_000_000 || nh*112+nm*9+(16<<20) > 384<<20 {
		return errors.New("EXACT_SET_MEMORY_BUDGET_EXCEEDED")
	}
	dict := identityStrings{ids: map[string]uint32{}}
	keys := make([]compactIdentity, 0, int(nh))
	progress := func(stage string, n int64) error {
		if n%10000 == 0 {
			if opt.Progress != nil {
				opt.Progress(stage, n)
			}
			if opt.ResourceCheck != nil {
				if e := opt.ResourceCheck(); e != nil {
					return e
				}
			}
		}
		return ctx.Err()
	}
	rows, e := s.db.QueryContext(ctx, "SELECT row_hash,kind,instrument_id,session FROM frozen.history_evidence_rows NOT INDEXED")
	if e != nil {
		return e
	}
	for rows.Next() {
		var hash, kind, iid, session string
		if e = rows.Scan(&hash, &kind, &iid, &session); e != nil {
			rows.Close()
			return e
		}
		var k compactIdentity
		if k.hash, e = identityHash(hash); e != nil {
			rows.Close()
			return e
		}
		if k.kind, e = dict.intern(kind); e != nil {
			rows.Close()
			return e
		}
		if k.instrument, e = dict.intern(iid); e != nil {
			rows.Close()
			return e
		}
		if k.session, e = dict.intern(session); e != nil {
			rows.Close()
			return e
		}
		if int64(len(keys)) >= nh {
			rows.Close()
			return errors.New("SOURCE_IDENTITY_COUNT_CHANGED")
		}
		keys = append(keys, k)
		if e = progress("source_identity_scan", int64(len(keys))); e != nil {
			rows.Close()
			return e
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	if int64(len(keys)) != nh {
		return errors.New("SOURCE_IDENTITY_COUNT_CHANGED")
	}
	comparisons := 0
	var sortErr error
	sort.Slice(keys, func(i, j int) bool {
		comparisons++
		if comparisons&4095 == 0 {
			sortErr = ctx.Err()
		}
		if sortErr != nil {
			return false
		}
		return bytes.Compare(keys[i].hash[:], keys[j].hash[:]) < 0
	})
	if sortErr != nil {
		return sortErr
	}
	for i := 1; i < len(keys); i++ {
		if keys[i-1].hash == keys[i].hash {
			return errors.New("DUPLICATE_SOURCE_HISTORY_IDENTITY")
		}
	}
	find := func(h [32]byte) int {
		i := sort.Search(len(keys), func(i int) bool { return bytes.Compare(keys[i].hash[:], h[:]) >= 0 })
		if i == len(keys) || keys[i].hash != h {
			return -1
		}
		return i
	}
	same := func(k compactIdentity, kind, iid, session string) bool {
		return dict.values[k.kind] == kind && dict.values[k.instrument] == iid && dict.values[k.session] == session
	}
	historyIDs := make(map[int64]uint32, int(nh))
	seen := make([]byte, (len(keys)+7)/8)
	var targetCount int64
	rows, e = s.db.QueryContext(ctx, "SELECT id,row_hash,kind,instrument_id,session FROM history_rows NOT INDEXED")
	if e != nil {
		return e
	}
	for rows.Next() {
		var id int64
		var hash, kind, iid, session string
		if e = rows.Scan(&id, &hash, &kind, &iid, &session); e != nil {
			rows.Close()
			return e
		}
		h, err := identityHash(hash)
		if err != nil {
			rows.Close()
			return err
		}
		i := find(h)
		if i < 0 {
			rows.Close()
			return errors.New("HISTORY_EXTRA_IDENTITY")
		}
		if !same(keys[i], kind, iid, session) {
			rows.Close()
			return errors.New("HISTORY_IDENTITY_METADATA_MISMATCH")
		}
		if !bitMark(seen, i) {
			rows.Close()
			return errors.New("DUPLICATE_TARGET_HISTORY_IDENTITY")
		}
		historyIDs[id] = uint32(i)
		targetCount++
		if e = progress("target_identity_scan", targetCount); e != nil {
			rows.Close()
			return e
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	if targetCount != nh {
		return errors.New("HISTORY_MISSING_IDENTITY")
	}
	out.Checked["history_identity_set"] = 0
	out.Checked["history_extra_identity"] = 0
	type part struct {
		hash    string
		session string
		count   int64
	}
	parts := []part{}
	partIDs := map[int64]uint32{}
	partHashes := map[string]uint32{}
	rows, e = s.db.QueryContext(ctx, "SELECT id,partition_hash FROM partitions ORDER BY id")
	if e != nil {
		return e
	}
	for rows.Next() {
		var id int64
		var hash string
		if e = rows.Scan(&id, &hash); e != nil {
			rows.Close()
			return e
		}
		if _, e = identityHash(hash); e != nil {
			rows.Close()
			return e
		}
		if _, ok := partHashes[hash]; ok {
			rows.Close()
			return errors.New("DUPLICATE_PARTITION_HASH")
		}
		if len(parts) >= 100000 {
			rows.Close()
			return errors.New("PARTITION_IDENTITY_BUDGET_EXCEEDED")
		}
		partIDs[id] = uint32(len(parts))
		partHashes[hash] = uint32(len(parts))
		parts = append(parts, part{hash: hash, count: -1})
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	rows, e = s.db.QueryContext(ctx, "SELECT partition_id,session,row_count FROM snapshot_partitions")
	if e != nil {
		return e
	}
	for rows.Next() {
		var id, count int64
		var session string
		if e = rows.Scan(&id, &session, &count); e != nil {
			rows.Close()
			return e
		}
		i, ok := partIDs[id]
		if !ok {
			rows.Close()
			return errors.New("MISSING_SNAPSHOT_PARTITION")
		}
		p := &parts[i]
		if p.count >= 0 && (p.count != count || p.session != session) {
			rows.Close()
			return errors.New("CONFLICTING_PARTITION_DESCRIPTOR")
		}
		p.count = count
		p.session = session
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	members := make([]uint64, 0, int(nm))
	rows, e = s.db.QueryContext(ctx, "SELECT partition_hash,row_hash,kind,instrument_id,session FROM frozen.history_partition_members NOT INDEXED")
	if e != nil {
		return e
	}
	for rows.Next() {
		var ph, rh, kind, iid, session string
		if e = rows.Scan(&ph, &rh, &kind, &iid, &session); e != nil {
			rows.Close()
			return e
		}
		pid, ok := partHashes[ph]
		if !ok {
			rows.Close()
			return errors.New("MISSING_TARGET_PARTITION")
		}
		h, err := identityHash(rh)
		if err != nil {
			rows.Close()
			return err
		}
		i := find(h)
		if i < 0 || !same(keys[i], kind, iid, session) {
			rows.Close()
			return errors.New("SOURCE_PARTITION_METADATA_CONFLICT")
		}
		if int64(len(members)) >= nm {
			rows.Close()
			return errors.New("SOURCE_MEMBER_COUNT_CHANGED")
		}
		members = append(members, uint64(pid)<<32|uint64(i))
		if e = progress("source_member_scan", int64(len(members))); e != nil {
			rows.Close()
			return e
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	if int64(len(members)) != nm {
		return errors.New("SOURCE_MEMBER_COUNT_CHANGED")
	}
	comparisons = 0
	sort.Slice(members, func(i, j int) bool {
		comparisons++
		if comparisons&4095 == 0 {
			sortErr = ctx.Err()
		}
		if sortErr != nil {
			return false
		}
		return members[i] < members[j]
	})
	if sortErr != nil {
		return sortErr
	}
	for i := 1; i < len(members); i++ {
		if members[i-1] == members[i] {
			return errors.New("DUPLICATE_SOURCE_PARTITION_MEMBER")
		}
	}
	seen = make([]byte, (len(members)+7)/8)
	targetCount = 0
	rows, e = s.db.QueryContext(ctx, "SELECT partition_id,row_id FROM partition_members")
	if e != nil {
		return e
	}
	for rows.Next() {
		var pid, rid int64
		if e = rows.Scan(&pid, &rid); e != nil {
			rows.Close()
			return e
		}
		p, ok := partIDs[pid]
		if !ok {
			rows.Close()
			return errors.New("TARGET_PARTITION_FK_MISSING")
		}
		h, ok := historyIDs[rid]
		if !ok {
			rows.Close()
			return errors.New("TARGET_HISTORY_FK_MISSING")
		}
		key := uint64(p)<<32 | uint64(h)
		i := sort.Search(len(members), func(i int) bool { return members[i] >= key })
		if i == len(members) || members[i] != key {
			rows.Close()
			return errors.New("PARTITION_EXTRA_MEMBER")
		}
		if !bitMark(seen, i) {
			rows.Close()
			return errors.New("DUPLICATE_TARGET_PARTITION_MEMBER")
		}
		targetCount++
		if e = progress("target_member_scan", targetCount); e != nil {
			rows.Close()
			return e
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	if targetCount != nm {
		return errors.New("PARTITION_MISSING_MEMBER")
	}
	out.Checked["partition_member_set"] = 0
	out.Checked["partition_extra_member"] = 0
	// Each target member now has an exact source counterpart and validated row
	// metadata. Reconstruct the established ordered partition hash from these
	// compact identities instead of randomly rereading the large payload table.
	start := 0
	for pid, p := range parts {
		end := start
		for end < len(members) && uint32(members[end]>>32) == uint32(pid) {
			end++
		}
		group := members[start:end]
		comparisons = 0
		sort.Slice(group, func(i, j int) bool {
			comparisons++
			if comparisons&4095 == 0 {
				sortErr = ctx.Err()
			}
			if sortErr != nil {
				return false
			}
			a, b := keys[uint32(group[i])], keys[uint32(group[j])]
			if dict.values[a.kind] != dict.values[b.kind] {
				return dict.values[a.kind] < dict.values[b.kind]
			}
			if dict.values[a.instrument] != dict.values[b.instrument] {
				return dict.values[a.instrument] < dict.values[b.instrument]
			}
			return bytes.Compare(a.hash[:], b.hash[:]) < 0
		})
		if sortErr != nil {
			return sortErr
		}
		sum := sha256.New()
		for _, m := range group {
			if e = ctx.Err(); e != nil {
				return e
			}
			k := keys[uint32(m)]
			session := dict.values[k.session]
			if p.session != "" && session != p.session {
				return errors.New("PARTITION_SESSION_MISMATCH")
			}
			line, err := json.Marshal([]string{hex.EncodeToString(k.hash[:]), dict.values[k.kind], dict.values[k.instrument], session})
			if err != nil {
				return err
			}
			sum.Write(line)
			sum.Write([]byte{'\n'})
		}
		if (p.count >= 0 && int64(len(group)) != p.count) || hex.EncodeToString(sum.Sum(nil)) != p.hash {
			return fmt.Errorf("PARTITION_MANIFEST_HASH_MISMATCH: %s", p.hash)
		}
		out.Checked["partition_content_hashes"]++
		if opt.Progress != nil {
			opt.Progress("partition_content_hashes", int64(pid+1))
		}
		start = end
	}
	return ctx.Err()
}
