"""Historical cutoff cannot silently enter the current-day outcome contract."""
import pytest
from stockfish_core.yaogu import r2_closure_cli as cli


def arguments(command):
    return [command, '--db', '/TEST_FIXTURE/service.sqlite3', '--state', '/TEST_FIXTURE/state']



def test_daily_cli_does_not_accept_historical_cutoff_implicitly():
    args = arguments('r2-daily-run') + ['--run-id', 'TEST_FIXTURE_run', '--input-id', 'TEST_FIXTURE_input',
                                     '--knowledge-cutoff', '2026-10-16T15:51:06Z']
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(args)
    assert exc.value.code == 2
