# StockFish Go Core Phase 0

R16 creates the Go boundary without moving production Daily Primary execution.

This module validates and writes `FEATURE_EVIDENCE_CONTRACT_V1` JSON/JSONL
snapshots and contains a tiny shadow slice for deterministic mechanical price
facts:

- OHLC pass-through
- one-session comparable return
- MA20
- MA60

The Go code does not read the Python application's SQLite internals, provider
cache layout, history artifact tables or strategy configuration. Future
migration work must enter Python Strategy through the versioned contract.

Run:

```powershell
go test ./...
```
