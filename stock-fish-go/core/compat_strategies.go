package core

import (
	"context"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	f "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	r1 "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/r1"
	s "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/shared"
	v2 "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/v2"
)

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
type Qualification = c.Qualification
type Decision = r1.Decision
type V2Decision = v2.V2Decision

const R1StrategyID = "STAGE_AWARE_RESEARCH_R1"
const V2StrategyID = "STAGE_AWARE_MOMENTUM_V2"

var Stages = s.Stages
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
var Start = s.Start
var HighLevelRepair = s.HighLevelRepair
var Pullback = s.Pullback
var WeakToStrong = s.WeakToStrong
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
var reasonSet = s.ReasonSet
var v2Acceleration = v2.Acceleration

func ComputeSectors(ctx context.Context, themes []Row, facts []StockFacts, market map[int]Benchmark) (map[string][]SectorContext, error) {
	out, e := f.ComputeSectors(ctx, themes, facts, market)
	if e != nil {
		return nil, e
	}
	for id, rows := range out {
		for i := range rows {
			for _, setup := range s.Stages {
				rows[i].Compatibility[setup] = s.SectorCompatibility(rows[i], setup)
			}
		}
		out[id] = rows
	}
	return out, nil
}
