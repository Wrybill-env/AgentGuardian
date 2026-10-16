"""
Regression tests for the v1 release safety hardening.

These lock in the fixes for the adversarial-review findings:

  * the Hermes gate can never be MORE permissive than the core gate
    (it previously authorised prompt replay that the core gate denies);
  * reconnect/replay-class actions cannot be authorised by a non-empty
    session id alone - they require proven-fresh, corroborated state;
  * backend waiting is not "always safe": it is blocked when a tool may have
    executed, because the shipped handler reconnects and abandons the turn.
"""

import pytest

from guardian.core.recovery_budget import RecoveryBudgetManager
from guardian.core.recovery_ladder import RecoveryLadder, LadderConfig
from guardian.core.recovery_planner import RecoveryContext, RecoveryPlanner
from guardian.core.backoff import BackoffStrategy, DEFAULT_BACKOFF
from guardian.core.safety_gate import SafetyGate
from guardian.core.session_state import SessionState, TurnState, SideEffectEvidence
from guardian.core.types import (
    AdapterCapability, FailureClassification, FailureType, RecoveryAction,
    SafetyMode, SafetyVerdict, TurnContext, TurnPhase,
)
from guardian.hermes.safety_gate import HermesSafetyGate


RECONNECT_OR_REPLAY = [
    RecoveryAction.WAIT_AND_RETRY,
    RecoveryAction.CANCEL_AND_REPLAY,
    RecoveryAction.RECONNECT_ACP,
    RecoveryAction.RECONNECT_STREAM,
    RecoveryAction.RELOAD_SESSION,
    RecoveryAction.FORK_SESSION,
]


# --------------------------------------------------------------------------
# One safety policy
# --------------------------------------------------------------------------

def test_hermes_gate_is_never_more_permissive_than_core():
    """
    Same inputs must not yield 'safe' from one gate and 'deny' from the other.
    (Before the fix: core=AMBIGUOUS, Hermes=SAFE for prompt replay.)
    """
    tc = TurnContext(session_id="s1", phase=TurnPhase.USER_MESSAGE_SENT)
    assert tc.verified_fresh is False  # nothing can grant freshness today

    core = SafetyGate(SafetyMode.AUTO_RECOVER).check_recovery_action_safety(
        action=RecoveryAction.WAIT_AND_RETRY, turn_context=tc
    ).verdict
    hermes = HermesSafetyGate(SafetyMode.AUTO_RECOVER).check_recovery_action_safety(
        action=RecoveryAction.WAIT_AND_RETRY, turn_context=tc
    ).to_core_result().verdict

    assert core is SafetyVerdict.AMBIGUOUS
    assert hermes is SafetyVerdict.AMBIGUOUS


@pytest.mark.parametrize("action", RECONNECT_OR_REPLAY)
def test_action_gates_agree_on_denial(action):
    """Core and Hermes gates must agree for every reconnect/replay-class action."""
    tc = TurnContext(session_id="s1", phase=TurnPhase.USER_MESSAGE_SENT)
    core = SafetyGate(SafetyMode.AUTO_RECOVER).check_recovery_action_safety(
        action=action, turn_context=tc
    ).verdict
    hermes = HermesSafetyGate(SafetyMode.AUTO_RECOVER).check_recovery_action_safety(
        action=action, turn_context=tc
    ).to_core_result().verdict
    sev = {SafetyVerdict.SAFE: 0, SafetyVerdict.AMBIGUOUS: 1, SafetyVerdict.UNSAFE: 2}
    assert sev[hermes] >= sev[core]


# --------------------------------------------------------------------------
# Reconnect / replay require proven freshness, not a session id
# --------------------------------------------------------------------------

@pytest.mark.parametrize("action", RECONNECT_OR_REPLAY)
def test_reconnect_and_replay_are_not_authorised_by_session_id(action):
    tc = TurnContext(session_id="a-perfectly-valid-session-id", phase=TurnPhase.USER_MESSAGE_SENT)
    result = SafetyGate(SafetyMode.AUTO_RECOVER).check_recovery_action_safety(
        action=action, turn_context=tc
    )
    assert result.verdict is not SafetyVerdict.SAFE, (
        f"{action.value} must not be authorised by a session id alone"
    )


def test_reconnect_acp_with_tools_started_is_not_safe():
    tc = TurnContext(session_id="s1", phase=TurnPhase.TOOL_EXECUTING)
    tc.tool_calls_started = 1
    result = SafetyGate(SafetyMode.AUTO_RECOVER).check_recovery_action_safety(
        action=RecoveryAction.RECONNECT_ACP, turn_context=tc
    )
    assert result.verdict is not SafetyVerdict.SAFE


# --------------------------------------------------------------------------
# Backend waiting is not unconditionally safe
# --------------------------------------------------------------------------

def test_wait_for_backend_blocked_when_tool_may_have_executed():
    tc = TurnContext(session_id="s1", phase=TurnPhase.TOOL_EXECUTING)
    tc.tool_calls_started = 5
    result = SafetyGate(SafetyMode.AUTO_RECOVER).check_recovery_action_safety(
        action=RecoveryAction.WAIT_FOR_BACKEND, turn_context=tc
    )
    assert result.verdict is not SafetyVerdict.SAFE
    assert any("tool" in str(f).lower() for f in result.blocking_factors)


def test_wait_for_backend_safe_without_side_effects():
    tc = TurnContext(session_id="s1", phase=TurnPhase.USER_MESSAGE_SENT)
    result = SafetyGate(SafetyMode.AUTO_RECOVER).check_recovery_action_safety(
        action=RecoveryAction.WAIT_FOR_BACKEND, turn_context=tc
    )
    assert result.verdict is SafetyVerdict.SAFE


def _planner():
    return RecoveryPlanner(
        safety_gate=SafetyGate(SafetyMode.AUTO_RECOVER),
        budget_manager=RecoveryBudgetManager(),
        ladder=RecoveryLadder(LadderConfig()),
        backoff=BackoffStrategy(DEFAULT_BACKOFF),
    )


def _context(tools: int, phase: TurnPhase):
    tc = TurnContext(session_id="sess_1", phase=phase)
    tc.tool_calls_started = tools
    fc = FailureClassification(
        failure_type=FailureType.NETWORK_TRANSIENT, retryable=True, replay_safe=True,
        requires_human=False, recommended_action=RecoveryAction.WAIT_AND_RETRY, confidence=0.9,
    )
    ev = SideEffectEvidence()
    ev.tools_started = ["t"] * tools
    return RecoveryContext(
        session=SessionState(session_id="sess_1", turn_history=[]),
        current_turn=TurnState(turn_id="t1", session_id="sess_1", evidence=ev),
        turn_context=tc,
        failure_classification=fc,
        adapter_capabilities={c.value: True for c in AdapterCapability},
        adapter_name="goose",
        safety_mode=SafetyMode.AUTO_RECOVER,
    )


def test_planner_never_selects_recovery_when_tool_may_have_run():
    """End-to-end: with a tool started, no automatic action may be selected."""
    plan = _planner().create_plan(_context(tools=1, phase=TurnPhase.TOOL_EXECUTING))
    assert plan.recommended_action not in RECONNECT_OR_REPLAY
    assert plan.recommended_action is not RecoveryAction.WAIT_FOR_BACKEND
    assert plan.safety_verdict is not SafetyVerdict.SAFE or \
        plan.recommended_action is RecoveryAction.HUMAN_INTERVENTION
