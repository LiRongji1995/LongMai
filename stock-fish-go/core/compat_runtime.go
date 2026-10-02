package core

import (
	contracts "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
)

type Budget = resources.Budget
type Metrics = contracts.Metrics

var NewBudget = resources.NewBudget
var ReadBounded = resources.ReadBounded
var currentRSS = resources.CurrentRSS

const MaxSourceBytes = resources.MaxSourceBytes
const MaxOutputBytes = resources.MaxOutputBytes
const MaxRSS = resources.MaxRSS
