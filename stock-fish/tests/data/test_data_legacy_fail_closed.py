import socket
import pytest
import pandas as pd
from stockfish_core.common import UnavailableError


def test_actual_provider_initializes_lazily_and_never_falls_back_to_mock(monkeypatch):
    from market_data.a_stock_provider import AStockProvider, MockBackend
    from market_data.provider_adapter import AdvancedBackend
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise ConnectionError("offline")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(AdvancedBackend, "get_quote", fail)
    provider = AStockProvider(backend="advanced")
    assert provider.backend_name == "advanced" and not calls
    assert isinstance(provider.backend, AdvancedBackend) and not calls
    with pytest.raises(UnavailableError): provider.get_quote("600519")
    assert provider.backend_name == "advanced" and not isinstance(provider.backend, MockBackend)
    explicit = AStockProvider(backend="mock").get_quote("600519")
    assert explicit.quality == "MOCK" and explicit.source == "mock"


def test_missing_live_quote_is_unavailable(monkeypatch):
    from market_data.a_stock_provider import AStockProvider
    from market_data.provider_adapter import AdvancedBackend
    monkeypatch.setattr(AdvancedBackend, "get_quote", lambda *a: None)
    with pytest.raises(UnavailableError): AStockProvider(backend="auto").get_quote("600519")


def test_unsafe_credential_paths_disabled_without_network_or_home_write(monkeypatch):
    import sxsc_tushare as sx
    from market_data.data_fetchers.tushare_fetcher import _TushareHttpClient, TushareFetcher
    def forbidden(*a, **kw): raise AssertionError("unexpected outbound connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    with pytest.raises(UnavailableError): sx.set_token("fixture-secret")
    with pytest.raises(UnavailableError): sx.get_api(token="fixture-secret")
    client = TushareFetcher.__new__(TushareFetcher)._build_api_client("fixture-secret")
    assert client._api_url == "https://api.tushare.pro"
    with pytest.raises(ValueError): _TushareHttpClient("fixture-secret", api_url="http://other.test")


def test_legacy_units_missing_ohlc_and_invalid_envelope():
    from market_data.data_fetchers.akshare_fetcher import AkshareFetcher
    from market_data.data_fetchers.efinance_fetcher import EfinanceFetcher
    from market_data.data_fetchers.base import BaseFetcher, DataFetchError
    source = pd.DataFrame({"日期":["2026-09-04"],"开盘":[10.],"最高":[11.],"最低":[9.],"收盘":[10.],"成交量":[123.],"成交额":[123000.]})
    ak = AkshareFetcher.__new__(AkshareFetcher)
    assert ak._normalize_data(source, "000001").iloc[0]["volume"] == 12300
    source.attrs["volume_unit"] = "SHARE"
    assert ak._normalize_data(source, "000001").iloc[0]["volume"] == 123
    ef = EfinanceFetcher.__new__(EfinanceFetcher)
    with pytest.raises(DataFetchError): ef._normalize_data(source.drop(columns=["开盘"]), "000001")
    normalized = ak._normalize_data(source, "000001"); normalized["high"] = 8.
    with pytest.raises(DataFetchError): BaseFetcher._clean_data(ak, normalized)


def test_cashflow_units_do_not_collapse_and_unsafe_fundamental_path_disabled():
    from market_data.data_fetchers.fundamental_adapter import _currency_to_cny
    from market_data.provider_adapter import AdvancedBackend
    assert [_currency_to_cny(v) for v in ["1亿元","1万元","1元"]] == [1e8,1e4,1.]
    assert _currency_to_cny("1") is None
    assert _currency_to_cny("1", bare_unit="元") == 1.
    result = AdvancedBackend().get_financials("000001")
    assert result.quality == "PIT_UNAVAILABLE" and result.free_cash_flow is None


def test_tencent_sdk_wrong_sz000_volume_contract_explicitly_disabled():
    from market_data.data_fetchers.akshare_fetcher import AkshareFetcher
    from market_data.data_fetchers.base import DataFetchError
    fetcher = AkshareFetcher.__new__(AkshareFetcher)
    with pytest.raises(DataFetchError, match="SZ000_VOLUME_CONTRACT_REJECTED"):
        fetcher._fetch_stock_data_tx("000001", "2026-09-04", "2026-09-04")


def test_limits_retry_is_bounded_and_error_contains_no_proxy_details(monkeypatch):
    import requests
    from datetime import date
    from stockfish_core.observed_data import EastmoneyDailyLimitAdapter
    calls = []
    def failed(*args, **kwargs):
        calls.append(kwargs)
        raise requests.exceptions.ProxyError("credential=must-not-appear")
    monkeypatch.setattr(requests,"get",failed)
    with pytest.raises(UnavailableError) as error:
        EastmoneyDailyLimitAdapter().fetch(date(2026,9,4),"000001.SZ")
    assert len(calls) == 2
    assert str(error.value) == "EASTMONEY_LIMITS_UNAVAILABLE"
    assert all(c["timeout"] == (5,8) for c in calls)
