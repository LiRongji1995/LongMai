// Isolated TEST_FIXTURE preparation, never imported by the product executable.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"os"
	"path/filepath"
)

func main() {
	if len(os.Args) != 3 || !stringsContains(filepath.Base(os.Args[1]), "TEST_FIXTURE") {
		panic("TEST_FIXTURE_NEW_DB_AND_INPUT_REQUIRED")
	}
	if _, e := os.Stat(os.Args[1]); e == nil {
		panic("NEW_DB_REQUIRED")
	}
	ctx := context.Background()
	s, e := sqlitev2.Open(ctx, os.Args[1], true)
	must(e)
	defer s.Close()
	_, e = s.DB().Exec("INSERT INTO migration_state VALUES(1,'TEST_FIXTURE','PUBLISHED','{}','TEST_FIXTURE')")
	must(e)
	raw, e := os.ReadFile(os.Args[2])
	must(e)
	var v c.Frozen
	must(json.Unmarshal(raw, &v))
	in := c.InputRecord{ID: "frozen_" + c.Hash(raw), Raw: raw, RawHash: c.Hash(raw), Observation: v.Input.Observation, Cutoff: v.Cutoff, Coverage: v.Input.Coverage}
	if in.Coverage != "TEST_FIXTURE" {
		panic("NON_FIXTURE_INPUT_REFUSED")
	}
	must(sqlitev2.NewAppRepository(s).PutInput(ctx, in))
	fmt.Println(in.ID)
}
func must(e error) {
	if e != nil {
		panic(e)
	}
}
func stringsContains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
