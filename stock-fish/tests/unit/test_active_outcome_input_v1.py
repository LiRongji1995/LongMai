"""TEST_FIXTURE original native archives, live capabilities and frozen oracle.

No provider call or original source is mocked. The original offline fixture
creator generates RAW, native core/export and seals before authentication.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import socket

import pytest

from scripts import active_outcome_input_v1 as composer
from stockfish_core.observed_data import exchange_calendar_2026
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import short_horizon_outcomes_v1 as outcomes
from tests.support.short_horizon_archive_fixture import fixture_archive


@pytest.fixture(scope="module", autouse=True)
def forbid_network():
    monkey = pytest.MonkeyPatch()
    def denied(*args, **kwargs):
        pytest.fail("TEST_FIXTURE_NETWORK_FORBIDDEN")
    for key in ("connect", "connect_ex"):
        monkey.setattr(socket.socket, key, denied)
    monkey.setattr(socket, "getaddrinfo", denied)
    monkey.setattr(socket, "create_connection", denied)
    yield
    monkey.undo()


@pytest.fixture(scope="module")
def original_days(tmp_path_factory):
    root = tmp_path_factory.mktemp("TEST_FIXTURE_daily_composition").resolve()
    axis = exchange_calendar_2026().sessions
    start = axis.index(date(2026, 10, 15))
    days = axis[start:start+11]
    assert len(days) == 11
    archives = [fixture_archive(root / str(day), observation_date=str(day)) for day in days]
    # These are eleven real original-native-core archives. A fixture budget is
    # explicit and isolated; it neither changes a production budget nor proves
    # whole-market capacity. One session pays every original terminal reread.
    session = bridge.ArchiveVerificationSession(max_seconds=600, max_files=20000)
    caps = [bridge.authenticate_archive(item["job"], expected_final_sha256=item["final_sha256"],
        allow_test_fixture=True, session=session, retain_input=True) for item in archives]
    return root, archives, session, caps


def evaluated(run, bundle):
    # Later than original fixture generation and all eleven business days;
    # does not rewrite any source clock or imply eleven real acquisition days.
    clock = max(datetime.now(timezone.utc), datetime(2026, 11, 5, tzinfo=timezone.utc)) + timedelta(minutes=1)
    return outcomes.evaluate(run, bundle, clock)["outcomes"]


def test_eleven_native_archives_mature_h10_without_promoting_late_historical_states(original_days):
    _, archives, session, caps = original_days
    run = archives[0]["run"]
    late = bridge._state(caps[-1])["retained_input"]
    baseline = evaluated(run, late)
    assert baseline["counts"] == {"UNKNOWN": 10}
    bundle, manifest = composer.compose(run, caps[0], caps[1:], session)
    product = evaluated(run, bundle)
    assert product["counts"] == {"MATURE": 10}
    assert {row["horizon"] for row in product["items"]} == {1, 2, 3, 5, 10}
    assert len(bundle.bars) == len(bundle.universe.securities) == 22
    assert manifest["input_id"] == product["outcome_input_id"] == "market_" + bridge.digest_market_input(bundle)
    assert manifest["source_input_ids"] == sorted({archive["run"]["input_id"] for archive in archives[1:]})
    assert manifest["signal"]["input_id"] == run["input_id"]
    assert manifest["all_input_ids"] == sorted({archive["run"]["input_id"] for archive in archives})
    assert manifest["coverage_type"] == "TEST_FIXTURE"
    assert manifest["historical_rows_promoted"] is manifest["prospective_authority_created"] is False
    assert product["market_edge"] == "NOT_ESTABLISHED"
    assert product["execution_status"] == "NOT_VERIFIED"


def test_missing_actual_day_stays_unknown_despite_later_archive_history(original_days):
    _, archives, session, caps = original_days
    # The fifth daily archive is missing; all late archives still carry its
    # historical bar and UNKNOWN board. Those rows must not be used to fill it.
    selected = caps[1:5] + caps[6:]
    bundle, manifest = composer.compose(archives[0]["run"], caps[0], selected, session)
    missing_day = date.fromisoformat(archives[5]["run"]["observation_date"])
    assert missing_day in bundle.sessions
    assert not [row for row in bundle.bars if row.session == missing_day]
    assert not [row for row in bundle.universe.securities if row.effective_from == missing_day]
    product = evaluated(archives[0]["run"], bundle)
    assert product["counts"] == {"MATURE": 6, "UNKNOWN": 4}
    assert all(row["status"] == ("UNKNOWN" if row["horizon"] >= 5 else "MATURE") for row in product["items"])
    assert manifest["missing_days_filled"] is False


def test_only_latest_day_is_not_a_ten_day_qualification_history(original_days):
    _, archives, session, caps = original_days
    bundle, _ = composer.compose(archives[0]["run"], caps[0], [caps[-1]], session)
    assert len(bundle.bars) == len(bundle.universe.securities) == 4
    assert evaluated(archives[0]["run"], bundle)["counts"] == {"UNKNOWN": 10}


def test_source_rows_clocks_and_receipts_are_unchanged(original_days):
    _, archives, session, caps = original_days
    bundle, manifest = composer.compose(archives[0]["run"], caps[0], caps[1:], session)
    for cap in caps:
        source = bridge._state(cap)["retained_input"]
        current_states = [row for row in source.universe.securities if row.effective_from == source.observation_date]
        current_bars = [row for row in source.bars if row.session == source.observation_date]
        assert current_states and current_bars
        for row in current_states:
            assert row in bundle.universe.securities
        for row in current_bars:
            assert row in bundle.bars
        for row in source.receipts:
            assert row in bundle.receipts
    assert tuple(day for day in bundle.sessions if day <= date.fromisoformat(archives[0]["run"]["observation_date"])) == bridge._state(caps[0])["retained_input"].sessions
    assert bundle.universe.snapshot_id.startswith("active_outcome_")
    assert not bundle.themes and not bundle.funds and not bundle.previous_emotions
    assert manifest["source_clocks_changed"] is False


def test_order_and_exact_duplicates_do_not_change_composition(original_days):
    _, archives, session, caps = original_days
    first, expected = composer.compose(archives[0]["run"], caps[0], caps[1:], session)
    actual, manifest = composer.compose(archives[0]["run"], caps[0], list(reversed(caps[1:])) + [caps[2]], session)
    assert actual == first and manifest == expected
    assert manifest["manifest_digest"] == bridge.digest_json_stream({key: value for key, value in manifest.items() if key != "manifest_digest"})


def test_fresh_native_reauthentication_produces_identical_manifest(original_days):
    _, archives, session, caps = original_days
    _, expected = composer.compose(archives[0]["run"], caps[0], caps[1:2], session)
    fresh = bridge.ArchiveVerificationSession(max_seconds=120, max_files=5000)
    new_caps = [bridge.authenticate_archive(item["job"], expected_final_sha256=item["final_sha256"],
        allow_test_fixture=True, session=fresh, retain_input=True) for item in archives[:2]]
    _, actual = composer.compose(archives[0]["run"], new_caps[0], new_caps[1:], fresh)
    assert actual == expected  # no current verification time / budget in identity
    with pytest.raises(ValueError, match="SAME_LIVE_VERIFICATION_SESSION_REQUIRED"):
        composer.compose(archives[0]["run"], caps[0], new_caps[1:], session)


def test_serialized_capability_and_mutated_signal_run_are_rejected(original_days):
    _, archives, session, caps = original_days
    with pytest.raises(ValueError, match="GENUINE_FRESH_ARCHIVE_CAPABILITY_REQUIRED"):
        composer.compose(archives[0]["run"], caps[0], [caps[1].audit()], session)
    changed = deepcopy(archives[0]["run"])
    changed["run_id"] = "TEST_FIXTURE_CHANGED"
    with pytest.raises(ValueError, match="SIGNAL_RUN_CHANGED"):
        composer.compose(changed, caps[0], caps[1:], session)
    with pytest.raises(ValueError, match="DAILY_MUST_FOLLOW_SIGNAL"):
        composer.compose(archives[0]["run"], caps[0], [caps[0]], session)
    with pytest.raises(ValueError, match="DAILY_CAPABILITIES_REQUIRED"):
        composer.compose(archives[0]["run"], caps[0], [], session)


def test_same_date_different_native_archive_is_rejected(original_days):
    root, archives, session, caps = original_days
    another = fixture_archive(root / "TEST_FIXTURE_conflicting_day", observation_date=archives[1]["run"]["observation_date"])
    cap = bridge.authenticate_archive(another["job"], expected_final_sha256=another["final_sha256"],
        allow_test_fixture=True, session=session, retain_input=True)
    with pytest.raises(ValueError, match="DATE_ARCHIVE_CONFLICT"):
        composer.compose(archives[0]["run"], caps[0], [caps[1], cap], session)


def test_pure_merge_rejects_calendar_hole_receipt_conflict_and_broad_interval(original_days):
    # Narrow typed-data unit oracles; these edited values are NEVER passed as
    # native capabilities and never published / resealed / registered.
    _, _, _, caps = original_days
    signal, daily = [bridge._state(cap)["retained_input"] for cap in caps[:2]]
    bad_calendar = daily.model_copy(update={"sessions": tuple(day for day in daily.sessions if day != signal.observation_date)})
    with pytest.raises(ValueError, match="CALENDAR_OVERLAP_CONFLICT"):
        composer._merge_calendar([signal, bad_calendar])
    source = signal.receipts[0]
    conflict = source.model_copy(update={"row_count": source.row_count + 1})
    with pytest.raises(ValueError, match="RECEIPT_ID_CONFLICT"):
        composer._unique_receipts([signal, signal.model_copy(update={"receipts": (conflict,)})], lambda: None)
    state = next(row for row in signal.universe.securities if row.effective_from == signal.observation_date)
    broad = state.model_copy(update={"effective_to": signal.observation_date + timedelta(days=3)})
    altered = signal.model_copy(update={"universe": signal.universe.model_copy(update={"securities": (broad,)})})
    with pytest.raises(ValueError, match="NON_DAILY_SECURITY_INTERVAL"):
        composer._current_rows(altered, lambda: None)


def test_terminal_source_change_is_not_hidden_by_retained_input(tmp_path):
    archive = fixture_archive(tmp_path / "TEST_FIXTURE_changed_original", observation_date="2026-10-15")
    daily = fixture_archive(tmp_path / "TEST_FIXTURE_later_original", observation_date="2026-10-16")
    session = bridge.ArchiveVerificationSession(max_seconds=120)
    caps = [bridge.authenticate_archive(item["job"], expected_final_sha256=item["final_sha256"],
        allow_test_fixture=True, session=session, retain_input=True) for item in (archive, daily)]
    from pathlib import Path
    source = Path(daily["items"][0]["raw"]["path"])
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(ValueError, match="IDENTITY_CHANGED|REGISTERED_FILE_CHANGED"):
        composer.compose(archive["run"], caps[0], caps[1:], session)
