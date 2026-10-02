// Package core preserves the original diagnostic API while new applications
// import the domain, evidence, facts and strategy boundaries directly.
package core

import (
	contracts "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	evpkg "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
)

type Number = contracts.Number
type Row = contracts.Row
type Input = contracts.Input
type Frozen = contracts.Frozen
type Condition = contracts.Condition
type State = contracts.State

var Parse = contracts.Parse
var N = contracts.N
var Hash = contracts.Hash
var RowHash = contracts.RowHash
var Canonical = contracts.Canonical
var CanonicalContext = contracts.CanonicalContext
var Compare = contracts.Compare
var All = contracts.All
var Any = contracts.Any
var C = contracts.C

const Pass = contracts.Pass
const Fail = contracts.Fail
const Unknown = contracts.Unknown
const NotEvaluated = contracts.NotEvaluated
const NotImplemented = contracts.NotImplemented
const RuntimeError = contracts.RuntimeError

type errCode = contracts.ErrCode

var count = contracts.Count
var contains = contracts.Contains

type Resolver = evpkg.Resolver
type Scope = evpkg.Scope
type Resolution = evpkg.Resolution
type Clocks = evpkg.Clocks

var NewResolver = evpkg.NewResolver
var SelectRevision = evpkg.SelectRevision
var Qualify = evpkg.Qualify
var DecodeFrozen = evpkg.DecodeFrozen
var DecodeFrozenContext = evpkg.DecodeFrozenContext
var ValidateFrozen = evpkg.ValidateFrozen
var ValidateFrozenContext = evpkg.ValidateFrozenContext
var CanonicalInputProjection = evpkg.CanonicalInputProjection
var ValidateEvidenceRow = evpkg.ValidateEvidenceRow
var DecodeEvidenceRow = evpkg.DecodeEvidenceRow
var validHash = evpkg.ValidHash

const InputContractVersion = evpkg.InputContractVersion

var isNull = evpkg.IsNull
