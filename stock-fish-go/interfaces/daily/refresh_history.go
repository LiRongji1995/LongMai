package daily

import (
	"context"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/native"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/provider"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"os"
	"path/filepath"
	"time"
)

func prepareRefreshHistory(ctx context.Context, conf Config, day, dir string, b ports.ResearchBudget) (c.InputRecord, native.RefreshHistoryProof, error) {
	var in c.InputRecord
	var proof native.RefreshHistoryProof
	module, e := os.ReadFile(filepath.Join(filepath.Dir(conf.Worker), "go_history_bridge.py"))
	if e != nil {
		return in, proof, e
	}
	if c.Hash(module) != provider.HistoryBridgeHash() {
		return in, proof, errors.New("GO_HISTORY_BRIDGE_IMPLEMENTATION_IDENTITY_MISMATCH")
	}
	r, e := openBackend(ctx, conf)
	if e != nil {
		return in, proof, e
	}
	defer r.Close()
	reader, ok := r.(interface {
		RefreshBase(context.Context, string, time.Time) (c.InputRecord, error)
		ReviewHistory(context.Context, string, string, ports.ResearchBudget) (*sqlitev2.History, error)
	})
	if !ok {
		return in, proof, errors.New("GO_HISTORY_READ_ONLY_BASE_PORT_REQUIRED")
	}
	in, e = reader.RefreshBase(ctx, day, time.Now().UTC())
	if e != nil {
		return in, proof, e
	}
	f, e := ev.DecodeFrozenContext(ctx, in.Raw)
	if e != nil {
		return in, proof, e
	}
	h, e := reader.ReviewHistory(ctx, f.Input.HistoryRef, f.Input.HistoryHash, b)
	if e != nil {
		return in, proof, e
	}
	proof, e = native.ExportRefreshHistory(in, h, day, dir, b)
	return in, proof, e
}

func recheckRefreshInput(ctx context.Context, conf Config, in c.InputRecord) error {
	r, e := openBackend(ctx, conf)
	if e != nil {
		return e
	}
	defer r.Close()
	reader, ok := r.(interface {
		ReviewInput(context.Context, string) (c.InputRecord, error)
	})
	if !ok {
		return errors.New("GO_HISTORY_READ_ONLY_INPUT_PORT_REQUIRED")
	}
	current, e := reader.ReviewInput(ctx, in.ID)
	if e != nil {
		return e
	}
	if current.RawHash != in.RawHash || c.Hash(current.Raw) != in.RawHash {
		return errors.New("REFRESH_ORIGINAL_INPUT_CHANGED")
	}
	return nil
}
