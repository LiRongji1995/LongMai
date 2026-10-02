package shared

import "sort"

func ReasonSet(c ...Condition) []string {
	seen := map[string]bool{}
	for _, x := range c {
		if x.Status != Pass {
			for _, r := range x.Reasons {
				seen[r] = true
			}
		}
	}
	out := []string{}
	for r := range seen {
		out = append(out, r)
	}
	sort.Strings(out)
	return out
}
