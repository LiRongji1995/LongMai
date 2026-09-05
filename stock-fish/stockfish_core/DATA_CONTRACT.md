# Trusted data boundary

`data.py` imports no Flask, provider SDK, Qlib runtime or network client. All timestamps are timezone aware. Canonical identities are `000001.SZ`, `600519.SH`, `920xxx.BJ`; a bare six-digit string requires an explicit exchange. Unknown facts remain missing or raise `UnavailableError(reason, component="data")`.

## Stable API

| API | Contract |
|---|---|
| `CanonicalMarketBar(...)` | Frozen OHLC, volume in **shares**, turnover in **CNY**, explicit adjustment, provider, market time, source time, ingestion time, quality, missing fields and snapshot ID. No missing OHLC or volume synthesis. |
| `normalize_market_bar(volume_unit=..., turnover_unit=..., lot_size=..., **payload)` | Converts only explicit units. A lot conversion requires an explicit lot size. |
| `bar.require_raw_price()` | Only VERIFIED, RAW, non-mock, known-provider close; adjusted and partial values raise `RAW_PRICE_UNAVAILABLE`. |
| `bar.assert_visible(as_of)` | Rejects data actually ingested after the decision instant. |
| `SecurityMaster(states).resolve(id, session_date, known_at)` | Historical effective interval and availability time both apply. Future revisions, unknown and ambiguous states are unavailable. |
| `TradingCalendar(sessions, coverage_start, coverage_end)` | Finite explicit sessions; outside coverage is unavailable. `earliest_trade_at(decision)` returns the next eligible 09:30 Asia/Shanghai opening; intraday decisions conservatively use the next opening. |
| `TradabilityService(calendar, securities).evaluate(id, decision_at, trade_date, "BUY"/"SELL", bar)` | Visible raw price and contemporaneously known listing, board, ST, suspension, industry, lot and per-date price limit checks. Current reference eligibility is not a future fill guarantee. |
| `TradabilityService.evaluate_execution(id, execution_at, side, raw_execution_price)` | Shared execution check using a separately evidenced raw fill price and only security facts already known at execution; never backdates end-of-day OHLC/status. |
| `PITFundamentals(records).visible(id, as_of)` | Requires verified PIT metadata; both publication and actual availability must precede decision. Returns highest visible revision for each reporting period; conflicting revisions fail. |
| `LocalQlibDataAdapter(path).manifest()` | Stable SHA256 digest of canonical JSON list of `{path,size,sha256}`, sorted by POSIX relative path, for calendars/instruments/features only. `snapshot_id == digest`; observation time does not change the digest. Rejects symlinks and files changed during hashing. |
| `LocalQlibDataAdapter(path).snapshot(as_of, instruments)` | Local Qlib prices are `QLIB_NORMALIZED / RESEARCH_ONLY`. Original raw prices, raw volume, turnover, original publication time and security facts are unavailable. Never “undoes” an unknown normalization using guessed factors. |
| `provider_classification(name)` | Separate entitlement inventory; all current external sources are REVIEW_REQUIRED, explicit fixtures/mock and unsafe relays NOT_FOR_PRODUCTION. VERIFIED data quality is not a commercial license. |

`SecurityState.effective_to` is exclusive. `min_buy_shares` differs from `lot_size` (the share increment); unsupported boards with unknown initial purchase size cannot pass BUY checks. The supplied live adapter only establishes main-board stock terms. It never assumes all A-shares have 100-share minimums or identical price limits.

## Real observation adapter

```python
from datetime import date
from stockfish_core.observed_data import (
    BaostockRawDataAdapter, EastmoneyDailyLimitAdapter,
    attach_observed_limits, exchange_calendar_2026,
)
from stockfish_core.data import SecurityMaster, TradabilityService

day = date(2026, 9, 4)
symbols = ["000001.SZ", "600519.SH"]
observed = BaostockRawDataAdapter().fetch(day, symbols, timeout=45)
limits = [EastmoneyDailyLimitAdapter().fetch(day, identity) for identity in symbols]
observed = attach_observed_limits(observed, limits)
service = TradabilityService(exchange_calendar_2026(), SecurityMaster(observed.security_states))
result = service.evaluate(symbols[0], observed.observed_at, day, "BUY", observed.bars[0])
```

Fetches are **explicit**, never constructor/import/startup side effects. Baostock runs in a short-lived subprocess with a hard deadline and a 10-symbol limit, unadjusted daily request `adjustflag=3`. No paid endpoint, credential store, order or messaging API is involved.

The bar market timestamp is the daily session close. Its `source_timestamp` is explicitly labelled **response receipt** because original vendor publication time is unknown; `available_at` is actual ingestion, never the historical bar date. Historical backtests cannot use observations captured today as if known years ago. Financial PIT remains unavailable until independently verified historical publication/revision data is supplied.

Eastmoney returns the **actual** f51/f52 daily limit values (`fltt=2`, yuan); source epoch f86 must match the requested market day, and its close must agree with the raw Baostock close within half a cent. Missing limits, date discrepancies and price discrepancies remain unavailable. No price-limit percentage or future-session exception status is guessed. The optional `DailyLimitEvidence` path requires independent traceable proof that a normal main-board rule applies; it is not needed by the native-limit adapter.

`MarketObservation` preserves public source response JSON, its SHA256 and actual receipt time under `source_payloads`. The joined snapshot versions both raw prices and limit evidence. The CLI writes exclusively to a new output path:

```powershell
.venv/Scripts/python.exe -m stockfish_core.observed_data --date 2026-09-04 --symbols 000001.SZ 600519.SH --with-observed-limits --output state/observations/new-snapshot.json
```

Friday states end at Saturday 00:00. A Saturday recommendation can assess Friday observed reference risk and state that Monday 09:30 is the **earliest conditional entry**. Monday suspension, price limits, order terms and sellable positions must be rechecked at execution. The adapter adds `EXECUTION_RECHECK_REQUIRED` and retains commercial status `REVIEW_REQUIRED`.

## Calendar and rule sources

The SH/SZ schedule covers only 2026. It combines the exchange weekday rule with explicit holiday closures; government weekend make-up workdays remain closed. Outside coverage or a missing next session fails. Emergency closures require a new version. Primary sources verified **2026-09-05**:

- [SSE 2026 holiday notice, 2025-12-22](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml).
- [SZSE 2026 holiday notice, 2025-12-22](https://investor.szse.cn/disclosure/notice/general/t20251222_618087.html).
- [SSE trading rules effective 2026-07-06](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml). Main-board ST limits changed to 10%; a permanent ST=5% assumption is invalid. The default research policy excludes ST buys.
- [SZSE 2026 trading rules](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf), sections 2.3, 3.3.8 and 3.3.13–16.
- [AKShare maintained Eastmoney bid/ask mapping](https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_ask_bid_em.py) maps f51/f52 to actual upper/lower limits; [official API documentation](https://akshare.akfamily.xyz/data/stock/stock.html) records history source units.
- [Baostock adjustment methodology](https://www.baostock.com/helpdocs/pdf/BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf) distinguishes raw preclose and adjusted history. Vendor API HTML documentation was not fully retrievable during verification; actual SDK responses and installed vendor SDK source were additionally checked.

## Safe Qlib updates

`qlib-zh/data_runner.py:run_data_update(..., expected_sha256=None)` requires either GitHub's asset SHA256 or an explicitly pinned caller SHA256. It downloads with byte/time bounds, validates the entire archive before extraction, rejects path traversal, links, duplicates and oversized expansion, checks calendar/universe/features, then publishes a content-addressed `.snapshots/<digest>` directory.

The active `QLIB_DATA_DIR` is never emptied, partially replaced or overwritten. Successful result includes `activation_required=true`, new `data_dir`, `data_version` and `archive_sha256`. Explicitly change `QLIB_DATA_DIR` for a future process/job to activate; retaining the old path supports rollback. Quant snapshots pin their own manifests before inference. Missing required files or invalid float32 binary offsets are failures, not successful partial updates.

## Legacy paths

Default backend is lazy `advanced`; `mock` requires explicit selection. Real fetch/init failure never falls through to mock. Facade quotes retain source/quality rather than zero-fill missing fields. Legacy non-PIT/mixed-period financial summaries and inconsistent dividend-basis merging are disabled. All sxsc relay/token-store entry points are disabled; Tushare uses only official HTTPS, in-memory credentials and no startup query. Process-wide Eastmoney HTTP patching is disabled.

CN Eastmoney history volumes convert lots to shares; Sina/Tencent SDK share outputs are tagged before normalization. A verified installed-SDK bug exempts `sz000*` Tencent stocks from the conversion even though live raw data is lots, so that specific legacy history branch is explicitly unavailable pending a corrected SDK contract. Generic cleaning rejects missing OHLC, nonfinite/negative data, duplicate dates and corrupt envelopes. Missing moving-volume baseline stays missing. These legacy readouts remain RESEARCH_ONLY and do not constitute the canonical recommendation authority.
