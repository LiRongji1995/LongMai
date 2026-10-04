package provider

import (
	_ "embed"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

//go:embed daily_refresh_worker.py
var Worker []byte

func WorkerHash() string { return c.Hash(Worker) }
