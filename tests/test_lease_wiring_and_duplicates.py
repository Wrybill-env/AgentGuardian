"""
Tests for recovery-lease wiring into the product and duplicate-recovery guarding.

Covers:
  * GuardianConfig enables the lease by default and can disable/redirect it;
  * the CLI watch path constructs the state machine WITH the lease;
  * a repeated attempt cannot initiate duplicate recovery.
"""

import asyncio
from types import SimpleNamespace

import pytest

from guardian.core.adapter_capabilities import create_capabilities
from guardian.core.config import GuardianConfig
from guardian.core.recovery_lease import RecoveryLease
from guardian.core.state_machine import GuardianStateMachine
from guardian.core.types import (
    AdapterCapability, FailureClassification, FailureType, GuardianState,
    RecoveryAction, SafetyMode, TurnContext, TurnPhase,
)


class FakeVerifiedAdapter:
    def __init__(self):
        self.capabilities = create_capabilities(
            "fake", "fake", {c: True for c in AdapterCapability}
        )
        self.calls = []

    async def execute_recovery_action(self, action, context):
        self.calls.append(action.value)
        return True

    async def verify_recovery(self, action, context):
        return (True, {})


# --------------------------------------------------------------------------
# Config -> lease
# --------------------------------------------------------------------------

def test_config_default_enables_lease():
    lease = GuardianConfig().build_recovery_lease()
    assert isinstance(lease, RecoveryLease)
    assert lease.lease_dir.as_posix().endswith("leases")


def test_config_can_disable_lease():
    assert GuardianConfig(recovery_lease_enabled=False).build_recovery_lease() is None


def test_config_custom_lease_dir(tmp_path):
    cfg = GuardianConfig(recovery_lease_dir=str(tmp_path / "locks"))
    assert cfg.resolved_lease_dir() == str(tmp_path / "locks")


# --------------------------------------------------------------------------
# CLI watch-path wiring
# --------------------------------------------------------------------------

def test_cli_build_guardian_wires_lease(tmp_path):
    from guardian.cli import GuardianCLI

    cli = GuardianCLI()
    args = SimpleNamespace(
        safety_mode="observe_only", stale_threshold=60.0,
        config=str(tmp_path / "does-not-exist.yaml"),
    )
    sm = cli._build_guardian(args, FakeVerifiedAdapter())

    assert sm.recovery_executor.recovery_lease is not None
    # Safety defaults preserved.
    assert sm.safety_mode is SafetyMode.OBSERVE_ONLY
    assert sm.adapter is not None


def test_cli_build_guardian_respects_disabled_config(tmp_path):
    from guardian.cli import GuardianCLI

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("recovery_lease_enabled: false\n")
    cli = GuardianCLI()
    args = SimpleNamespace(
        safety_mode="observe_only", stale_threshold=60.0, config=str(cfg_path),
    )
    sm = cli._build_guardian(args, FakeVerifiedAdapter())
    assert sm.recovery_executor.recovery_lease is None


def test_cli_default_mode_is_observe_only(tmp_path):
    """Default CLI mode must never be AUTO_RECOVER."""
    from guardian.cli import GuardianCLI

    cli = GuardianCLI()
    parsed = cli.create_parser().parse_args(["observe", "goose"])
    assert parsed.safety_mode == "observe_only"


# --------------------------------------------------------------------------
# Duplicate-recovery guard
# --------------------------------------------------------------------------

def _failure(action=RecoveryAction.RECONNECT_ACP):
    return FailureClassification(
        failure_type=FailureType.RATE_LIMIT, retryable=True, replay_safe=True,
        requires_human=False, recommended_action=action, confidence=0.9,
    )


def test_attempt_recovery_refuses_while_in_flight(tmp_path):
    adapter = FakeVerifiedAdapter()
    sm = GuardianStateMachine(safety_mode=SafetyMode.AUTO_RECOVER, adapter=adapter,
                              recovery_lease=RecoveryLease(str(tmp_path / "leases")))
    sm.current_failure = _failure()
    sm.turn_context = TurnContext(session_id="s", phase=TurnPhase.USER_MESSAGE_SENT)
    sm._recovery_in_flight = True

    assert sm._attempt_recovery() is False
    assert adapter.calls == [], "no execution may occur while a recovery is in flight"


@pytest.mark.asyncio
async def test_repeated_attempt_does_not_duplicate_recovery(tmp_path):
    adapter = FakeVerifiedAdapter()
    sm = GuardianStateMachine(safety_mode=SafetyMode.AUTO_RECOVER, adapter=adapter,
                              recovery_lease=RecoveryLease(str(tmp_path / "leases")))
    sm.current_failure = _failure()
    sm.turn_context = TurnContext(session_id="s", phase=TurnPhase.USER_MESSAGE_SENT)
    sm.current_state = GuardianState.POSSIBLE_FAILURE

    first = sm._attempt_recovery()
    second = sm._attempt_recovery()   # repeated event, no await in between
    assert first is True
    assert second is False, "a duplicate attempt must be refused"
    assert len(sm.recovery_history) == 1

    await asyncio.sleep(0.15)         # let the scheduled recovery complete
    assert sm._recovery_in_flight is False
    assert len(adapter.calls) <= 1


@pytest.mark.asyncio
async def test_in_flight_guard_cleared_after_execution(tmp_path):
    adapter = FakeVerifiedAdapter()
    sm = GuardianStateMachine(safety_mode=SafetyMode.AUTO_RECOVER, adapter=adapter,
                              recovery_lease=RecoveryLease(str(tmp_path / "leases")))
    sm.current_failure = _failure()
    sm.turn_context = TurnContext(session_id="s", phase=TurnPhase.USER_MESSAGE_SENT)
    sm.current_state = GuardianState.POSSIBLE_FAILURE

    await sm._execute_recovery_async()
    assert sm._recovery_in_flight is False, "guard must be released after execution"


# --------------------------------------------------------------------------
# AUTO_RECOVER requires cross-process exclusion
# --------------------------------------------------------------------------

def test_auto_recover_refused_without_a_lease():
    """
    The state machine must refuse to start automatic recovery when no
    cross-process lease is configured (fail closed, not fail open).
    """
    adapter = FakeVerifiedAdapter()
    sm = GuardianStateMachine(safety_mode=SafetyMode.AUTO_RECOVER, adapter=adapter)
    sm.current_failure = _failure()
    sm.turn_context = TurnContext(session_id="s", phase=TurnPhase.USER_MESSAGE_SENT)
    sm.current_state = GuardianState.POSSIBLE_FAILURE

    assert sm._attempt_recovery() is False
    assert adapter.calls == [], "no execution without concurrency protection"
    assert sm.recovery_history == [], "no phantom attempt may be recorded"


def test_observe_only_still_runs_without_a_lease(tmp_path):
    """OBSERVE_ONLY must never require a lease (it never executes recovery)."""
    adapter = FakeVerifiedAdapter()
    sm = GuardianStateMachine(safety_mode=SafetyMode.OBSERVE_ONLY, adapter=adapter)
    sm.current_failure = _failure()
    sm.turn_context = TurnContext(session_id="s", phase=TurnPhase.USER_MESSAGE_SENT)
    assert sm._attempt_recovery() is False
    assert adapter.calls == []
