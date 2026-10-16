"""
Freshness-contract tests.

`TurnContext.verified_fresh` is the only signal that authorizes *replay* recovery
actions (WAIT_AND_RETRY / CANCEL_AND_REPLAY). These tests pin down exactly who may
set it and prove an adapter cannot unilaterally authorize a replay.

Verified facts asserted here:
  * verified_fresh defaults to False;
  * the ONLY writer is persistence-backed reconstruction
    (guardian.core.persistence.create_turn_context_from_session);
  * no adapter / CLI source writes it;
  * a replay action is denied unless the context is verified fresh, even when the
    adapter claims every capability.
"""

import re
from pathlib import Path

import pytest

from guardian.core.adapter_capabilities import create_capabilities
from guardian.core.persistence import (
    StatePersister, create_persisted_session, create_turn_context_from_session,
)
from guardian.core.recovery_budget import RecoveryBudgetManager
from guardian.core.recovery_executor import ExecutionResult, RecoveryExecutor
from guardian.core.recovery_planner import RecoveryPlan
from guardian.core.safety_gate import SafetyGate
from guardian.core.session_state import create_session
from guardian.core.types import (
    AdapterCapability, FailureClassification, FailureType, RecoveryAction,
    SafetyMode, SafetyVerdict, TurnContext, TurnPhase,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARDIAN_DIR = REPO_ROOT / "guardian"

# Files legitimately allowed to *write* verified_fresh. freshness.py is the
# authorized evaluator (apply_freshness); persistence.py/ types.py are the
# reconstruction path and the field declaration.
_ALLOWED_WRITERS = {"persistence.py", "types.py", "freshness.py"}


def test_verified_fresh_defaults_false():
    ctx = TurnContext(session_id="s", phase=TurnPhase.USER_MESSAGE_SENT)
    assert ctx.verified_fresh is False


def test_only_persistence_assigns_verified_fresh():
    """
    Structural guard: no adapter, CLI, or core module may set verified_fresh.

    This prevents a future regression where an agent adapter asserts freshness
    (which is not independent evidence) and thereby unlocks replay.
    """
    # Match assignments only (plain, annotated, or keyword), never reads.
    pattern = re.compile(r"verified_fresh\s*(:[^=\n]*)?=(?!=)")
    offenders = []
    for path in GUARDIAN_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line) and path.name not in _ALLOWED_WRITERS:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert offenders == [], f"verified_fresh must not be assigned outside persistence: {offenders}"


def test_no_persisted_state_denies_freshness(tmp_path):
    session = create_session("sess_a", "fake")
    ctx = create_turn_context_from_session(session, loaded_state=None)
    assert ctx.verified_fresh is False


def test_persisted_state_alone_does_not_grant_freshness(tmp_path):
    """
    Persisted state is NOT fresh state: a snapshot alone must not authorize
    replay (it cannot prove the agent is still at that state, or that no tool
    executed since).
    """
    storage = str(tmp_path / "state.json")
    session = create_session("sess_b", "fake")
    session.start_turn(run_id="r1", user_prompt="hello")
    StatePersister(storage).save(session)

    session2, loaded = create_persisted_session("sess_b", "fake", storage)
    assert loaded is not None
    ctx = create_turn_context_from_session(session2, loaded_state=loaded)
    assert ctx.verified_fresh is False
    assert ctx.freshness_evidence is not None
    assert ctx.freshness_evidence.status.value == "historical_only"


def test_session_identity_mismatch_denies_freshness(tmp_path):
    """A persisted state file for a different session must not grant freshness."""
    storage = str(tmp_path / "state.json")
    session = create_session("sess_c", "fake")
    session.start_turn(run_id="r1", user_prompt="hello")
    StatePersister(storage).save(session)

    # Ask for a different session id -> load fails closed -> loaded is None.
    session2, loaded = create_persisted_session("sess_OTHER", "fake", storage)
    assert loaded is None
    ctx = create_turn_context_from_session(session2, loaded_state=loaded)
    assert ctx.verified_fresh is False


class _AllCapabilitiesAdapter:
    """Claims every capability must still not authorize an unverified replay."""

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


@pytest.mark.asyncio
async def test_adapter_cannot_authorize_unverified_replay():
    adapter = _AllCapabilitiesAdapter()
    executor = RecoveryExecutor(
        safety_gate=SafetyGate(SafetyMode.AUTO_RECOVER),
        budget_manager=RecoveryBudgetManager(),
        default_mode=SafetyMode.AUTO_RECOVER,
    )
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)
    assert turn.verified_fresh is False  # no persistence-backed reconstruction

    plan = RecoveryPlan(
        plan_id="p",
        failure_classification=FailureClassification(
            failure_type=FailureType.RATE_LIMIT, retryable=True, replay_safe=True,
            requires_human=False, recommended_action=RecoveryAction.WAIT_AND_RETRY,
            confidence=0.9,
        ),
        session_id="sess_1", turn_id="t",
        safety_verdict=SafetyVerdict.UNSAFE,  # set by the gate when not fresh
        recommended_action=RecoveryAction.WAIT_AND_RETRY,
        cycle_number=1, max_cycles=5, can_continue=True,
        state_snapshot={
            "assistant_chunks": 0, "tools_started": 0, "partial_tool_names": [],
            "turn_phase": TurnPhase.USER_MESSAGE_SENT.value, "stop_reason": None,
            "session_id": "sess_1", "state_generation": 0,
        },
    )

    result = await executor.execute_plan(plan, adapter, turn, {"partial_tool_names": []})
    assert result.success is False
    assert adapter.calls == [], "an unverified replay must never reach the adapter"
