# R05 数据访问矩阵

| 数据能力 | 当前接入 | 真实状态 | 用途 | 官方/来源文档 |
| --- | --- | --- | --- | --- |
| 当前主板目录 | AkShare 上交所/深交所目录 | R05 运行完成 | 主板身份分母 | 本地 R03/R04 已接线 |
| 交易日历 | BaoStock + 本地 2026 兜底 | R05 运行完成 | 新股满 60 交易日与近 6 日窗口 | 本地 R03/R04 已接线 |
| 近 6 日 RAW 日线 | BaoStock `market_daily` | R05 运行完成 | A、H 与 RAW 成交额窗口 | 本地 R03/R04 已接线 |
| 可比收盘 | BaoStock 复权因子 | R05 运行完成 | R 与同口径可比收盘 | 本地 R03/R04 已接线 |
| 当前停牌/退市整理 | BaoStock `master_all` + SSE 退市整理集 + SZSE 简称规则 | R05 运行完成 | 默认排除停牌、退市整理 | 本地 R03/R04 已接线 |
| Tushare 基础资料 | `stock_basic` | 已实现 adapter，缺 token 未真验 | 历史身份、上市/退市日期、行业补充 | https://tushare.pro/document/2?doc_id=25 |
| Tushare 交易日历 | `trade_cal` | 已实现 adapter，缺 token 未真验 | 可替代/交叉验证交易日历 | https://tushare.pro/document/2?doc_id=26 |
| Tushare 日线/复权 | `daily`、`adj_factor`、`daily_basic` | 已实现 adapter，缺 token 未真验 | RAW 行情、可比价格、换手/市值 | https://tushare.pro/document/2?doc_id=27 / https://tushare.pro/document/2?doc_id=28 / https://tushare.pro/document/2?doc_id=32 |
| Tushare 涨跌停 | `stk_limit`、`limit_list_d` | 已实现 adapter，缺 token 未真验 | 直接涨跌停价、涨停事件 | https://tushare.pro/document/2?doc_id=183 / https://tushare.pro/document/2?doc_id=298 |
| Tushare ST/停复牌 | `stock_st`、`suspend_d` | 已实现 adapter，缺 token 未真验 | ST、停牌与资格排除 | https://tushare.pro/document/2?doc_id=397 / https://tushare.pro/document/2?doc_id=31 |
| Tushare 资金/龙虎榜 | `moneyflow`、`top_list`、`top_inst` | 已实现 adapter，缺 token 未真验 | FUNDS_V1 与后验解释 | https://tushare.pro/document/2?doc_id=170 / https://tushare.pro/document/2?doc_id=106 / https://tushare.pro/document/2?doc_id=107 |
| Tushare 题材/竞价 | `dc_index`、`dc_member`、`stk_auction`、`stk_auction_o` | 已实现 adapter，缺 token 未真验 | S3 题材、09:25 竞价复核 | https://tushare.pro/document/2?doc_id=362 / https://tushare.pro/document/2?doc_id=363 / https://tushare.pro/document/2?doc_id=369 |
