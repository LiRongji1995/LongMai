package facts

import (
	"context"
	"encoding/json"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"sort"
	"strings"
)

type FundsContext struct {
	ThemeID   string         `json:"theme_id"`
	Condition Condition      `json:"funds_condition"`
	Metrics   map[string]any `json:"funds_metrics"`
}

func fieldNumber(r Row, key string) Number {
	var n Number
	if raw := r[key]; raw != nil {
		_ = json.Unmarshal(raw, &n)
	}
	return n
}
func firstText(r Row, keys ...string) string {
	for _, k := range keys {
		if v := r.Text(k); v != "" {
			return v
		}
	}
	return ""
}
func fundSession(r Row) string { return firstText(r, "session", "trade_date", "event_session") }
func fundWindow(r Row) (string, string) {
	day := fundSession(r)
	a, z := firstText(r, "window_start", "start_session"), firstText(r, "window_end", "end_session")
	if a == "" {
		a = day
	}
	if z == "" {
		z = day
	}
	return a, z
}
func fundClass(r Row) string {
	layer := strings.ToUpper(firstText(r, "source_layer", "evidence_layer"))
	c := strings.ToUpper(r.Text("classification"))
	if contains([]string{"CAPITAL_PROXY", "LARGE_ORDER_PROXY", "MAIN_NET_FLOW_PROXY"}, layer) {
		return "PROXY"
	}
	if contains([]string{"PUBLIC_EXCHANGE_LHB", "EXCHANGE_TRADING_INFO", "SUPPLIER_CLASSIFIED_ACTIVE_SEAT", "SUPPLIER_CLASSIFIED_SEAT", "CLASSIFIED_ACTIVE_MONEY_SEAT"}, layer) || (strings.Contains(c, "ACTIVE") && (strings.Contains(c, "SEAT") || strings.Contains(c, "HOT_MONEY"))) {
		return "CLASSIFIED"
	}
	return "OTHER"
}
func fundKey(r Row) string {
	if s := r.Text("dedupe_key"); s != "" {
		return s
	}
	a, z := fundWindow(r)
	return strings.Join([]string{r.Text("instrument_id"), r.Text("theme_id"), firstText(r, "seat_id", "seat_name", "source_id"), fundSession(r), a, z, r.Text("source_layer")}, "\x00")
}
func fundFingerprint(r Row) string {
	m := map[string]any{}
	for _, k := range []string{"instrument_id", "theme_id", "seat_id", "seat_name", "source_id", "classification"} {
		m[k] = r.Text(k)
	}
	m["session"] = fundSession(r)
	m["window_start"], m["window_end"] = fundWindow(r)
	m["source_layer"] = firstText(r, "source_layer", "evidence_layer")
	for _, k := range []string{"buy_amount_cny", "sell_amount_cny", "net_amount_cny"} {
		m[k] = fundMoneyIdentity(r, k)
	}
	refs := r.Strings("source_refs")
	sort.Strings(refs)
	m["source_refs"] = refs
	raw, _ := json.Marshal(m)
	return Hash(raw)
}
func FundForTheme(ctx context.Context, iid string, theme Row, funds []Row, eligible map[string]bool, resolver *Resolver, sessions []string) (FundsContext, error) {
	batch, err := NewBackgroundBatch(ctx, []Row{theme}, funds, eligible, resolver, sessions)
	if err != nil {
		return FundsContext{}, err
	}
	return batch.FundForTheme(ctx, iid, batch.themes[0])
}

func (batch *BackgroundBatch) FundForTheme(ctx context.Context, iid string, theme *ThemeEvidence) (FundsContext, error) {
	id := theme.ID
	out := FundsContext{ThemeID: id, Condition: C(Unknown, "SAM_FUNDS_DATA_UNAVAILABLE"), Metrics: map[string]any{}}
	grade := func(c Condition, g string) FundsContext {
		c.Details = map[string]any{"theme_id": id, "grade": g}
		out.Condition = c
		return out
	}
	if err := ctx.Err(); err != nil {
		return out, err
	}
	if !batch.hasFunds {
		return grade(out.Condition, "UNKNOWN"), nil
	}
	lookback := batch.lookback
	classified := map[string]*fundEvidence{}
	fingerprints := map[string]string{}
	rejected := []EvidenceRejection{}
	proxy := 0
	complete := []Row{}
	completeRejected := []EvidenceRejection{}
	for _, r := range batch.fundsByTheme[id] {
		if err := ctx.Err(); err != nil {
			return out, err
		}
		if r.marker {
			reason := r.completenessReason
			if reason == "" && theme.completenessScopeRejected[r] {
				reason = "SAM_FUNDS_COMPLETENESS_EXPECTED_SCOPE_MISMATCH"
			}
			if reason != "" {
				completeRejected = append(completeRejected, EvidenceRejection{"FUNDS", id, []string{reason}})
			} else {
				complete = append(complete, r.row)
			}
		}
		if r.instrument == iid || !theme.eligibleMembers[r.instrument] {
			continue
		}
		if r.reason != "" {
			rejected = append(rejected, EvidenceRejection{"FUNDS", r.instrument, []string{r.reason}})
			continue
		}
		switch r.class {
		case "PROXY":
			proxy++
		case "CLASSIFIED":
			if old, ok := fingerprints[r.key]; ok && old != r.fingerprint {
				out.Metrics = map[string]any{"classified_events": 0, "proxy_events": proxy, "rejected_events": len(rejected)}
				return grade(C(Unknown, "SAM_FUNDS_DISCLOSURE_KEY_CONFLICT"), "UNKNOWN"), nil
			}
			fingerprints[r.key] = r.fingerprint
			classified[r.key] = r
		}
	}
	out.Metrics = map[string]any{"theme_id": id, "classified_events": len(classified), "proxy_events": proxy, "rejected_events": len(rejected), "rejected_funds": rejected, "completeness_rejected": completeRejected}
	if len(classified) == 0 {
		if len(complete) > 0 && proxy == 0 && len(rejected) == 0 {
			out.Metrics["completeness_status"] = "COMPLETE_NO_RECORD"
			out.Metrics["completeness_evidence"] = complete
			return grade(C(Fail, "SAM_FUNDS_COMPLETE_NO_RECORD"), "WEAK"), nil
		}
		reason := "SAM_FUNDS_NOT_CAPTURED_OR_COMPLETENESS_UNPROVEN"
		if proxy > 0 {
			reason = "SAM_FUNDS_PROXY_ONLY_PENDING_VERIFICATION"
		} else if len(rejected)+len(completeRejected) > 0 {
			reason = "SAM_FUNDS_EVIDENCE_REJECTED_OR_UNVERIFIED"
		}
		out.Metrics["completeness_status"] = "UNKNOWN"
		return grade(C(Unknown, reason), "UNKNOWN"), nil
	}

	type interval struct{ a, z, key string }
	holders := map[string][]interval{}
	for key, r := range classified {
		a, z := r.start, r.end
		h := r.instrument + "\x00" + r.seat
		holders[h] = append(holders[h], interval{a, z, key})
	}
	for _, ranges := range holders {
		sort.Slice(ranges, func(i, j int) bool {
			if ranges[i].a != ranges[j].a {
				return ranges[i].a < ranges[j].a
			}
			return ranges[i].z < ranges[j].z
		})
		for i := 1; i < len(ranges); i++ {
			if ranges[i-1].z >= ranges[i].a {
				out.Metrics["overlapping_window_risk"] = true
				return grade(C(Unknown, "SAM_FUNDS_OVERLAPPING_CLASSIFIED_WINDOWS"), "UNKNOWN"), nil
			}
		}
	}
	buy, sell, net := N("0"), N("0"), N("0")
	instruments, seats := map[string]bool{}, map[string]bool{}
	last := ""
	evidence := []Row{}
	keys := []string{}
	for key := range classified {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		r := classified[key]
		b, s, n := r.buy, r.sell, r.net
		if !n.Known() {
			n = b.Sub(s)
		}
		if !b.Known() || !n.Known() {
			return grade(C(Unknown, "SAM_FUNDS_AMOUNT_UNAVAILABLE"), "UNKNOWN"), nil
		}
		if !s.Known() {
			s = N("0")
		}
		buy = buy.Add(b)
		sell = sell.Add(s)
		net = net.Add(n)
		instruments[r.instrument] = true
		seat := r.seat
		if seat != "" {
			seats[seat] = true
		}
		if r.session > last {
			last = r.session
		}
		evidence = append(evidence, r.row)
	}
	days := -1
	for i, day := range lookback {
		if day == last {
			days = len(lookback) - 1 - i
		}
	}
	g := "WEAK"
	if len(instruments) >= 2 && len(seats) >= 2 && buy.Cmp(N("100000000")) >= 0 && net.Positive() && days >= 0 && days <= 3 {
		g = "STRONG"
	} else if len(instruments) >= 1 && len(seats) >= 1 && buy.Cmp(N("20000000")) >= 0 && net.Positive() && days >= 0 && days <= 5 {
		g = "MODERATE"
	}
	for k, v := range map[string]any{"active_instrument_count": len(instruments), "active_seat_count": len(seats), "dedup_buy_amount_cny": buy, "dedup_sell_amount_cny": sell, "dedup_net_amount_cny": net, "days_since_last_activity": days, "overlapping_window_risk": false, "evidence": evidence, "grade": g} {
		out.Metrics[k] = v
	}
	if g == "WEAK" {
		return grade(C(Fail, "SAM_FUNDS_GRADE_WEAK"), g), nil
	}
	return grade(C(Pass, ""), g), nil
}

func fundMoneyIdentity(r Row, key string) string {
	raw := r[key]
	if raw == nil || string(raw) == "null" {
		return "None"
	}
	s := string(raw)
	if len(s) > 0 && s[0] == '"' {
		if e := json.Unmarshal(raw, &s); e != nil {
			return "None"
		}
	}
	if _, e := parseDecimal(s); e != nil {
		return "None"
	}
	return ev.DecimalProjection(s)
}
