package core

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestPythonRawEvidenceDifferential(t *testing.T) {
	root := os.Getenv("STOCKFISH_R1_ORACLE")
	if root == "" {
		t.Skip("explicit generated TEST_FIXTURE oracle required; run tools/oracle.py")
	}
	paths, e := filepath.Glob(filepath.Join(root, "*.expected.json"))
	if e != nil || len(paths) != 5 {
		t.Fatal("expected all five oracle cases", paths, e)
	}
	for _, path := range paths {
		t.Run(filepath.Base(path), func(t *testing.T) {
			var expected struct {
				Coverage string   `json:"coverage_type"`
				Eligible []string `json:"eligible_ids"`
				Universe bool     `json:"universe_complete"`
				Expected map[string]struct {
					Hashes []string `json:"selected_hashes"`

					Candidate string           `json:"candidate_status"`
					Matched   []string         `json:"matched_setups"`
					Common    State            `json:"common"`
					Setups    map[string]State `json:"setups"`
					Returns   map[int]Number   `json:"returns"`
					RS        map[int]Number   `json:"rs"`
					MA20      Number           `json:"ma20"`
					PriorMA20 Number           `json:"prior_ma20"`
					Limit     string           `json:"close_limit_down"`
				}
			}
			raw, e := os.ReadFile(path)
			if e != nil {
				t.Fatal(e)
			}
			if e = json.Unmarshal(raw, &expected); e != nil {
				t.Fatal(e)
			}
			if expected.Coverage != "TEST_FIXTURE" {
				t.Fatal("not fixture")
			}
			input := path[:len(path)-len(".expected.json")] + ".json"
			b, _ := NewBudget(context.Background(), 30*time.Second, nil)
			defer b.Close()
			d, e := Analyze(input, "", b)
			if e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(d.Qualification.Eligible, expected.Eligible) || d.Qualification.UniverseComplete != expected.Universe {
				t.Error("qualification mismatch", d.Qualification)
			}
			if len(d.Decisions) != len(expected.Expected) {
				t.Fatal("row population mismatch")
			}
			for ix, row := range d.Decisions {
				x, ok := expected.Expected[row.InstrumentID]
				if !ok {
					t.Fatal("unexpected security")
				}
				if row.CandidateStatus != x.Candidate || !reflect.DeepEqual(row.Matched, x.Matched) || row.Common.Status != x.Common {
					t.Error(row.InstrumentID, "decision mismatch", row.CandidateStatus, x.Candidate, row.Matched, x.Matched, row.Common.Status, x.Common)
				}
				for s, c := range row.Setups {
					if c.Status != x.Setups[s] {
						t.Error(row.InstrumentID, s, c.Status, x.Setups[s])
					}
				}
				sf := d.Facts[ix]
				if !reflect.DeepEqual(sf.EvidenceHashes, x.Hashes) {
					t.Error(row.InstrumentID, "selected evidence hashes mismatch", len(sf.EvidenceHashes), len(x.Hashes))
				}
				for n, v := range x.Returns {
					if sf.Facts.Returns[n].String() != v.String() {
						t.Error(row.InstrumentID, "return", n, sf.Facts.Returns[n], v)
					}
				}
				for n, v := range x.RS {
					if sf.Facts.RS[n].String() != v.String() {
						t.Error(row.InstrumentID, "rs", n, sf.Facts.RS[n], v)
					}
				}
				if sf.CurrentMA20.String() != x.MA20.String() || sf.PriorMA20.String() != x.PriorMA20.String() || sf.Facts.Path.CloseLimitDown != x.Limit {
					t.Error(row.InstrumentID, "ma/limit mismatch")
				}
			}
		})
	}
}
