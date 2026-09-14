package main

import (
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
	if err := contract.DecodeJSONStrict(raw, &rows); err != nil {
		log.Fatal(err)
	}
	facts, err := shadow.BuildFacts(rows)
	if err != nil {
		log.Fatal(err)
	}
	file, err := os.Create(*output)
	if err != nil {
		log.Fatal(err)
	}
	defer file.Close()
	if err := contract.WriteFactsJSONL(file, facts); err != nil {
		log.Fatal(err)
	}
}
