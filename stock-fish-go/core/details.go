package core

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
)

// Exact equal-weight benchmark fractions can have large denominators. Detail is
// encoded one security at a time and compressed, instead of buffering a whole
// market JSON value or repeating it in multiple uncompressed files.
const MaxExpandedDetails int64 = 1 << 30
const MaxDetailLine = 4 << 20

type detailRecord struct {
	Fact     StockFacts `json:"fact"`
	Decision Decision   `json:"decision"`
}
type logicalWriter struct {
	w io.Writer
	b *Budget
	n int64
}

func (w *logicalWriter) Write(p []byte) (int, error) {
	if e := w.b.Check(); e != nil {
		return 0, e
	}
	w.n += int64(len(p))
	w.b.mu.Lock()
	w.b.m.ExpandedOutputBytes += int64(len(p))
	w.b.mu.Unlock()
	if w.n > MaxExpandedDetails {
		return 0, errCode("EXPANDED_OUTPUT_BUDGET_EXCEEDED")
	}
	return w.w.Write(p)
}
func writeDetails(root string, d Diagnostic, b *Budget) (string, error) {
	if len(d.Facts) != len(d.Decisions) {
		return "", errCode("DETAIL_COUNT_MISMATCH")
	}
	f, e := os.CreateTemp(root, ".unpublished-details-")
	if e != nil {
		return "", e
	}
	defer os.Remove(f.Name())
	h := sha256.New()
	gz := gzip.NewWriter(budgetWriter{io.MultiWriter(f, h), b})
	logical := &logicalWriter{w: gz, b: b}
	enc := json.NewEncoder(logical)
	for i, sf := range d.Facts {
		if e = b.Check(); e != nil {
			gz.Close()
			f.Close()
			return "", e
		}
		if sf.ID != d.Decisions[i].InstrumentID {
			gz.Close()
			f.Close()
			return "", errCode("DETAIL_IDENTITY_CONFLICT")
		}
		if e = enc.Encode(detailRecord{sf, d.Decisions[i]}); e != nil {
			gz.Close()
			f.Close()
			return "", e
		}
	}
	e = gz.Close()
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return "", e
	}
	if e = b.Check(); e != nil {
		return "", e
	}
	p := filepath.Join(root, "details.ndjson.gz")
	if _, e = os.Lstat(p); !os.IsNotExist(e) {
		return "", errCode("OUTPUT_EXISTS")
	}
	if e = os.Rename(f.Name(), p); e != nil {
		return "", e
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func verifyDetails(root string, d Diagnostic, b *Budget) error {
	if d.DetailFile != "details.ndjson.gz" || d.DetailCount != len(d.Qualification.Eligible) {
		return errCode("DETAIL_REFERENCE_CONFLICT")
	}
	f, e := os.Open(filepath.Join(root, d.DetailFile))
	if e != nil {
		return e
	}
	defer f.Close()
	gz, e := gzip.NewReader(f)
	if e != nil {
		return e
	}
	defer gz.Close()
	limit := &io.LimitedReader{R: gz, N: MaxExpandedDetails + 1}
	s := bufio.NewScanner(limit)
	s.Buffer(make([]byte, 64<<10), MaxDetailLine)
	n := 0
	for s.Scan() {
		if e = b.Check(); e != nil {
			return e
		}
		var r detailRecord
		if e = json.Unmarshal(s.Bytes(), &r); e != nil {
			return e
		}
		if n >= d.DetailCount || r.Fact.ID != d.Qualification.Eligible[n] || r.Decision.InstrumentID != r.Fact.ID {
			return errCode("DETAIL_IDENTITY_CONFLICT")
		}
		n++
	}
	if e = s.Err(); e != nil {
		return e
	}
	if limit.N <= 0 {
		return errCode("EXPANDED_OUTPUT_BUDGET_EXCEEDED")
	}
	if n != d.DetailCount {
		return errCode("DETAIL_COUNT_MISMATCH")
	}
	return b.Check()
}
