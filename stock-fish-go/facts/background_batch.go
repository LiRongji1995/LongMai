package facts

import (
	"context"
	"sort"
	"strings"
)

// BackgroundBatch is built once for an immutable, validated input. Index order
// follows the source rows so disclosure conflicts retain their original order.
// Raw rows are retained only for the returned evidence, never reparsed per stock.
type BackgroundBatch struct {
	themes       []*ThemeEvidence
	byInstrument map[string][]*ThemeEvidence
	fundsByTheme map[string][]*fundEvidence
	hasFunds     bool
	lookback     []string
}

type ThemeEvidence struct {
	ID, Name, Type, Hash      string
	Sources                   []string
	members                   []string
	eligibleMembers           map[string]bool
	completenessScopeRejected map[*fundEvidence]bool
}

type fundEvidence struct {
	row                                   Row
	instrument, session, start, end, seat string
	class, key, fingerprint               string
	buy, sell, net                        Number
	marker                                bool
	expectedScope                         map[string]bool
	reason, completenessReason            string
}

func stringSet(values []string) map[string]bool {
	out := make(map[string]bool, len(values))
	for _, value := range values {
		out[value] = true
	}
	return out
}

func decodeTheme(ctx context.Context, row Row, eligible map[string]bool) (*ThemeEvidence, error) {
	t := &ThemeEvidence{ID: row.Text("theme_id"), Name: row.Text("name"), Type: row.Text("theme_type"), Hash: RowHash(row), Sources: row.Strings("source_refs"), eligibleMembers: map[string]bool{}, completenessScopeRejected: map[*fundEvidence]bool{}}
	set := map[string]bool{}
	for _, id := range row.Strings("member_ids") {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		set[id] = true
		if eligible[id] {
			t.eligibleMembers[id] = true
		}
	}
	for id := range set {
		t.members = append(t.members, id)
	}
	sort.Strings(t.members)
	return t, ctx.Err()
}

func NewBackgroundBatch(ctx context.Context, themes, funds []Row, eligible map[string]bool, resolver *Resolver, sessions []string) (*BackgroundBatch, error) {
	b := &BackgroundBatch{byInstrument: map[string][]*ThemeEvidence{}, fundsByTheme: map[string][]*fundEvidence{}, hasFunds: len(funds) > 0}
	start := len(sessions) - 5
	if start < 0 {
		start = 0
	}
	b.lookback = append([]string(nil), sessions[start:]...)
	calendar, lookback := stringSet(sessions), stringSet(b.lookback)
	visibleIDs := map[string]bool{}
	for _, row := range themes {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		t, err := decodeTheme(ctx, row, eligible)
		if err != nil {
			return nil, err
		}
		b.themes = append(b.themes, t)
		visibleIDs[t.ID] = true
		for _, id := range t.members {
			b.byInstrument[id] = append(b.byInstrument[id], t)
		}
	}
	for _, row := range funds {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		id := row.Text("theme_id")
		if !visibleIDs[id] {
			continue
		}
		f := &fundEvidence{row: row, instrument: row.Text("instrument_id"), session: fundSession(row), seat: firstText(row, "seat_id", "seat_name", "source_id"), class: fundClass(row), buy: fieldNumber(row, "buy_amount_cny"), sell: fieldNumber(row, "sell_amount_cny"), net: fieldNumber(row, "net_amount_cny")}
		f.start, f.end = fundWindow(row)
		count := fieldNumber(row, "row_count")
		for _, k := range []string{"record_type", "evidence_type", "completion_status", "provider_status", "query_status", "result_status"} {
			v := strings.ToUpper(row.Text(k))
			if v == "COMPLETE_NO_RECORD" || (v == "COMPLETE" && count.Known() && count.Cmp(N("0")) == 0) {
				f.marker = true
			}
		}
		if f.marker {
			session := f.session
			if session == "" {
				session = resolver.Observation
			}
			proof := resolver.Resolve(row, Scope{Session: session, Complete: true, RequireSession: f.session != ""})
			switch {
			case f.end > resolver.Observation:
				f.completenessReason = "SAM_FUNDS_COMPLETENESS_FUTURE_WINDOW_END"
			case !lookback[session]:
				f.completenessReason = "SAM_FUNDS_COMPLETENESS_SESSION_OUTSIDE_LOOKBACK"
			case !proof.Allowed:
				f.completenessReason = proof.Reason
			}
			scope := row.Strings("expected_scope")
			if len(scope) == 0 {
				scope = row.Strings("expected_instruments")
			}
			if len(scope) == 0 {
				scope = row.Strings("instrument_ids")
			}
			f.expectedScope = stringSet(scope)
		}
		switch {
		case !lookback[f.session]:
			f.reason = "SAM_FUNDS_SESSION_OUTSIDE_LOOKBACK_OR_INVALID"
		case f.start == "" || f.end == "" || f.start > f.end:
			f.reason = "SAM_FUNDS_WINDOW_UNAVAILABLE_OR_INVALID"
		case f.end > resolver.Observation:
			f.reason = "SAM_FUNDS_FUTURE_WINDOW_END"
		case !calendar[f.start] || !calendar[f.end] || f.session < f.start || f.session > f.end:
			f.reason = "SAM_FUNDS_WINDOW_NOT_ALIGNED_TO_EXCHANGE_SESSIONS"
		default:
			proof := resolver.Resolve(row, Scope{Instrument: f.instrument, Session: f.session, RequireSession: true})
			if !proof.Allowed {
				f.reason = proof.Reason
			}
		}
		if f.class == "CLASSIFIED" && f.reason == "" {
			f.key, f.fingerprint = fundKey(row), fundFingerprint(row)
		}
		b.fundsByTheme[id] = append(b.fundsByTheme[id], f)
	}
	for _, theme := range b.themes {
		for _, fund := range b.fundsByTheme[theme.ID] {
			if !fund.marker || fund.completenessReason != "" || len(fund.expectedScope) == 0 {
				continue
			}
			for member := range theme.eligibleMembers {
				if err := ctx.Err(); err != nil {
					return nil, err
				}
				if !fund.expectedScope[member] {
					theme.completenessScopeRejected[fund] = true
					break
				}
			}
		}
	}
	return b, ctx.Err()
}

func (b *BackgroundBatch) ThemesForInstrument(id string) []*ThemeEvidence { return b.byInstrument[id] }
