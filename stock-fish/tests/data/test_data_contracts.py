from datetime import date,datetime
import pytest
from stockfish_core.common import UnavailableError
from stockfish_core.data import SHANGHAI,TradingCalendar
from stockfish_core.observed_data import exchange_calendar_2026
NOW=datetime(2026,9,5,12,tzinfo=SHANGHAI)

def test_official_finite_calendar_and_earliest_entry():
    cal = exchange_calendar_2026()
    assert cal.earliest_trade_at(NOW) == datetime(2026,9,7,9,30,tzinfo=SHANGHAI)
    assert not cal.is_session(date(2026,2,23))
    assert not cal.is_session(date(2026,2,28))  # govt make-up Saturday is closed
    assert cal.next_session(date(2026,9,24)) == date(2026,9,28)
    with pytest.raises(UnavailableError): cal.next_session(date(2026,12,31))
    with pytest.raises(UnavailableError): cal.is_session(date(2027,1,4))
