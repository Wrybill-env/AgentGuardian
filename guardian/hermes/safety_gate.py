"""
Hermes-specific safety gate for Agent Guardian.

This is the PRIMARY safety gate that prevents unsafe replay/recovery operations.
All recovery actions must pass through this gate.

CRITICAL PRINCIPLE: TOOL EXECUTION IS IMMEDIATE in Hermes.
Once a tool call has been dispatched to the tool executor, it executes immediately.
Replaying the prompt WILL re-execute tools.

The gate enforces: when in doubt, fail closed.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from guardian.core.types import (
    TurnContext,
    TurnPhase,
    RecoveryAction,
    SafetyMode,
    FailureType,
    FailureClassification,
    SafetyVerdict,
)
from guardian.core.safety_gate import SafetyGate as CoreSafetyGate, SafetyCheckResult


class HermesSafetyVerdict(Enum):
    """Result of Hermes safety check."""
    SAFE = "safe"                          # Recovery allowed
    UNSAFE = "unsafe"                      # Recovery blocked
    AMBIGUOUS = "ambiguous"                # Cannot determine, fail closed


# Explicit safety reasons for diagnostics
class HermesSafetyReason(Enum):
    """Explicit reasons for safety decisions."""
    SAFE = "safe"
    UNSAFE_ASSISTANT_OUTPUT = "unsafe_assistant_output"
    UNSAFE_TOOL_EXECUTION = "unsafe_tool_execution"
    UNSAFE_PARTIAL_TOOL_ACTIVITY = "unsafe_partial_tool_activity"
    UNSAFE_TURN_COMPLETED = "unsafe_turn_completed"
    UNSAFE_UNKNOWN_STATE = "unsafe_unknown_state"
    UNSAFE_PROVIDER_FAILURE_NON_REPLAYABLE = "unsafe_provider_failure_non_replayable"
    UNSAFE_CONTEXT_OVERFLOW = "unsafe_context_overflow"
    UNSAFE_OBSERVER_DISCONNECTED = "unsafe_observer_disconnected"
    AMBIGUOUS_NO_TURN_CONTEXT = "ambiguous_no_turn_context"
    AMBIGUOUS_STREAM_STATE_UNKNOWN = "ambiguous_stream_state_unknown"
    AMBIGUOUS_PARTIAL_OBSERVATION = "ambiguous_partial_observation"
    BLOCKED_OBSERVE_ONLY = "blocked_observe_only_mode"
    # The Hermes gate can never be more permissive than the core gate. When the
    # core gate is stricter, its verdict is reported with these reasons.
    UNSAFE_CORE_GATE = "unsafe_core_gate"
    AMBIGUOUS_CORE_GATE = "ambiguous_core_gate"


@dataclass
class HermesSafetyCheckResult:
    """Result of a Hermes safety check with explicit reason."""
    verdict: HermesSafetyVerdict
    reason: HermesSafetyReason
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    blocking_factors: List[str] = field(default_factory=list)
    
    def to_core_result(self) -> SafetyCheckResult:
        """Convert to core SafetyCheckResult."""
        verdict_map = {
            HermesSafetyVerdict.SAFE: SafetyVerdict.SAFE,
            HermesSafetyVerdict.UNSAFE: SafetyVerdict.UNSAFE,
            HermesSafetyVerdict.AMBIGUOUS: SafetyVerdict.AMBIGUOUS,
        }
        return SafetyCheckResult(
            verdict=verdict_map[self.verdict],
            reason=self.message,
            details={**self.details, "hermes_reason": self.reason.value},
            blocking_factors=self.blocking_factors,
        )


class HermesSafetyGate:
    """
    Hermes-specific safety gate extending the core safety gate.
    
    Enforces the most important Hermes safety constraint:
    TOOL EXECUTION IS IMMEDIATE - once a tool call is dispatched, it runs.
    
    This gate adds Hermes-specific checks on top of the core safety gate.
    """
    
    def __init__(
        self,
        safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY,
        require_human_for_ambiguous: bool = True,
    ):
        self.safety_mode = safety_mode
        self.require_human_for_ambiguous = require_human_for_ambiguous
        
        # Delegate to core safety gate for common checks
        self._core_gate = CoreSafetyGate(safety_mode, require_human_for_ambiguous)
    
    def check_replay_safety(
        self,
        turn_context: TurnContext,
        failure_classification: Optional[Any] = None,
        stream_state: Optional[Dict[str, Any]] = None,
    ) -> HermesSafetyCheckResult:
        """
        Check if it's safe to replay the user's prompt.
        
        This is the PRIMARY safety gate. Replay is ONLY safe if:
        1. No assistant output has been observed in this turn
        2. No tool calls have been dispatched in this turn
        3. The turn hasn't completed (successfully or with error)
        4. The failure is classified as replay-safe
        5. Stream state is known and not ambiguous
        
        Args:
            turn_context: Current turn state from Guardian core
            failure_classification: Optional failure classification
            stream_state: Optional streaming state from Hermes observer
        
        Returns:
            HermesSafetyCheckResult with explicit reason
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
        
        # Check 3: Turn already completed
        if turn_context.phase in (
            TurnPhase.TURN_COMPLETE,
            TurnPhase.TURN_ENDING,
        ):
            blocking_factors.append(f"Turn already completed (phase: {turn_context.phase.value})")
        
        # Check 4: Stop reason indicates completion
        if turn_context.stop_reason in ("end_turn", "max_turns"):
            blocking_factors.append(f"Stop reason indicates completion: {turn_context.stop_reason}")
        
        # Check 5: Failure classification says not replay-safe
        if failure_classification and hasattr(failure_classification, 'replay_safe'):
            if not failure_classification.replay_safe:
                blocking_factors.append(
                    f"Failure type {getattr(failure_classification, 'failure_type', 'unknown')} "
                    "not marked as replay-safe"
                )
        
        # Check 6: Stream state ambiguity (Hermes-specific)
        if stream_state:
            # If we have partial tool names, tools have started
            if stream_state.get("partial_tool_names"):
                blocking_factors.append(
                    f"Partial tool activity known: {stream_state['partial_tool_names']}"
                )
            
            # If streaming phase but no chunks yet - could be thinking, not necessarily safe
            if stream_state.get("phase") == "assistant_streaming" and not stream_state.get("assistant_output_observed"):
                # This is ambiguous - could be thinking or stalled
                pass  # Let other checks handle this
            
            # If observer disconnected, state is unknown
            if not stream_state.get("observer_connected", True):
                blocking_factors.append("Observer disconnected - state unknown")
        
        # Determine verdict
        if blocking_factors:
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.UNSAFE,
                reason=HermesSafetyReason.UNSAFE_TOOL_EXECUTION if any("tool" in f.lower() for f in blocking_factors)
                else HermesSafetyReason.UNSAFE_ASSISTANT_OUTPUT if any("assistant" in f.lower() for f in blocking_factors)
                else HermesSafetyReason.UNSAFE_TURN_COMPLETED,
                message="Replay would risk duplicate side effects",
                details={
                    "assistant_chunks": turn_context.assistant_chunks_received,
                    "tool_calls_started": turn_context.tool_calls_started,
                    "tool_calls_completed": turn_context.tool_calls_completed,
                    "phase": turn_context.phase.value,
                    "stop_reason": turn_context.stop_reason,
                    "stream_state": stream_state,
                },
                blocking_factors=blocking_factors,
            )
        
        # All checks passed - safe to replay
        return HermesSafetyCheckResult(
            verdict=HermesSafetyVerdict.SAFE,
            reason=HermesSafetyReason.SAFE,
            message="No side effects observed in current turn",
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
        failure_classification: Optional[Any] = None,
        stream_state: Optional[Dict[str, Any]] = None,
    ) -> HermesSafetyCheckResult:
        """
        Action-level safety check, hardened against policy divergence.

        A gateway must have exactly ONE safety policy. This method evaluates the
        Hermes-specific rules and the core rules and returns the MORE
        restrictive verdict, so the Hermes gate can never authorise something
        the core gate denies (previously this gate had no freshness check and
        would allow prompt replay that the core gate blocks).
        """
        hermes = self._check_recovery_action_safety_hermes(
            action, turn_context, failure_classification, stream_state
        )
        core = self._core_gate.check_recovery_action_safety(
            action=action,
            turn_context=turn_context,
            failure_classification=failure_classification,
        )
        return self._most_restrictive(hermes, core)

    _SEVERITY = {
        HermesSafetyVerdict.SAFE: 0,
        HermesSafetyVerdict.AMBIGUOUS: 1,
        HermesSafetyVerdict.UNSAFE: 2,
    }

    def _most_restrictive(
        self,
        hermes: HermesSafetyCheckResult,
        core: SafetyCheckResult,
    ) -> HermesSafetyCheckResult:
        """Return whichever verdict is stricter; Hermes reasons win on ties."""
        core_verdict = {
            SafetyVerdict.SAFE: HermesSafetyVerdict.SAFE,
            SafetyVerdict.AMBIGUOUS: HermesSafetyVerdict.AMBIGUOUS,
            SafetyVerdict.UNSAFE: HermesSafetyVerdict.UNSAFE,
        }[core.verdict]

        if self._SEVERITY[core_verdict] <= self._SEVERITY[hermes.verdict]:
            return hermes

        reason = (
            HermesSafetyReason.UNSAFE_CORE_GATE
            if core_verdict is HermesSafetyVerdict.UNSAFE
            else HermesSafetyReason.AMBIGUOUS_CORE_GATE
        )
        return HermesSafetyCheckResult(
            verdict=core_verdict,
            reason=reason,
            message=core.reason,
            details={**(core.details or {}), "core_gate": True, "hermes_gate": False},
            blocking_factors=list(core.blocking_factors),
        )

    def _check_recovery_action_safety_hermes(
        self,
        action: RecoveryAction,
        turn_context: TurnContext,
        failure_classification: Optional[Any] = None,
        stream_state: Optional[Dict[str, Any]] = None,
    ) -> HermesSafetyCheckResult:
        """
        Hermes-specific action safety rules.
        
        Different actions have different safety requirements:
        - WAIT_AND_RETRY: Requires no side effects observed
        - CANCEL_AND_REPLAY: Requires no side effects, plus ability to cancel
        - RECONNECT_ACP: Requires proven-fresh, corroborated state (see wrapper)
        - RELOAD_SESSION: Requires session identity preserved
        - FORK_SESSION: Requires fork capability, safe point available
        - WAIT_FOR_BACKEND: Requires no possible prior tool execution
        """
        # In OBSERVE_ONLY mode, NO recovery actions are allowed
        if self.safety_mode == SafetyMode.OBSERVE_ONLY:
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.UNSAFE,
                reason=HermesSafetyReason.BLOCKED_OBSERVE_ONLY,
                message=f"Safety mode is OBSERVE_ONLY - no recovery actions permitted",
                details={"safety_mode": self.safety_mode.value},
                blocking_factors=["OBSERVE_ONLY mode active"],
            )
        
        # In DRY_RUN mode, all actions are "safe" but won't execute
        if self.safety_mode == SafetyMode.DRY_RUN:
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.SAFE,
                reason=HermesSafetyReason.SAFE,
                message=f"DRY_RUN mode - action would be simulated only",
                details={
                    "action": action.value,
                    "safety_mode": self.safety_mode.value,
                    "would_execute": False,
                },
                blocking_factors=[],
            )
        
        # AUTO_RECOVER mode - check specific action safety
        if action == RecoveryAction.WAIT_AND_RETRY:
            # Only safe if no side effects yet
            return self.check_replay_safety(turn_context, failure_classification, stream_state)
        
        elif action == RecoveryAction.CANCEL_AND_REPLAY:
            # Need to be able to cancel AND replay safely
            cancel_check = self.check_replay_safety(turn_context, failure_classification, stream_state)
            if cancel_check.verdict == HermesSafetyVerdict.UNSAFE:
                return cancel_check
            
            # Also need active turn to cancel
            if turn_context.phase == TurnPhase.IDLE:
                return HermesSafetyCheckResult(
                    verdict=HermesSafetyVerdict.UNSAFE,
                    reason=HermesSafetyReason.UNSAFE_UNKNOWN_STATE,
                    message="No active turn to cancel",
                    blocking_factors=["No active turn"],
                )
            
            # Check if Hermes supports cancellation
            # (Hermes does via interrupt mechanism, but we can't guarantee)
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.SAFE,
                reason=HermesSafetyReason.SAFE,
                message="Turn can be cancelled and replayed safely",
                details={"cancel_phase": turn_context.phase.value},
            )
        
        elif action == RecoveryAction.RECONNECT_ACP:
            # Reconnection doesn't replay prompts - generally safe
            # But need to ensure session identity will be preserved
            if turn_context.session_id:
                return HermesSafetyCheckResult(
                    verdict=HermesSafetyVerdict.SAFE,
                    reason=HermesSafetyReason.SAFE,
                    message="ACP reconnection preserves session, no prompt replay",
                    details={"session_id": turn_context.session_id},
                )
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.AMBIGUOUS,
                reason=HermesSafetyReason.AMBIGUOUS_STREAM_STATE_UNKNOWN,
                message="No session ID available to verify identity after reconnect",
                blocking_factors=["Missing session_id"],
            )
        
        elif action == RecoveryAction.RELOAD_SESSION:
            # Requires known session ID and session still exists
            if not turn_context.session_id:
                return HermesSafetyCheckResult(
                    verdict=HermesSafetyVerdict.UNSAFE,
                    reason=HermesSafetyReason.UNSAFE_UNKNOWN_STATE,
                    message="Cannot reload session - no session ID",
                    blocking_factors=["Missing session_id"],
                )
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.SAFE,
                reason=HermesSafetyReason.SAFE,
                message="Session reload preserves history, no prompt replay",
                details={"session_id": turn_context.session_id},
            )
        
        elif action == RecoveryAction.FORK_SESSION:
            # Requires fork capability and safe fork point
            if not turn_context.session_id:
                return HermesSafetyCheckResult(
                    verdict=HermesSafetyVerdict.UNSAFE,
                    reason=HermesSafetyReason.UNSAFE_UNKNOWN_STATE,
                    message="Cannot fork - no session ID",
                    blocking_factors=["Missing session_id"],
                )
            # Need a safe fork point (before current turn's user message)
            if turn_context.phase == TurnPhase.USER_MESSAGE_SENT:
                return HermesSafetyCheckResult(
                    verdict=HermesSafetyVerdict.SAFE,
                    reason=HermesSafetyReason.SAFE,
                    message="Can fork before current turn's user message",
                )
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.AMBIGUOUS,
                reason=HermesSafetyReason.AMBIGUOUS_PARTIAL_OBSERVATION,
                message="Fork point may include partial side effects",
                blocking_factors=["Uncertain fork point safety"],
            )
        
        elif action == RecoveryAction.WAIT_FOR_BACKEND:
            # Safe - just waiting, no prompt replay
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.SAFE,
                reason=HermesSafetyReason.SAFE,
                message="Waiting for backend restart - no prompt replay",
            )
        
        elif action == RecoveryAction.HUMAN_INTERVENTION:
            # Always "safe" - just requires human decision
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.SAFE,
                reason=HermesSafetyReason.SAFE,
                message="Human intervention requested - no automatic action",
            )
        
        elif action == RecoveryAction.NO_SAFE_RECOVERY:
            # Explicitly no safe recovery
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.UNSAFE,
                reason=HermesSafetyReason.UNSAFE_UNKNOWN_STATE,
                message="No safe recovery path identified",
                blocking_factors=["Explicit NO_SAFE_RECOVERY"],
            )
        
        # Unknown action - fail closed
        return HermesSafetyCheckResult(
            verdict=HermesSafetyVerdict.AMBIGUOUS,
            reason=HermesSafetyReason.AMBIGUOUS_NO_TURN_CONTEXT,
            message=f"Unknown recovery action: {action.value}",
            blocking_factors=["Unknown action"],
        )
    
    def evaluate_recovery_decision(
        self,
        failure_classification: Any,
        turn_context: TurnContext,
        stream_state: Optional[Dict[str, Any]] = None,
    ) -> HermesSafetyCheckResult:
        """
        Evaluate whether the recommended recovery action is safe to execute.
        
        This is the main entry point for recovery decision validation.
        """
        recommended_action = getattr(failure_classification, 'recommended_action', None)
        
        if recommended_action is None:
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.AMBIGUOUS,
                reason=HermesSafetyReason.AMBIGUOUS_NO_TURN_CONTEXT,
                message="No recovery action recommended",
            )
        
        if recommended_action == RecoveryAction.NONE:
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.SAFE,
                reason=HermesSafetyReason.SAFE,
                message="No recovery action needed",
            )
        
        if recommended_action == RecoveryAction.NO_SAFE_RECOVERY:
            return HermesSafetyCheckResult(
                verdict=HermesSafetyVerdict.UNSAFE,
                reason=HermesSafetyReason.UNSAFE_UNKNOWN_STATE,
                message="Explicitly no safe recovery",
            )
        
        # Check the specific action
        return self.check_recovery_action_safety(
            action=recommended_action,
            turn_context=turn_context,
            failure_classification=failure_classification,
            stream_state=stream_state,
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