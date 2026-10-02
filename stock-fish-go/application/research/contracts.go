package research

import (
	"context"
	contracts "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	f "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	r1 "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/r1"
	shared "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/shared"
	v2 "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/v2"
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

type Resolver = ev.Resolver
type Scope = ev.Scope
type Resolution = ev.Resolution
type Clocks = ev.Clocks

var NewResolver = ev.NewResolver
var NewResolverContext = ev.NewResolverContext
var SelectRevision = ev.SelectRevision
var SelectRevisionIndex = ev.SelectRevisionIndex
var Qualify = ev.Qualify
var DecodeFrozen = ev.DecodeFrozen
var DecodeFrozenContext = ev.DecodeFrozenContext
var ValidateFrozen = ev.ValidateFrozen
var ValidateFrozenContext = ev.ValidateFrozenContext
var CanonicalInputProjection = ev.CanonicalInputProjection
var ValidateEvidenceRow = ev.ValidateEvidenceRow
var DecodeEvidenceRow = ev.DecodeEvidenceRow
var validHash = ev.ValidHash

const InputContractVersion = ev.InputContractVersion

var isNull = ev.IsNull

type StockFacts = f.StockFacts
type Facts = f.Facts
type StrengthMomentum = f.StrengthMomentum
type Bar = f.Bar
type Path = f.Path
type Window = f.Window
type Benchmark = f.Benchmark
type Trend = f.Trend
type Percentiles = f.Percentiles
type EvidenceRejection = f.EvidenceRejection
type Background = f.Background
type LimitFact = f.LimitFact
type MarketWidth = f.MarketWidth
type MarketContext = f.MarketContext
type SectorWindow = f.SectorWindow
type SectorContext = f.SectorContext
type FundsContext = f.FundsContext
type Qualification = contracts.Qualification
type Decision = r1.Decision
type V2Decision = v2.V2Decision

const R1StrategyID = "STAGE_AWARE_RESEARCH_R1"
const V2StrategyID = "STAGE_AWARE_MOMENTUM_V2"

var Stages = shared.Stages
var ParseBar = f.ParseBar
var Limits = f.Limits
var DailyPath = f.DailyPath
var Market = f.Market
var Percentile = f.Percentile
var Momentum = f.Momentum
var SortedIDs = f.SortedIDs
var NewPercentiles = f.NewPercentiles
var WindowLimit = f.WindowLimit
var ComputeMarket = f.ComputeMarket
var SupplyPressure = f.SupplyPressure
var SectorRS = f.SectorRS
var FundForTheme = f.FundForTheme
var Start = shared.Start
var HighLevelRepair = shared.HighLevelRepair
var Pullback = shared.Pullback
var WeakToStrong = shared.WeakToStrong
var Acceleration = r1.Acceleration
var MA20Trend = r1.MA20Trend
var Identify = r1.Identify
var Decide = r1.Decide
var DecideV2 = v2.DecideV2
var RankV2 = v2.RankV2
var visibleThemes = f.VisibleThemes
var marketRaw = f.MarketRaw
var median = f.Median
var deltaState = f.DeltaState
var summarize = f.Summarize
var buildFacts = f.BuildFacts
var reasonSet = shared.ReasonSet
var v2Acceleration = v2.Acceleration

func ComputeSectors(ctx context.Context, themes []Row, facts []StockFacts, market map[int]Benchmark) (map[string][]SectorContext, error) {
	out, e := f.ComputeSectors(ctx, themes, facts, market)
	if e != nil {
		return nil, e
	}
	return completeSectorCompatibility(out), nil
}

func completeSectorCompatibility(out map[string][]SectorContext) map[string][]SectorContext {
	for id, rows := range out {
		for i := range rows {
			for _, setup := range shared.Stages {
				rows[i].Compatibility[setup] = shared.SectorCompatibility(rows[i], setup)
			}
		}
		out[id] = rows
	}
	return out
}

type Budget = ports.ResearchBudget
type Metrics = contracts.Metrics
type HistoryReader = ports.HistoryReader
type FactsCache = ports.FactsCache
