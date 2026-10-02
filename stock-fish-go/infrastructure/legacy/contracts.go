package legacy

import (
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
)

type Row = c.Row
type HistoryManifest = c.HistoryManifest
type Partition = c.Partition
type Budget = ports.ResearchBudget
type errCode = c.ErrCode

var Hash = c.Hash
var Canonical = c.Canonical
var CanonicalContext = c.CanonicalContext
var contains = c.Contains
var isNull = ev.IsNull
var validHash = ev.ValidHash
var ValidateEvidenceRow = ev.ValidateEvidenceRow
var DecodeEvidenceRow = ev.DecodeEvidenceRow
