"""
Safety gate for Guardian recovery decisions.

This module implements the critical safety checks that prevent
unsafe replay/recovery operations. All recovery paths must pass
through the safety gate.
"""

from typing import Optional
from dataclasses import dataclass

from .types import (
    TurnContext,
    TurnPhase,
    RecoveryAction,
    FailureType,
    SafetyMode,
    FailureClassification,
    SafetyVerdict,
)


@dataclass
class SafetyCheckResult:
    """Result of a safety check."""
    verdict: SafetyVerdict
    reason: str
    details: dict = None
    blocking_factors: list = None
    
    def __post_init__(self):
        if self.details is None:
            self.details = {}
        if self.blocking_factors is None:
            self.blocking_factors = []


class SafetyGate:
    """
    Centralized safety gate for all recovery decisions.
    
    Every recovery action must pass through this gate.
    The gate enforces the principle: when in doubt, fail closed.
    """
    
    def __init__(
        self,
        safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY,
        require_human_for_ambiguous: bool = True,
    ):
        self.safety_mode = safety_mode
        self.require_human_for_ambiguous = require_human_for_ambiguous
    
    def check_replay_safety(
        self,
        turn_context: TurnContext,
        failure_classification: Optional[object] = None,
        session_state: Optional[object] = None,
    ) -> SafetyCheckResult:
        """
        Check if it's safe to replay the user's prompt.
        
        This is the PRIMARY safety gate. Replay is ONLY safe if:
        1. No assistant output has been observed in this turn
        2. No tool calls have been dispatched in this turn
        3. No partial tool names have been observed (tools may have started)
        4. The turn hasn't completed (successfully or with error)
        5. The failure is classified as replay-safe
        
        Args:
            turn_context: Current turn state
            failure_classification: Optional FailureClassification
            session_state: Optional streaming state with partial tool info
            
        Returns:
            SafetyCheckResult with verdict and reasoning
        """
        blocking_factors = []
        
        # Check 1: Assistant output observed
        if turn_context.has_assistant_output():
            blocking_factors.append(
                f"Assistant output observed ({turn_context.assistant_chunks_received} chunks)"
            )
        
        # Check 2: Tool activity observed
        if turn_context.has_tool_activity():
            blocking_factors.append(
                f"Tool activity observed ({turn_context.tool_calls_started} started, "
                f"{turn_context.tool_calls_completed} completed)"
            )
        
        # Check 3: Partial tool names observed (from stream state)
        if session_state and hasattr(session_state, 'get'):
            partial_tools = session_state.get("partial_tool_names", [])
            if partial_tools:
                blocking_factors.append(
                    f"Partial tool names observed: {partial_tools}"
                )
        
        # Check 4: Turn already completed
        if turn_context.phase in (
            TurnPhase.TURN_COMPLETE,
            TurnPhase.TURN_ENDING,
        ):
            blocking_factors.append(f"Turn already completed (phase: {turn_context.phase.value})")
        
        # Check 5: Stop reason indicates completion
        if turn_context.stop_reason in ("end_turn", "max_turns"):
            blocking_factors.append(f"Stop reason indicates completion: {turn_context.stop_reason}")
        
        # Check 6: Failure classification says not replay-safe
        if failure_classification and hasattr(failure_classification, 'replay_safe'):
            if not failure_classification.replay_safe:
                blocking_factors.append(
                    f"Failure type {getattr(failure_classification, 'failure_type', 'unknown')} "
                    "not marked as replay-safe"
                )
        
        # Determine verdict
        if blocking_factors:
            return SafetyCheckResult(
                verdict=SafetyVerdict.UNSAFE,
                reason="Replay would risk duplicate side effects",
                details={
                    "assistant_chunks": turn_context.assistant_chunks_received,
                    "tool_calls_started": turn_context.tool_calls_started,
                    "tool_calls_completed": turn_context.tool_calls_completed,
                    "phase": turn_context.phase.value,
                    "stop_reason": turn_context.stop_reason,
                },
                blocking_factors=blocking_factors,
            )
        
        # All checks passed - safe to replay
        return SafetyCheckResult(
            verdict=SafetyVerdict.SAFE,
            reason="No side effects observed in current turn",
            details={
                "assistant_chunks": turn_context.assistant_chunks_received,
                "tool_calls_started": turn_context.tool_calls_started,
                "phase": turn_context.phase.value,
            },
            blocking_factors=[],
        )
    
    def check_recovery_action_safety(
        self,
        action: RecoveryAction,
        turn_context: TurnContext,
        failure_classification: Optional[object] = None,
        session_state: Optional[object] = None,
    ) -> SafetyCheckResult:
        """
        Check if a specific recovery action is safe.
        
        Different actions have different safety requirements:
        - WAIT_AND_RETRY: Requires no side effects observed
        - CANCEL_AND_REPLAY: Requires no side effects, plus ability to cancel
        - RECONNECT_ACP: Generally safe (no prompt replay)
        - RELOAD_SESSION: Requires session identity preserved
        - FORK_SESSION: Requires fork capability, safe point available
        - WAIT_FOR_BACKEND: Safe (no prompt replay)
        """
        # In OBSERVE_ONLY mode, NO recovery actions are allowed
        if self.safety_mode == SafetyMode.OBSERVE_ONLY:
            return SafetyCheckResult(
                verdict=SafetyVerdict.UNSAFE,
                reason=f"Safety mode is OBSERVE_ONLY - no recovery actions permitted",
                details={"safety_mode": self.safety_mode.value},
                blocking_factors=["OBSERVE_ONLY mode active"],
            )
        
        # In DRY_RUN mode, all actions are "safe" but won't execute
        if self.safety_mode == SafetyMode.DRY_RUN:
            return SafetyCheckResult(
                verdict=SafetyVerdict.SAFE,
                reason=f"DRY_RUN mode - action would be simulated only",
                details={
                    "action": action.value,
                    "safety_mode": self.safety_mode.value,
                    "would_execute": False,
                },
                blocking_factors=[],
            )
        
        # AUTO_RECOVER mode - check specific action safety
        # For actions that could replay the prompt, require verified fresh state
        # (proof that no restart gap exists). Without persistence, this defaults
        # to False, meaning replay is blocked after any potential restart.
        if self.safety_mode == SafetyMode.AUTO_RECOVER:
            replay_actions = {
                RecoveryAction.WAIT_AND_RETRY,
                RecoveryAction.CANCEL_AND_REPLAY,
                RecoveryAction.RELOAD_SESSION,
                RecoveryAction.FORK_SESSION,
            }
            if action in replay_actions and not turn_context.verified_fresh:
                return SafetyCheckResult(
                    verdict=SafetyVerdict.AMBIGUOUS,
                    reason=(
                        "Cannot verify turn state is fresh (no restart gap). "
                        "Prior execution state unknown - automatic replay blocked."
                    ),
                    blocking_factors=["Unverified fresh state: potential restart gap"],
                )

        if action == RecoveryAction.WAIT_AND_RETRY:
            # Only safe if no side effects yet
            return self.check_replay_safety(turn_context, failure_classification, session_state)
        
        elif action == RecoveryAction.CANCEL_AND_REPLAY:
            # Need to be able to cancel AND replay safely
            cancel_check = self.check_replay_safety(turn_context, failure_classification, session_state)
            if cancel_check.verdict == SafetyVerdict.UNSAFE:
                return cancel_check
            # Also need cancellation capability
            if turn_context.phase == TurnPhase.IDLE:
                return SafetyCheckResult(
                    verdict=SafetyVerdict.UNSAFE,
                    reason="No active turn to cancel",
                    blocking_factors=["No active turn"],
                )
            return SafetyCheckResult(
                verdict=SafetyVerdict.SAFE,
                reason="Turn can be cancelled and replayed safely",
                details={"cancel_phase": turn_context.phase.value},
            )
        
        elif action == RecoveryAction.RECONNECT_ACP:
            # Reconnection doesn't replay prompts - generally safe
            # But need to ensure session identity will be preserved
            if turn_context.session_id:
                return SafetyCheckResult(
                    verdict=SafetyVerdict.SAFE,
                    reason="ACP reconnection preserves session, no prompt replay",
                    details={"session_id": turn_context.session_id},
                )
            return SafetyCheckResult(
                verdict=SafetyVerdict.AMBIGUOUS,
                reason="No session ID available to verify identity after reconnect",
                blocking_factors=["Missing session_id"],
            )
        
        elif action == RecoveryAction.RELOAD_SESSION:
            # Requires known session ID and session still exists
            if not turn_context.session_id:
                return SafetyCheckResult(
                    verdict=SafetyVerdict.UNSAFE,
                    reason="Cannot reload session - no session ID",
                    blocking_factors=["Missing session_id"],
                )
            return SafetyCheckResult(
                verdict=SafetyVerdict.SAFE,
                reason="Session reload preserves history, no prompt replay",
                details={"session_id": turn_context.session_id},
            )
        
        elif action == RecoveryAction.FORK_SESSION:
            # Requires fork capability and safe fork point
            if not turn_context.session_id:
                return SafetyCheckResult(
                    verdict=SafetyVerdict.UNSAFE,
                    reason="Cannot fork - no session ID",
                    blocking_factors=["Missing session_id"],
                )
            # Need a safe fork point (before current turn)
            if turn_context.phase == TurnPhase.USER_MESSAGE_SENT:
                return SafetyCheckResult(
                    verdict=SafetyVerdict.SAFE,
                    reason="Can fork before current turn's user message",
                )
            return SafetyCheckResult(
                verdict=SafetyVerdict.AMBIGUOUS,
                reason="Fork point may include partial side effects",
                blocking_factors=["Uncertain fork point safety"],
            )
        
        elif action == RecoveryAction.WAIT_FOR_BACKEND:
            # Safe - just waiting, no prompt replay
            return SafetyCheckResult(
                verdict=SafetyVerdict.SAFE,
                reason="Waiting for backend restart - no prompt replay",
            )
        
        elif action == RecoveryAction.HUMAN_INTERVENTION:
            # Always "safe" - just requires human decision
            return SafetyCheckResult(
                verdict=SafetyVerdict.SAFE,
                reason="Human intervention requested - no automatic action",
            )
        
        elif action == RecoveryAction.NO_SAFE_RECOVERY:
            # Explicitly no safe recovery
            return SafetyCheckResult(
                verdict=SafetyVerdict.UNSAFE,
                reason="No safe recovery path identified",
                blocking_factors=["Explicit NO_SAFE_RECOVERY"],
            )
        
        # Unknown action - fail closed
        return SafetyCheckResult(
            verdict=SafetyVerdict.AMBIGUOUS,
            reason=f"Unknown recovery action: {action.value}",
            blocking_factors=["Unknown action"],
        )
    
    def evaluate_recovery_decision(
        self,
        failure_classification: object,
        turn_context: TurnContext,
    ) -> SafetyCheckResult:
        """
        Evaluate whether the recommended recovery action is safe to execute.
        
        This is the main entry point for recovery decision validation.
        """
        recommended_action = getattr(failure_classification, 'recommended_action', None)
        
        if recommended_action is None:
            return SafetyCheckResult(
                verdict=SafetyVerdict.AMBIGUOUS,
                reason="No recovery action recommended",
            )
        
        if recommended_action == RecoveryAction.NONE:
            return SafetyCheckResult(
                verdict=SafetyVerdict.SAFE,
                reason="No recovery action needed",
            )
        
        if recommended_action == RecoveryAction.NO_SAFE_RECOVERY:
            return SafetyCheckResult(
                verdict=SafetyVerdict.UNSAFE,
                reason="Explicitly no safe recovery",
            )
        
        # Check the specific action
        return self.check_recovery_action_safety(
            action=recommended_action,
            turn_context=turn_context,
            failure_classification=failure_classification,
        )
    
    def can_auto_recover(self) -> bool:
        """Check if auto-recovery is enabled."""
        return self.safety_mode == SafetyMode.AUTO_RECOVER
    
    def is_observe_only(self) -> bool:
        """Check if in observe-only mode."""
        return self.safety_mode == SafetyMode.OBSERVE_ONLY
    
    def is_dry_run(self) -> bool:
        """Check if in dry-run mode."""
        return self.safety_mode == SafetyMode.DRY_RUN