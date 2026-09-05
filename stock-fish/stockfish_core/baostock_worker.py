"""One-shot public Baostock observation worker; parent enforces a hard timeout.

No credentials, purchases, order endpoints or messaging. SDK global connection
state is confined to this subprocess. Stdout contains one JSON document only.
"""
from __future__ import annotations
import contextlib
import json
import sys
from datetime import date


def rows(result):
    if result.error_code != "0":
        raise RuntimeError("BAOSTOCK_QUERY_FAILED")
    values = []
    while result.next():
        if result.error_code != "0":
            raise RuntimeError("BAOSTOCK_QUERY_FAILED")
        values.append(dict(zip(result.fields, result.get_row_data())))
        if len(values) > 1000:
            raise RuntimeError("BAOSTOCK_ROW_LIMIT")
    return values


def main():
    request = json.load(sys.stdin)
    day = date.fromisoformat(request["date"]).isoformat()
    codes = request["codes"]
    if not 0 < len(codes) <= 10:
        raise ValueError("observation limited to 10 symbols")
    import baostock as bs
    with contextlib.redirect_stdout(sys.stderr):
        if bs.login().error_code != "0":
            raise RuntimeError("BAOSTOCK_LOGIN_FAILED")
        try:
            records = []
            for code in codes:
                records.append({"code": code,
                    "daily": rows(bs.query_history_k_data_plus(code,
                        "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST",
                        start_date=day, end_date=day, frequency="d", adjustflag="3")),
                    "basic": rows(bs.query_stock_basic(code=code)),
                    "industry": rows(bs.query_stock_industry(code=code, date=day))})
        finally:
            bs.logout()
    print(json.dumps({"date": day, "records": records}, ensure_ascii=False))


if __name__ == "__main__":
    main()
