# R14 cProfile Summary

cProfile includes sampler thread sleep/join overhead; remaining hotspots below exclude that overhead where noted.

## Remaining Hotspots

| rank | component | status | evidence |
| --- | --- | --- | --- |
| 1 | V2 market limit status percentile ranks | MEASURED | cProfile: _limit_market_status / percentile_rank_midpoint dominate profiled strategy compute after sampler overhead is ignored. |
| 2 | MarketInput load + history artifact bind | MEASURED | warm phase market_input_load_and_bind=16.408s |
| 3 | Stage-Aware strategy compute | MEASURED | warm strategy_compute=32.274s |
| 4 | Derived feature cache lookup/deserialization | MEASURED | lookup=3.090s; hit_rate=1.0 |
| 5 | Output serialization, compact estimate, and persistence projection | MEASURED | output_size_stream=2.846s; compact_size_estimate=2.086s; persist_projection=6.139s |

## Raw pstats Top Cumulative

```text
Thu Sep 10 10:39:30 2026    R14_FAST_WARM.cprofile

         697886366 function calls (581671877 primitive calls) in 166.555 seconds

   Ordered by: cumulative time
   List reduced from 1583 to 40 due to restriction <40>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
     2788   27.285    0.010  294.353    0.106 {built-in method time.sleep}
        1    0.000    0.000  170.297  170.297 benchmark_daily_v2.py:166(_measure)
        1    0.000    0.000  169.584  169.584 threading.py:1117(join)
        1    0.000    0.000  169.584  169.584 threading.py:1155(_wait_for_tstate_lock)
     10/8    0.000    0.000  169.584   21.198 {method 'acquire' of '_thread.lock' objects}
        1    0.000    0.000  169.584  169.584 threading.py:1018(_bootstrap)
        1    0.027    0.027   95.916   95.916 strategy_experiments.py:672(run_active_daily_strategy)
        1    0.011    0.011   89.789   89.789 stage_aware_momentum.py:2645(run_stage_aware_momentum)
     3041    0.033    0.000   53.783    0.018 stage_aware_momentum.py:1067(_limit_market_status)
     6082    0.007    0.000   53.736    0.009 stage_aware_momentum.py:92(percentile_rank_midpoint)
     6082    0.080    0.000   53.704    0.009 strategy_experiments.py:87(percentile_rank_midpoint)
     6082    1.700    0.000   53.615    0.009 strategy_experiments.py:82(_percentile_rank_decimal)
        1    0.004    0.004   53.175   53.175 threading.py:1058(_bootstrap_inner)
    65753    4.126    0.000   50.457    0.001 {built-in method builtins.sum}
 27575226    3.689    0.000   34.441    0.000 encoder.py:414(_iterencode)
139960648/27575224   21.434    0.000   30.631    0.000 encoder.py:334(_iterencode_dict)
 18707937    3.161    0.000   24.609    0.000 fractions.py:955(__lt__)
31333880/27551266    4.255    0.000   23.345    0.000 encoder.py:278(_iterencode_list)
 18995268    9.705    0.000   21.877    0.000 fractions.py:933(_richcmp)
95682461/95682448   12.005    0.000   21.191    0.000 {built-in method builtins.isinstance}
        1    0.000    0.000   20.368   20.368 benchmark_daily_v2.py:92(_load_bundle)
        1    0.003    0.003   19.348   19.348 history_artifacts.py:262(bind_history)
 18501444    3.235    0.000   17.475    0.000 strategy_experiments.py:85(<genexpr>)
 18548801    5.537    0.000   14.285    0.000 fractions.py:912(__eq__)
        1    0.027    0.027   11.722   11.722 stage_aware_momentum.py:88(_preload_bars)
  1961270    1.619    0.000   11.236    0.000 __init__.py:183(dumps)
  1961270    1.328    0.000    9.254    0.000 encoder.py:183(encode)
38942224/38942211    5.045    0.000    9.166    0.000 <frozen abc>:117(__instancecheck__)
  1961219    7.088    0.000    7.088    0.000 encoder.py:205(iterencode)
        1    0.003    0.003    7.069    7.069 benchmark_daily_v2.py:131(_persist_daily_primary_result)
    59339    0.205    0.000    7.011    0.000 history_artifacts.py:352(resolve)
  1677790    0.724    0.000    6.767    0.000 history_artifacts.py:26(_json)
        1    0.004    0.004    6.698    6.698 candidate_service.py:813(_write_strategy_result)
     3041    0.046    0.000    6.669    0.002 stage_aware_momentum.py:2054(_stages)
    59339    0.928    0.000    5.422    0.000 visibility.py:110(resolve)
        1    0.214    0.214    5.250    5.250 derived_feature_store.py:177(get_many)
        1    0.030    0.030    4.627    4.627 benchmark_daily_v2.py:104(_compact_daily_primary_size)
        1    0.016    0.016    4.189    4.189 stage_aware_momentum.py:2386(_evaluate_rows)
38942224/38942211    4.123    0.000    4.123    0.000 {built-in method _abc._abc_instancecheck}
 29806815    3.298    0.000    3.298    0.000 {method 'encode' of 'str' objects}



```
