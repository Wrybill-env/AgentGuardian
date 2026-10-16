"""
Tests for the hardening added in this run:

  * cross-process RecoveryLease (fail-closed concurrent recovery exclusion);
  * three-state capability reporting (SUPPORTED / UNSUPPORTED / UNKNOWN);
  * structured, secret-safe recovery diagnostics (explain_last_recovery).
"""

import asyncio

import pytest

from guardian.core.adapter_capabilities import (
    CapabilityState, create_capabilities,
)
from guardian.core.recovery_budget import RecoveryBudgetManager
from guardian.core.recovery_executor import (
    ExecutionResult, RecoveryExecutor,
)
from guardian.core.recovery_lease import RecoveryLease
from guardian.core.recovery_planner import RecoveryPlan
from guardian.core.safety_gate import SafetyGate
from guardian.core.state_machine import GuardianStateMachine
from guardian.core.types import (
    AdapterCapability, FailureClassification, FailureType, GuardianState,
    RecoveryAction, SafetyMode, SafetyVerdict, TurnContext, TurnPhase,
)

ALL_CAPS = {cap: True for cap in AdapterCapability}


class VerifiedAdapter:
    """Fake adapter with a verification hook (no external side effects)."""

    def __init__(self, execute_result=True, capabilities=None, raise_on_execute=False):
        self.capabilities = create_capabilities(
            "fake", "fake", capabilities if capabilities is not None else ALL_CAPS
        )
        self.execute_result = execute_result
        self.raise_on_execute = raise_on_execute
        self.calls = []

    async def execute_recovery_action(self, action, context):
        self.calls.append(("execute", action.value))
        if self.raise_on_execute:
            raise RuntimeError("adapter boom")
        return self.execute_result

    async def verify_recovery(self, action, context):
        self.calls.append(("verify", action.value))
        return (True, {})


def _plan(action=RecoveryAction.WAIT_AND_RETRY, verdict=SafetyVerdict.SAFE, session_id="sess_1"):
    return RecoveryPlan(
        plan_id="p",
        failure_classification=FailureClassification(
            failure_type=FailureType.RATE_LIMIT, retryable=True, replay_safe=True,
            requires_human=False, recommended_action=action, confidence=0.9,
        ),
        session_id=session_id,
        turn_id="t",
        safety_verdict=verdict,
        recommended_action=action,
        cycle_number=1,
        max_cycles=5,
        can_continue=True,
        state_snapshot={
            "assistant_chunks": 0, "tools_started": 0, "partial_tool_names": [],
            "turn_phase": TurnPhase.USER_MESSAGE_SENT.value, "stop_reason": None,
            "session_id": session_id, "state_generation": 0,
        },
    )


def _executor(lease=None, mode=SafetyMode.AUTO_RECOVER):
    return RecoveryExecutor(
        safety_gate=SafetyGate(mode),
        budget_manager=RecoveryBudgetManager(),
        default_mode=mode,
        recovery_lease=lease,
    )


# --------------------------------------------------------------------------
# Three-state capability reporting
# --------------------------------------------------------------------------

def test_capability_state_tristate():
    caps = create_capabilities(
        "a", "a",
        {AdapterCapability.RETRY_REQUEST: True, AdapterCapability.FORK_SESSION: False},
    )
    assert caps.state(AdapterCapability.RETRY_REQUEST) == CapabilityState.SUPPORTED
    assert caps.state(AdapterCapability.FORK_SESSION) == CapabilityState.UNSUPPORTED
    # Never declared -> UNKNOWN, which must not be treated as available.
    assert caps.state(AdapterCapability.SWITCH_PROVIDER) == CapabilityState.UNKNOWN
    assert caps.has(AdapterCapability.SWITCH_PROVIDER) is False


@pytest.mark.asyncio
async def test_unsupported_capability_fails_closed_in_executor():
    # Adapter explicitly does NOT support RETRY_REQUEST.
    caps = dict(ALL_CAPS)
    caps[AdapterCapability.RETRY_REQUEST] = False
    adapter = VerifiedAdapter(capabilities=caps)
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)

    result = await _executor().execute_plan(_plan(), adapter, turn, {"partial_tool_names": []})

    assert result.result == ExecutionResult.CAPABILITY_UNAVAILABLE
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_unknown_capability_fails_closed_in_executor():
    # Adapter omits RETRY_REQUEST entirely -> UNKNOWN.
    caps = {c: True for c in AdapterCapability if c != AdapterCapability.RETRY_REQUEST}
    adapter = VerifiedAdapter(capabilities=caps)
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)

    result = await _executor().execute_plan(_plan(), adapter, turn, {"partial_tool_names": []})

    assert result.result == ExecutionResult.CAPABILITY_UNAVAILABLE
    assert adapter.calls == []


# --------------------------------------------------------------------------
# Cross-process recovery lease
# --------------------------------------------------------------------------

class BlockingAdapter(VerifiedAdapter):
    def __init__(self, entered: asyncio.Event, release: asyncio.Event, **kw):
        super().__init__(**kw)
        self._entered = entered
        self._release = release

    async def execute_recovery_action(self, action, context):
        self.calls.append(("execute", action.value))
        self._entered.set()
        await self._release.wait()
        return True


@pytest.mark.asyncio
async def test_recovery_lease_excludes_concurrent_execution(tmp_path):
    lease = RecoveryLease(str(tmp_path / "leases"))
    entered, release = asyncio.Event(), asyncio.Event()

    adapter_a = BlockingAdapter(entered, release)
    adapter_b = VerifiedAdapter()
    exec_a = _executor(lease=lease)
    exec_b = _executor(lease=lease)
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)

    task_a = asyncio.create_task(
        exec_a.execute_plan(_plan(), adapter_a, turn, {"partial_tool_names": []})
    )
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    # A now holds the lease and is blocked inside the adapter.

    result_b = await exec_b.execute_plan(_plan(), adapter_b, turn, {"partial_tool_names": []})
    assert result_b.result == ExecutionResult.BLOCKED
    assert result_b.success is False
    assert adapter_b.calls == [], "second process must not reach the adapter"

    # Release A; the lease must then be available again.
    release.set()
    result_a = await asyncio.wait_for(task_a, timeout=2.0)
    assert result_a.success is True

    result_b2 = await exec_b.execute_plan(_plan(), adapter_b, turn, {"partial_tool_names": []})
    assert result_b2.success is True
    assert adapter_b.calls, "after release the second executor must proceed"


@pytest.mark.asyncio
async def test_recovery_lease_released_after_failure(tmp_path):
    lease = RecoveryLease(str(tmp_path / "leases"))
    adapter_fail = VerifiedAdapter(execute_result=False)
    adapter_ok = VerifiedAdapter()
    turn = TurnContext(session_id="sess_x", phase=TurnPhase.USER_MESSAGE_SENT)

    r1 = await _executor(lease=lease).execute_plan(_plan(session_id="sess_x"), adapter_fail, turn, {"partial_tool_names": []})
    assert r1.success is False

    # Lease must have been released -> a subsequent attempt can acquire it.
    r2 = await _executor(lease=lease).execute_plan(_plan(session_id="sess_x"), adapter_ok, turn, {"partial_tool_names": []})
    assert r2.success is True


@pytest.mark.asyncio
async def test_recovery_lease_released_after_exception(tmp_path):
    lease = RecoveryLease(str(tmp_path / "leases"))
    adapter_boom = VerifiedAdapter(raise_on_execute=True)
    adapter_ok = VerifiedAdapter()
    turn = TurnContext(session_id="sess_y", phase=TurnPhase.USER_MESSAGE_SENT)

    r1 = await _executor(lease=lease).execute_plan(_plan(session_id="sess_y"), adapter_boom, turn, {"partial_tool_names": []})
    assert r1.result == ExecutionResult.EXECUTION_FAILED

    r2 = await _executor(lease=lease).execute_plan(_plan(session_id="sess_y"), adapter_ok, turn, {"partial_tool_names": []})
    assert r2.success is True


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

def test_exec_context_carries_failure_classification():
    """Execution revalidation must carry the plan's failure classification."""
    from guardian.core.recovery_executor import RecoveryExecutionContext

    plan = _plan()
    adapter = VerifiedAdapter()
    turn = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)
    ctx = _executor()._create_execution_context(plan, adapter, turn, {"partial_tool_names": []})
    assert isinstance(ctx, RecoveryExecutionContext)
    assert ctx.failure_classification is plan.failure_classification


def test_state_machine_wires_recovery_lease(tmp_path):
    """The state machine must pass its lease through to the executor."""
    lease = RecoveryLease(str(tmp_path / "leases"))
    sm = GuardianStateMachine(safety_mode=SafetyMode.AUTO_RECOVER, recovery_lease=lease)
    assert sm.recovery_executor.recovery_lease is lease


def test_lease_fails_closed_when_dir_unwritable(tmp_path):
    """An unusable lease directory must deny recovery, not silently allow it."""
    blocker = tmp_path / "leases"
    blocker.write_text("i am a file, not a directory")
    lease = RecoveryLease(str(blocker))
    assert lease.try_acquire("sess") is None


def test_explain_last_recovery_before_any_decision():
    sm = GuardianStateMachine(safety_mode=SafetyMode.OBSERVE_ONLY)
    assert sm.explain_last_recovery() == {
        "available": False,
        "reason": "No recovery decision has been made yet",
    }


@pytest.mark.asyncio
async def test_explain_last_recovery_after_success():
    adapter = VerifiedAdapter()
    sm = GuardianStateMachine(safety_mode=SafetyMode.AUTO_RECOVER, adapter=adapter)
    sm.current_state = GuardianState.STREAMING
    sm.turn_context = TurnContext(session_id="sess_1", phase=TurnPhase.USER_MESSAGE_SENT)
    sm.turn_context.verified_fresh = True
    sm.current_failure = FailureClassification(
        failure_type=FailureType.RATE_LIMIT, retryable=True, replay_safe=True,
        requires_human=False, recommended_action=RecoveryAction.WAIT_AND_RETRY,
        confidence=0.95,
    )

    await sm._execute_recovery_async()

    info = sm.explain_last_recovery()
    assert info["available"] is True
    assert info["action"] == "wait_and_retry"
    assert info["safety"]["verdict"] in ("safe", "unsafe", "ambiguous")
    assert info["execution"]["attempted"] is True
    assert info["execution"]["success"] is True
    # Diagnostics must never leak prompt/payload material.
    assert "user_prompt" not in info
    assert "raw_error" not in info
