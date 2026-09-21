package main

import (
	"encoding/json"
	"flag"
	"log"
	"os"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/go-core/internal/contract"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/go-core/internal/fastpath"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/go-core/internal/shadow"
)

func main() {
	input := flag.String("input", "", "canonical resolved source rows JSON")
	output := flag.String("output", "", "output facts JSONL")
	derivedInput := flag.String("derived-input", "", "daily primary Go fast-path source rows JSON")
	derivedOutput := flag.String("derived-output", "", "daily primary Go fast-path runtime rows JSON")
	flag.Parse()
	if *derivedInput != "" || *derivedOutput != "" {
		if *derivedInput == "" || *derivedOutput == "" || *input != "" || *output != "" {
			log.Fatal("--derived-input and --derived-output must be used together and without --input/--output")
		}
		raw, err := os.ReadFile(*derivedInput)
		if err != nil {
			log.Fatal(err)
		}
		var req fastpath.Request
		if err := contract.DecodeJSONStrict(raw, &req); err != nil {
			log.Fatal(err)
		}
		response, err := fastpath.BuildRuntimeRows(req)
		if err != nil {
			log.Fatal(err)
		}
		raw, err = json.Marshal(response)
		if err != nil {
			log.Fatal(err)
		}
		if err := os.WriteFile(*derivedOutput, raw, 0o644); err != nil {
			log.Fatal(err)
		}
		return
	}
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
