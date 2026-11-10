# A-share exchange monitoring of speculative trading in hot stocks: research notes

Compiled 2026-11-10. Scope: SSE/SZSE main board, with ChiNext (创业板) and STAR (科创板) differences noted.
The 3-day ±20% abnormal-volatility (异常波动) announcement rule is mentioned only where other rules build on it.

**Verification legend**
- **[P]**: verified against the primary rule text or the exchange's own publication. For the rule PDFs/DOCX I downloaded and read the full text.
- **[S]**: secondary source (financial media or broker material quoting the rule). Not checked against the primary text.
- **[?]**: uncertain or unverified. Treat with caution.

---

## 0. Rule map and change history (what is current as of 2026-11)

| Rule | Current version | Status / change notes |
|---|---|---|
| 《上海证券交易所主板股票异常交易实时监控细则》 | 上证发〔2023〕39号, published 2023-02-17, effective from the first registration-based main-board IPO (2023-04-10) | [P] SSE rule library shows it as 现行有效. It replaced 《上海证券交易所证券异常交易实时监控细则》, and 上证发〔2018〕62号 was repealed. No newer revision found as of 2026-11. The 2025 URL path (`sselawsrules2025`) reflects only a rule-library migration. https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20250519_10779397.shtml |
| 《上海证券交易所科创板股票异常交易实时监控细则》 | 上证发〔2023〕40号, 2023-02-17 (replaces the 2019 试行 version) | [P] Thresholds are essentially identical to the SSE main board rule (see table). https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20250519_10779400.shtml |
| 《深圳证券交易所主板股票异常交易实时监控细则》 | 2023-02-17 (part of the 30 registration-reform rules) | [P] Full text: http://docs.static.szse.cn/www/lawrules/rule/stock/trade/W020230217570611262391.pdf . No newer version found. |
| 《深圳证券交易所创业板股票异常交易实时监控细则（试行）（2023年修订）》 | 2023 revision. Baidu says 2023-08-25 [S]. A copy is attached to the SZSE notice of 2025-03-27 (关于修改、废止部分业务规则和业务指南的通知), which made a wording change to Art. 24 [P] | https://docs.static.szse.cn/www/lawrules/rule/all/W020250327502095568538.pdf ; notice: https://www.szse.cn/lawrules/rule/all/t20250327_612561.html |
| 《深圳证券交易所交易规则（2026年修订）》 / 《上海证券交易所交易规则（2026年修订）》 | Published 2026-04-24, effective **2026-07-06** | [P] SZSE full text: https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf ; SSE release: https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml . Main changes: main-board risk-warning (ST) price limit 5% → **10%**; post-close fixed-price trading extended to all A-shares and ETFs; SSE fund closing call auction. The abnormal-volatility threshold for ST stocks moved from ±12% to ±20% [S, Jiemian/Sina]. **Severe-abnormal-volatility thresholds unchanged** [P, SZSE 2026 text Art. 5.4.4]. |
| 《上海/深圳证券交易所程序化交易管理实施细则》 | Published 2025-04-03, effective **2025-07-07** | [P] SSE Q&A: http://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20250403_10776805.shtml . Based on the CSRC 《证券市场程序化交易管理规定（试行）》 of 2024-05-15. Indicators trial-run since **April 2024**. |
| 《上海证券交易所纪律处分和监管措施实施办法（2026年修订）》 | 上证发〔2026〕9号, 2026-01-27 | [S, search snippet of SSE page] https://www.sse.com.cn/lawandrules/sselawsrules2025/disciplinary/c/c_20260127_10807093.shtml . Contents not reviewed. |
| Margin (融资保证金) ratio | 80% → **100%** for new contracts, from 2026-01-19 (announced 2026-01-14) | [P, SSE release] https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260114_10805178.shtml . Explicitly a counter-cyclical cooling tool. It had been cut 100% → 80% in Aug 2023. |

**No 2025/2026 revision of the real-time monitoring 细则 was found.** Some 自媒体 posts (e.g. Sohu, "2025年12月12日沪深同步发布异动新规：10天4次同向涨停即停牌") claim one exists. They are not supported by any exchange source and look fabricated **[?]**.

---

## 1. Real-time monitoring rules (异常交易实时监控细则): categories, thresholds, measures

### 1.1 Categories (identical structure on all four boards) [P]
"5 大类 14 种" typical behaviours, plus a catch-all:
1. **虚假申报** (spoofing): orders placed and cancelled to mislead, not to trade.
2. **拉抬打压股价** (ramping/pressing): large, continuous or dense orders, or orders priced far from the last trade, during a marked price move.
3. **维持涨（跌）幅限制价格** (holding the limit price, i.e. 封板): large, continuous or dense orders that keep the price pinned at the limit.
4. **自买自卖 / 互为对手方交易** (wash trades among own, controlled or suspected-related accounts).
5. **严重异常波动股票申报速率异常** (order-rate abnormality in a severe-abnormal-volatility stock).
6. Other violations.

**Aggregation rule** [P, Art. 8]. Ordinary, credit and "suspected-related" (涉嫌关联) accounts are **merged** before thresholds are applied. Relatedness is judged by account-opening info, trading terminal info, trading-behaviour similarity, or source of funds. Splitting orders across accounts does not avoid the thresholds.

**Near-miss rule** [P, Art. 7]. Behaviour that comes close to a threshold and repeats ("接近指标且多次实施") can be classified as abnormal.

### 1.2 Defined quantity terms (附则 Art. 33) [P]

| Term | SSE main / STAR; SZSE main: non-ST stock | SSE main / STAR; SZSE main: risk-warning (ST) stock | SZSE ChiNext |
|---|---|---|---|
| 数量或金额**较大** (large) | ≥30万股 or ≥300万元 | ≥30万股 or ≥100万元 | ≥30万股 or ≥300万元 |
| 数量或金额**巨大** (huge) | ≥100万股 or ≥1000万元 | ≥50万股 or ≥200万元 | ≥100万股 or ≥1000万元 |
| 比例/占比**较高** (high share) | ≥30% | ≥30% | ≥30% |
| **多次** (multiple) | ≥3 times | ≥3 times | "反复多次" = same type ≥3 times in the same day |

### 1.3 Thresholds by behaviour (main board; ChiNext/STAR differences noted) [P]

All conditions in a row must hold **at the same time** ("同时存在下列情形") before the behaviour becomes a 重点监控 (focus-monitoring) case.

| # | Behaviour / phase | Main-board conditions (SSE & SZSE) | Differences |
|---|---|---|---|
| 1a | 虚假申报, opening call auction (Art. 11) | Order price deviates ≥5% from previous close (ST ≥3%); cumulative orders 较大; share of same-side orders ≥30%; cancellations ≥50% of orders; opposite-side orders at crossing prices; virtual opening reference price moves ≥5% (ST ≥3%) | ChiNext/STAR: 5% (no ST variant on ChiNext) |
| 1b | 虚假申报, continuous trading, best-5 levels (Art. 12) | Orders within best 5 levels; after a single order the account's resting orders in best 5 are 巨大 **and** ≥30% of same-side best-5 depth; this happens ≥3 times; cancels ≥50%; plus opposite-direction fills (SSE: "存在反向卖出（买入）成交") | ChiNext: "≥3 times" stated in the article heading; no reverse-fill condition |
| 1c | 虚假申报 **at limit price** (Art. 13). This is the "封单撤单" pattern | **≥2 times** in continuous trading: price at limit; after a single limit-price order the account's resting limit-price orders are 巨大 (≥100万股 / ≥1000万元) **and** ≥30% of all resting orders at that price; after a single cancel, cumulative cancels ≥50% of the account's limit-price orders | Same on all boards |
| 2a | 拉抬打压, opening call (Art. 15) | Fills 较大; ≥30% of auction volume; opening move ≥5% (ST ≥3%); if opening at the limit, the account's limit-price orders ≥10% of all orders at that price | Same structure |
| 2b | 拉抬打压, **any 3-minute window** in continuous trading (Art. 16) | Rising buy-fill prices (or falling sell-fill prices); fills 较大 (≥30万股/300万元); ≥30% of market volume in the window; price move **≥4%** (SSE: SSE 50 constituents ≥2%) | ChiNext/STAR: 4% (STAR: SSE 50 constituents 2%). [?] With a 20% limit, 4% in 3 minutes is easier to reach |
| 2c | 拉抬打压, closing call (Art. 17) | Fills 较大; ≥30% of closing-auction volume; move ≥3% (SSE 50 ≥2%) | Same |
| 2d–f | Ramp then reverse (Arts. 18–20) | Open move ≥2% with large share, then reverse fills ≥10万股 / ≥100万元 before 10:00; or a 3-min ramp ≥2% plus ≥10万股/100万元 reverse within 30 min; or a close ramp ≥2% plus reverse before 10:00 next day | STAR adds post-close fixed-price phase windows |
| 3a | **维持涨（跌）幅限制价格**, continuous (Art. 22). This is the key 封板 rule | Price at limit; after a single limit-price order the account's resting orders at the limit are **巨大 (≥100万股 or ≥1000万元; ST ≥50万股/200万元)** and **≥30%** of all resting orders at that price; this state lasts **≥10 minutes** or until the end of continuous trading; of those resting plus new orders at that price, **<70% get filled** that day | **ChiNext**: only "巨大 + ≥30% + lasting ≥10 minutes" (no 70% fill test), so it is stricter in practice |
| 3b | 维持涨跌停, closing call (Art. 23) | At limit at 14:57; market resting at limit 巨大 at 14:57 and 15:00; new limit orders in the closing call 较大; account share of resting ≥30% | Same |
| 4 | 自买自卖 / 对倒 (Arts. 25–26) | Trades among own/controlled/related accounts ≥10% of day volume, or ≥30% of closing-auction volume | Same |
| 5 | **严重异常波动股票申报速率异常** (Art. 29; ChiNext Art. 26) | Within **10 trading days after** a severe-abnormal-volatility event, a single-direction **buy (or sell) order total in 1 minute that is 巨大**: non-ST **≥1000万元** (ST ≥200万元; SSE/SZSE main also ≥100万股 / 50万股 by the definition) | ChiNext: "金额超过1000万元" (strictly more than 10m) |

**Stricter-application clause** [P, SSE/SZSE main Art. 31; STAR Art. 31; ChiNext Art. 28]. The exchange may cut the thresholds above by **up to 50%** (more "in especially serious cases") and impose heavier measures when the abnormal trading involves:
- repeated or continuous behaviour;
- **severe-abnormal-volatility stocks**, ST, delisting-arrangement (退市整理) stocks, or stocks with delisting risk;
- **"重点监控股票" that the exchange has publicly named in its trading-supervision disclosures**. This links the weekly reports (section 3) to the rule: once a stock is named there, effective thresholds can be halved;
- suspected manipulation, or a prior restriction or sanction.

ChiNext's list adds "abnormal trading together with reverse trading". It does not say "up to 50%".

So for a named hot stock, the effective 封板 threshold can drop to roughly **50万股 / 500万元 and 15% of the resting queue**, and the 1-minute order-rate threshold to **500万元**. This is an inference from the 50% clause **[?]**; the exchange does not publish the actual cut.

### 1.4 Measures (escalation ladder)

| SSE main/STAR (Art. 30) [P] | SZSE main/ChiNext (Art. 30/27) [P] |
|---|---|
| 口头警示 (oral warning, in practice by phone via the broker) | 口头警示 |
| 书面警示 (written warning letter) | 书面警示 |
| 监管谈话 (regulatory talk) | 约见谈话 |
| 将账户列为重点监控账户 | 将证券账户列入重点监控账户 |
| 要求提交合规交易承诺书 | 要求提交合规交易承诺书 |
| **暂停投资者账户交易** (suspension, typically intraday / same day) | **盘中暂停当日交易** |
| **限制投资者账户交易** (restriction, post-close for a period; a disciplinary sanction under SSE rules) | **盘后限制交易** |

The SZSE 2026 trading rules Art. 6.8 list the same ladder [P]. An investor can request a review (复核) of a 限制交易 decision within 15 trading days; the measure stays in force during review.

**What triggers the "large account cannot buy" outcome.** No public rule says "an account holding X% of a severe-volatility stock cannot buy". In practice the 限制/暂停 measure is imposed on an **account (group)**:
- (a) that repeatedly hits the 封板 / limit-price spoofing / ramping / 1-minute order-rate thresholds above, with related accounts merged;
- (b) especially when the stock is severe-abnormal-volatility, ST or publicly named, where thresholds can be halved;
- (c) usually after warnings have been ignored.

The weekly reports in 2025–2026 show a consistent sequence:
1. The stock hits severe abnormal volatility.
2. 停牌核查 (suspension for verification).
3. The stock resumes trading and keeps hitting the limit.
4. The exchange announces "对相关投资者采取了暂停交易等自律监管措施".

Examples: *ST宇顺 (2025-08), *ST铖昌 (2026-01-16), 锋龙股份 (2026-01-23), 嘉美包装 (2026-02-13), ST京蓝 (2026-03); SSE: 天普股份 (2025-09) [S], 沈鼓集团 IPO (2026-09). Details in section 4.

Brokers also act on the investor's behalf. Under 《会员客户交易行为管理》 rules, brokers send warnings and can refuse orders; the SZSE 2026 trading rules Art. 6.5 [P] require it.

**Hard quantity cap, ST stocks only** [P, SZSE 2026 Art. 4.5.4; SSE has an equivalent rule [? not re-verified]]. An investor may buy at most **50万股 per day** of a single risk-warning stock (auction + block + post-close combined). Brokers monitor this. This is the only explicit per-account buy cap. It does **not** apply to normal hot stocks.

---

## 2. Severe abnormal volatility (严重异常波动): definitions and consequences

### 2.1 Definitions per board [P: SZSE 2026 trading rules Art. 5.4.4; SSE trading rules (2023修订) Art. 5.4.3 main / 6.1.11 STAR, via broker copy [S] and consistent with SSE monitoring rule Art. 27 [P]]

Deviation (偏离值) = (stock close at end / previous close at start − 1) − (index end / index start − 1).
- Benchmark index for SSE main board: 上证A股指数 [S].
- SZSE main board: 深证A股指数; ChiNext: 创业板综合指数 [P, SZSE Art. 5.4.1].
- STAR: 科创50 [S].

Adjust for ex-rights. Limit-free days (IPO first 5 days, etc.) and 退市整理 days are excluded.

| Board | 3-day abnormal (异常波动, for reference) | Severe abnormal (严重异常波动): count rule | Severe: 10-day cumulative deviation | Severe: 30-day cumulative deviation | Price limit (2026-07-06 →) |
|---|---|---|---|---|---|
| SSE main | ±20% (ST: ±12% before 2026-07-06; ±20% after [S]) | **4 same-direction abnormal-volatility events (3-day ±20% type) within 10 trading days** | **+100% / −50%** | **+200% / −70%** | 10% (ST 10%, was 5%) |
| SZSE main | ±20% (also: 3-day avg turnover ≥30× prior-5-day avg and 3-day cumulative turnover ≥20%) | 4 within 10 days | +100% / −50% | +200% / −70% | 10% (ST 10%, was 5%) |
| ChiNext | ±30% | **3** within 10 days | +100% / −50% | +200% / −70% | 20% |
| STAR | ±30% | **3** within 10 days | +100% / −50% | +200% / −70% | 20% |

Counters reset from the trading day after the exchange publishes the event, or from resumption [P, SZSE 5.4.5].

The exchange "may adjust the standards according to market conditions" [P, SZSE 5.4.5]. **No 2025–2026 change to these thresholds** was found; the 2026 trading-rule revision kept them [P]. Main board SSE/SZSE moved to these "10-day 4 times / +100% / +200%" standards with the 2023 registration-reform rules (effective 2023-04-10) [S].

### 2.2 What happens after a severe-abnormal-volatility event
1. **Exchange disclosure** [P]. The exchange publishes **投资者分类交易统计** (trading statistics by investor type) for the severe-volatility period.
2. **Company verification** [S: SSE 上市规则 7.1.4 / SZSE 7.1.3, quoted in secondary sources]. The company must publish a **核查公告** (verification announcement) on the next trading day. If it cannot, the stock is **停牌核查** (suspended for verification) from the next trading day and resumes on the day the 核查公告 is published.
   - In practice the suspension is typically "不超过3个交易日" (e.g. 上纬新材 688585, 2025-07-31 [S]). Repeat offenders such as 天普股份 had longer suspensions (4 days) [S].
   - ChiNext additionally may require an investor briefing (投资者说明会) [S].
   - If, after verification, nothing undisclosed is found and the move cannot be explained, the exchange may add warnings, **strengthen abnormal-trading monitoring**, and ask brokers to warn clients [P, SZSE 2026 Art. 5.4.6]. SSE 上市规则 7.1.6 says the exchange may publicly warn and suspend again [S].
3. **10-day order-rate window** [P]. For 10 trading days after the event the 1-minute ≥1000万元 single-direction order rule applies (section 1.3 row 5). All other thresholds can be cut by up to 50% because the stock is a severe-volatility stock (Art. 31).
4. **Public 重点监控 naming** in the weekly report (section 3). It triggers the further 50%-cut clause and signals intraday scrutiny.
5. **Account suspensions** if limit-up continues after resumption (section 1.4 examples).

**Not found** [?]:
- any official rule capping a single account's cumulative buy share during the severe-volatility period;
- any 盘中临停 (intraday halt) for price-limited stocks.

These appear only in 自媒体 posts, not in exchange rules. Intraday halts apply only to stocks without price limits (see 5.3).

---

## 3. Weekly regulatory disclosures

### 3.1 SZSE: 《深市监管动态》 (weekly PDF) [P]
- **Listing page**: https://www.szse.cn/disclosure/supervision/dynamic/index.html (pages `index_1.html` … `index_26.html`, 27 pages, 20 items per page).
- **Item URL pattern**: `https://www.szse.cn/disclosure/supervision/dynamic/P0YYYYMMDD<digits>.pdf`. The listing page embeds `var curHref = './P02026....pdf'; var curTitle ='深市监管动态（2026年11月2日-2026年11月6日）'` and a `<span class="time">` date, so it is easy to regex.
- **Coverage**: **2016-04-25 → present** (527 weekly PDFs as of 2026-11-06). Published every Friday after the close (the 2016–17 issues came on the following Monday).
- **Machine-readability**: text-based PDF (pypdf extracts it cleanly), one short paragraph per section. No tables, CSV or API.
- **Content (section 二、市场交易监管动态)**:
  - "本所共对 **N 起证券异常交易行为**采取了自律监管措施，涉及盘中拉抬打压、虚假申报等"
  - "对 **'XXX'** 进行**重点监控**" (named stocks; frequent 2022–2026)
  - "共对 **M 起上市公司重大事项**进行核查"
  - "上报证监会 **K 起**涉嫌违法违规案件线索"
  - since 2025-08, narrative lines like "对相关投资者采取了**暂停交易**等自律监管措施" for named stocks.
  - Format change: 2016 issues had full detail (电话警示 count, 警示函 count, 限制账户交易 count, accounts involved). From about 2017 on, only aggregate counts. **No breakdown by measure type** (书面警示 vs 暂停 vs 限制) since then.
  - Other sections: 一、上市公司监管 (监管函, 问询函, 其他函件 counts) and 三、会员监管.

### 3.2 SSE: weekly press release 《沪市市场运行情况例行发布》 / 新闻发布会 (weekly PDF) [P]
- **Listing page**: https://www.sse.com.cn/aboutus/mediacenter/conference/ (`s_list.shtml`, `s_list_2.shtml` … `s_list_35.shtml`, 516 items). Items read "第五百二十四期新闻发布会", dated 2026-11-06.
- **Item URL pattern**: `/aboutus/mediacenter/conference/c/<id>/files/<hash>.pdf`. The earliest items (2015) are `.shtml` pages.
- **Coverage**: 2015-03-20 → present, weekly (Friday).
- **Machine-readability**: text PDF. The section "3、沪市监管概览 → 交易监管" reads:
  - "上交所对 **N 起**拉抬打压、虚假申报等证券异常交易行为采取了自律监管措施"
  - "对 **XXX 等严重异常波动股票 / 波动幅度较大的股票**进行重点监控"
  - "对 **M 起**上市公司重大事项等进行专项核查"
  - "向证监会上报涉嫌违法违规案件线索 **K 起**"
  - Occasionally it names account suspensions (e.g. 2026-09-18: 沈鼓集团 IPO accounts "暂停账户交易").
  - The 上市公司监管 subsection gives 监管工作函 count, and since about 2024 "提请启动内幕交易、异常交易核查 N 单".
  - Older (around 2015–2016) issues had full detail ("共对222起证券异常交易行为进行调查，涉及…证券账户875个次，共出具书面警示函121份，…盘中暂停账户交易56次").
- **Also**: SSE 监管动态 (individual named measures such as the 2024-02-20 宁波灵均 restriction): http://www.sse.com.cn/disclosure/credibility/regulatory/news/ ; STAR-specific: https://star.sse.com.cn/regulation/trading/dynamic/

### 3.3 Sample numbers / time series

**SZSE: full series parsed** (527 weeks; CSV saved as `stockfish-data/research/szse_weekly_supervision.csv`; 6 weeks without a parsable count). Yearly statistics of "abnormal-trading cases with self-regulatory measures per week":

| Year | Weeks | Mean | Median | Min | Max |
|---|---|---|---|---|---|
| 2016 (from Apr) | 35 | 32 | 28 | 13 | 87 |
| 2017 | 51 | 42 | 34 | 14 | 130 |
| 2018 | 47 | **166** | 138 | 37 | **467** (2018-02-02) |
| 2019 | 49 | 30 | 31 | 1 | 64 |
| 2020 | 51 | 63 | 48 | 23 | 159 |
| 2021 | 49 | 66 | 67 | 32 | 111 |
| 2022 | 50 | 73 | 70.5 | 24 | 123 |
| 2023 | 49 | 71 | 62 | 33 | 143 |
| 2024 | 49 | **123** | 106 | 30 | **447** (2024-10-11) |
| 2025 | 50 | **132** | 124 | 64 | 287 |
| 2026 (to 11-06) | 41 | **168** | 149 | 92 | **387** (2026-01-16) |

SZSE monthly mean, 2023–2026:

| | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | 43 | 89 | 51 | 65 | 68 | 92 | 61 | 48 | 61 | 101 | 114 | 68 |
| 2024 | 82 | 124 | 75 | 83 | 142 | 101 | 83 | 95 | 88 | **244** | **234** | 145 |
| 2025 | 84 | 133 | 107 | 104 | 138 | 112 | 83 | 152 | 186 | 127 | **228** | 130 |
| 2026 | **294** | 179 | 176 | 151 | **227** | 179 | 180 | 111 | 99 | 118 | 119 (1 wk) | |

Selected SZSE weeks (count / company matters checked / CSRC clues / named focus stocks):
- 2023-07-14: 61 / 3 / 2. Quiet market.
- 2023-11-24: 112 / 7 / 2. Named: 三柏硕, 西陇科学, 森远股份, 中文在线, *ST左江 (late-2023 small-cap and ST mania).
- 2024-02-23: 82 / 7 / 1. "*ST左江等涨跌异常证券**以及量化交易行为**" (quant crackdown after the Feb 2024 sell-off).
- 2024-09-20: 34 / 5 / –. Pre-"924" quiet.
- **2024-10-11: 447** / 5 / 2. Post-924 rally week; named "上市初期的新股交易".
- 2024-11-08: 304 / 8 / 6. Named 中化岩土, 华映科技.
- 2025-08-29: 278. Named 思泉新材, 北方长龙.
- 2025-11-14: 256 / 4 / 1. Named 平潭发展 (严重异常波动).
- **2026-01-16: 387** / 15 / 5. *ST铖昌 account suspensions; same week as the margin-ratio hike.
- 2026-01-23: 326. 锋龙股份 account suspensions.
- 2026-05-15: 293.
- 2026-08-14: 114. Named 蓝盾光电.
- 2026-11-06: 119.

**SSE**: see section 3.4 (full series parsed from the weekly PDFs).

**Caveats for using these counts as an "enforcement intensity" index**
- (1) They are counts of *cases handled* (mostly 口头/书面警示), so they scale with market turnover and the number of hot stocks. Normalise by turnover or by the number of limit-up stocks.
- (2) Definitions shifted: 2016 counted "进行调查"; from 2018 on, "采取了自律监管措施".
- (3) Holiday-short weeks are lower.
- (4) Neither exchange breaks the count down by measure type, so the number of 暂停/限制 actions is **not** observable. Only narrative mentions are.

### 3.4 SSE weekly series (parsed) [P]
Source: 516 weekly items from the SSE press-conference listing, parsed into `stockfish-data/research/sse_weekly_supervision.csv`.
- 282 items are PDFs.
- 234 items (mostly 2015–2022, some 2023–24) are `.shtml` intro pages whose body lacks the supervision paragraph, so counts are missing there.
- **Coverage of counts is good only from about 2022-H2 onward.** 2016–2021 is sparse.

| Year | Weeks with count | Mean | Median | Min | Max |
|---|---|---|---|---|---|
| 2016 | 7 | 91 | 82 | 55 | 144 |
| 2017 | 15 | 73 | 61 | 42 | 159 |
| 2018 | 10 | 60 | 55 | 35 | 105 |
| 2019 | 12 | 29 | 29 | 8 | 52 |
| 2020 | 21 | 36 | 33 | 17 | 79 |
| 2021 | 9 | 90 | 78 | 41 | 182 |
| 2022 | 24 | 80 | 71 | 37 | 189 |
| 2023 | 36 | 82 | 48 | 22 | 471 (2023-03-10) |
| 2024 | 37 | 99 | 70 | 25 | 353 (2024-11-01) |
| 2025 | 47 | **152** | 146 | 44 | 360 (2025-11-21) |
| 2026 (to 11-06) | 39 | **214** | 165 | 50 | **629 (2026-06-26)** |

SSE monthly mean, 2023–2026:

| | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | 67 | 142 | 272 | 52 | 77 | 153 | 41 | 36 | 42 | 52 | 32 | 50 |
| 2024 | 50 | 74 | 75 | 58 | 67 | 54 | 58 | 82 | 41 | 110 | **240** | 140 |
| 2025 | 66 | 218 | 99 | 112 | 167 | 96 | 190 | 230 | 194 | 139 | 191 | 114 |
| 2026 | 234 | 133 | 285 | 170 | 364 | **437** | 228 | 106 | **73** | 145 | 119 (1 wk) | |

**Measure breakdown, an important finding** [P].
- From **2021-12-17 to 2023-06-30** the SSE weekly text split the count into two parts: "对 X 起加剧市场波动、误导投资者决策、影响交易秩序的异常交易行为及时采取了**暂停投资者账户交易**等监管措施" and "对 Y 起拉抬打压、虚假申报等…采取了**书面警示**等监管措施". The CSV column `of_which_account_suspension_cases` holds X.
  - Examples: 2023-03-10: **452 suspension cases** + 19 warning cases (ST stocks *ST泽达, *ST紫晶, *ST辅仁, *ST瑞德 under 重点监控). 2023-06-16: 167 + 40 (退市整理 stocks). 2022-12-23: 166 + 23.
  - Most suspension cases in this window relate to **risk-warning and delisting-arrangement stocks**, not ordinary hot stocks.
- From mid-2023 to about 2025-10 the wording is "采取了书面警示等监管措施". It occasionally reads "书面警示、暂停交易等" (e.g. 2025-07-25 → 2025-08-29, during 上纬新材 / 东芯股份 / 长城军工 / *ST亚振).
- From about 2025-11 the wording is "采取了自律监管措施", with no breakdown.
- So **the number of suspensions is only directly observable for 2021-12 → 2023-06.** After that it is only a flag.

Selected SSE weeks (total / matters checked / CSRC clues / named):
- 2023-07-14: 39 / 23 / 1.
- 2023-11-24: 22 / 21 / –. "个别近期涨跌幅异常的股票".
- 2023-12-08/15: *ST同达 plus severe-volatility stocks 惠发食品, 东安动力.
- 2024-02-23: 64 / 26. "近期波动较大的退市风险警示股票".
- 2024-03-15: 艾艾精工. 2024-04-12: 莱绅通灵. 2024-05-24: 南京化纤.
- 2024-09-20: 25 / 16. Pre-924 low.
- **2024-11-01: 353** / 18 / 1. Severe-volatility stocks. 2024-11-29: 日出东方 plus new listings.
- 2025-07-25 → 08-29: 上纬新材 (from 07-25), 西藏旅游, 南方路机, 东芯股份, 长城军工, *ST亚振, *ST苏吴; "暂停交易" mentioned.
- 2025-09-05 → 2026-02-13: **天普股份** named almost every week (about 5 months). 首开股份 (09-19); 大有能源 (10-24).
- 2025-11-14/21: 合富中国, 华盛锂电; **360** cases on 11-21.
- 2026-01-09/16: 国晟科技, 航天动力, 天普股份, 上纬新材; **365** on 01-16. 2026-01-30: 白银有色; 南方原油LOF (fund premiums).
- 2026-03-27: **457**. 标普油气ETF, 南方原油LOF, 中韩半导体ETF (QDII premium), *ST熊猫, *ST正平, 华电辽能.
- 2026-05-15 → 07-10: 445 / 360 / 301 / 250 / 433 / **629 (06-26)** / 366 / 277. Named 福达合金, 海泰新光, 长盈通, 泰金新能, 中船特气, 昀冢科技, 恒尚节能, 有研硅, and premium LOFs (全球芯片LOF, 财通福鑫LOF). **This is the highest SSE intensity on record.**
- 2026-07-31 → 08-28: 爱丽家居 (after its 停牌核查 from 08-03); 百花医药.
- 2026-09-11/18: 龙版传媒; 50 cases (lowest since 2024); 沈鼓集团 IPO accounts suspended.
- 2026-10-16: 新华传媒 (severe volatility). 2026-11-06: 119 / 31 / 1.

**Cross-exchange read.**
- Both series are pro-cyclical with turnover. Peaks: Feb–Mar 2023 (SSE, ST/delisting suspensions), Oct–Nov 2024, Aug–Sep 2025, Nov 2025, Jan 2026, Mar 2026 and May–Jul 2026 (SSE).
- Both have a clear trough in Sep–Oct 2026 (SSE 73/145, SZSE 99/118).
- The SSE series diverges from SZSE in May–Jul 2026: SSE 364–437 monthly mean vs SZSE 179–227. Much of that is fund-premium (LOF/QDII ETF) and STAR severe-volatility names.

---

## 4. Evidence of enforcement elasticity (监管松紧)

### 4.1 Episodes (dated)
| Period | Direction | Evidence |
|---|---|---|
| 2018 H1 | Tight | SZSE weekly counts averaged 166 in 2018 (max 467 on 2018-02-02); 2018-04-27 SZSE article "加强重点监控账户管理 精准打击异常交易行为" [P, SZSE news]. Followed by a collapse to about 30/week in 2019 when the market was weak. Counts are pro-cyclical. |
| 2022–2023 | Moderate; frequent naming | SZSE named a 近期涨幅异常 stock almost every week in 2022 (雅本化学, 浙江建投, 中通客车, 竞业达, ST大集, 安奈儿, 西安饮食 …) [P] |
| 2022-12 → 2023-06 | Tight on ST/delisting stocks (SSE) | SSE reported separate counts of **account-suspension cases**: 166 (2022-12-23), 215 (2023-03-03), **452 (2023-03-10)**, 167 (2023-06-16), mostly in *ST and 退市整理 stocks (*ST泽达, *ST紫晶, 退市辅仁 …) [P]. |
| 2023-08 | Loosening (pro-market) | Margin ratio cut 100% → 80% (2023-08) [S]; stamp-duty halving in the same package (2023-08-28) [? memory]. |
| Late 2023 → Q1 2024 | Tight on ST/small-cap speculation; quant | *ST左江 named on 重点监控 continuously for about 4 months (2023-11-17 → 2024-04-19) [P]. 2024-02-20 SSE/SZSE restricted 宁波灵均 accounts (quant sell-off) [S/P listing]. 2024-02-23 SZSE names "量化交易行为" [P]. CSRC programmatic-trading rules 2024-05-15. Exchange indicators trial from 2024-04 [P]. |
| 2024-10/11 (post-924) | Very high volume of handling | SZSE 447 (2024-10-11), 304 (11-08); named new-listing trading, 中化岩土, 华映科技, *ST景峰, ST加加 [P]. SSE 353 (2024-11-01) and 235 (2024-11-08), naming severe-volatility stocks [P]. |
| 2025 H2 | Tight on 连板妖股 | Frequent 停牌核查 of streak stocks: 天普股份 (three 停牌核查 in Aug–Sep 2025, account measures) [S]; 上纬新材 (2025-07-31) [S]; 合富中国 (12 limit-ups in 14 days, 3 severe-volatility hits, +256%, SSE 重点监控 2025-11-14) [S]; 平潭发展 (+255% in a month, SZSE 停牌核查, 重点监控 2025-11-14) [P/S]; *ST宇顺 (SZSE 重点监控 2025-05 → 2025-10, accounts suspended 2025-08-22) [P]. |
| 2026-01 | Peak tightness | Market turnover record (≈3.99万亿 on 2026-01-14) [S]. Margin ratio 80% → 100% from 2026-01-19 [P]. "1月12日以来百余家公司发布异常波动公告，多家连板股停牌核查" [S]. SZSE weekly counts 387 / 326 / 261. Account suspensions in *ST铖昌, 锋龙股份 and the premium LOF 国投白银LOF [P]. |
| 2026-02/03 | Still tight | 嘉美包装 (2026-02-13), ST京蓝 (2026-03-06/13/20) suspensions after 停牌核查 and continued limit-ups [P]. SSE 457 cases in the week of 2026-03-23→27, with 标普油气ETF and 华电辽能 named [P]. |
| 2026-07/08 | Moderate | SZSE names 万邦医药, 传智教育, 高争民爆, 蓝盾光电 as 严重异常波动 [P]. 爱丽家居 (9 one-word limit-ups, +135.8% in 10 days, P/E 317) 停牌核查 from 2026-08-03 [S]. |
| 2026-09 → 11 | Relatively relaxed | SZSE counts back to about 100–120/week (2026-09: 99 avg; 2026-10: 118) [P]. SSE 50 cases on 2026-09-18 [P] (沈鼓集团 IPO account suspensions); SSE 119 on 2026-11-06 [P]. |

### 4.2 Specific stocks named on 重点监控 or with account action (subset; primary = SZSE/SSE weekly PDFs)
- SZSE [P]:
  - 2023: 汉王科技, 赛为智能 (02-10); 丰立智能 (05-26); 鸿博股份, 铭普光磁 (06-16); 捷荣技术 (09-28 → 10-20); 高新发展 (11-03); *ST左江 (11-17 → 2024-04); 亚世光电 (12-29).
  - 2024: 深中华A (01-19); 正丹股份 (04-19 → 05-24); 朗源股份 (05-10); *ST围海 (05-17); 英力股份 (05-31); 百邦科技 (06-28); *ST景峰 (08-30 → 11-15); 银之杰 (09-27); 中化岩土, 华映科技 (11-08); ST加加 (11-15); *ST同洲 (12-13/20).
  - 2025: *ST旭蓝 (03-14); *ST东通 (05-09); *ST宇顺 (05-16 → 10-24, **accounts suspended 08-22**); 广生堂 (08-15); 思泉新材, 北方长龙 (08-29); *ST东易, ST中迪 (11-07); 平潭发展 (11-14); 胜通能源 (12-26, 2026-01-09); 嘉美包装 (12-31; **suspensions 2026-02-13**).
  - 2026: 锋龙股份 (01-09; **suspensions 01-23**); *ST铖昌 (**suspensions 01-16**); *ST立方, *ST长药 (01-23); 国投白银LOF (**suspensions 01-30**); ST京蓝 (**suspensions 03-06/13/20**); 豫能控股 (03-06); 兴业科技 (06-26); 万邦医药 (07-17); 传智教育, 高争民爆 (08-07); 蓝盾光电 (08-14).
- SSE [P, weekly PDFs; full list in sse_weekly_supervision.csv `focus_monitor_text`]:
  - 2023: *ST泽达, *ST紫晶, *ST辅仁, *ST瑞德 (03-10); *ST同达, 惠发食品, 东安动力 (12).
  - 2024: 艾艾精工 (03-15); 莱绅通灵 (04-12); 南京化纤 (05-24); *ST富润 (09-06); 日出东方 (11-29); *ST卓朗 (12-13).
  - 2025: *ST锦港, 中毅达 (05); *ST亚振 (06 → 2026-01); 上纬新材, 西藏旅游, 南方路机, 东芯股份, 长城军工 (07–08, 暂停交易 mentioned); 天普股份 (09 → 2026-02); 首开股份 (09-19); *ST正平 (10 → 2026); 大有能源 (10-24); 合富中国, 华盛锂电 (11-14).
  - 2026: 国晟科技, 航天动力 (01); 白银有色 (01-30); *ST岩石, *ST精伦, *ST熊猫, *ST观典; 华电辽能 (03-27); 福达合金, 海泰新光, 长盈通, 泰金新能, *ST春天 (05); 中船特气, 昀冢科技, 恒尚节能, 有研硅 (06–07); 爱丽家居 (07-31 → 09-04); 百花医药 (08-14); 龙版传媒 (09; plus 沈鼓集团 IPO account suspensions on 09-18); 新华传媒 (10-16).
  - Funds: premium LOF/QDII ETFs named repeatedly in 2026 (标普油气ETF, 中韩半导体ETF, 南方原油LOF, 全球芯片LOF, 财通福鑫LOF).

---

## 5. Other practical limits on hot-money behaviour

### 5.1 Company-side announcements
- **股票交易异常波动公告** (3-day ±20%), **严重异常波动公告 / 核查公告**, and repeated **风险提示公告**. Companies often add self-imposed warnings such as P/E far above the industry, "no undisclosed matters", or major shareholders' reduction plans. These are disclosed through cninfo/SSE disclosure and can be scraped.
- Exchange **问询函 / 关注函 / 监管工作函**:
  - SZSE weekly counts these: e.g. 2026-11-06 issue: "问询函3份，其他函件36份" [P].
  - SSE counts 监管工作函 weekly, e.g. 10 in the 2026-11-06 issue [P].
  - SZSE publishes each letter under "监管信息公开 → 问询函件" [S, not checked]. A letter to a hot stock often lands the day after a streak.

### 5.2 停牌核查
See 2.2. Mandatory if the company cannot publish the 核查公告 the next day. In 2025–2026 the exchanges have repeatedly required 停牌核查 for 连板 stocks even when they were barely past the thresholds [S]. The exchanges can also suspend a stock directly for abnormal trading: SZSE 2026 Art. 4.3.3 says "证券交易出现第6.1条规定的异常交易行为或情形的，本所可以视情况对相关证券实施停牌" [P].

### 5.3 盘中临时停牌 (intraday halts) [P, SZSE 2026 Art. 4.3.4]
- Applies **only to stocks without price limits**: IPO first 5 trading days, relisting day, first day of 退市整理.
- Halt of 10 minutes when the price first moves **±30%** from the day's open, and again at **±60%**. If the halt would cross 14:57, the stock resumes at 14:57.
- Same structure on SSE main board, ChiNext and STAR since the 2023 registration reform [S].
- Price-limited stocks (including hot main-board 10% stocks) have **no** intraday halt. The old pre-2023 main-board first-day IPO halt rules (halts at ±10%/±20% from the open, 44% cap) were replaced [? memory].

### 5.4 Price cage (价格笼子 / 有效申报价格范围), main board since 2023-04-10 [P, SZSE 2026 Art. 3.3.16; S for the start date]
- In continuous trading, a limit buy order may not exceed **max(102% × buy benchmark, buy benchmark + 10 ticks)**. A sell order may not be below **min(98% × sell benchmark, sell benchmark − 10 ticks)**.
- Buy benchmark = best ask; if there is none, best bid; then last trade; then previous close.
- Orders outside the cage are rejected as invalid. They are **not** queued; some brokers hold them locally and release them later.
- Effect: you cannot place a single order at the limit-up price (110%) while the stock trades well below it. Chasing a stock to the limit needs repeated sweeps, and 封板 orders can only be added once the price is within 2% of the limit. This interacts with the "维持涨跌停" and "limit-price spoofing" rules, which measure the account's share of the resting queue at the limit.
- ChiNext: same rule per the SZSE 2026 text [P] (ChiNext had the 2% cage since its 2020-08 reform [? memory]). STAR: 2% only, no 10-tick floor [S]. Not applicable during call auctions or intraday-halt call auctions.

### 5.5 Programmatic / high-frequency trading rules (2024–2025) [P, SSE Q&A 2025-04-03]
- CSRC 《证券市场程序化交易管理规定（试行）》 issued **2024-05-15**, effective 2024-10-08 [? from memory, not re-verified]. Exchange implementation rules issued **2025-04-03**, effective **2025-07-07**.
- **Reporting**: all programmatic traders report to the exchange via their broker (account, funds size, strategy type, trading software, etc.). The earlier stock/convertible-bond reporting notices stay valid.
- **High-frequency definition**: an account reaching **≥300 orders+cancels per second** or **≥20,000 orders+cancels per day**. HFT accounts must also report server location, system test reports and contingency plans. Differential (higher) order/cancel fees are planned.
- **Four programmatic abnormal behaviours**, monitored on top of the 5 types / 14 behaviours: 瞬时申报速率异常, 频繁瞬时撤单, 频繁拉抬打压 (small moves across many stocks), 短时间大额成交 (same manager's products trading together, merged). Numeric thresholds are **not public**. During the trial (from 2024-04), mostly quant private funds and broker proprietary desks triggered them.
- **Measures**: if an account is restricted more than once within a month for abnormal trading, the broker must suspend its co-location (主机托管) access. Cases can be referred to the CSRC local office or industry associations.
- Relevance for hot-money retail: HFT and quant-like "打板" bots and order-splitting scripts that reach 300/sec are reportable. Typical 游资 discretionary trading is unaffected.

### 5.6 Other levers
- Margin ratio (2026-01-19 → 100%).
- Removal from the 融资融券 target list (not researched).
- Daily disclosure of **龙虎榜** (top-5 seats) for ±7% deviation, 15% amplitude or 20% turnover (main board), and for 3-day abnormal volatility. These make hot-money seats visible [P, SZSE 2026 Art. 5.4.1–5.4.3].
- ST daily buy cap of 50万股 per investor [P].

---

## 6. Implications for a quantitative signal

### 6.1 Computable from daily OHLC + index data (no scraping)
| Item | How |
|---|---|
| 3-day abnormal volatility count (main ±20%, ChiNext/STAR ±30%; ST main-board ±12% before 2026-07-06, ±20% after) | Rolling 3-day cumulative deviation vs board index (上证A股 / 深证A股 / 创业板综指 / 科创50). Apply the "reset after publication" logic: after an event, restart counting from the next day. |
| **Severe abnormal volatility flags**: (i) 4 (main) / 3 (ChiNext/STAR) same-direction 3-day events in 10 days, (ii) 10-day cum. deviation ≥ +100%, (iii) 30-day ≥ +200% | Same deviation series. **Also compute the "distance to trigger"** (e.g. remaining % to +100% in the 10-day window, number of abnormal events left). Traders front-run the expected 停牌核查; stocks often stall or reverse just before or after the trigger. |
| 10-trading-day "post-severe window" | Days since the last severe flag ≤ 10 means the 1-minute ≥1000万 rule is active and thresholds can be halved. This is a regime dummy for "harder to 封板 with big money". |
| Price-limit status, 一字板, 封板 streak length, limit price (10% / 20%; ST 5% before 2026-07-06, 10% after) | OHLC (close == limit, high == low == limit). Mind the board/date switch for ST stocks. |
| Approximate 封板 threshold in shares: 100万股 or 1000万元 / limit price (ST: 50万股/200万元), halved if the stock is "named" | Gives a rough estimate of what limit-up order size a single account can keep resting without hitting the "巨大 + 30% + 10 min" rule. Needs order-book data to use properly. |
| Market-wide regime proxies | Count of limit-up stocks, number of stocks in a severe-volatility state, total turnover. These correlate with the weekly enforcement counts (pro-cyclical). |
| 龙虎榜 trigger conditions | From OHLC + turnover (requires float shares). |

### 6.2 Needs scraping announcements / exchange publications
| Item | Source | Notes |
|---|---|---|
| Weekly enforcement intensity (cases handled, matters checked, CSRC clues) | SZSE 深市监管动态 PDF listing (2016→); SSE weekly 新闻发布会 PDF (2015→) | Both regex-friendly, see section 3. Normalise by turnover. Publication is Friday evening, so it is usable from the next Monday. |
| **Publicly named 重点监控 stocks** | Same weekly PDFs | Strongest single "regulatory heat" flag per stock: the 50%-threshold cut applies, and it often precedes account suspensions. Extract names inside “ ” quotes. |
| Account-suspension events per stock | Same weekly PDFs (narrative), SSE 监管动态 page | Rare but high-signal: in all the 2025–2026 SZSE cases they followed 停牌核查 plus continued limit-ups. |
| 停牌核查 / 核查公告 / 风险提示公告 / 异常波动公告 | Company announcements (cninfo / SSE disclosure / Eastmoney announcement API) | Exact suspension and resumption dates. The number of 风险提示 announcements in N days is a good escalation counter. |
| 问询函 / 关注函 | SZSE 监管信息公开 (问询函件), SSE 监管问询 pages | Per-stock date of the letter. |
| 严重异常波动 investor-type trading statistics | Exchange 公开信息 on the event day | Shows institutional vs retail share. |
| Rule-regime dates | This file | 2023-04-10 (main-board price cage and registration rules), 2025-07-07 (programmatic rules), 2026-01-19 (margin 100%), 2026-07-06 (ST 10% limit, ST abnormal-volatility ±20%). |

### 6.3 Suggested features
- `sev_flag_10d4x`, `sev_flag_10d100`, `sev_flag_30d200`, `dist_to_sev_10d` (pct points), `dist_to_sev_30d`, `days_since_sev` (≤10 = post-severe window). All from OHLC.
- `named_focus_szse/sse` (stock named in this week's report), `weeks_named_consecutive`, `acct_suspension_flag`. From scraping.
- `reg_intensity_z` = z-score of (SZSE weekly cases + SSE weekly cases) / market turnover over a 52-week window. Use it as a regime variable for the persistence of 连板 / 妖股 runs. Hypothesis to test **[?]**: high intensity shortens streaks and lowers the next-day return after the 3rd+ limit-up.
- `halt_check_flag` (company in or just out of 停牌核查), `risk_notice_count_5d`. From announcements.

---

## Sources (main)
- SSE main board monitoring rule (上证发〔2023〕39号) and drafting note: https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20250519_10779397.shtml [P]
- SSE STAR monitoring rule (上证发〔2023〕40号): https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20250519_10779400.shtml [P]
- SZSE main board monitoring rule: http://docs.static.szse.cn/www/lawrules/rule/stock/trade/W020230217570611262391.pdf [P]
- SZSE ChiNext monitoring rule (2023修订): https://docs.static.szse.cn/www/lawrules/rule/all/W020250327502095568538.pdf [P]
- SZSE trading rules (2026修订): https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf [P]
- SSE trading rules 2026 release: https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml [P]
- SSE trading rules (2023修订) broker copy: https://www.mgzq.com/userfiles/ecb5375bc6ab4174a6d9fb405222c2a7/files/cms/article/%E4%B8%8A%E6%B5%B7%E8%AF%81%E5%88%B8%E4%BA%A4%E6%98%93%E6%89%80%E4%BA%A4%E6%98%93%E8%A7%84%E5%88%99%EF%BC%882023%E5%B9%B4%E4%BF%AE%E8%AE%A2%EF%BC%89.pdf [S]
- SSE programmatic trading Q&A (2025-04-03): http://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20250403_10776805.shtml [P]
- Margin ratio change (2026-01-14): https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260114_10805178.shtml [P]
- SZSE 深市监管动态 listing: https://www.szse.cn/disclosure/supervision/dynamic/index.html [P]
- SSE weekly press conference listing: https://www.sse.com.cn/aboutus/mediacenter/conference/ [P]
- 2026 rule change coverage: https://www.xinhuanet.com/money/20260706/6fc6123488fc42c1852d5775089ae460/c.html ; https://www.stcn.com/article/detail/3998201.html [S]
- Price cage explanation: https://www.cls.cn/detail/1272303 ; https://stcn.com/article/detail/836446.html [S]
- 2025-11 SSE 重点监控 (合富中国, 华盛锂电): https://finance.sina.com.cn/roll/2025-11-14/doc-infxkhuy3026762.shtml [S]
- 合富中国 停牌核查: https://www.cls.cn/detail/2201181 [S]
- 上纬新材 停牌核查 announcement: https://file.finance.qq.com/finance/hs/pdf/2025/07/31/1224342574.PDF [S]
- 爱丽家居 停牌核查: https://www.sina.cn/news/detail/5327462557813610.html [S]
- 2026-01 margin / cooling coverage: https://m.21jingji.com/article/20260115/aa9be3e162c1f471390f5e9d15b40863.html [S]
