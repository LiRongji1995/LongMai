"""TEST_FIXTURE SDK transport failures; no core strategy is mocked."""
import sys
from types import SimpleNamespace
from stockfish_core.yaogu.provider_requests_worker import baostock_request


class Cursor:
    def __init__(self,code,error='0'):
        self.error_code=error;self.fields=['code','dividOperateDate','backAdjustFactor'];self.code=code;self.read=False
    def next(self):
        if self.read:return False
        self.read=True;return True
    def get_row_data(self):return [self.code,'2000-01-01','1.0']


def test_failed_middle_factor_security_keeps_both_successful_neighbors(monkeypatch):
    called=[]
    def query(code,**kwargs):
        called.append(code)
        return Cursor(code,'10001001' if code=='sh.600001' else '0')
    sdk=SimpleNamespace(login=lambda:SimpleNamespace(error_code='0'),logout=lambda:None,query_adjust_factor=query,__version__='TEST_FIXTURE')
    monkeypatch.setitem(sys.modules,'baostock',sdk)
    result=baostock_request('adjustment_factors',dict(codes=['sh.600000','sh.600001','sh.600002'],start_date='1990-01-01',end_date='2026-09-04'))
    assert result['status']=='PARTIAL' and result['pagination_complete'] is False
    assert [row['code'] for row in result['rows']]==['sh.600000','sh.600002']
    states={row['code']:row for row in result['coverage']['security_statuses']}
    assert states['sh.600001']['status']=='FETCH_FAILED' and states['sh.600001']['error_code']=='BAOSTOCK_ERROR_10001001'
    assert states['sh.600000']['cursor_exhausted'] and states['sh.600002']['cursor_exhausted']
    assert called==['sh.600000','sh.600001','sh.600002']
