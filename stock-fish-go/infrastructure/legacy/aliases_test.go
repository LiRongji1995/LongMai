package legacy

import (
	a "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/research"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	f "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	o "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/files"
	r "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	r1 "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/r1"
)

type Bar = f.Bar

var N = c.N
var Limits = f.Limits
var Qualify = ev.Qualify
var NewBudget = r.NewBudget
var SaveDiagnostic = o.SaveDiagnostic
var VerifyDiagnostic = o.VerifyDiagnostic

type Diagnostic = a.Diagnostic
type Qualification = c.Qualification
type Manifest = o.Manifest

const MaxOutputBytes = r.MaxOutputBytes

var RowHash = c.RowHash

type StockFacts = f.StockFacts
type Decision = r1.Decision
