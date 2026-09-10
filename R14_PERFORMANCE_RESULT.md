# R14 Performance Result

- cold fast total: 174.295s; peak RSS 2864689152 bytes; cache misses 79066
- warm fast total: 53.109s; strategy compute 32.178s; peak RSS 995033088 bytes
- warm cache hit rate: 1.0
- compact run header+summary estimate: 11340320 bytes
- detail compressed estimate: 13402726 bytes
- combined run header+all detail estimate: 24743046 bytes; details are stored separately and loaded on demand
- V2 business hash identical to saved research full: True
- market_edge: `NOT_ESTABLISHED`

## Warm Phase Timings

| sqlite_store_init | market_input_load_and_bind | config_load | derived_cache_store_init | strategy_compute | output_size_stream | compact_size_estimate | total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.002022 | 16.035248 | 0.003414 | 0.0014 | 32.178166 | 2.794301 | 2.093991 | 53.108541 |
