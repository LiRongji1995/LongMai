package facts

import (
	"fmt"
	"testing"
)

var benchmarkNumber Number

func BenchmarkPopulationPercentiles(b *testing.B) {
	values := make([]Number, 3500)
	for i := range values {
		values[i] = N(fmt.Sprint(i % 317)).Div(N("10000"))
	}
	b.Run("legacy_per_security_scan", func(b *testing.B) {
		for n := 0; n < b.N; n++ {
			for _, v := range values {
				benchmarkNumber = Percentile(v, values)
			}
		}
	})
	b.Run("prepared_midpoint_table", func(b *testing.B) {
		for n := 0; n < b.N; n++ {
			ranks := NewPercentiles(values)
			for _, v := range values {
				benchmarkNumber = ranks.Lookup(v)
			}
		}
	})
}
func BenchmarkThemePeerMeans(b *testing.B) {
	values := make([]Number, 500)
	for i := range values {
		values[i] = N(fmt.Sprint(i % 91)).Div(N("10000"))
	}
	b.Run("legacy_peer_rescan", func(b *testing.B) {
		for n := 0; n < b.N; n++ {
			for i := range values {
				peers := make([]Number, 0, len(values)-1)
				for j, v := range values {
					if i != j {
						peers = append(peers, v)
					}
				}
				benchmarkNumber = Market(peers, len(peers)).Value
			}
		}
	})
	b.Run("shared_sum_excluding_self", func(b *testing.B) {
		for n := 0; n < b.N; n++ {
			sum := Summarize(values)
			for _, v := range values {
				benchmarkNumber = sum.excluding(v, len(values)-1).Value
			}
		}
	})
}
