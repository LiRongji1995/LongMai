package core

import (
	a "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/research"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	o "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/files"
	l "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/legacy"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
)

type Diagnostic = a.Diagnostic
type Coverage = a.Coverage
type Support = a.Support
type HistoryReader = ports.HistoryReader
type FactsCache = ports.FactsCache
type History = l.History
type HistoryManifest = c.HistoryManifest
type Partition = c.Partition
type Manifest = o.Manifest
type Completion = o.Completion

var OpenHistory = l.OpenHistory
var AnalyzeFrozen = a.AnalyzeFrozen
var AnalyzeStrategyFrozen = a.AnalyzeStrategyFrozen
var EvaluateCoverage = a.EvaluateCoverage
var EvaluateV2Coverage = a.EvaluateV2Coverage
var SaveResearch = o.SaveResearch
var VerifyResearch = o.VerifyResearch
var SaveDiagnostic = o.SaveDiagnostic
var VerifyDiagnostic = o.VerifyDiagnostic

const MaxExpandedDetails = o.MaxExpandedDetails
const MaxDetailLine = o.MaxDetailLine
