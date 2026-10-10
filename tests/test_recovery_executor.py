"""
Tests for RecoveryExecutor - the core safety-revalidated execution engine.

These tests verify the complete safety contract:
- Plan -> Revalidate -> Execute -> Observe Result
- Safety gate is never bypassed
- Capability checks are mandatory
- State changes abort stale plans
- OBSERVE_ONLY and DRY_RUN modes block execution
- UNKNOWN capability never executes
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime

from guardian.core.types import (
    GuardianState,
    FailureType,
    RecoveryAction,
    SafetyMode,
    TurnPhase,
    TurnContext,
    FailureClassification,
    SafetyVerdict,
    AdapterCapability,
)
from guardian.core.safety_gate import SafetyGate, SafetyCheckResult
from guardian.core.recovery_budget import RecoveryBudgetManager, RecoveryBudgetConfig
from guardian.core.recovery_ladder import RecoveryLadder, LadderConfig
from guardian.core.backoff import BackoffStrategy, BackoffConfig
from guardian.core.adapter_capabilities import (
    AdapterCapabilities,
    CapabilityResult,
    AgentAdapter,
)
from guardian.core.recovery_planner import RecoveryPlan, RecoveryContext
from guardian.core.recovery_executor import (
    RecoveryExecutor,
    RecoveryExecutionContext,
    RecoveryExecutionResult,
    ExecutionResult,
)


# =============================================================================
# Test Doubles / Mocks
# =============================================================================

class MockAdapter(AgentAdapter):
    """Test adapter with controllable capabilities."""
    
    def __init__(self, name: str = "test_adapter"):
        self.adapter_name = name
        self._caps = AdapterCapabilities(adapter_name=name, agent_type="test")
        self._connected = False
        self._execute_results = {}
        self._execute_side_effects = {}
        self._verify_results = {}  # (action) -> (bool, dict)
        self._verify_side_effects = {}
    
    @property
    def capabilities(self) -> AdapterCapabilities:
        return self._caps
    
    async def connect(self) -> bool:
        self._connected = True
        return True
    
    async def disconnect(self):
        self._connected = False
    
    def is_connected(self) -> bool:
        return self._connected
    
    def set_capability(self, cap: AdapterCapability, available: bool, reason: str = ""):
        if available:
            self._caps.set_available(cap, reason)
        else:
            self._caps.set_unavailable(cap, reason)
    
    def set_execute_result(self, action: RecoveryAction, result: bool):
        self._execute_results[action] = result
    
    def set_execute_side_effect(self, action: RecoveryAction, exc: Exception):
        self._execute_side_effects[action] = exc
    
    def set_verify_result(self, action: RecoveryAction, result: bool, details: dict = None):
        self._verify_results[action] = (result, details or {})
    
    def set_verify_side_effect(self, action: RecoveryAction, exc: Exception):
        self._verify_side_effects[action] = exc
    
    async def execute_recovery_action(self, action: RecoveryAction, context: dict) -> bool:
        if action in self._execute_side_effects:
            raise self._execute_side_effects[action]
        return self._execute_results.get(action, True)
    
    async def verify_recovery(self, action: RecoveryAction, context: dict) -> tuple[bool, dict]:
        if action in self._verify_side_effects:
            raise self._verify_side_effects[action]
        return self._verify_results.get(action, (True, {"mock": "verified"}))
    
    # Abstract method stubs
    async def reconnect_stream(self, session_id: str) -> bool:
        return True
    async def cancel_stream(self, session_id: str) -> bool:
        return True
    async def reattach_session(self, session_id: str, **kwargs) -> bool:
        return True
    async def fork_session(self, session_id: str, fork_point: float, **kwargs) -> Optional[str]:
        return "forked_session"
    async def load_session(self, session_id: str, **kwargs) -> bool:
        return True
    async def save_session(self, session_id: str) -> bool:
        return True
    async def switch_provider(self, new_provider: str, **kwargs) -> bool:
        return True
    async def cancel_turn(self, session_id: str, turn_id: str = None) -> bool:
        return True
    async def retry_request(self, session_id: str, turn_id: str = None) -> bool:
        return True
    async def get_partial_tool_names(self, session_id: str) -> List[str]:
        return []
    async def get_tool_state(self, session_id: str, tool_call_id: str) -> Dict[str, Any]:
        return {}
    async def get_session_state(self, session_id: str) -> Dict[str, Any]:
        return {}
    async def get_turn_context(self, session_id: str) -> Dict[str, Any]:
        return {}
    async def verify_session_identity(self, session_id: str) -> bool:
        return True
    async def verify_session_exists(self, session_id: str) -> bool:
        return True
    async def get_agent_status(self) -> Dict[str, Any]:
        return {"status": "running"}
    async def is_agent_alive(self) -> bool:
        return True
    def get_status(self) -> Dict[str, Any]:
        return {"connected": self._connected}


def create_mock_session(
    session_id: str = "test_session",
    turn_id: str = "test_turn",
    phase: TurnPhase = TurnPhase.ASSISTANT_STREAMING,
    assistant_chunks: int = 0,
    tools_started: int = 0,
    partial_tool_names: List[str] = None,
    stop_reason: Optional[str] = None,
) -> TurnContext:
    """Create a mock turn context for testing."""
    return TurnContext(
        session_id=session_id,
        run_id=turn_id,
        phase=phase,
        assistant_chunks_received=assistant_chunks,
        tool_calls_started=tools_started,
        tool_calls_completed=0,
        tool_calls_failed=0,
        last_event_timestamp=0,
        stop_reason=stop_reason,
    )


def create_mock_plan(
    plan_id: str = "test_plan",
    action: RecoveryAction = RecoveryAction.WAIT_AND_RETRY,
    safety_verdict: SafetyVerdict = SafetyVerdict.SAFE,
    failure_type: FailureType = FailureType.RATE_LIMIT,
    session_id: str = "test_session",
    turn_id: str = "test_turn",
    cycle_number: int = 1,
    max_cycles: int = 5,
    safety_reason: str = "Safe to retry",
    blocking_factors: List[str] = None,
    budget_manager: RecoveryBudgetManager = None,
    # Failure classification parameters
    retryable: bool = True,
    replay_safe: bool = True,
    # Planning-time state (for revalidation)
    planning_assistant_chunks: int = 0,
    planning_tools_started: int = 0,
    planning_partial_tool_names: List[str] = None,
    planning_turn_phase: TurnPhase = TurnPhase.ASSISTANT_STREAMING,
    planning_stop_reason: str = None,
) -> RecoveryPlan:
    """Create a mock recovery plan."""
    classification = FailureClassification(
        failure_type=failure_type,
        retryable=retryable,
        replay_safe=replay_safe,
        requires_human=False,
        recommended_action=action,
        confidence=0.9,
        details={},
    )
    
    if budget_manager is None:
        budget_manager = RecoveryBudgetManager()
    budget = budget_manager.start_sequence(
        failure_type.value,
        RecoveryBudgetConfig(max_total_attempts=10, max_recovery_cycles=max_cycles)
    )
    
    plan = RecoveryPlan(
        plan_id=plan_id,
        failure_classification=classification,
        session_id=session_id,
        turn_id=turn_id,
        safety_verdict=safety_verdict,
        safety_reason=safety_reason,
        safety_blocking_factors=blocking_factors or [],
        recommended_action=action,
        cycle_number=cycle_number,
        max_cycles=max_cycles,
        budget_status=budget.get_status(),
        can_continue=True,
        required_capabilities=[],
        available_capabilities=[],
        missing_capabilities=[],
        reason="Test plan",
        confidence=0.9,
    )
    
    # Add planning-time state snapshot for revalidation
    plan.state_snapshot = {
        "assistant_chunks": planning_assistant_chunks,
        "tools_started": planning_tools_started,
        "partial_tool_names": planning_partial_tool_names or [],
        "turn_phase": planning_turn_phase.value,
        "stop_reason": planning_stop_reason,
    }
    
    return plan


# =============================================================================
# Safety Revalidation Tests (CRITICAL)
# =============================================================================

class TestSafetyRevalidation:
    """Tests proving executor revalidates safety before execution."""
    
    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def budget_manager(self, executor):
        """Get the budget manager from the executor."""
        return executor.budget_manager
    
    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        # Default: verification passes
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter
    
    @pytest.mark.asyncio
    async def test_executor_revalidates_safety_before_execution(self, executor, adapter, budget_manager):
        """CRITICAL: Executor must revalidate safety with CURRENT state before execution."""
        
        # Plan created when NO side effects
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
        )
        
        # Current state: NO side effects (matches plan)
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        assert result.result == ExecutionResult.VERIFICATION_PASSED
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_executor_blocks_if_assistant_output_appeared(self, executor, adapter, budget_manager):
        """CRITICAL: If assistant output appeared between plan and execute, BLOCK."""
        
        # Plan created when NO side effects
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
        )
        
        # Current state: ASSISTANT OUTPUT NOW EXISTS (changed since plan)
        current_state = create_mock_session(
            assistant_chunks=5,  # Changed!
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "State changed" in result.message
        assert result.details["changes"]["assistant_chunks"]["planned"] == 0
        assert result.details["changes"]["assistant_chunks"]["current"] == 5
    
    @pytest.mark.asyncio
    async def test_executor_blocks_if_tool_started_appeared(self, executor, adapter, budget_manager):
        """CRITICAL: If tool started between plan and execute, BLOCK."""
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            budget_manager=budget_manager,
        )
        
        # Current state: TOOL STARTED (changed since plan)
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=1,  # Changed!
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "State changed" in result.message
    
    @pytest.mark.asyncio
    async def test_executor_blocks_if_partial_tool_names_appeared(self, executor, adapter, budget_manager):
        """CRITICAL: If partial tool names appeared, BLOCK."""
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            budget_manager=budget_manager,
        )
        
        # Current state: PARTIAL TOOL NAMES EXIST (changed since plan)
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": ["write_file", "read_file"]}  # Changed!
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "partial_tool_names" in result.details["changes"]
    
    @pytest.mark.asyncio
    async def test_executor_blocks_if_turn_phase_changed(self, executor, adapter, budget_manager):
        """CRITICAL: If turn phase changed, BLOCK."""
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            budget_manager=budget_manager,
        )
        
        # Current state: TURN PHASE CHANGED
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
            phase=TurnPhase.TOOL_EXECUTING,  # Changed!
        )
        current_stream = {"partial_tool_names": []}
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "turn_phase" in result.details["changes"]
    
    @pytest.mark.asyncio
    async def test_executor_blocks_if_stop_reason_changed(self, executor, adapter, budget_manager):
        """CRITICAL: If stop reason appeared, BLOCK."""
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            budget_manager=budget_manager,
        )
        
        # Current state: STOP REASON APPEARED
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
            stop_reason="rate_limit",  # Changed!
        )
        current_stream = {"partial_tool_names": []}
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "stop_reason" in result.details["changes"]


# =============================================================================
# Mode Enforcement Tests
# =============================================================================

class TestModeEnforcement:
    """Tests proving OBSERVE_ONLY and DRY_RUN block execution."""
    
    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        # Default: verification passes
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter
    
    @pytest.fixture
    def plan(self):
        return create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
    
    @pytest.fixture
    def state(self):
        return create_mock_session(assistant_chunks=0, tools_started=0)
    
    @pytest.mark.asyncio
    async def test_observe_only_blocks_execution(self, adapter, plan, state):
        """OBSERVE_ONLY mode must block ALL recovery execution."""
        
        safety_gate = SafetyGate(SafetyMode.OBSERVE_ONLY)
        budget_manager = RecoveryBudgetManager()
        executor = RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.OBSERVE_ONLY,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.BLOCKED
        assert result.success is False
        assert "OBSERVE_ONLY" in result.message
    
    @pytest.mark.asyncio
    async def test_dry_run_blocks_execution(self, adapter, plan, state):
        """DRY_RUN mode must block execution but report what WOULD happen."""
        
        safety_gate = SafetyGate(SafetyMode.DRY_RUN)
        budget_manager = RecoveryBudgetManager()
        executor = RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.DRY_RUN,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.BLOCKED
        assert result.success is False
        assert "DRY_RUN" in result.message
        assert result.details.get("would_execute") is True


# =============================================================================
# Capability Enforcement Tests
# =============================================================================

class TestCapabilityEnforcement:
    """Tests proving executor requires VERIFIED_IMPLEMENTED capability."""
    
    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def state(self):
        return create_mock_session(assistant_chunks=0, tools_started=0)
    
    @pytest.mark.asyncio
    async def test_executor_blocks_if_capability_unavailable(self, executor, state):
        """Executor must block if adapter capability is UNAVAILABLE."""
        
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, False, "Not implemented")
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.CAPABILITY_UNAVAILABLE
        assert result.success is False
        assert "capability not available" in result.message.lower()
        assert result.details["required_capability"] == "retry_request"
    
    @pytest.mark.asyncio
    async def test_executor_allows_verified_implemented(self, executor, state):
        """Executor allows execution when capability is VERIFIED_IMPLEMENTED."""
        
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.VERIFICATION_PASSED
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_executor_blocks_unknown_capability(self, executor, state):
        """Executor must block if capability status is UNKNOWN."""
        
        adapter = MockAdapter()
        # Don't set capability at all = UNKNOWN
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.CAPABILITY_UNAVAILABLE
        assert result.success is False


# =============================================================================
# Safety Gate Integration Tests
# =============================================================================

class TestSafetyGateIntegration:
    """Tests proving executor integrates with SafetyGate correctly."""
    
    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        # Default: verification passes
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter
    
    @pytest.fixture
    def state(self):
        return create_mock_session(assistant_chunks=0, tools_started=0)
    
    @pytest.mark.asyncio
    async def test_executor_respects_unsafe_verdict(self, executor, adapter, state):
        """If plan has UNSAFE verdict, executor blocks."""
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.UNSAFE,
            safety_reason="Tool already started",
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "not SAFE" in result.message
    
    @pytest.mark.asyncio
    async def test_executor_respects_ambiguous_verdict(self, executor, adapter, state):
        """If plan has AMBIGUOUS verdict, executor blocks (fail closed)."""
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.AMBIGUOUS,
            safety_reason="Unknown tool state",
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "not SAFE" in result.message
    
    @pytest.mark.asyncio
    async def test_executor_respects_human_intervention(self, executor, adapter, state):
        """HUMAN_INTERVENTION should be allowed as it's always safe."""
        
        # Need GET_AGENT_STATUS capability for HUMAN_INTERVENTION
        adapter.set_capability(AdapterCapability.GET_AGENT_STATUS, True, "Implemented")
        
        plan = create_mock_plan(
            action=RecoveryAction.HUMAN_INTERVENTION,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.VERIFICATION_PASSED
        assert result.success is True


# =============================================================================
# Budget Enforcement Tests
# =============================================================================

class TestBudgetEnforcement:
    """Tests proving executor respects recovery budget."""
    
    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        # Default: verification passes
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter
    
    @pytest.fixture
    def state(self):
        return create_mock_session(assistant_chunks=0, tools_started=0)
    
    @pytest.mark.asyncio
    async def test_executor_blocks_budget_exhausted(self, executor, adapter, state):
        """Executor must block when budget is exhausted."""
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        # Manually exhaust budget
        budget_manager = executor.budget_manager
        budget = budget_manager.start_sequence(
            FailureType.RATE_LIMIT.value,
            RecoveryBudgetConfig(max_total_attempts=0)  # Zero attempts
        )
        budget.record_attempt(
            cycle_number=1,
            action="wait_and_retry",
            delay_seconds=1.0,
            success=False,
            safety_verdict="SAFE",
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.BUDGET_EXHAUSTED
        assert result.success is False
        assert "exhausted" in result.message.lower()


# =============================================================================
# Execution Result Tests
# =============================================================================

class TestExecutionResults:
    """Tests for execution result reporting."""
    
    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        # Default: verification passes
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter
    
    @pytest.fixture
    def state(self):
        return create_mock_session(assistant_chunks=0, tools_started=0)
    
    @pytest.mark.asyncio
    async def test_successful_execution_returns_succeeded(self, executor, adapter, state):
        """Successful execution returns VERIFICATION_PASSED result."""
        
        adapter.set_execute_result(RecoveryAction.WAIT_AND_RETRY, True)
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.VERIFICATION_PASSED
        assert result.success is True
        assert result.adapter_result is True
    
    @pytest.mark.asyncio
    async def test_failed_execution_returns_failed(self, executor, adapter, state):
        """Failed execution returns EXECUTION_FAILED result."""
        
        adapter.set_execute_result(RecoveryAction.WAIT_AND_RETRY, False)
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.EXECUTION_FAILED
        assert result.success is False
        assert result.adapter_result is False
    
    @pytest.mark.asyncio
    async def test_exception_during_execution_returns_failed(self, executor, adapter, state):
        """Exception during execution returns EXECUTION_FAILED result."""
        
        adapter.set_execute_side_effect(RecoveryAction.WAIT_AND_RETRY, RuntimeError("Connection lost"))
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.EXECUTION_FAILED
        assert result.success is False
        assert "Connection lost" in result.message


# =============================================================================
# Recovery Failure Handling Tests
# =============================================================================

class TestRecoveryFailureHandling:
    """Tests for proper recovery failure handling."""
    
    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        # Default: verification passes
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter
    
    @pytest.fixture
    def state(self):
        return create_mock_session(assistant_chunks=0, tools_started=0)
    
    @pytest.mark.asyncio
    async def test_recovery_failure_does_not_auto_retry(self, executor, adapter, state):
        """Recovery failure should NOT automatically retry - must re-plan."""
        
        # This is tested by verifying executor doesn't loop
        adapter.set_execute_result(RecoveryAction.WAIT_AND_RETRY, False)
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        result = await executor.execute_plan(plan, adapter, state, {})
        
        assert result.result == ExecutionResult.EXECUTION_FAILED
        # Executor does NOT automatically retry - it returns EXECUTION_FAILED
        # The caller (state machine) must re-observe, re-classify, re-plan


# =============================================================================
# Unknown Tool State Tests
# =============================================================================

class TestUnknownToolState:
    """Tests proving UNKNOWN tool state prevents replay."""
    
    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        adapter.set_capability(AdapterCapability.GET_TOOL_STATE, False, "Not implemented")
        return adapter
    
    @pytest.mark.asyncio
    async def test_unknown_tool_state_blocks_replay(self, executor, adapter):
        """UNKNOWN tool state must fail closed - no replay."""
        
        # Plan created when we don't know tool state
        plan = create_mock_plan(
            action=RecoveryAction.CANCEL_AND_REPLAY,
            safety_verdict=SafetyVerdict.AMBIGUOUS,  # Planner marks ambiguous
            safety_reason="Unknown tool state - cannot verify replay safety",
        )
        
        # Current state shows tool activity but unknown completion
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=1,  # Tool was started
            partial_tool_names=["write_file"],
        )
        current_stream = {"partial_tool_names": ["write_file"]}
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        # Should be blocked by revalidation (state changed or safety verdict not SAFE)
        assert result.result in (ExecutionResult.REVALIDATION_FAILED, ExecutionResult.SAFETY_BLOCKED)
        assert result.success is False


# =============================================================================
# Cancellation Respect Tests
# =============================================================================

class TestCancellationRespect:
    """Tests proving cancelled turns are not automatically replayed."""
    
    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        # Default: verification passes
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter
    
    @pytest.mark.asyncio
    async def test_cancelled_turn_blocks_replay(self, executor, adapter):
        """If turn was cancelled, replay must be blocked."""
        
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
        )
        
        # Current state: TURN CANCELLED
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            stop_reason="user_cancelled",
            phase=TurnPhase.TURN_CANCELLED,
        )
        current_stream = {}
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        # State changed (stop_reason) -> revalidation fails
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False


# =============================================================================
# Integration Test: Complete Loop
# =============================================================================

class TestCompleteRecoveryLoop:
    """Test the complete OBSERVE -> CLASSIFY -> VERIFY -> SAFETY -> PLAN -> REVALIDATE -> EXECUTE loop."""
    
    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        ladder = RecoveryLadder(LadderConfig())
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            ladder=ladder,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        # Default: verification passes
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter
    
    @pytest.mark.asyncio
    async def test_complete_safe_recovery_loop(self, executor, adapter):
        """Test complete recovery loop with verified safe conditions."""
        
        # Simulate observed state: rate limit, no output, no tools
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}
        
        # Create plan for rate limit (retryable, no side effects)
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            failure_type=FailureType.RATE_LIMIT,
        )
        
        # Execute
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        # Verify complete loop
        assert result.result == ExecutionResult.VERIFICATION_PASSED
        assert result.success is True
        assert result.adapter_result is True
        assert result.action == RecoveryAction.WAIT_AND_RETRY


# =============================================================================
# Deterministic TOCTOU Race Tests
# =============================================================================

class TestTOCTOURaceClosure:
    """Tests proving the TOCTOU race between safety revalidation and adapter execution is closed."""

    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )
    
    @pytest.fixture
    def budget_manager(self, executor):
        return executor.budget_manager

    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter

    @pytest.mark.asyncio
    async def test_race_assistant_output_between_revalidation_and_execution(self, executor, adapter, budget_manager):
        """Race test: assistant output appears AFTER revalidation but BEFORE adapter execution.
        
        The fix should catch this by checking generation at execution boundary.
        """
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
        )
        
        # Current state: NO side effects (matches plan)
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}
        
        # Monkey-patch the adapter to inject state change at the right moment
        original_execute = adapter.execute_recovery_action
        
        async def execute_with_mutation(action, context):
            # This runs AFTER revalidation but BEFORE actual external call
            # Simulate assistant output arriving during this window
            if action == RecoveryAction.WAIT_AND_RETRY:
                current_state.assistant_chunks_received = 5  # Mutate state!
                current_state.increment_generation()  # Increment generation!
            return await original_execute(action, context)
        
        adapter.execute_recovery_action = execute_with_mutation
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        # Should be BLOCKED by generation check at execution boundary
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "State generation changed" in result.message
        assert result.details["changes"]["state_generation"]["planned"] == 0
        assert result.details["changes"]["state_generation"]["current"] == 1

    @pytest.mark.asyncio
    async def test_race_tool_start_between_revalidation_and_execution(self, executor, adapter, budget_manager):
        """Race test: tool starts AFTER revalidation but BEFORE adapter execution."""
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            budget_manager=budget_manager,
        )
        
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}
        
        original_execute = adapter.execute_recovery_action
        
        async def execute_with_mutation(action, context):
            if action == RecoveryAction.WAIT_AND_RETRY:
                current_state.tool_calls_started = 1
                current_state.increment_generation()
            return await original_execute(action, context)
        
        adapter.execute_recovery_action = execute_with_mutation
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "State generation changed" in result.message
        assert result.details["changes"]["state_generation"]["current"] == 1

    @pytest.mark.asyncio
    async def test_race_partial_tool_between_revalidation_and_execution(self, executor, adapter, budget_manager):
        """Race test: partial tool names appear AFTER revalidation but BEFORE adapter execution."""
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            budget_manager=budget_manager,
        )
        
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}
        
        original_execute = adapter.execute_recovery_action
        
        async def execute_with_mutation(action, context):
            if action == RecoveryAction.WAIT_AND_RETRY:
                # Partial tools come from stream state, not TurnContext
                current_stream["partial_tool_names"] = ["write_file"]
            return await original_execute(action, context)
        
        adapter.execute_recovery_action = execute_with_mutation
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        # Should be blocked by partial tool name check at revalidation
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "partial_tool_names" in result.details["changes"]

    @pytest.mark.asyncio
    async def test_race_stop_reason_between_revalidation_and_execution(self, executor, adapter, budget_manager):
        """Race test: stop reason changes AFTER revalidation but BEFORE adapter execution."""
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            budget_manager=budget_manager,
        )
        
        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
            stop_reason=None,
        )
        current_stream = {"partial_tool_names": []}
        
        original_execute = adapter.execute_recovery_action
        
        async def execute_with_mutation(action, context):
            if action == RecoveryAction.WAIT_AND_RETRY:
                current_state.stop_reason = "rate_limit"
                current_state.increment_generation()
            return await original_execute(action, context)
        
        adapter.execute_recovery_action = execute_with_mutation
        
        result = await executor.execute_plan(plan, adapter, current_state, current_stream)
        
        assert result.result == ExecutionResult.REVALIDATION_FAILED
        assert result.success is False
        assert "State generation changed" in result.message

    @pytest.mark.asyncio
    async def test_no_false_block_when_state_unchanged(self, executor, adapter, budget_manager):
        """Prove that a genuinely unchanged safe state still permits the intended safe path."""
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
        )

        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}

        result = await executor.execute_plan(plan, adapter, current_state, current_stream)

        assert result.result == ExecutionResult.VERIFICATION_PASSED
        assert result.success is True
        assert result.adapter_result is True


# =============================================================================
# Retry Eligibility Tests (Build #26 - Separation of retryable vs replay_safe)
# =============================================================================

class TestRetryEligibility:
    """Tests for the new retry eligibility check in RecoveryExecutor.
    
    This verifies that retry actions (WAIT_AND_RETRY, RECONNECT_ACP, etc.) 
    are blocked when the failure is NOT technically retryable (auth failures, 
    config errors, cancellations) but allowed when technically retryable.
    """

    @pytest.fixture
    def executor(self):
        safety_gate = SafetyGate(SafetyMode.AUTO_RECOVER)
        budget_manager = RecoveryBudgetManager()
        return RecoveryExecutor(
            safety_gate=safety_gate,
            budget_manager=budget_manager,
            default_mode=SafetyMode.AUTO_RECOVER,
        )

    @pytest.fixture
    def budget_manager(self, executor):
        return executor.budget_manager

    @pytest.fixture
    def adapter(self):
        adapter = MockAdapter()
        adapter.set_capability(AdapterCapability.RETRY_REQUEST, True, "Implemented")
        adapter.set_capability(AdapterCapability.RECONNECT_STREAM, True, "Implemented")
        adapter.set_verify_result(RecoveryAction.WAIT_AND_RETRY, True)
        adapter.set_verify_result(RecoveryAction.RECONNECT_STREAM, True)
        adapter.set_verify_result(RecoveryAction.HUMAN_INTERVENTION, True)
        return adapter

    @pytest.mark.asyncio
    async def test_retry_action_allowed_when_technically_retryable(self, executor, adapter, budget_manager):
        """WAIT_AND_RETRY allowed when failure is technically retryable (rate limit, network)."""
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
            failure_type=FailureType.RATE_LIMIT,
            retryable=True,
            replay_safe=True,
        )

        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}

        result = await executor.execute_plan(plan, adapter, current_state, current_stream)

        # Should pass retry eligibility check and proceed to execution
        assert result.result in (ExecutionResult.VERIFICATION_PASSED, ExecutionResult.EXECUTION_SUCCEEDED, ExecutionResult.NOT_PROVEN)
        assert result.success is True or result.result == ExecutionResult.NOT_PROVEN

    @pytest.mark.asyncio
    async def test_retry_action_blocked_when_not_technically_retryable_auth_failure(self, executor, adapter, budget_manager):
        """WAIT_AND_RETRY blocked for auth failure (not technically retryable)."""
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
            failure_type=FailureType.AUTHENTICATION_FAILURE,
            retryable=False,
            replay_safe=False,
        )

        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}

        result = await executor.execute_plan(plan, adapter, current_state, current_stream)

        # Should be blocked by retry eligibility check
        assert result.result == ExecutionResult.SAFETY_BLOCKED
        assert result.success is False
        assert "not technically retryable" in result.message
        assert "authentication_failure" in result.message

    @pytest.mark.asyncio
    async def test_retry_action_blocked_when_not_technically_retryable_config_error(self, executor, adapter, budget_manager):
        """WAIT_AND_RETRY blocked for config error (not technically retryable)."""
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
            failure_type=FailureType.INVALID_CONFIGURATION,
            retryable=False,
            replay_safe=False,
        )

        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}

        result = await executor.execute_plan(plan, adapter, current_state, current_stream)

        # Should be blocked by retry eligibility check
        assert result.result == ExecutionResult.SAFETY_BLOCKED
        assert result.success is False
        assert "not technically retryable" in result.message
        assert "invalid_configuration" in result.message

    @pytest.mark.asyncio
    async def test_retry_action_blocked_when_not_technically_retryable_user_cancelled(self, executor, adapter, budget_manager):
        """WAIT_AND_RETRY blocked for user cancellation (not technically retryable)."""
        plan = create_mock_plan(
            action=RecoveryAction.WAIT_AND_RETRY,
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
            failure_type=FailureType.USER_CANCELLED,
            retryable=False,
            replay_safe=False,
        )

        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}

        result = await executor.execute_plan(plan, adapter, current_state, current_stream)

        # Should be blocked by retry eligibility check
        assert result.result == ExecutionResult.SAFETY_BLOCKED
        assert result.success is False
        assert "not technically retryable" in result.message
        assert "user_cancelled" in result.message

    @pytest.mark.asyncio
    async def test_reconnect_acp_blocked_when_not_technically_retryable(self, executor, adapter, budget_manager):
        """RECONNECT_ACP blocked for auth failure (not technically retryable)."""
        plan = create_mock_plan(
            action=RecoveryAction.RECONNECT_STREAM,  # Maps to RECONNECT_ACP capability
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
            failure_type=FailureType.AUTHENTICATION_FAILURE,
            retryable=False,
            replay_safe=False,
        )

        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}

        result = await executor.execute_plan(plan, adapter, current_state, current_stream)

        # Should be blocked by retry eligibility check
        assert result.result == ExecutionResult.SAFETY_BLOCKED
        assert result.success is False
        assert "not technically retryable" in result.message

    @pytest.mark.asyncio
    async def test_non_retry_action_no_eligibility_check(self, executor, adapter, budget_manager):
        """HUMAN_INTERVENTION (non-retry action) does not require retry eligibility check."""
        plan = create_mock_plan(
            action=RecoveryAction.HUMAN_INTERVENTION,
            safety_verdict=SafetyVerdict.SAFE,
            safety_reason="No side effects observed",
            budget_manager=budget_manager,
            failure_type=FailureType.AUTHENTICATION_FAILURE,
            retryable=False,  # Even though not retryable, HUMAN_INTERVENTION should not be blocked
            replay_safe=False,
        )

        current_state = create_mock_session(
            assistant_chunks=0,
            tools_started=0,
            partial_tool_names=[],
        )
        current_stream = {"partial_tool_names": []}

        result = await executor.execute_plan(plan, adapter, current_state, current_stream)

        # Should NOT be blocked by retry eligibility check
        # HUMAN_INTERVENTION is not a retry action
        assert result.result != ExecutionResult.SAFETY_BLOCKED
        # It will be blocked by other checks (no verification for HUMAN_INTERVENTION in mock)
        # but the retry eligibility check should pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])