package core

import "context"

// SectorRS reproduces the same-theme peer population and completeness used by
// HLR and pullback. Full sector context labels remain a separate migration task.
func SectorRS(ctx context.Context, themes []Row, resolver *Resolver, facts []StockFacts) (map[string]Number, error) {
	groups := map[string][]Row{}
	for _, t := range themes {
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		if !contains([]string{"EVENT", "INDUSTRY"}, t.Text("theme_type")) {
			continue
		}
		if resolver.Resolve(t, Scope{Session: resolver.Observation, Complete: true}).Allowed {
			groups[t.Text("theme_id")] = append(groups[t.Text("theme_id")], t)
		}
	}
	eligible := map[string]*StockFacts{}
	for i := range facts {
		eligible[facts[i].ID] = &facts[i]
	}
	result := map[string]Number{}
	for _, rows := range groups {
		t, e := SelectRevision(rows)
		if e != nil {
			if e.Error() == "CONFLICTING_REVISION" {
				continue
			}
			return nil, e
		}
		members := map[string]bool{}
		for _, id := range t.Strings("member_ids") {
			if _, ok := eligible[id]; ok {
				members[id] = true
			}
		}
		for id := range members {
			if e := ctx.Err(); e != nil {
				return nil, e
			}
			expected := len(members) - 1
			if expected < 2 {
				continue
			}
			values := []Number{}
			for peer := range members {
				if peer != id {
					values = append(values, eligible[peer].Facts.Returns[1])
				}
			}
			bench := Market(values, expected)
			if bench.Status != "COMPLETE" {
				continue
			}
			rs := eligible[id].Facts.Returns[1].Sub(bench.Value).Mul(N("100")).Rounded(4).Div(N("100"))
			if rs.Known() && (!result[id].Known() || rs.Cmp(result[id]) > 0) {
				result[id] = rs
			}
		}
	}
	return result, nil
}
