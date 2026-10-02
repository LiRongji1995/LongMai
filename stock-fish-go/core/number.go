// Package core contains independent research primitives. It never invokes Python.
package core

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/big"
	"regexp"
)

// Number is an immutable optional rational. A missing number is distinct from zero.
// No float64 conversion is used for business comparisons.
type Number struct{ r *big.Rat }

var decimalRE = regexp.MustCompile(`^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$`)
var rationalRE = regexp.MustCompile(`^-?[0-9]+/[1-9][0-9]*$`)

func Parse(s string) (Number, error) {
	if len(s) > 256 || !decimalRE.MatchString(s) {
		return Number{}, fmt.Errorf("INVALID_DECIMAL")
	}
	// Bound exponent before allocating arbitrarily large integers.
	for i, c := range s {
		if c == 'e' || c == 'E' {
			exp, ok := new(big.Int).SetString(s[i+1:], 10)
			if !ok || !exp.IsInt64() || exp.Int64() > 100 || exp.Int64() < -100 {
				return Number{}, fmt.Errorf("DECIMAL_EXPONENT_LIMIT")
			}
			break
		}
	}
	r, ok := new(big.Rat).SetString(s)
	if !ok {
		return Number{}, fmt.Errorf("INVALID_DECIMAL")
	}
	return Number{r}, nil
}
func N(s string) Number {
	n, e := Parse(s)
	if e != nil {
		panic(e)
	}
	return n
}
func (n Number) Known() bool { return n.r != nil }
func (n Number) String() string {
	if n.r == nil {
		return ""
	}
	return n.r.RatString()
}
func (n Number) MarshalJSON() ([]byte, error) {
	if !n.Known() {
		return []byte("null"), nil
	}
	return json.Marshal(n.String())
}
func (n *Number) UnmarshalJSON(b []byte) error {
	if bytes.Equal(b, []byte("null")) {
		*n = Number{}
		return nil
	}
	s := string(b)
	if len(b) > 0 && b[0] == '"' {
		if e := json.Unmarshal(b, &s); e != nil {
			return e
		}
	}
	v, e := Parse(s)
	// The versioned Go result format uses exact rational strings. Raw market
	// ingestion additionally enforces decimal-only syntax in ParseBar.
	if e != nil && len(s) <= 1<<20 && rationalRE.MatchString(s) {
		if r, ok := new(big.Rat).SetString(s); ok {
			v, e = Number{r}, nil
		}
	}
	if e == nil {
		*n = v
	}
	return e
}
func (a Number) Add(b Number) Number {
	if !a.Known() || !b.Known() {
		return Number{}
	}
	return Number{new(big.Rat).Add(a.r, b.r)}
}
func (a Number) Sub(b Number) Number {
	if !a.Known() || !b.Known() {
		return Number{}
	}
	return Number{new(big.Rat).Sub(a.r, b.r)}
}
func (a Number) Mul(b Number) Number {
	if !a.Known() || !b.Known() {
		return Number{}
	}
	return Number{new(big.Rat).Mul(a.r, b.r)}
}
func (a Number) Div(b Number) Number {
	if !a.Known() || !b.Known() || b.r.Sign() == 0 {
		return Number{}
	}
	return Number{new(big.Rat).Quo(a.r, b.r)}
}
func (a Number) Abs() Number {
	if !a.Known() {
		return a
	}
	return Number{new(big.Rat).Abs(a.r)}
}
func (a Number) Cmp(b Number) int {
	if !a.Known() || !b.Known() {
		panic("comparison of missing number")
	}
	return a.r.Cmp(b.r)
}
func (a Number) Positive() bool { return a.Known() && a.r.Sign() > 0 }

// Rounded performs decimal ROUND_HALF_EVEN, matching Python's Decimal quantize.
func (a Number) Rounded(places int) Number {
	if !a.Known() {
		return a
	}
	scale := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(places)), nil)
	v := new(big.Rat).Mul(a.r, new(big.Rat).SetInt(scale))
	num := new(big.Int).Abs(v.Num())
	q, r := new(big.Int), new(big.Int)
	q.QuoRem(num, v.Denom(), r)
	cmp := new(big.Int).Lsh(r, 1).Cmp(v.Denom())
	if cmp > 0 || (cmp == 0 && q.Bit(0) == 1) {
		q.Add(q, big.NewInt(1))
	}
	if v.Sign() < 0 {
		q.Neg(q)
	}
	return Number{new(big.Rat).SetFrac(q, scale)}
}
func count(n int) Number { return Number{new(big.Rat).SetInt64(int64(n))} }
