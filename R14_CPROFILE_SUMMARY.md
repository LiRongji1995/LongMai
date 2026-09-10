# R14 cProfile Summary

cProfile includes sampler thread sleep/join overhead; remaining hotspots below exclude that overhead where noted.

## Remaining Hotspots

| rank | component | status | evidence |
| --- | --- | --- | --- |
| 1 | V2 market limit status percentile ranks | MEASURED | cProfile: _limit_market_status / percentile_rank_midpoint dominate profiled strategy compute after sampler overhead is ignored. |
| 2 | MarketInput load + history artifact bind | MEASURED | warm phase market_input_load_and_bind=16.035s |
| 3 | Stage-Aware strategy compute | MEASURED | warm strategy_compute=32.178s |
| 4 | Derived feature cache lookup/deserialization | MEASURED | lookup=3.060s; hit_rate=1.0 |
| 5 | Output serialization and compact estimate | MEASURED | output_size_stream=2.794s; compact_size_estimate=2.094s |

## Raw pstats Top Cumulative

```text
Thu Sep 10 10:13:48 2026    R14_FAST_WARM.cprofile

         673783047 function calls (565144330 primitive calls) in 164.268 seconds

   Ordered by: cumulative time
   List reduced from 1332 to 40 due to restriction <40>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
     2433   22.711    0.009  248.318    0.102 {built-in method time.sleep}
        1    0.000    0.000  168.051  168.051 benchmark_daily_v2.py:130(_measure)
        1    0.000    0.000  147.346  147.346 threading.py:1117(join)
        1    0.000    0.000  147.346  147.346 threading.py:1155(_wait_for_tstate_lock)
      8/6    0.000    0.000  147.346   24.558 {method 'acquire' of '_thread.lock' objects}
        1    0.000    0.000  147.346  147.346 threading.py:1018(_bootstrap)
        1    0.027    0.027   98.812   98.812 strategy_experiments.py:672(run_active_daily_strategy)
        1    0.010    0.010   92.408   92.408 stage_aware_momentum.py:2645(run_stage_aware_momentum)
     3041    0.037    0.000   55.178    0.018 stage_aware_momentum.py:1067(_limit_market_status)
     6082    0.008    0.000   55.127    0.009 stage_aware_momentum.py:92(percentile_rank_midpoint)
     6082    0.106    0.000   55.069    0.009 strategy_experiments.py:87(percentile_rank_midpoint)
     6082    1.750    0.000   54.969    0.009 strategy_experiments.py:82(_percentile_rank_decimal)
    65643    4.243    0.000   51.701    0.001 {built-in method builtins.sum}
 25023960    3.432    0.000   32.622    0.000 encoder.py:414(_iterencode)
130044852/25023958   20.449    0.000   29.057    0.000 encoder.py:334(_iterencode_dict)
        1    0.003    0.003   27.488   27.488 threading.py:1058(_bootstrap_inner)
 18707937    3.133    0.000   25.295    0.000 fractions.py:955(__lt__)
28587990/25011852    3.951    0.000   22.601    0.000 encoder.py:278(_iterencode_list)
 18995268   10.050    0.000   22.598    0.000 fractions.py:933(_richcmp)
 93667899   12.055    0.000   21.494    0.000 {built-in method builtins.isinstance}
        1    0.000    0.000   20.920   20.920 benchmark_daily_v2.py:91(_load_bundle)
        1    0.070    0.070   20.883   20.883 stage_aware_momentum.py:2386(_evaluate_rows)
        2    3.791    1.896   19.983    9.992 history_artifacts.py:32(digest_json_stream)
        1    0.003    0.003   19.907   19.907 history_artifacts.py:262(bind_history)
 18501444    3.313    0.000   17.852    0.000 strategy_experiments.py:85(<genexpr>)
 18548801    5.675    0.000   14.586    0.000 fractions.py:912(__eq__)
        1    0.027    0.027   12.284   12.284 stage_aware_momentum.py:88(_preload_bars)
  1948596    1.654    0.000   10.299    0.000 __init__.py:183(dumps)
 38941778    5.155    0.000    9.419    0.000 <frozen abc>:117(__instancecheck__)
  1948596    1.352    0.000    8.283    0.000 encoder.py:183(encode)
        1    0.096    0.096    7.916    7.916 strategy_experiments.py:199(_preload_bars)
    59339    0.220    0.000    7.411    0.000 history_artifacts.py:352(resolve)
  1677790    0.734    0.000    6.952    0.000 history_artifacts.py:26(_json)
     3041    0.048    0.000    6.900    0.002 stage_aware_momentum.py:2054(_stages)
  1948543    6.335    0.000    6.335    0.000 encoder.py:205(iterencode)
    59339    0.980    0.000    5.693    0.000 visibility.py:110(resolve)
        1    0.038    0.038    5.416    5.416 derived_feature_store.py:483(ensure_stage_aware_derived_features)
        1    0.223    0.223    5.358    5.358 derived_feature_store.py:177(get_many)
        1    0.031    0.031    4.794    4.794 benchmark_daily_v2.py:103(_compact_daily_primary_size)
 38941778    4.265    0.000    4.265    0.000 {built-in method _abc._abc_instancecheck}



```
