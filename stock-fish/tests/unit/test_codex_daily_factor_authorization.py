"""TEST_FIXTURE daily routing never executes an acquisition or business run."""
from unittest.mock import patch

import pytest

from scripts import codex_daily_v2 as daily
from scripts import codex_premarket_continuation as continuation
from tests.unit.test_codex_core import Base


class DailyFactorAuthorizationTests(Base):
    def arguments(self, command, *extra):
        return daily.parser().parse_args(["--allow-test-fixture", command,
            "--operation", str(self.root / "TEST_FIXTURE-operation.json"),
            "--expected-terminal-sha256", "a" * 64, *extra])

    def test_plan_only_prepares_bound_original_operation(self):
        args = self.arguments("premarket-factor-plan")
        proposal = {"TEST_FIXTURE": True, "status": "ADMITTED"}
        with patch.object(continuation, "prepare_future_factor_authorization", return_value=proposal) as prepare, \
                patch.object(continuation, "register_future_factor_authorization") as register:
            code, result = daily.execute(self.runtime, args)
        self.assertEqual((code, result), (0, proposal))
        prepare.assert_called_once_with(self.runtime, operation_path=args.operation,
            expected_terminal_sha256="a" * 64, allow_test_fixture=True)
        register.assert_not_called()

    def test_authorize_requires_explicit_flag_before_any_preparation(self):
        args = self.arguments("premarket-factor-authorize")
        with patch.object(continuation, "prepare_future_factor_authorization") as prepare:
            with pytest.raises(ValueError, match="EXPLICIT_FUTURE_FACTOR_AUTHORIZATION_REQUIRED"):
                daily.execute(self.runtime, args)
        prepare.assert_not_called()

    def test_authorize_uses_only_original_bounded_proposal(self):
        args = self.arguments("premarket-factor-authorize", "--authorize-future-observations")
        proposal = {"TEST_FIXTURE": True}
        with patch.object(continuation, "prepare_future_factor_authorization", return_value=proposal), \
                patch.object(continuation, "register_future_factor_authorization", return_value={"status": "RECORDED"}) as register:
            code, result = daily.execute(self.runtime, args)
        self.assertEqual((code, result["status"]), (0, "RECORDED"))
        register.assert_called_once_with(self.runtime, proposal, authorize_future_observations=True)

    def test_authorization_has_no_key_date_budget_or_retry_arguments(self):
        for option in ("--request-key", "--observation-date", "--max-requests", "--authorize-one-source-retry"):
            with self.subTest(option=option), pytest.raises(SystemExit):
                self.arguments("premarket-factor-authorize", option, "TEST_FIXTURE")
