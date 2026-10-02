package facts

import (
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
)

type Number = c.Number
type Row = c.Row
type SelectedEvidenceBinding = c.SelectedEvidenceBinding
type Condition = c.Condition
type State = c.State

var N = c.N
var C = c.C
var All = c.All
var Any = c.Any
var Compare = c.Compare
var RowHash = c.RowHash
var Hash = c.Hash
var count = c.Count
var contains = c.Contains

type errCode = c.ErrCode

const Pass = c.Pass
const Fail = c.Fail
const Unknown = c.Unknown
const NotEvaluated = c.NotEvaluated

type Resolver = ev.Resolver
type Scope = ev.Scope

var SelectRevision = ev.SelectRevision

var parseDecimal = c.Parse
