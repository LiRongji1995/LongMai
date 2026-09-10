# R14 Performance Result

- cold fast total: 179.799s; peak RSS 2865180672 bytes; cache misses 79066
- warm fast total: 59.760s; strategy compute 32.274s; peak RSS 992960512 bytes
- warm cache hit rate: 1.0
- compact run header+summary estimate: 11340320 bytes
- detail compressed estimate: 13402726 bytes
- combined run header+all detail estimate: 24743046 bytes; details are stored separately and loaded on demand
- measured temp persistence: 6.139207899919711s; run document bytes 12037566
- V2 business hash identical to saved research full: True
- market_edge: `NOT_ESTABLISHED`

## Warm Phase Timings

| sqlite_store_init | market_input_load_and_bind | config_load | derived_cache_store_init | strategy_compute | output_size_stream | compact_size_estimate | persist_projection | total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.001622 | 16.408483 | 0.002947 | 0.001529 | 32.274212 | 2.845625 | 2.086386 | 6.139208 | 59.760011 |
