package v2

import (
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	f "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	shared "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/shared"
)

type Number = c.Number
type Row = c.Row
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

type Facts = f.Facts
type StockFacts = f.StockFacts
type Background = f.Background
type MarketContext = f.MarketContext
type SectorContext = f.SectorContext
type FundsContext = f.FundsContext
type Path = f.Path

var Stages = shared.Stages

const V2StrategyID = "STAGE_AWARE_MOMENTUM_V2"
