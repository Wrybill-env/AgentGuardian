"""
Recovery Executor for Agent Guardian.

Executes planned recovery actions with mandatory safety revalidation.
Only executes actions where the adapter has VERIFIED_IMPLEMENTED capability.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, Awaitable
from enum import Enum
from datetime import datetime

from .types import (
    RecoveryAction,
    SafetyVerdict,
    SafetyMode,
    RecoveryAction,
    FailureType,
    TurnContext,
    FailureClassification,
)
from .safety_gate import SafetyGate, SafetyCheckResult
from .recovery_budget import RecoveryBudget, RecoveryBudgetManager, BudgetExhaustedReason, RecoveryBudgetConfig
from .recovery_ladder import RecoveryLadder
from .backoff import BackoffStrategy, BackoffConfig
from .adapter_capabilities import AdapterCapability, AdapterCapabilities, AgentAdapter


class ExecutionResult(Enum):
    """Result of recovery execution."""
    EXECUTED = "executed"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED = "execution_failed"
    BLOCKED = "blocked"
    ABORTED = "aborted"
    UNKNOWN = "unknown"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    SAFETY_BLOCKED = "safety_blocked"
    REVALIDATION_FAILED = "revalidation_failed"
    VERIFICATION_PENDING = "verification_pending"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    NOT_PROVEN = "not_proven"


@dataclass
class RecoveryExecutionContext:
    """Context for recovery execution with state snapshot for revalidation."""
    plan_id: str
    session_id: str
    turn_id: Optional[str] = None
    failure_type: Optional[FailureType] = None
    action: Optional[RecoveryAction] = None
    safety_verdict: SafetyVerdict = SafetyVerdict.AMBIGUOUS
    safety_reason: str = ""
    safety_blocking_factors: list = field(default_factory=list)
    required_capability: Optional[AdapterCapability] = None
    adapter_capability_status: str = "UNKNOWN"
    cycle_number: int = 0
    max_cycles: int = 0
    budget_remaining_attempts: Optional[int] = None
    budget_remaining_cycles: int = 0
    budget_remaining_time: float = 0.0
    # State snapshot at planning time for revalidation
    observed_state_snapshot: Dict[str, Any] = field(default_factory=dict)
    observed_assistant_chunks: int = 0
    observed_tools_started: int = 0
    observed_partial_tool_names: list = field(default_factory=list)
    observed_session_id: str = ""
    turn_phase: str = "IDLE"
    stop_reason: Optional[str] = None
    # TOCTOU protection - state generation
    planned_state_generation: int = 0
    current_state_generation: int = 0
    # Revalidation
    revalidation_timestamp: Optional[float] = None
    state_changed: bool = False
    state_change_details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryExecutionResult:
    """Result of a recovery execution attempt."""
    result: ExecutionResult
    action: RecoveryAction
    success: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    adapter_result: Optional[bool] = None
    verification_result: Optional[bool] = None
    timestamp: datetime = field(default_factory=datetime.now)
    # Post-execution state
    post_execution_safe: Optional[bool] = None
    post_execution_state: Dict[str, Any] = field(default_factory=dict)


class RecoveryExecutor:
    """
    Executes recovery actions with mandatory safety revalidation.
    
    The executor NEVER trusts the plan blindly. It revalidates critical
    safety conditions immediately before execution. If state has changed,
    execution is aborted.
    """
    
    def __init__(
        self,
        safety_gate: SafetyGate,
        budget_manager: RecoveryBudgetManager,
        ladder: Optional["RecoveryLadder"] = None,
        backoff: Optional[BackoffStrategy] = None,
        default_mode: SafetyMode = SafetyMode.OBSERVE_ONLY,
    ):
        self.safety_gate = safety_gate
        self.budget_manager = budget_manager
        self.ladder = ladder
        self.backoff = backoff
        self.default_mode = default_mode
    
    async def execute_plan(
            self,
            plan: "RecoveryPlan",
            adapter: AgentAdapter,
            current_session_state: TurnContext,
            current_stream_state: Optional[Dict[str, Any]] = None,
        ) -> RecoveryExecutionResult:
            """
            Execute a recovery plan with full safety revalidation.

            This is the main entry point. It will:
            1. Check budget
            2. Check mode (OBSERVE_ONLY blocks execution)
            3. Revalidate safety with CURRENT state
            4. Check adapter capability
            5. Execute via adapter
            6. Verify result
            7. Return explicit result
            """

            # Create execution context with state snapshot
            exec_context = self._create_execution_context(
                plan, adapter, current_session_state, current_stream_state
            )

            # Step 1: Check budget
            budget_result = self._check_budget(exec_context)
            if budget_result.result != ExecutionResult.EXECUTED:
                return budget_result

            # Step 2: Check mode
            mode_result = self._check_mode(exec_context)
            if mode_result.result != ExecutionResult.EXECUTED:
                return mode_result

            # Step 3: CRITICAL - Revalidate safety with CURRENT state
            revalidation_result = self._revalidate_safety(
                exec_context, current_session_state, current_stream_state
            )
            if revalidation_result.result != ExecutionResult.EXECUTED:
                return revalidation_result

            # Step 4: Check adapter capability
            capability_result = self._check_capability(exec_context, adapter)
            if capability_result.result != ExecutionResult.EXECUTED:
                return capability_result

            # Step 5: Check retry eligibility for retry actions
            retry_check = self._check_retry_eligibility(exec_context, plan)
            if retry_check.result != ExecutionResult.EXECUTED:
                return retry_check

            # Step 6: Execute via adapter
            execution_result = await self._execute_action(exec_context, adapter, current_session_state, current_stream_state)

            # Step 7: Verify result
            verification_result = await self._verify_result(
                execution_result, exec_context, adapter, current_session_state
            )

            return verification_result
    
    def _create_execution_context(
        self,
        plan: "RecoveryPlan",
        adapter: AgentAdapter,
        session_state: TurnContext,
        stream_state: Optional[Dict[str, Any]] = None,
    ) -> RecoveryExecutionContext:
        """Create execution context with state snapshot."""
        
        # Get planning-time state from plan's state_snapshot
        state_snapshot = getattr(plan, 'state_snapshot', {})
        observed_assistant_chunks = state_snapshot.get("assistant_chunks", 0)
        observed_tools_started = state_snapshot.get("tools_started", 0)
        observed_partial_tool_names = state_snapshot.get("partial_tool_names", [])
        turn_phase = state_snapshot.get("turn_phase", session_state.phase.value)
        stop_reason = state_snapshot.get("stop_reason", session_state.stop_reason)
        observed_session_id = state_snapshot.get("session_id", session_state.session_id)
        # Capture state generation at planning time
        state_generation = state_snapshot.get("state_generation", session_state.state_generation)
        
        # Determine required capability
        required_capability = self._action_to_capability(plan.recommended_action)
        
        # Check adapter capability status
        cap_result = adapter.capabilities.get(required_capability) if required_capability else None
        if cap_result:
            cap_status = "AVAILABLE" if cap_result.available else "UNAVAILABLE"
        else:
            cap_status = "UNAVAILABLE"
        
        return RecoveryExecutionContext(
            plan_id=plan.plan_id,
            session_id=plan.session_id,
            turn_id=plan.turn_id,
            failure_type=plan.failure_classification.failure_type if plan.failure_classification else None,
            action=plan.recommended_action,
            safety_verdict=plan.safety_verdict,
            safety_reason=plan.safety_reason,
            safety_blocking_factors=plan.safety_blocking_factors,
            required_capability=required_capability,
            adapter_capability_status=cap_status,
            cycle_number=plan.cycle_number,
            max_cycles=plan.max_cycles,
            budget_remaining_attempts=plan.budget_status.get("remaining_attempts"),
            budget_remaining_cycles=plan.budget_status.get("remaining_cycles", 0),
            budget_remaining_time=plan.budget_status.get("remaining_time_seconds", 0),
            # State snapshot at planning time
            observed_state_snapshot=state_snapshot,
            observed_assistant_chunks=observed_assistant_chunks,
            observed_tools_started=observed_tools_started,
            observed_partial_tool_names=observed_partial_tool_names,
            turn_phase=turn_phase,
            stop_reason=stop_reason,
            observed_session_id=observed_session_id,
            # TOCTOU protection
            planned_state_generation=state_generation,
            current_state_generation=session_state.state_generation,
        )
    
    def _check_budget(self, ctx: RecoveryExecutionContext) -> RecoveryExecutionResult:
        """Check if recovery budget allows execution."""
        budget = self.budget_manager.get_budget(
            ctx.failure_type.value if ctx.failure_type else "unknown"
        )
        
        # If no budget exists for this failure type, create one with generous defaults
        if not budget:
            budget = self.budget_manager.start_sequence(
                ctx.failure_type.value if ctx.failure_type else "unknown",
                RecoveryBudgetConfig(max_total_attempts=10, max_recovery_cycles=ctx.max_cycles or 5)
            )
        
        if not budget.can_continue():
            return RecoveryExecutionResult(
                result=ExecutionResult.BUDGET_EXHAUSTED,
                action=ctx.action,
                success=False,
                message=f"Recovery budget exhausted: {budget.exhausted_reason.value if budget and budget.exhausted_reason else 'unknown'}",
                details=ctx.get_status() if hasattr(ctx, 'get_status') else {},
            )
        
        # Update context with current budget
        ctx.budget_remaining_attempts = budget.get_remaining_attempts()
        ctx.budget_remaining_cycles = budget.get_remaining_cycles()
        ctx.budget_remaining_time = budget.get_remaining_time()
        
        return RecoveryExecutionResult(
            result=ExecutionResult.EXECUTED,
            action=ctx.action,
            success=True,
            message="Budget check passed",
        )
    
    def _check_mode(self, ctx: RecoveryExecutionContext) -> RecoveryExecutionResult:
        """Check if execution is allowed in current safety mode."""
        # The safety gate already enforces mode, but double-check
        if self.default_mode == SafetyMode.OBSERVE_ONLY:
            return RecoveryExecutionResult(
                result=ExecutionResult.BLOCKED,
                action=ctx.action,
                success=False,
                message="OBSERVE_ONLY mode - execution blocked",
                details={"safety_mode": self.default_mode.value},
            )
        
        if self.default_mode == SafetyMode.DRY_RUN:
            return RecoveryExecutionResult(
                result=ExecutionResult.BLOCKED,
                action=ctx.action,
                success=False,
                message="DRY_RUN mode - execution simulated only",
                details={
                    "safety_mode": self.default_mode.value,
                    "would_execute": True,
                },
            )
        
        return RecoveryExecutionResult(
            result=ExecutionResult.EXECUTED,
            action=ctx.action,
            success=True,
            message="Mode check passed",
        )
    
    def _revalidate_safety(
            self,
            ctx: RecoveryExecutionContext,
            current_session_state: TurnContext,
            current_stream_state: Optional[Dict[str, Any]] = None,
        ) -> RecoveryExecutionResult:
            """
            CRITICAL: Revalidate safety with CURRENT state before execution.

            This is the key safety mechanism - the world can change between
            planning and execution. If any critical state has changed,
            execution is aborted.
            """
        
            # TOCTOU protection: Check state generation FIRST
            if ctx.planned_state_generation != ctx.current_state_generation:
                return RecoveryExecutionResult(
                    result=ExecutionResult.REVALIDATION_FAILED,
                    action=ctx.action,
                    success=False,
                    message=f"State generation changed during planning - possible concurrent modification",
                    details={
                        "state_changed": True,
                        "changes": {
                            "state_generation": {
                                "planned": ctx.planned_state_generation,
                                "current": ctx.current_state_generation,
                            }
                        },
                        "revalidation_verdict": SafetyVerdict.UNSAFE.value,
                        "revalidation_reason": "State generation mismatch - concurrent state modification detected",
                        "blocking_factors": ["State generation changed"],
                    },
                )
        
            # Compare planned state with current state
            state_changed = False
            changes = {}
        
            # Check assistant output
            if current_session_state.assistant_chunks_received != ctx.observed_assistant_chunks:
                state_changed = True
                changes["assistant_chunks"] = {
                    "planned": ctx.observed_assistant_chunks,
                    "current": current_session_state.assistant_chunks_received,
                }
        
            # Check tool activity
            if current_session_state.tool_calls_started != ctx.observed_tools_started:
                state_changed = True
                changes["tools_started"] = {
                    "planned": ctx.observed_tools_started,
                    "current": current_session_state.tool_calls_started,
                }
        
            # Check partial tool names
            current_partial = list(current_stream_state.get("partial_tool_names", [])) if current_stream_state else []
            if set(current_partial) != set(ctx.observed_partial_tool_names):
                state_changed = True
                changes["partial_tool_names"] = {
                    "planned": ctx.observed_partial_tool_names,
                    "current": current_partial,
                }
        
            # Check turn phase
            if current_session_state.phase.value != ctx.turn_phase:
                state_changed = True
                changes["turn_phase"] = {
                    "planned": ctx.turn_phase,
                    "current": current_session_state.phase.value,
                }
        
            # Check stop reason
            if current_session_state.stop_reason != ctx.stop_reason:
                state_changed = True
                changes["stop_reason"] = {
                    "planned": ctx.stop_reason,
                    "current": current_session_state.stop_reason,
                }
        
            # Check session_id
            if current_session_state.session_id != ctx.observed_session_id:
                state_changed = True
                changes["session_id"] = {
                    "planned": ctx.observed_session_id,
                    "current": current_session_state.session_id,
                }
        
            ctx.state_changed = state_changed
            ctx.state_change_details = changes
            ctx.revalidation_timestamp = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0
        
            # If state changed, re-evaluate safety
            if state_changed:
                # Re-evaluate safety with current state
                safety_result = self.safety_gate.check_recovery_action_safety(
                    action=ctx.action,
                    turn_context=current_session_state,
                    failure_classification=None,  # We'll use the plan's classification
                    session_state=current_stream_state,
                )
            
                # Additionally, if the turn phase has advanced beyond assistant streaming,
                # that's a strong signal that side effects may have occurred
                if "turn_phase" in changes:
                    planned_phase = changes["turn_phase"]["planned"]
                    current_phase = changes["turn_phase"]["current"]
                    # If phase advanced from streaming to tool execution or beyond, block
                    phase_order = {
                        "idle": 0,
                        "user_message_sent": 1,
                        "assistant_streaming": 2,
                        "tool_executing": 3,
                        "tool_completing": 4,
                        "turn_ending": 5,
                        "turn_complete": 6,
                        "turn_error": 7,
                        "turn_cancelled": 8,
                    }
                    planned_order = phase_order.get(planned_phase, 0)
                    current_order = phase_order.get(current_phase, 0)
                    if current_order > planned_order:
                        safety_result = SafetyCheckResult(
                            verdict=SafetyVerdict.UNSAFE,
                            reason=f"Turn phase advanced from {planned_phase} to {current_phase} - side effects likely",
                            blocking_factors=[f"Phase advanced: {planned_phase} -> {current_phase}"],
                        )
            
                # Also check stop_reason - if it appeared when it was None before
                if "stop_reason" in changes:
                    planned_reason = changes["stop_reason"]["planned"]
                    current_reason = changes["stop_reason"]["current"]
                    if planned_reason is None and current_reason is not None:
                        safety_result = SafetyCheckResult(
                            verdict=SafetyVerdict.UNSAFE,
                            reason=f"Stop reason appeared: {current_reason} - turn may have completed",
                            blocking_factors=[f"Stop reason appeared: {current_reason}"],
                        )
            
                if safety_result.verdict != SafetyVerdict.SAFE:
                    return RecoveryExecutionResult(
                        result=ExecutionResult.REVALIDATION_FAILED,
                        action=ctx.action,
                        success=False,
                        message=f"State changed during planning - revalidation blocked execution",
                        details={
                            "state_changed": True,
                            "changes": changes,
                            "revalidation_verdict": safety_result.verdict.value,
                            "revalidation_reason": safety_result.reason,
                            "blocking_factors": safety_result.blocking_factors,
                        },
                    )
        
            # Even if no state change, verify safety verdict is still SAFE
            if ctx.safety_verdict != SafetyVerdict.SAFE:
                return RecoveryExecutionResult(
                    result=ExecutionResult.REVALIDATION_FAILED,
                    action=ctx.action,
                    success=False,
                    message=f"Plan safety verdict was not SAFE: {ctx.safety_verdict.value}",
                    details={"original_verdict": ctx.safety_verdict.value},
                )
        
            return RecoveryExecutionResult(
                result=ExecutionResult.EXECUTED,
                action=ctx.action,
                success=True,
                message="Safety revalidation passed",
                details={"state_changed": state_changed, "changes": changes} if state_changed else {},
            )
    
    def _check_capability(
        self,
        ctx: RecoveryExecutionContext,
        adapter: AgentAdapter,
    ) -> RecoveryExecutionResult:
        """Verify adapter has the required capability."""
        
        if not ctx.required_capability:
            return RecoveryExecutionResult(
                result=ExecutionResult.CAPABILITY_UNAVAILABLE,
                action=ctx.action,
                success=False,
                message=f"No capability mapped for action: {ctx.action.value}",
            )
        
        cap_result = adapter.capabilities.get(ctx.required_capability)
        
        if not cap_result or not cap_result.available:
            return RecoveryExecutionResult(
                result=ExecutionResult.CAPABILITY_UNAVAILABLE,
                action=ctx.action,
                success=False,
                message=f"Required capability not available: {ctx.required_capability.value}",
                details={
                    "required_capability": ctx.required_capability.value,
                    "capability_status": "AVAILABLE" if cap_result and cap_result.available else "UNAVAILABLE" if cap_result else "UNKNOWN",
                    "capability_reason": cap_result.reason if cap_result else "Not declared",
                },
            )
        
        return RecoveryExecutionResult(
            result=ExecutionResult.EXECUTED,
            action=ctx.action,
            success=True,
            message="Capability check passed",
        )

    def _check_retry_eligibility(
            self,
            ctx: RecoveryExecutionContext,
            plan: "RecoveryPlan",
        ) -> RecoveryExecutionResult:
            """
            Check if the failure is technically retryable for retry-type actions.

            This is a critical safety check: we only allow retry actions (WAIT_AND_RETRY,
            RECONNECT_ACP, CANCEL_AND_REPLAY, RELOAD_SESSION, FORK_SESSION) when the
            failure classification indicates the failure is technically retryable.

            This prevents retries for auth failures, config errors, user cancellations, etc.
            """

            # Actions that require technical retry eligibility
            retry_actions = {
                RecoveryAction.WAIT_AND_RETRY,
                RecoveryAction.RECONNECT_STREAM,
                RecoveryAction.RECONNECT_ACP,
                RecoveryAction.CANCEL_AND_REPLAY,
                RecoveryAction.RELOAD_SESSION,
                RecoveryAction.FORK_SESSION,
            }

            # If action is not a retry action, no retry eligibility check needed
            if ctx.action not in retry_actions:
                return RecoveryExecutionResult(
                    result=ExecutionResult.EXECUTED,
                    action=ctx.action,
                    success=True,
                    message="Action does not require retry eligibility check",
                )

            # Get failure classification from plan
            failure_classification = plan.failure_classification
            if not failure_classification:
                return RecoveryExecutionResult(
                    result=ExecutionResult.SAFETY_BLOCKED,
                    action=ctx.action,
                    success=False,
                    message="No failure classification available for retry eligibility check",
                    details={"action": ctx.action.value, "requires_retry_eligible": True},
                )

            # Check technical retry eligibility
            if not getattr(failure_classification, 'retryable', False):
                return RecoveryExecutionResult(
                    result=ExecutionResult.SAFETY_BLOCKED,
                    action=ctx.action,
                    success=False,
                    message=f"Failure type {failure_classification.failure_type.value} is not technically retryable - retry action blocked",
                    details={
                        "action": ctx.action.value,
                        "failure_type": failure_classification.failure_type.value,
                        "retryable": getattr(failure_classification, 'retryable', False),
                        "replay_safe": getattr(failure_classification, 'replay_safe', False),
                        "requires_retry_eligible": True,
                    },
                )

            return RecoveryExecutionResult(
                result=ExecutionResult.EXECUTED,
                action=ctx.action,
                success=True,
                message="Retry eligibility check passed",
            )

    async def execute_retry_with_backoff(
        self,
        plan: "RecoveryPlan",
        adapter: AgentAdapter,
        current_session_state: TurnContext,
        current_stream_state: Optional[Dict[str, Any]] = None,
        max_attempts: Optional[int] = None,
    ) -> RecoveryExecutionResult:
        """
        Execute a retry-type recovery action with exponential backoff.
        
        This method handles the complete retry loop:
        1. Execute plan (with safety checks)
        2. If failed and should retry, wait for backoff delay
        3. Repeat until success, max attempts, or budget exhausted
        
        Args:
            plan: Recovery plan with retry action
            adapter: Agent adapter
            current_session_state: Current session state
            current_stream_state: Current stream state
            max_attempts: Override max attempts (uses backoff config if None)
            
        Returns:
            Final RecoveryExecutionResult
        """
        if not self.backoff:
            raise ValueError("BackoffStrategy not configured - cannot execute with backoff")
        
        # Reset backoff for new sequence
        self.backoff.reset()
        
        max_tries = max_attempts if max_attempts is not None else self.backoff.config.max_attempts
        if self.backoff.config.indefinite_retry:
            max_tries = float('inf')
        
        attempt = 0
        last_result = None
        
        while attempt < max_tries:
            # Check if we should continue (budget, backoff)
            if not self.backoff.should_retry(attempt):
                break
            
            # Execute the plan
            result = await self.execute_plan(plan, adapter, current_session_state, current_stream_state)
            last_result = result
            
            # If successful, return
            if result.success and result.result == ExecutionResult.VERIFICATION_PASSED:
                result.details["backoff_attempts"] = attempt
                result.details["total_waited"] = self.backoff.get_total_waited()
                return result
            
            # If execution failed but we should retry
            if result.result in (ExecutionResult.EXECUTION_FAILED, ExecutionResult.NOT_PROVEN, ExecutionResult.VERIFICATION_FAILED):
                attempt += 1
                if attempt >= max_tries:
                    break
                
                # Wait for backoff delay
                delay = self.backoff.next_delay()
                await asyncio.sleep(delay)
                continue
            
            # For other results (safety blocked, capability unavailable, etc.), don't retry
            break
        
        # Return the last result
        if last_result:
            last_result.details["backoff_attempts"] = attempt
            last_result.details["total_waited"] = self.backoff.get_total_waited()
        return last_result

    async def _execute_action(
        self,
        ctx: RecoveryExecutionContext,
        adapter: AgentAdapter,
        current_session_state: TurnContext,
        current_stream_state: Optional[Dict[str, Any]] = None,
    ) -> RecoveryExecutionResult:
        """Execute the recovery action via adapter with final safety authorization."""
        
        # Record budget attempt BEFORE execution
        budget = self.budget_manager.get_budget(
            ctx.failure_type.value if ctx.failure_type else "unknown"
        )
        if budget:
            # Mark that an attempt is about to be made
            pass  # Budget tracking will record on completion
        
        # FINAL SAFETY AUTHORIZATION: Check generation immediately before external action
        # This is the TOCTOU closure - check generation immediately before external side effect
        if ctx.planned_state_generation != current_session_state.state_generation:
            return RecoveryExecutionResult(
                result=ExecutionResult.REVALIDATION_FAILED,
                action=ctx.action,
                success=False,
                message=f"State generation changed before external action - concurrent modification detected",
                details={
                    "state_changed": True,
                    "changes": {
                        "state_generation": {
                            "planned": ctx.planned_state_generation,
                            "current": current_session_state.state_generation,
                        }
                    },
                    "revalidation_verdict": SafetyVerdict.UNSAFE.value,
                    "revalidation_reason": "State generation mismatch at execution boundary - concurrent state modification detected",
                    "blocking_factors": ["State generation changed at execution boundary"],
                },
            )
        
        # Also check partial tool names at execution boundary (stream state not covered by generation)
        current_partial = list(current_stream_state.get("partial_tool_names", [])) if current_stream_state else []
        if set(current_partial) != set(ctx.observed_partial_tool_names):
            return RecoveryExecutionResult(
                result=ExecutionResult.REVALIDATION_FAILED,
                action=ctx.action,
                success=False,
                message=f"Partial tool names changed before external action - concurrent stream modification detected",
                details={
                    "state_changed": True,
                    "changes": {
                        "partial_tool_names": {
                            "planned": ctx.observed_partial_tool_names,
                            "current": current_partial,
                        }
                    },
                    "revalidation_verdict": SafetyVerdict.UNSAFE.value,
                    "revalidation_reason": "Partial tool names mismatch at execution boundary - concurrent stream modification detected",
                    "blocking_factors": ["Partial tool names changed at execution boundary"],
                },
            )
        
        try:
            # Execute via adapter - this is the FIRST EXTERNAL SIDE EFFECT boundary
            success = await adapter.execute_recovery_action(
                action=ctx.action,
                context={
                    "session_id": ctx.session_id,
                    "turn_id": ctx.turn_id,
                    "failure_type": ctx.failure_type.value if ctx.failure_type else "unknown",
                    "cycle_number": ctx.cycle_number,
                },
            )
            
            # POST-EXECUTION GENERATION CHECK: Catch mutations that occurred during adapter call
            if ctx.planned_state_generation != current_session_state.state_generation:
                return RecoveryExecutionResult(
                    result=ExecutionResult.REVALIDATION_FAILED,
                    action=ctx.action,
                    success=False,
                    message=f"State generation changed during external action - concurrent modification detected",
                    details={
                        "state_changed": True,
                        "changes": {
                            "state_generation": {
                                "planned": ctx.planned_state_generation,
                                "current": current_session_state.state_generation,
                            }
                        },
                        "revalidation_verdict": SafetyVerdict.UNSAFE.value,
                        "revalidation_reason": "State generation mismatch after execution boundary - concurrent state modification detected",
                        "blocking_factors": ["State generation changed during external action"],
                    },
                )
            
            # POST-EXECUTION PARTIAL TOOL CHECK: Catch stream mutations during adapter call
            current_partial_post = list(current_stream_state.get("partial_tool_names", [])) if current_stream_state else []
            if set(current_partial_post) != set(ctx.observed_partial_tool_names):
                return RecoveryExecutionResult(
                    result=ExecutionResult.REVALIDATION_FAILED,
                    action=ctx.action,
                    success=False,
                    message=f"Partial tool names changed during external action - concurrent stream modification detected",
                    details={
                        "state_changed": True,
                        "changes": {
                            "partial_tool_names": {
                                "planned": ctx.observed_partial_tool_names,
                                "current": current_partial_post,
                            }
                        },
                        "revalidation_verdict": SafetyVerdict.UNSAFE.value,
                        "revalidation_reason": "Partial tool names mismatch after execution boundary - concurrent stream modification detected",
                        "blocking_factors": ["Partial tool names changed during external action"],
                    },
                )
            
            if success:
                return RecoveryExecutionResult(
                    result=ExecutionResult.EXECUTION_SUCCEEDED,
                    action=ctx.action,
                    success=True,
                    message=f"Adapter executed action: {ctx.action.value}",
                    adapter_result=True,
                    details={"adapter": adapter.capabilities.adapter_name},
                )
            else:
                return RecoveryExecutionResult(
                    result=ExecutionResult.EXECUTION_FAILED,
                    action=ctx.action,
                    success=False,
                    message=f"Adapter executed action but returned failure: {ctx.action.value}",
                    adapter_result=False,
                    details={"adapter": adapter.capabilities.adapter_name},
                )
            
        except Exception as e:
            return RecoveryExecutionResult(
                result=ExecutionResult.EXECUTION_FAILED,
                action=ctx.action,
                success=False,
                message=f"Adapter execution failed: {str(e)}",
                adapter_result=False,
                details={"error": str(e), "adapter": adapter.capabilities.adapter_name},
            )
    
    async def _verify_result(
        self,
        execution_result: RecoveryExecutionResult,
        ctx: RecoveryExecutionContext,
        adapter: AgentAdapter,
        current_session_state: TurnContext,
    ) -> RecoveryExecutionResult:
        """Verify the execution result by observing new state."""
        
        # Preserve revalidation failures - they are not execution failures
        if execution_result.result == ExecutionResult.REVALIDATION_FAILED:
            return execution_result
        
        if not execution_result.success or not execution_result.adapter_result:
            return RecoveryExecutionResult(
                result=ExecutionResult.EXECUTION_FAILED,
                action=ctx.action,
                success=False,
                message=f"Execution failed: {execution_result.message}",
                adapter_result=execution_result.adapter_result,
                details=execution_result.details,
            )
        
        # Execution succeeded at transport level - now verify recovery
        # Call adapter-specific verification if available
        verification_result = None
        verification_details = {}
        
        if hasattr(adapter, 'verify_recovery'):
            try:
                verification_result, verification_details = await adapter.verify_recovery(
                    action=ctx.action,
                    context={
                        "session_id": ctx.session_id,
                        "turn_id": ctx.turn_id,
                        "failure_type": ctx.failure_type.value if ctx.failure_type else "unknown",
                        "cycle_number": ctx.cycle_number,
                        "execution_result": execution_result,
                    }
                )
            except Exception as e:
                verification_result = False
                verification_details = {"error": str(e)}
        
        # If adapter doesn't provide verification, we can't prove success
        if verification_result is None:
            return RecoveryExecutionResult(
                result=ExecutionResult.NOT_PROVEN,
                action=ctx.action,
                success=False,
                message=f"Adapter does not provide recovery verification - cannot prove recovery succeeded",
                adapter_result=execution_result.adapter_result,
                verification_result=None,
                details={
                    **execution_result.details,
                    "verification_note": "No verification provided by adapter",
                },
            )
        
        if verification_result:
            return RecoveryExecutionResult(
                result=ExecutionResult.VERIFICATION_PASSED,
                action=ctx.action,
                success=True,
                message=f"Recovery action {ctx.action.value} verified successfully",
                adapter_result=execution_result.adapter_result,
                verification_result=True,
                details={
                    **execution_result.details,
                    "verification": verification_details,
                },
            )
        else:
            return RecoveryExecutionResult(
                result=ExecutionResult.VERIFICATION_FAILED,
                action=ctx.action,
                success=False,
                message=f"Recovery verification failed",
                adapter_result=execution_result.adapter_result,
                verification_result=False,
                details={
                    **execution_result.details,
                    "verification": verification_details,
                },
            )
    
    def _action_to_capability(self, action: RecoveryAction) -> Optional[AdapterCapability]:
            """Map recovery action to adapter capability."""
            mapping = {
                RecoveryAction.WAIT_AND_RETRY: AdapterCapability.RETRY_REQUEST,
                RecoveryAction.CANCEL_AND_REPLAY: AdapterCapability.RETRY_REQUEST,  # Requires cancel + retry
                RecoveryAction.RECONNECT_STREAM: AdapterCapability.RECONNECT_STREAM,
                RecoveryAction.REATTACH_SESSION: AdapterCapability.REATTACH_SESSION,
                RecoveryAction.RELOAD_SESSION: AdapterCapability.LOAD_SESSION,
                RecoveryAction.FORK_SESSION: AdapterCapability.FORK_SESSION,
                RecoveryAction.SWITCH_PROVIDER: AdapterCapability.SWITCH_PROVIDER,
                RecoveryAction.WAIT_FOR_BACKEND: AdapterCapability.WAIT_FOR_BACKEND,
                RecoveryAction.RESTART_AGENT: AdapterCapability.RESTART_AGENT,
                RecoveryAction.HUMAN_INTERVENTION: AdapterCapability.GET_AGENT_STATUS,  # Always available
            }
            return mapping.get(action)