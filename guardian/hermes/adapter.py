"""
Hermes Adapter for Agent Guardian.

Provides a read-only interface to Hermes agent state that implements
the AgentAdapter protocol expected by Guardian core.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from guardian.core.types import (
    GuardianState,
    TurnPhase,
    FailureType,
    RecoveryAction,
    SafetyMode,
    TurnContext,
)
from guardian.core import SafetyGate, SafetyVerdict
from guardian.logging import get_logger

from .observer import HermesObserver, HermesSessionState, run_hermes_observer
from .failure_mapper import map_hermes_failure
from .safety_gate import HermesSafetyGate

logger = get_logger("hermes.adapter")


@dataclass
class HermesAdapterConfig:
    """Configuration for Hermes adapter."""
    stale_threshold: float = 60.0
    first_chunk_threshold: float = 30.0
    chunk_gap_threshold: float = 15.0
    tool_timeout_threshold: float = 120.0
    safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY
    
    # Internal: for in-process attachment
    agent: Optional[Any] = None


class HermesAdapter:
    """
    Adapter that connects Hermes agent to Guardian core.
    
    This is a READ-ONLY adapter that observes Hermes through the
    HermesObserver and translates events into Guardian's core abstractions.
    
    Does NOT perform any recovery actions - only observation and safety evaluation.
    """
    
    def __init__(
        self,
        config: HermesAdapterConfig,
    ):
        self.config = config
        self.observer: Optional[HermesObserver] = None
        self._agent = config.agent
        self._connected = False
        self._event_callbacks: List[Callable[[dict], None]] = []
        self._failure_callbacks: List[Callable[[str, dict], None]] = []
        self._state_change_callbacks: List[Callable[[GuardianState, GuardianState], None]] = []
        
        # Guardian core components
        self.safety_gate = HermesSafetyGate(config.safety_mode)
        self.current_state = GuardianState.IDLE
    
    async def connect(self) -> bool:
        """Connect to Hermes agent (attach observer in-process)."""
        if not self._agent:
            logger.error("No Hermes agent provided for in-process attachment")
            return False
        
        try:
            self.observer = await run_hermes_observer(
                agent=self._agent,
                stale_threshold=self.config.stale_threshold,
                first_chunk_threshold=self.config.first_chunk_threshold,
                chunk_gap_threshold=self.config.chunk_gap_threshold,
                tool_timeout_threshold=self.config.tool_timeout_threshold,
                on_event=self._handle_event,
                on_failure=self._handle_failure,
            )
            self._connected = True
            self._transition_state(GuardianState.OBSERVING)
            logger.info("Hermes adapter connected (in-process observer attached)")
            return True
        except Exception as e:
            logger.error(f"Failed to attach Hermes observer: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from Hermes agent."""
        if self.observer:
            self.observer.detach()
            self.observer = None
        self._connected = False
        self._transition_state(GuardianState.IDLE)
        logger.info("Hermes adapter disconnected")
    
    def _transition_state(self, new_state: GuardianState):
        """Transition Guardian state and notify callbacks."""
        old_state = self.current_state
        if old_state != new_state:
            self.current_state = new_state
            for callback in self._state_change_callbacks:
                try:
                    callback(old_state, new_state)
                except Exception as e:
                    logger.error(f"State change callback error: {e}")
    
    def on_event(self, callback: Callable[[dict], None]):
        """Register event callback."""
        self._event_callbacks.append(callback)
    
    def on_failure(self, callback: Callable[[str, dict], None]):
        """Register failure callback."""
        self._failure_callbacks.append(callback)
    
    def on_state_change(self, callback: Callable[[GuardianState, GuardianState], None]):
        """Register state change callback."""
        self._state_change_callbacks.append(callback)
    
    def _handle_event(self, event: dict):
        """Handle event from observer."""
        for callback in self._event_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")
    
    def _handle_failure(self, failure: str, details: dict):
        """Handle failure from observer."""
        for callback in self._failure_callbacks:
            try:
                callback(failure, details)
            except Exception as e:
                logger.error(f"Failure callback error: {e}")
    
    # --- Adapter Interface ---
    
    @property
    def session_id(self) -> Optional[str]:
        """Get current session ID."""
        if self.observer and self.observer.sessions:
            # Return most recently active session
            for session in self.observer.sessions.values():
                if session.turn_phase != TurnPhase.IDLE:
                    return session.session_id
            # Fallback to any session
            return next(iter(self.observer.sessions.keys()))
        return None
    
    @property
    def turn_context(self) -> Optional[TurnContext]:
        """Get current turn context."""
        session_id = self.session_id
        if not session_id or not self.observer:
            return None
        
        session = self.observer.get_session_state(session_id)
        if not session:
            return None
        
        return TurnContext(
            session_id=session.session_id,
            run_id=session.turn_id,
            phase=session.turn_phase,
            assistant_chunks_received=1 if session.assistant_output_observed else 0,
            tool_calls_started=len(session.partial_tool_names),
            tool_calls_completed=1 if session.completion_state == "completed" else 0,
            tool_calls_failed=1 if session.completion_state == "error" else 0,
            last_event_timestamp=session.last_event_timestamp,
            last_assistant_chunk_time=session.last_assistant_chunk_timestamp,
            last_tool_event_time=session.last_tool_event_timestamp,
            first_chunk_time=session.first_assistant_chunk_timestamp,
            error_message=session.observed_errors[-1] if session.observed_errors else None,
        )
    
    @property
    def stream_active(self) -> bool:
        """Check if a stream is currently active."""
        if not self.observer or not self.session_id:
            return False
        session = self.observer.get_session_state(self.session_id)
        if not session:
            return False
        return session.turn_phase in (
            TurnPhase.ASSISTANT_STREAMING,
            TurnPhase.TOOL_EXECUTING,
            TurnPhase.USER_MESSAGE_SENT,
        )
    
    @property
    def streaming_state(self) -> Optional[Dict[str, Any]]:
        """Get detailed streaming state."""
        if not self.observer or not self.session_id:
            return None
        session = self.observer.get_session_state(self.session_id)
        if not session:
            return None
        
        return {
            "phase": session.turn_phase.value,
            "assistant_output_observed": session.assistant_output_observed,
            "tool_activity_observed": session.tool_activity_observed,
            "partial_tool_names": session.partial_tool_names.copy(),
            "last_assistant_chunk_ago": session.time_since_last_assistant_chunk(),
            "last_reasoning_ago": session.time_since_last_reasoning(),
            "last_tool_event_ago": session.time_since_last_tool_event(),
            "first_chunk_ago": time.time() - session.first_assistant_chunk_timestamp 
                if session.first_assistant_chunk_timestamp else None,
            "retry_count": session.retry_count,
            "auto_recovery_cycles": session.auto_recovery_cycles,
            "stale_detected": session.stale_detected,
        }
    
    @property
    def observed_failures(self) -> List[dict]:
        """Get list of observed failures."""
        if not self.observer:
            return []
        
        failures = []
        for session in self.observer.sessions.values():
            for error in session.observed_errors:
                failures.append({
                    "session_id": session.session_id,
                    "error": error,
                    "turn_phase": session.turn_phase.value,
                    "turn_count": session.turn_count,
                })
        return failures
    
    @property
    def safety_state(self) -> Dict[str, Any]:
        """Get safety evaluation state."""
        turn_ctx = self.turn_context
        if not turn_ctx:
            return {"safe_to_replay": True, "reason": "No active turn"}
        
        safe = turn_ctx.is_safe_to_replay()
        reason = "No side effects observed"
        
        if turn_ctx.has_assistant_output():
            reason = f"Assistant output observed ({turn_ctx.assistant_chunks_received} chunks)"
            safe = False
        elif turn_ctx.has_tool_activity():
            reason = f"Tool activity observed ({turn_ctx.tool_calls_started} started)"
            safe = False
        
        return {
            "safe_to_replay": safe,
            "reason": reason,
            "assistant_chunks": turn_ctx.assistant_chunks_received,
            "tool_calls_started": turn_ctx.tool_calls_started,
            "phase": turn_ctx.phase.value,
        }
    
    def evaluate_recovery_safety(
        self,
        failure_type: FailureType,
        recommended_action: RecoveryAction,
    ) -> SafetyVerdict:
        """Evaluate if a recovery action is safe for current state."""
        turn_ctx = self.turn_context
        if not turn_ctx:
            return SafetyVerdict.UNSAFE  # Fail closed
        
        # Use Hermes-specific safety gate
        result = self.safety_gate.check_recovery_action_safety(
            action=recommended_action,
            turn_context=turn_ctx,
        )
        return result.verdict
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive adapter status."""
        return {
            "connected": self._connected,
            "guardian_state": self.current_state.value,
            "safety_mode": self.config.safety_mode.value,
            "session_id": self.session_id,
            "stream_active": self.stream_active,
            "streaming_state": self.streaming_state,
            "turn_context": self._turn_context_to_dict(self.turn_context) if self.turn_context else None,
            "safety_state": self.safety_state,
            "observed_failures": self.observed_failures,
            "observer_status": self.observer.get_status() if self.observer else None,
        }
    
    def _turn_context_to_dict(self, ctx: TurnContext) -> Dict[str, Any]:
        return {
            "session_id": ctx.session_id,
            "run_id": ctx.run_id,
            "phase": ctx.phase.value,
            "assistant_chunks": ctx.assistant_chunks_received,
            "tool_calls_started": ctx.tool_calls_started,
            "tool_calls_completed": ctx.tool_calls_completed,
            "tool_calls_failed": ctx.tool_calls_failed,
            "safe_to_replay": ctx.is_safe_to_replay(),
        }


def _import_time():
    import time
    return time


time = _import_time()