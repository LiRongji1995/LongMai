package files

import (
	a "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/research"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	f "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	r "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	r1 "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/r1"
	v2 "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/v2"
)

type Diagnostic = a.Diagnostic
type Budget = ports.ResearchBudget
type StockFacts = f.StockFacts
type Decision = r1.Decision
type V2Decision = v2.V2Decision
type errCode = c.ErrCode

var Hash = c.Hash
var ReadBounded = r.ReadBounded

const MaxOutputBytes = r.MaxOutputBytes
