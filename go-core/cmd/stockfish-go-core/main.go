package main

import (
	"encoding/json"
	"flag"
	"log"
	"os"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/go-core/internal/contract"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/go-core/internal/shadow"
)

func main() {
	input := flag.String("input", "", "canonical resolved source rows JSON")
	output := flag.String("output", "", "output facts JSONL")
	flag.Parse()
	if *input == "" || *output == "" {
		log.Fatal("--input and --output are required")
	}
	raw, err := os.ReadFile(*input)
	if err != nil {
		log.Fatal(err)
	}
	var rows []shadow.SourceRow
	if err := json.Unmarshal(raw, &rows); err != nil {
		log.Fatal(err)
	}
	facts := shadow.BuildFacts(rows)
	file, err := os.Create(*output)
	if err != nil {
		log.Fatal(err)
	}
	defer file.Close()
	if err := contract.WriteFactsJSONL(file, facts); err != nil {
		log.Fatal(err)
	}
}
