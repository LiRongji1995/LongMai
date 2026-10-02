package core

import (
	"context"
	"reflect"
	"testing"
	"time"
)

func TestPrecomputedPercentilesExactTiesAndMissing(t *testing.T) {
	v := []Number{N(".1"), N(".1"), N("-.05"), {}, N(".3")}
	p := NewPercentiles(v)
	for _, x := range v {
		if p.Lookup(x).String() != Percentile(x, v).String() {
			t.Fatal("midpoint rank drift", x)
		}
	}
}
func TestSectorAggregateExcludeSelfMissingAndCancellation(t *testing.T) {
	rows := []StockFacts{{ID: "A", Facts: Facts{Returns: map[int]Number{1: N(".2"), 3: N(".2"), 5: N(".2")}}, Recent3: N(".2"), Previous3: N(".1")}, {ID: "B", Facts: Facts{Returns: map[int]Number{1: N(".1"), 3: N(".1"), 5: N(".1")}}, Recent3: N(".1"), Previous3: N(".05")}, {ID: "C", Facts: Facts{Returns: map[int]Number{1: N(".3"), 3: N(".3"), 5: N(".3")}}, Recent3: N(".3"), Previous3: N(".2")}, {ID: "D", Facts: Facts{Returns: map[int]Number{}}}}
	theme := Row{"theme_id": []byte(`"T"`), "member_ids": []byte(`["A","B","C","D"]`)}
	got, e := ComputeSectors(context.Background(), []Row{theme}, rows, map[int]Benchmark{})
	if e != nil {
		t.Fatal(e)
	}
	if got["A"][0].Windows[1].Value.String() != N(".2").String() || got["A"][0].Windows[1].Valid != 2 || got["A"][0].Windows[1].Expected != 3 {
		t.Fatal("peer exclusion/coverage failure", got["A"])
	}
	if got["D"][0].Windows[1].Valid != 3 {
		t.Fatal("missing own must not subtract valid peer")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, e = ComputeSectors(ctx, []Row{theme}, rows, nil); e == nil {
		t.Fatal("cancellation ignored")
	}
}
func TestMarketDivergenceAndCoverage(t *testing.T) {
	w := MarketWidth{Expected: 100, Known: 100, Coverage: N("1"), CloseUp: 30, CloseDown: 3, Broken: 25, Height: N("5")}
	state, reasons := marketRaw(w)
	if state != "DIVERGENCE" || !contains(reasons, "SAM_MARKET_EXPANSION_WITH_MAJOR_DIVERGENCE_PRESSURE") {
		t.Fatal(state, reasons)
	}
	w.Known = 59
	w.Coverage = N(".59")
	if state, _ = marketRaw(w); state != "UNKNOWN" {
		t.Fatal("incomplete evidence was decisive")
	}
}
func TestV2IndependentFromR1Acceleration(t *testing.T) {
	f := Facts{Returns: map[int]Number{1: N(".01"), 3: N(".02"), 5: N(".10"), 10: N(".12")}, RS: map[int]Number{1: N(".01"), 3: N(".01"), 5: N(".01")}, LegacyRS: map[int]Number{5: N(".01")}, Momentum: "RISING", RecentTurnover: N("3")}
	if Acceleration(f).Status != Fail {
		t.Fatal("R1 absolute strength gate removed")
	}
	if v2Acceleration(f).Status != Pass {
		t.Fatal("V2 original high-level OR semantics replaced by R1")
	}
}
func TestResearchPublishAndVerify(t *testing.T) {
	b, _ := NewBudget(context.Background(), time.Minute, nil)
	defer b.Close()
	for _, strategy := range []string{R1StrategyID, V2StrategyID} {
		d := Diagnostic{StrategyID: strategy, RunStatus: "SUCCEEDED", ImplementationAcceptance: "FIXTURE_ONLY", InputBytesHash: "input", HistoryHash: "history", Cutoff: "cutoff", Observation: "observation", Facts: []StockFacts{{ID: "A"}}, Qualification: Qualification{Eligible: []string{"A"}}, Blockers: []string{}}
		if strategy == R1StrategyID {
			d.Decisions = []Decision{{InstrumentID: "A", CandidateStatus: "CORE_PASS"}}
		} else {
			d.V2 = []V2Decision{{InstrumentID: "A", StrategyID: strategy, Status: "FOCUS"}}
		}
		root := t.TempDir()
		m := Manifest{InputHash: "input", HistoryHash: "history", Cutoff: "cutoff", Observation: "observation", StrategyID: strategy}
		if e := SaveResearch(root, d, m, b); e != nil {
			t.Fatal(e)
		}
		c, e := VerifyResearch(root, b)
		if e != nil {
			t.Fatal(e)
		}
		if !c.ResearchComplete || c.ImplementationAcceptance != "FIXTURE_ONLY" {
			t.Fatal(c)
		}
		if _, e = VerifyDiagnostic(root, b); e == nil {
			t.Fatal("new research mislabeled as historical diagnostic")
		}
	}
}
func TestCoverageKnownFailureWithIncompleteUniverse(t *testing.T) {
	d, _ := Decide("A", N("1"), "UNKNOWN", Identify(Facts{}), C(Unknown, ""), C(Unknown, ""), "UNKNOWN", C(Unknown, ""), nil)
	q := Qualification{Eligible: []string{"A"}, Unknown: map[string]string{"B": "UNKNOWN_IDENTITY"}, UniverseComplete: false}
	c := EvaluateCoverage([]Decision{d}, q)
	if c.Selection != "COMPLETE" || c.Universe != "INCOMPLETE" || c.CandidateAvailability != "ABSENT" || c.Evaluation != "NOT_EVALUABLE_AS_COMPLETE_STRATEGY" {
		t.Fatal(c)
	}
}
func TestSortedMedianExclusion(t *testing.T) {
	v := []Number{N("1"), N("1"), N("2"), N("4"), {}}
	a := summarize(v)
	for _, x := range v {
		other := append([]Number{}, v...)
		for i, n := range other {
			if reflect.DeepEqual(n.String(), x.String()) {
				other = append(other[:i], other[i+1:]...)
				break
			}
		}
		if a.MedianExcluding(x).String() != median(other).String() {
			t.Fatal(x, a.MedianExcluding(x), median(other))
		}
	}
}
