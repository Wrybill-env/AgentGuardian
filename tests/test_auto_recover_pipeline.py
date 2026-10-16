"""
Deterministic end-to-end tests for the AUTO_RECOVER pipeline.

These exercise the *real* pipeline objects — GuardianStateMachine, RecoveryPlanner,
RecoveryExecutor, SafetyGate, RecoveryLadder, RecoveryBudgetManager — with a fake
adapter that performs no external side effects.

They prove:
  * a proven-safe, eligible recovery action reaches the executor and succeeds;
  * OBSERVE_ONLY and DRY_RUN never invoke the adapter;
  * a possibly-executed tool blocks *replay* actions unless state is proven fresh;
  * UNSAFE / UNKNOWN safety verdicts block execution;
  * session-identity mismatch and state-generation change block execution (TOCTOU);
  * a failed action never reports success;
  * an adapter exception fails closed with diagnostics;
  * a missing verification hook yields NOT_PROVEN, never success.
"""

import asyncio
import tempfile

import pytest

from guardian.core.backoff import BackoffStrategy, DEFAULT_BACKOFF
from guardian.core.adapter_capabilities import AdapterCapabilities, create_capabilities
from guardian.core.recovery_budget import RecoveryBudgetManager
from guardian.core.recovery_executor import (
    RecoveryExecutor, RecoveryExecutionResult, ExecutionResult,
)
from guardian.core.recovery_lease import RecoveryLease
from guardian.core.recovery_ladder import RecoveryLadder, LadderConfig
from guardian.core.recovery_planner import RecoveryPlanner, RecoveryContext, RecoveryPlan
from guardian.core.safety_gate import SafetyGate
from guardian.core.session_state import SessionState, TurnState
from guardian.core.state_machine import GuardianStateMachine
from guardian.core.types import (
    AdapterCapability, FailureClassification, FailureType, GuardianState,
    RecoveryAction, SafetyMode, SafetyVerdict, TurnContext, TurnPhase,
)

# Actions that re-send the user's prompt (and thus risk duplicate side effects).
# NOTE: RECONNECT_STREAM / RELOAD_SESSION / FORK_SESSION preserve session identity
# and do NOT re-execute the prompt, so they are intentionally permitted even when
# a tool may have executed — reconnect is not replay.
PROMPT_REPLAY_ACTIONS = {
    RecoveryAction.WAIT_AND_RETRY,
    RecoveryAction.CANCEL_AND_REPLAY,
}


# --------------------------------------------------------------------------
# Fake adapters (no external side effects)
# --------------------------------------------------------------------------

class FakeAdapter:
    """Implements the recovery-execution contract but NO verification hook."""

    def __init__(self, execute_result=True, raise_on_execute=False):
        self.capabilities = create_capabilities(
            "fake", "fake", {cap: True for cap in AdapterCapability}
        )
        self.execute_result = execute_result
        self.raise_on_execute = raise_on_execute
        self.calls = []

    async def execute_recovery_action(self, action, context):
        self.calls.append(("execute", action.value))
        if self.raise_on_execute:
            raise RuntimeError("adapter boom")
        return self.execute_result


class VerifiedFakeAdapter(FakeAdapter):
    """Adds the verify_recovery hook so a success can be *proven*."""

    def __init__(self, execute_result=True, verify_result=(True, {}), **kwargs):
        super().__init__(execute_result=execute_result, **kwargs)
        self.verify_result = verify_result

    async def verify_recovery(self, action, context):
        self.calls.append(("verify", action.value))
        return self.verify_result


def _rate_limit_classification(action=RecoveryAction.WAIT_AND_RETRY):
    return FailureClassification(
        failure_type=FailureType.RATE_LIMIT,
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=action,
        confidence=0.95,
    )


def _make_state_machine(mode, adapter, *, verified_fresh=True,
                        tool_started=0, assistant_chunks=0, session_id="sess_1"):
    # AUTO_RECOVER requires cross-process exclusion: the state machine refuses to
    # start automatic recovery without a lease, so supply one.
    lease = RecoveryLease(tempfile.mkdtemp()) if mode is SafetyMode.AUTO_RECOVER else None
    sm = GuardianStateMachine(safety_mode=mode, adapter=adapter, recovery_lease=lease)
    # Place the machine in an observably-active state without a real transport.
    sm.current_state = GuardianState.STREAMING
    sm.turn_context = TurnContext(session_id=session_id, phase=TurnPhase.USER_MESSAGE_SENT)
    sm.turn_context.verified_fresh = verified_fresh
    sm.turn_context.tool_calls_started = tool_started
    sm.turn_context.assistant_chunks_received = assistant_chunks
    sm.current_failure = _rate_limit_classification()
    return sm


def _planner(mode):
    return RecoveryPlanner(
        safety_gate=SafetyGate(mode),
        budget_manager=RecoveryBudgetManager(),
        ladder=RecoveryLadder(LadderConfig()),
        backoff=BackoffStrategy(DEFAULT_BACKOFF),
    )


def _context(mode, turn_context, session_id="sess_1"):
    return RecoveryContext(
        session=SessionState(session_id=session_id),
        current_turn=TurnState(session_id=session_id),
        turn_context=turn_context,
        failure_classification=_rate_limit_classification(),
        adapter_capabilities={cap.value: True for cap in AdapterCapability},
        adapter_name="fake",
        safety_mode=mode,
    )


def _plan(action=RecoveryAction.WAIT_AND_RETRY, verdict=SafetyVerdict.SAFE,
          retryable=True, snapshot=None):
    plan = RecoveryPlan(
        plan_id="plan_test",
        failure_classification=FailureClassification(
            failure_type=FailureType.RATE_LIMIT, retryable=retryable, replay_safe=True,
            requires_human=False, recommended_action=action, confidence=0.9,
        ),
        session_id="sess_1",
        turn_id="turn_1",
        safety_verdict=verdict,
        recommended_action=action,
        cycle_number=1,
        max_cycles=5,
        can_continue=True,
    )
    plan.state_snapshot = snapshot if snapshot is not None else {
        "assistant_chunks": 0, "tools_started": 0, "partial_tool_names": [],
        "turn_phase": TurnPhase.USER_MESSAGE_SENT.value, "stop_reason": None,
        "session_id": "sess_1", "state_generation": 0,
    }
    return plan


def _executor(mode=SafetyMode.AUTO_RECOVER):
    return RecoveryExecutor(
        safety_gate=SafetyGate(mode),
        budget_manager=RecoveryBudgetManager(),
        default_mode=mode,
    )


# --------------------------------------------------------------------------
# 1. Successful safe recovery through the real pipeline
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_recover_executes_proven_safe_action():
    """AUTO_RECOVER with a fresh, side-effect-free turn executes a safe action."""
    adapter = VerifiedFakeAdapter()
    sm = _make_state_machine(SafetyMode.AUTO_RECOVER, adapter, verified_fresh=True)

    ok = await sm._execute_recovery_async()

    assert ok is True
    assert adapter.calls, "adapter must have been invoked"
    assert adapter.calls[0] == ("execute", "wait_and_retry")
    assert ("verify", "wait_and_retry") in adapter.calls
    assert sm.metrics.successful_recoveries == 1
    assert sm.metrics.failed_recoveries == 0


@pytest.mark.asyncio
async def test_auto_recover_routing_reaches_recovery():
    """A connection-reset failure in AUTO_RECOVER is routed to the recovery executor."""
    adapter = VerifiedFakeAdapter()
    sm = _make_state_machine(SafetyMode.AUTO_RECOVER, adapter, verified_fresh=True)

    handled = sm._handle_failure({"error_message": "connection reset by peer"})
    assert handled is True

    # Let the scheduled recovery task run.
    await asyncio.sleep(0.1)

    assert adapter.calls, "scheduled recovery must have reached the adapter"
    assert sm.metrics.recovery_attempts >= 1
    assert any(t.to_state == GuardianState.RECOVERY_ATTEMPT for t in sm.transition_history)


# --------------------------------------------------------------------------
# 2. OBSERVE_ONLY / DRY_RUN never execute
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_observe_only_never_executes():
    adapter = VerifiedFakeAdapter()
    sm = _make_state_machine(SafetyMode.OBSERVE_ONLY, adapter)

    ok = await sm._execute_recovery_async()

    assert ok is False
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_dry_run_never_executes():
    adapter = VerifiedFakeAdapter()
    sm = _make_state_machine(SafetyMode.DRY_RUN, adapter)

    ok = await sm._execute_recovery_async()

    assert ok is False
    assert adapter.calls == []


# --------------------------------------------------------------------------
# 3. Possible tool execution blocks REPLAY unless state is proven fresh
# --------------------------------------------------------------------------

def test_planner_blocks_replay_when_tool_may_have_executed():
    turn = TurnContext(
        session_id="sess_1", phase=TurnPhase.TOOL_EXECUTING,
        tool_calls_started=1, verified_fresh=True,
    )
    plan = _planner(SafetyMode.AUTO_RECOVER).create_plan(
        _context(SafetyMode.AUTO_RECOVER, turn)
    )
    # A prompt-replaying action must never be selected when a tool may have executed.
    assert plan.recommended_action not in PROMPT_REPLAY_ACTIONS, (
        f"prompt-replay action {plan.recommended_action} chosen despite tool activity"
    )
    # And the gate itself must mark the replay action UNSAFE.
    gate = SafetyGate(SafetyMode.AUTO_RECOVER)
    assert gate.check_recovery_action_safety(
        RecoveryAction.WAIT_AND_RETRY, turn
    ).verdict == SafetyVerdict.UNSAFE


def test_planner_blocks_replay_when_not_verified_fresh():
    turn = TurnContext(
        session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT, verified_fresh=False,
    )
    plan = _planner(SafetyMode.AUTO_RECOVER).create_plan(
        _context(SafetyMode.AUTO_RECOVER, turn)
    )
    assert plan.recommended_action not in PROMPT_REPLAY_ACTIONS, (
        f"prompt-replay action {plan.recommended_action} chosen despite unverified freshness"
    )


def test_safety_gate_wait_and_retry_unsafe_with_tool_activity():
    gate = SafetyGate(SafetyMode.AUTO_RECOVER)
    turn = TurnContext(session_id="s", phase=TurnPhase.TOOL_EXECUTING, tool_calls_started=1,
                       verified_fresh=True)
    result = gate.check_recovery_action_safety(RecoveryAction.WAIT_AND_RETRY, turn)
    assert result.verdict == SafetyVerdict.UNSAFE


def test_safety_gate_ambiguous_without_turn_context():
    """Missing turn context must fail closed (AMBIGUOUS), never SAFE."""
    gate = SafetyGate(SafetyMode.AUTO_RECOVER)
    result = gate.check_recovery_action_safety(RecoveryAction.WAIT_AND_RETRY, None)
    assert result.verdict == SafetyVerdict.AMBIGUOUS


# --------------------------------------------------------------------------
# 4. Executor blocks unsafe / mismatched state
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_executor_blocks_unsafe_plan():
    adapter = VerifiedFakeAdapter()
    plan = _plan(verdict=SafetyVerdict.UNSAFE)
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)

    result = await _executor().execute_plan(plan, adapter, turn, {"partial_tool_names": []})

    assert result.result == ExecutionResult.REVALIDATION_FAILED
    assert result.success is False
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_executor_blocks_ambiguous_plan():
    adapter = VerifiedFakeAdapter()
    plan = _plan(verdict=SafetyVerdict.AMBIGUOUS)
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)

    result = await _executor().execute_plan(plan, adapter, turn, {"partial_tool_names": []})

    assert result.result == ExecutionResult.REVALIDATION_FAILED
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_executor_blocks_session_identity_mismatch():
    adapter = VerifiedFakeAdapter()
    plan = _plan()
    turn = TurnContext(session_id="DIFFERENT", phase=TurnPhase.USER_MESSAGE_SENT)

    result = await _executor().execute_plan(plan, adapter, turn, {"partial_tool_names": []})

    assert result.result == ExecutionResult.REVALIDATION_FAILED
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_executor_blocks_state_generation_change():
    adapter = VerifiedFakeAdapter()
    snapshot = _plan().state_snapshot
    snapshot["state_generation"] = 0
    plan = _plan(snapshot=snapshot)
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT, state_generation=1)

    result = await _executor().execute_plan(plan, adapter, turn, {"partial_tool_names": []})

    assert result.result == ExecutionResult.REVALIDATION_FAILED
    assert adapter.calls == []


# --------------------------------------------------------------------------
# 5. Failure / exception / no-proof handling
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_executor_failed_action_is_not_reported_as_success():
    adapter = VerifiedFakeAdapter(execute_result=False)
    plan = _plan()
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)

    result = await _executor().execute_plan(plan, adapter, turn, {"partial_tool_names": []})

    assert result.success is False
    assert result.result in (ExecutionResult.EXECUTION_FAILED, ExecutionResult.VERIFICATION_FAILED)


@pytest.mark.asyncio
async def test_executor_exception_fails_closed_with_diagnostics():
    adapter = VerifiedFakeAdapter(raise_on_execute=True)
    plan = _plan()
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)

    result = await _executor().execute_plan(plan, adapter, turn, {"partial_tool_names": []})

    assert result.success is False
    assert result.result == ExecutionResult.EXECUTION_FAILED
    assert "boom" in result.message


@pytest.mark.asyncio
async def test_executor_missing_verification_is_not_proven():
    """Without a verification hook, execution cannot be proven successful."""
    adapter = FakeAdapter()  # no verify_recovery
    plan = _plan()
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)

    result = await _executor().execute_plan(plan, adapter, turn, {"partial_tool_names": []})

    assert result.success is False
    assert result.result == ExecutionResult.NOT_PROVEN


@pytest.mark.asyncio
async def test_executor_non_retryable_failure_blocks_retry_action():
    adapter = VerifiedFakeAdapter()
    plan = _plan(action=RecoveryAction.WAIT_AND_RETRY, retryable=False)
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)

    result = await _executor().execute_plan(plan, adapter, turn, {"partial_tool_names": []})

    assert result.result == ExecutionResult.SAFETY_BLOCKED
    assert adapter.calls == []
