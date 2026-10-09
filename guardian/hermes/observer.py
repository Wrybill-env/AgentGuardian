"""
Read-only observer for Hermes Agent.

Connects to a running Hermes agent instance by hooking into its callback system
and observes events without interfering with Hermes' operation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from guardian.logging import get_logger
from guardian.core.types import (
    TurnPhase,
    FailureType,
    RecoveryAction,
)

logger = get_logger("hermes.observer")


@dataclass
class HermesSessionState:
    """Tracks state for an observed Hermes session/turn."""
    session_id: str
    turn_id: Optional[str] = None
    turn_phase: TurnPhase = TurnPhase.IDLE
    assistant_output_observed: bool = False
    tool_activity_observed: bool = False
    partial_tool_names: List[str] = field(default_factory=list)
    last_event_timestamp: float = field(default_factory=time.time)
    last_assistant_chunk_timestamp: Optional[float] = None
    last_reasoning_timestamp: Optional[float] = None
    last_tool_event_timestamp: Optional[float] = None
    first_assistant_chunk_timestamp: Optional[float] = None
    completion_state: Optional[str] = None  # completed, error, cancelled, interrupted
    observed_errors: List[str] = field(default_factory=list)
    tool_calls: Dict[str, Dict] = field(default_factory=dict)  # tool_call_id -> info
    turn_count: int = 0
    retry_count: int = 0
    auto_recovery_cycles: int = 0
    stale_detected: bool = False
    
    def update_event(self, event_type: str, **kwargs):
        """Update state based on observed event."""
        self.last_event_timestamp = time.time()
        
        if event_type == "assistant_chunk":
            self.assistant_output_observed = True
            self.last_assistant_chunk_timestamp = time.time()
            if self.first_assistant_chunk_timestamp is None:
                self.first_assistant_chunk_timestamp = time.time()
            if self.turn_phase in (TurnPhase.USER_MESSAGE_SENT, TurnPhase.IDLE):
                self.turn_phase = TurnPhase.ASSISTANT_STREAMING
        
        elif event_type == "reasoning_chunk":
            self.last_reasoning_timestamp = time.time()
            if self.turn_phase in (TurnPhase.USER_MESSAGE_SENT, TurnPhase.IDLE):
                self.turn_phase = TurnPhase.ASSISTANT_STREAMING
        
        elif event_type == "tool_started":
            self.tool_activity_observed = True
            self.last_tool_event_timestamp = time.time()
            tool_name = kwargs.get("tool_name")
            if tool_name:
                self.partial_tool_names.append(tool_name)
            if self.turn_phase in (TurnPhase.USER_MESSAGE_SENT, TurnPhase.ASSISTANT_STREAMING, TurnPhase.IDLE):
                self.turn_phase = TurnPhase.TOOL_EXECUTING
        
        elif event_type == "tool_progress":
            self.last_tool_event_timestamp = time.time()
        
        elif event_type == "tool_completed":
            self.last_tool_event_timestamp = time.time()
        
        elif event_type == "stream_start":
            self.turn_phase = TurnPhase.ASSISTANT_STREAMING
            self.first_assistant_chunk_timestamp = None
            self.last_assistant_chunk_timestamp = None
            self.last_tool_event_timestamp = None
        
        elif event_type == "stream_end":
            if self.completion_state != "error":
                self.completion_state = "completed"
            self.turn_count += 1
            self.turn_phase = TurnPhase.TURN_COMPLETE
            # Reset per-turn flags for next turn
            self.assistant_output_observed = False
            self.tool_activity_observed = False
            self.partial_tool_names.clear()
            self.first_assistant_chunk_timestamp = None
        
        elif event_type == "turn_error":
            self.completion_state = "error"
            if kwargs.get("error_message"):
                self.observed_errors.append(kwargs["error_message"])
            self.turn_phase = TurnPhase.TURN_ENDING
        
        elif event_type == "turn_cancelled":
            self.completion_state = "cancelled"
            self.turn_phase = TurnPhase.TURN_ENDING
        
        elif event_type == "retry":
            self.retry_count += 1
        
        elif event_type == "auto_recovery":
            self.auto_recovery_cycles += 1
        
        elif event_type == "stale_detected":
            self.stale_detected = True
    
    def is_safe_to_replay(self) -> bool:
        """
        Determine if current turn is safe to replay (no side effects observed).
        
        This is the PRIMARY safety gate. Replay is ONLY safe if:
        - No assistant output observed in current turn
        - No tool activity observed in current turn
        """
        return not self.assistant_output_observed and not self.tool_activity_observed
    
    def time_since_last_event(self) -> float:
        return time.time() - self.last_event_timestamp
    
    def time_since_last_assistant_chunk(self) -> Optional[float]:
        if self.last_assistant_chunk_timestamp:
            return time.time() - self.last_assistant_chunk_timestamp
        return None
    
    def time_since_last_reasoning(self) -> Optional[float]:
        if self.last_reasoning_timestamp:
            return time.time() - self.last_reasoning_timestamp
        return None
    
    def time_since_last_tool_event(self) -> Optional[float]:
        if self.last_tool_event_timestamp:
            return time.time() - self.last_tool_event_timestamp
        return None


class HermesFailureDetector:
    """Detects and reports observable failure conditions from Hermes events."""
    
    def __init__(
        self,
        stale_threshold: float = 60.0,
        first_chunk_threshold: float = 30.0,
        chunk_gap_threshold: float = 15.0,
        tool_timeout_threshold: float = 120.0,
        on_failure: Optional[Callable[[str, dict], None]] = None
    ):
        self.stale_threshold = stale_threshold
        self.first_chunk_threshold = first_chunk_threshold
        self.chunk_gap_threshold = chunk_gap_threshold
        self.tool_timeout_threshold = tool_timeout_threshold
        self.on_failure = on_failure
        self._reported_stale: Set[str] = set()
        self._reported_first_chunk_timeout: Set[str] = set()
        self._reported_chunk_gap: Set[str] = set()
        self._reported_tool_timeout: Set[str] = set()
    
    def check_session(self, session: HermesSessionState) -> List[str]:
        """Check a session for failure conditions. Returns list of failure descriptions."""
        failures = []
        now = time.time()
        
        # First chunk timeout - no chunks received after stream started
        if session.turn_phase == TurnPhase.ASSISTANT_STREAMING:
            if session.first_assistant_chunk_timestamp is None:
                elapsed = now - session.last_event_timestamp
                if elapsed > self.first_chunk_threshold:
                    key = f"{session.session_id}:first_chunk"
                    if key not in self._reported_first_chunk_timeout:
                        self._reported_first_chunk_timeout.add(key)
                        failures.append(
                            f"FIRST_CHUNK_TIMEOUT: no assistant chunk for {elapsed:.0f}s after stream start"
                        )
            # Chunk gap timeout - gap between chunks
            elif session.last_assistant_chunk_timestamp:
                elapsed = now - session.last_assistant_chunk_timestamp
                if elapsed > self.chunk_gap_threshold:
                    key = f"{session.session_id}:chunk_gap"
                    if key not in self._reported_chunk_gap:
                        self._reported_chunk_gap.add(key)
                        failures.append(
                            f"CHUNK_GAP: no assistant chunk for {elapsed:.0f}s (gap threshold: {self.chunk_gap_threshold}s)"
                        )
        
        # Stale stream detection (general)
        if session.turn_phase in (
            TurnPhase.ASSISTANT_STREAMING,
            TurnPhase.TOOL_EXECUTING,
            TurnPhase.USER_MESSAGE_SENT,
        ) or (session.turn_phase == TurnPhase.IDLE and session.last_event_timestamp > 0 and session.assistant_output_observed):
            time_since_event = session.time_since_last_event()
            if time_since_event > self.stale_threshold:
                key = f"{session.session_id}:stale"
                if key not in self._reported_stale:
                    self._reported_stale.add(key)
                    failures.append(
                        f"POSSIBLE_STALE_STREAM: no observable event for {time_since_event:.0f}s"
                    )
        
        # Tool execution timeout
        if session.turn_phase == TurnPhase.TOOL_EXECUTING:
            if session.last_tool_event_timestamp:
                elapsed = now - session.last_tool_event_timestamp
                if elapsed > self.tool_timeout_threshold:
                    key = f"{session.session_id}:tool_timeout"
                    if key not in self._reported_tool_timeout:
                        self._reported_tool_timeout.add(key)
                        failures.append(
                            f"TOOL_TIMEOUT: tool execution for {elapsed:.0f}s (threshold: {self.tool_timeout_threshold}s)"
                        )
        
        # Connection/backend failures
        for error in session.observed_errors:
            error_lower = error.lower()
            if "429" in error or "rate limit" in error_lower:
                failures.append(f"RATE_LIMIT_DETECTED: {error}")
            elif "500" in error or "502" in error or "503" in error or "504" in error:
                failures.append(f"SERVER_ERROR_DETECTED: {error}")
            elif "timeout" in error_lower or "408" in error:
                failures.append(f"TIMEOUT_DETECTED: {error}")
            elif any(pattern in error_lower for pattern in [
                "connection reset", "connection refused", "econnreset", "econnrefused",
                "connection aborted", "broken pipe", "pipe closed", "connection lost"
            ]):
                failures.append(f"CONNECTION_RESET_DETECTED: {error}")
            elif "dns" in error_lower or "name or service not known" in error_lower:
                failures.append(f"DNS_FAILURE_DETECTED: {error}")
            elif "authentication" in error_lower or "401" in error or "403" in error:
                failures.append(f"AUTHENTICATION_ERROR_DETECTED: {error}")
            elif "invalid model" in error_lower or "model not found" in error_lower:
                failures.append(f"INVALID_MODEL_DETECTED: {error}")
            else:
                failures.append(f"PROVIDER_ERROR_DETECTED: {error}")
        
        # Unexpected termination
        if session.completion_state in ("error", "cancelled") and session.turn_count == 0:
            failures.append(f"UNEXPECTED_TERMINATION: {session.completion_state} before any turn completed")
        
        return failures
    
    def report(self, session_id: str, failure: str, details: dict):
        """Report a detected failure."""
        if self.on_failure:
            self.on_failure(failure, details)
        logger.warning(f"[{session_id}] {failure}")
    
    def clear_stale_reports(self, session_id: str):
        """Clear reported stale flags for a session (e.g., after recovery)."""
        for key in list(self._reported_stale):
            if key.startswith(f"{session_id}:"):
                self._reported_stale.discard(key)
        for key in list(self._reported_first_chunk_timeout):
            if key.startswith(f"{session_id}:"):
                self._reported_first_chunk_timeout.discard(key)
        for key in list(self._reported_chunk_gap):
            if key.startswith(f"{session_id}:"):
                self._reported_chunk_gap.discard(key)
        for key in list(self._reported_tool_timeout):
            if key.startswith(f"{session_id}:"):
                self._reported_tool_timeout.discard(key)


class HermesObserver:
    """
    Read-only observer for Hermes Agent.
    
    Hooks into Hermes' callback system to observe events without sending
    any commands that would modify Hermes state.
    
    Callbacks observed:
    - stream_delta_callback: Assistant text chunks
    - reasoning_callback: Reasoning/thinking chunks
    - tool_progress_callback: Tool start/progress/complete/output_risk
    """
    
    def __init__(
        self,
        stale_threshold: float = 60.0,
        first_chunk_threshold: float = 30.0,
        chunk_gap_threshold: float = 15.0,
        tool_timeout_threshold: float = 120.0,
        on_event: Optional[Callable[[dict], None]] = None,
        on_failure: Optional[Callable[[str, dict], None]] = None,
    ):
        self.stale_threshold = stale_threshold
        self.first_chunk_threshold = first_chunk_threshold
        self.chunk_gap_threshold = chunk_gap_threshold
        self.tool_timeout_threshold = tool_timeout_threshold
        self.on_event = on_event
        self.on_failure = on_failure
        
        self.sessions: Dict[str, HermesSessionState] = {}
        self.failure_detector = HermesFailureDetector(
            stale_threshold=stale_threshold,
            first_chunk_threshold=first_chunk_threshold,
            chunk_gap_threshold=chunk_gap_threshold,
            tool_timeout_threshold=tool_timeout_threshold,
            on_failure=on_failure,
        )
        
        # Callback references (set when attached to agent)
        self._agent = None
        self._original_callbacks = {}
        self._running = False
        self._stale_check_task: Optional[asyncio.Task] = None
    
    def attach(self, agent) -> bool:
        """
        Attach to a Hermes AIAgent instance by replacing its callbacks.
        
        This is the in-process integration mechanism. The observer
        wraps the agent's existing callbacks to observe events.
        """
        self._agent = agent
        
        # Store original callbacks
        self._original_callbacks = {
            "stream_delta_callback": getattr(agent, "stream_delta_callback", None),
            "reasoning_callback": getattr(agent, "reasoning_callback", None),
            "tool_progress_callback": getattr(agent, "tool_progress_callback", None),
            "tool_start_callback": getattr(agent, "tool_start_callback", None),
            "tool_complete_callback": getattr(agent, "tool_complete_callback", None),
        }
        
        # Install observer callbacks
        agent.stream_delta_callback = self._on_stream_delta
        agent.reasoning_callback = self._on_reasoning_chunk
        agent.tool_progress_callback = self._on_tool_progress
        
        self._running = True
        # Start stale check task lazily when there's a running event loop
        try:
            loop = asyncio.get_running_loop()
            self._stale_check_task = loop.create_task(self._stale_check_loop())
        except RuntimeError:
            # No running event loop - will start when one exists
            self._stale_check_task = None
        
        logger.info("Hermes observer attached to agent")
        return True
    
    def detach(self):
        """Detach from the agent and restore original callbacks."""
        self._running = False
        
        if self._stale_check_task and not self._stale_check_task.done():
            self._stale_check_task.cancel()
        
        if self._agent:
            # Restore original callbacks
            for name, original in self._original_callbacks.items():
                if original is not None:
                    setattr(self._agent, name, original)
                elif hasattr(self._agent, name):
                    # Remove if there was no original
                    try:
                        delattr(self._agent, name)
                    except AttributeError:
                        pass
        
        logger.info("Hermes observer detached from agent")
    
    def _get_or_create_session(self, session_id: str) -> HermesSessionState:
        """Get or create session state."""
        if session_id not in self.sessions:
            self.sessions[session_id] = HermesSessionState(session_id=session_id)
        return self.sessions[session_id]
    
    def _on_stream_delta(self, text: str):
        """Observe assistant text chunk."""
        if not self._agent:
            return
        
        session_id = getattr(self._agent, "session_id", "unknown")
        session = self._get_or_create_session(session_id)
        
        session.update_event("assistant_chunk")
        
        event = {
            "type": "assistant_chunk",
            "session_id": session_id,
            "timestamp": time.time(),
            "text_length": len(text),
            "turn_phase": session.turn_phase.value,
            "safe_to_replay": session.is_safe_to_replay(),
        }
        
        if self.on_event:
            self.on_event(event)
        
        # Check for failures
        failures = self.failure_detector.check_session(session)
        for failure in failures:
            self.failure_detector.report(session_id, failure, {"session_id": session_id})
    
    def _on_reasoning_chunk(self, text: str):
        """Observe reasoning/thinking chunk."""
        if not self._agent:
            return
        
        session_id = getattr(self._agent, "session_id", "unknown")
        session = self._get_or_create_session(session_id)
        
        session.update_event("reasoning_chunk")
        
        event = {
            "type": "reasoning_chunk",
            "session_id": session_id,
            "timestamp": time.time(),
            "text_length": len(text),
            "turn_phase": session.turn_phase.value,
        }
        
        if self.on_event:
            self.on_event(event)
        
        failures = self.failure_detector.check_session(session)
        for failure in failures:
            self.failure_detector.report(session_id, failure, {"session_id": session_id})
    
    def _on_tool_progress(self, event_type: str, function_name: str, *args, **kwargs):
        """Observe tool progress events."""
        if not self._agent:
            return
        
        session_id = getattr(self._agent, "session_id", "unknown")
        session = self._get_or_create_session(session_id)
        
        tool_call_id = kwargs.get("tool_call_id")
        display_args = args[0] if args else None
        result = args[1] if len(args) > 1 else None
        risk_metadata = kwargs.get("risk_metadata")
        
        if event_type == "tool.started":
            session.update_event("tool_started", tool_name=function_name)
            event = {
                "type": "tool_started",
                "session_id": session_id,
                "tool_name": function_name,
                "tool_call_id": tool_call_id,
                "display_args": display_args,
                "timestamp": time.time(),
                "turn_phase": session.turn_phase.value,
                "safe_to_replay": session.is_safe_to_replay(),
            }
        
        elif event_type == "tool.progress":
            session.update_event("tool_progress", tool_name=function_name)
            event = {
                "type": "tool_progress",
                "session_id": session_id,
                "tool_name": function_name,
                "tool_call_id": tool_call_id,
                "timestamp": time.time(),
            }
        
        elif event_type == "tool.completed":
            session.update_event("tool_completed", tool_name=function_name)
            event = {
                "type": "tool_completed",
                "session_id": session_id,
                "tool_name": function_name,
                "tool_call_id": tool_call_id,
                "result_preview": str(result)[:200] if result else None,
                "timestamp": time.time(),
            }
        
        elif event_type == "tool.output_risk":
            event = {
                "type": "tool_output_risk",
                "session_id": session_id,
                "tool_name": function_name,
                "tool_call_id": tool_call_id,
                "risk_metadata": risk_metadata,
                "timestamp": time.time(),
            }
        
        else:
            return
        
        if self.on_event:
            self.on_event(event)
        
        failures = self.failure_detector.check_session(session)
        for failure in failures:
            self.failure_detector.report(session_id, failure, {"session_id": session_id})
    
    async def _stale_check_loop(self):
        """Periodically check for stale streams."""
        while self._running:
            await asyncio.sleep(10)
            for session in self.sessions.values():
                failures = self.failure_detector.check_session(session)
                for failure in failures:
                    self.failure_detector.report(
                        session.session_id,
                        failure,
                        {"session_id": session.session_id}
                    )
    
    def get_session_state(self, session_id: str) -> Optional[HermesSessionState]:
        """Get state for a session."""
        return self.sessions.get(session_id)
    
    def get_all_sessions(self) -> Dict[str, HermesSessionState]:
        """Get all observed sessions."""
        return self.sessions.copy()
    
    def get_status(self) -> dict:
        """Get observer status summary."""
        return {
            "running": self._running,
            "attached": self._agent is not None,
            "sessions_count": len(self.sessions),
            "stale_threshold": self.stale_threshold,
            "first_chunk_threshold": self.first_chunk_threshold,
            "chunk_gap_threshold": self.chunk_gap_threshold,
            "tool_timeout_threshold": self.tool_timeout_threshold,
        }


async def run_hermes_observer(
    agent,
    stale_threshold: float = 60.0,
    first_chunk_threshold: float = 30.0,
    chunk_gap_threshold: float = 15.0,
    tool_timeout_threshold: float = 120.0,
    on_event: Optional[Callable[[dict], None]] = None,
    on_failure: Optional[Callable[[str, dict], None]] = None,
) -> HermesObserver:
    """
    Convenience function to create and attach an observer to a Hermes agent.
    
    Returns the attached observer.
    """
    observer = HermesObserver(
        stale_threshold=stale_threshold,
        first_chunk_threshold=first_chunk_threshold,
        chunk_gap_threshold=chunk_gap_threshold,
        tool_timeout_threshold=tool_timeout_threshold,
        on_event=on_event,
        on_failure=on_failure,
    )
    
    attached = observer.attach(agent)
    if not attached:
        raise ConnectionError("Failed to attach to Hermes agent")
    
    return observer