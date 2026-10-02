package contracts

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"unicode/utf8"
)

// Row is confined to the raw evidence boundary. Strategy facts are typed.
type Row map[string]json.RawMessage

func (r Row) Text(k string) string      { var v string; _ = json.Unmarshal(r[k], &v); return v }
func (r Row) Bool(k string) *bool       { var v *bool; _ = json.Unmarshal(r[k], &v); return v }
func (r Row) Strings(k string) []string { var v []string; _ = json.Unmarshal(r[k], &v); return v }
func (r Row) Num(k string) Number       { var v Number; _ = json.Unmarshal(r[k], &v); return v }
func Hash(b []byte) string              { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }

// Canonical preserves numeric lexemes and Unicode and rejects duplicate keys.
// It does not insert Pydantic defaults or normalize datetime strings.
func Canonical(raw []byte) ([]byte, error) {
	return CanonicalContext(context.Background(), raw)
}

// CanonicalContext includes parsing and canonical encoding in the caller's total
// deadline. It rejects duplicate keys at every depth before typed decoding.
func CanonicalContext(ctx context.Context, raw []byte) ([]byte, error) {
	if e := ctx.Err(); e != nil {
		return nil, e
	}
	if !utf8.Valid(raw) {
		return nil, ErrCode("INVALID_UTF8_JSON")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	v, e := jsonValueContext(ctx, d)
	if e != nil {
		return nil, e
	}
	if _, e = d.Token(); e != io.EOF {
		return nil, ErrCode("TRAILING_JSON")
	}
	var b bytes.Buffer
	e = encodeCanonicalContext(ctx, &b, v)
	return b.Bytes(), e
}
func jsonValue(d *json.Decoder) (any, error) {
	return jsonValueContext(context.Background(), d)
}
func jsonValueContext(ctx context.Context, d *json.Decoder) (any, error) {
	if e := ctx.Err(); e != nil {
		return nil, e
	}
	t, e := d.Token()
	if e != nil {
		return nil, e
	}
	switch v := t.(type) {
	case json.Delim:
		if v == '{' {
			m := map[string]any{}
			for d.More() {
				t, e := d.Token()
				if e != nil {
					return nil, e
				}
				k, ok := t.(string)
				if !ok {
					return nil, ErrCode("INVALID_JSON_KEY")
				}
				if _, ok = m[k]; ok {
					return nil, ErrCode("DUPLICATE_JSON_KEY")
				}
				m[k], e = jsonValueContext(ctx, d)
				if e != nil {
					return nil, e
				}
			}
			_, e = d.Token()
			return m, e
		}
		if v == '[' {
			a := []any{}
			for d.More() {
				x, e := jsonValueContext(ctx, d)
				if e != nil {
					return nil, e
				}
				a = append(a, x)
			}
			_, e = d.Token()
			return a, e
		}
		return nil, ErrCode("INVALID_JSON")
	default:
		return t, nil
	}
}
func encodeCanonical(b *bytes.Buffer, v any) error {
	return encodeCanonicalContext(context.Background(), b, v)
}
func encodeCanonicalContext(ctx context.Context, b *bytes.Buffer, v any) error {
	if e := ctx.Err(); e != nil {
		return e
	}
	switch x := v.(type) {
	case map[string]any:
		keys := []string{}
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			if e := writeStringContext(ctx, b, k); e != nil {
				return e
			}
			b.WriteByte(':')
			if e := encodeCanonicalContext(ctx, b, x[k]); e != nil {
				return e
			}
		}
		b.WriteByte('}')
	case []any:
		b.WriteByte('[')
		for i, v := range x {
			if i > 0 {
				b.WriteByte(',')
			}
			if e := encodeCanonicalContext(ctx, b, v); e != nil {
				return e
			}
		}
		b.WriteByte(']')
	case string:
		if e := writeStringContext(ctx, b, x); e != nil {
			return e
		}
	case json.Number:
		b.WriteString(x.String())
	case bool:
		if x {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case nil:
		b.WriteString("null")
	default:
		return ErrCode("INVALID_CANONICAL_TYPE")
	}
	return nil
}
func writeString(b *bytes.Buffer, s string) {
	_ = writeStringContext(context.Background(), b, s)
}
func writeStringContext(ctx context.Context, b *bytes.Buffer, s string) error {
	b.WriteByte('"')
	for i, c := range s {
		if i&4095 == 0 {
			if e := ctx.Err(); e != nil {
				return e
			}
		}
		switch c {
		case '"', '\\':
			b.WriteByte('\\')
			b.WriteRune(c)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if c < 32 {
				fmt.Fprintf(b, `\u%04x`, c)
			} else {
				b.WriteRune(c)
			}
		}
	}
	b.WriteByte('"')
	return ctx.Err()
}
func RowHash(r Row) string {
	b, _ := json.Marshal(r)
	c, e := Canonical(b)
	if e != nil {
		return ""
	}
	return Hash(c)
}
