package native

import (
	"context"
	"encoding/json"
	"errors"
	research "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/research"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	evidence "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	facts "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	v2 "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/v2"
	"io"
	"runtime"
	"strings"
	"time"
)

type HistoryFactory func(context.Context, c.Frozen, ports.ResearchBudget) (ports.HistoryReader, func(), error)

// NativeEngine is application composition. The injected history port is the only
// way research obtains historical data; there is no fallback path or subprocess.
type NativeEngine struct {
	History                HistoryFactory
	Cache                  ports.FactsCache
	StorageBackend         string
	DatabaseIdentity       map[string]string
	DatabaseIdentitySource func(context.Context) (map[string]string, error)
	SourceHash             string
	ConfigHashes           map[string]string
	Progress               io.Writer
}

func (e *NativeEngine) Validate(raw []byte) (c.InputRecord, error) {
	f, err := evidence.DecodeFrozen(raw)
	if err != nil {
		return c.InputRecord{}, err
	}
	h := c.Hash(raw)
	return c.InputRecord{ID: "frozen_" + h, Raw: raw, RawHash: h, Observation: f.Input.Observation, Cutoff: f.Cutoff, Coverage: f.Input.Coverage}, nil
}
func (e *NativeEngine) Evaluate(ctx context.Context, in c.InputRecord, r c.ResearchRequest) (c.Run, error) {
	out := c.Run{InputID: in.ID, Strategy: r.Strategy}
	if e.SourceHash == "" || strings.HasPrefix(e.SourceHash, "UNRECORDED") {
		return out, errors.New("BUILD_SOURCE_IDENTITY_REQUIRED_USE_TOOLS_BUILD")
	}
	b := ports.BudgetFromContext(ctx)
	if b == nil {
		return out, errors.New("RESEARCH_RESOURCE_SCOPE_REQUIRED")
	}
	var err error
	if err = b.Read(int64(len(in.Raw))); err != nil {
		return out, err
	}
	if c.Hash(in.Raw) != in.RawHash {
		return out, errors.New("INPUT_BYTES_CORRUPT")
	}
	f, err := evidence.DecodeFrozenContext(b.Ctx(), in.Raw)
	if err != nil {
		return out, err
	}
	historyHash := f.Input.HistoryHash
	manifest := map[string]any{"contract": "STOCKFISH_GO_RUN_V2", "input_id": in.ID, "input_bytes_hash": in.RawHash, "history_hash": f.Input.HistoryHash, "history_ref": f.Input.HistoryRef, "strategy_id": r.Strategy, "go_source_hash": e.SourceHash, "config_hashes": e.ConfigHashes, "schema_version": 2, "history_storage_backend": e.StorageBackend, "conversion_version": evidence.InputContractVersion, "observation_date": f.Input.Observation, "cutoff": f.Cutoff, "budget_seconds": r.BudgetSeconds, "source_bytes_limit": resources.MaxSourceBytes, "rss_bytes_limit": resources.MaxRSS, "product_role": "RESEARCH_ONLY", "production_promotion": "DISABLED", "market_edge": "NOT_ESTABLISHED", "parameter_validation": "尚未验证"}
	manifest["selected_evidence_binding_contract"] = map[string]string{"version": "GO_SELECTED_EVIDENCE_BINDING_V1", "projection": "same index in fact.selected_evidence_hashes", "HISTORY_ROW_parent": "manifest.history_hash", "HISTORY_ROW_locator": "raw_sha256; if raw_sha256_is_projection=true use corresponding projection digest (exact equality checked)", "FROZEN_INPUT_parent": "manifest.input_bytes_hash", "FROZEN_INPUT_locator": "JSON pointer in original frozen input"}
	identity := e.DatabaseIdentity
	if e.DatabaseIdentitySource != nil {
		identity, err = e.DatabaseIdentitySource(b.Ctx())
		if err != nil {
			return out, err
		}
	}
	manifest["database_identity"] = identity
	manifest["frozen_envelope_contract"] = evidence.FrozenEnvelopeContractVersion
	manifest["go_version"] = runtime.Version()
	manifest["go_os"] = runtime.GOOS
	manifest["go_arch"] = runtime.GOARCH
	out.Manifest, err = json.Marshal(manifest)
	if err != nil {
		return out, err
	}
	var history ports.HistoryReader
	if f.Input.HistoryRef != "" {
		if e.History == nil {
			return out, errors.New("V2_HISTORY_PORT_REQUIRED")
		}
		h, close, err := e.History(b.Ctx(), f, b)
		if err != nil {
			return out, err
		}
		if close != nil {
			defer close()
		}
		history = h
		if _, ok := history.(ports.HistoryIdentityReader); !ok {
			return out, errors.New("AUTHENTICATED_HISTORY_IDENTITIES_REQUIRED")
		}
	}
	d, err := research.AnalyzeStrategyFrozen(f, in.RawHash, history, b, r.Strategy, e.Cache)
	if err != nil {
		return out, err
	}
	out.ImplementationStatus = d.ImplementationAcceptance
	out.CandidateAvailability = d.CandidateAvailability
	v2 := map[string]v2.V2Decision{}
	for _, decision := range d.V2 {
		v2[decision.InstrumentID] = decision
	}
	d.V2 = nil
	out.Items = make([]c.Item, 0, len(d.Facts))
	for i, fact := range d.Facts {
		if err = b.Check(); err != nil {
			return out, err
		}
		if err = compactEvidence(fact.EvidenceBindings, fact.EvidenceHashes, in.RawHash, historyHash); err != nil {
			return out, err
		}
		var decision any
		if r.Strategy == c.R1 {
			item := d.Decisions[i]
			item.Evidence = nil
			// Market background is one shared object in the saved summary.
			item.Background.Market = facts.MarketContext{}
			decision = item
		} else {
			item := v2[fact.ID]
			item.Evidence = nil
			item.Background.Market = facts.MarketContext{}
			decision = item
		}
		raw, er := json.Marshal(map[string]any{"fact": fact, "decision": decision, "coverage": d.Coverage.Support[fact.ID], "shared_market_ref": "summary.market_context", "selected_evidence_ref": "fact.selected_evidence_hashes"})
		if er != nil {
			return out, er
		}
		if err = b.Expanded(int64(len(raw))); err != nil {
			return out, err
		}
		out.Items = append(out.Items, c.Item{ID: fact.ID, Payload: raw})
		// Release each computed object after encoding so the complete fact graph
		// and serialized run do not coexist in memory.
		d.Facts[i] = facts.StockFacts{}
		if r.Strategy == c.R1 {
			d.Decisions[i] = research.Decision{}
		} else {
			delete(v2, fact.ID)
		}
	}
	d.DetailCount = len(out.Items)
	d.Facts = nil
	d.Decisions = nil
	d.V2 = nil
	d.Coverage.Support = nil
	d.Metrics = b.Snapshot()
	out.Summary, err = json.Marshal(d)
	if err != nil {
		return out, err
	}
	b.Stage("save_and_verify")
	return out, b.Check()
}

func (e *NativeEngine) Start(ctx context.Context, seconds int) (context.Context, func(), error) {
	b, err := resources.NewBudget(ctx, time.Duration(seconds)*time.Second, e.Progress)
	if err != nil {
		return nil, nil, err
	}
	return ports.WithBudget(b.Ctx(), b), b.Close, nil
}
