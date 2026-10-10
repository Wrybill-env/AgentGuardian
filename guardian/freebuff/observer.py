"""
FreeBuff Observer.

Observes FreeBuff events via HTTP API / WebSocket.
"""

import asyncio
import json
import time
import aiohttp
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from guardian.logging import get_logger
from guardian.core.types import (
    TurnPhase,
    FailureType,
    RecoveryAction,
)
from .protocol import (
    FreeBuffEvent,
    FreeBuffEventType,
    FreeBuffProtocolParser,
    FreeBuffConnectionInfo,
)

logger = get_logger("freebuff.observer")


@dataclass
class FreeBuffSessionState:
    """Tracks state for an observed FreeBuff session."""
    session_id: str
    run_id: Optional[str] = None
    turn_phase: TurnPhase = TurnPhase.IDLE
    assistant_output_observed: bool = False
    tool_activity_observed: bool = False
    partial_tool_names: List[str] = field(default_factory=list)
    last_event_timestamp: float = field(default_factory=time.time)
    last_assistant_chunk_timestamp: Optional[float] = None
    last_reasoning_timestamp: Optional[float] = None
    last_tool_event_timestamp: Optional[float] = None
    first_assistant_chunk_timestamp: Optional[float] = None
    completion_state: Optional[str] = None
    observed_errors: List[str] = field(default_factory=list)
    tool_calls: Dict[str, Dict] = field(default_factory=dict)
    turn_count: int = 0
    retry_count: int = 0
    auto_recovery_cycles: int = 0
    stale_detected: bool = False
    current_message_id: Optional[str] = None
    current_tool_call_id: Optional[str] = None
    
    def update_event(self, event: FreeBuffEvent, **kwargs):
        """Update state based on observed event."""
        self.last_event_timestamp = time.time()
        
        if event.event_type in (FreeBuffEventType.SESSION_CREATE, FreeBuffEventType.SESSION_START):
            self.turn_phase = TurnPhase.USER_MESSAGE_SENT
            self.assistant_output_observed = False
            self.tool_activity_observed = False
            self.partial_tool_names.clear()
            self.first_assistant_chunk_timestamp = None
            self.last_assistant_chunk_timestamp = None
            self.last_tool_event_timestamp = None
            self.completion_state = None
            self.observed_errors.clear()
            self.tool_calls.clear()
            self.turn_count += 1
        
        elif event.event_type in (FreeBuffEventType.MESSAGE_START, FreeBuffEventType.MESSAGE_CHUNK):
            self.assistant_output_observed = True
            self.last_assistant_chunk_timestamp = time.time()
            if self.first_assistant_chunk_timestamp is None:
                self.first_assistant_chunk_timestamp = time.time()
            if self.turn_phase in (TurnPhase.USER_MESSAGE_SENT, TurnPhase.IDLE):
                self.turn_phase = TurnPhase.ASSISTANT_STREAMING
            self.current_message_id = event.message_id
        
        elif event.event_type == FreeBuffEventType.MESSAGE_END:
            self.turn_phase = TurnPhase.TURN_ENDING
            self.completion_state = "completed"
            self.turn_count += 1
            # Reset per-turn flags for next turn
            self.assistant_output_observed = False
            self.tool_activity_observed = False
            self.partial_tool_names.clear()
            self.first_assistant_chunk_timestamp = None
            self.current_message_id = None
        
        elif event.event_type in (FreeBuffEventType.TOOL_CALL_START, FreeBuffEventType.TOOL_CALL_UPDATE):
            self.tool_activity_observed = True
            self.last_tool_event_timestamp = time.time()
            if event.tool_name:
                self.partial_tool_names.append(event.tool_name)
            self.current_tool_call_id = event.tool_call_id
            if event.tool_call_id:
                self.tool_calls[event.tool_call_id] = {
                    "name": event.tool_name,
                    "arguments": event.tool_arguments,
                    "start_time": time.time(),
                }
            if self.turn_phase in (TurnPhase.USER_MESSAGE_SENT, TurnPhase.ASSISTANT_STREAMING, TurnPhase.IDLE):
                self.turn_phase = TurnPhase.TOOL_EXECUTING
        
        elif event.event_type == FreeBuffEventType.TOOL_CALL_END:
            self.last_tool_event_timestamp = time.time()
            if event.tool_call_id and event.tool_call_id in self.tool_calls:
                self.tool_calls[event.tool_call_id]["result"] = event.tool_result
                self.tool_calls[event.tool_call_id]["end_time"] = time.time()
                self.tool_calls[event.tool_call_id]["completed"] = True
            self.current_tool_call_id = None
        
        elif event.event_type == FreeBuffEventType.TOOL_CALL_ERROR:
            self.last_tool_event_timestamp = time.time()
            if event.tool_call_id and event.tool_call_id in self.tool_calls:
                self.tool_calls[event.tool_call_id]["error"] = event.tool_error
                self.tool_calls[event.tool_call_id]["end_time"] = time.time()
                self.tool_calls[event.tool_call_id]["failed"] = True
            if event.tool_error:
                self.observed_errors.append(f"Tool error: {event.tool_error}")
            self.current_tool_call_id = None
        
        elif event.event_type == FreeBuffEventType.ERROR:
            if event.error_message:
                self.observed_errors.append(event.error_message)
            self.completion_state = "error"
            self.turn_phase = TurnPhase.TURN_ENDING
        
        elif event.event_type in (FreeBuffEventType.SESSION_END, FreeBuffEventType.RUN_END):
            self.completion_state = "completed"
            self.turn_phase = TurnPhase.IDLE
    
    def is_safe_to_replay(self) -> bool:
        """Determine if current turn is safe to replay (no side effects observed)."""
        return not self.assistant_output_observed and not self.tool_activity_observed
    
    def time_since_last_event(self) -> float:
        return time.time() - self.last_event_timestamp
    
    def time_since_last_assistant_chunk(self) -> Optional[float]:
        if self.last_assistant_chunk_timestamp:
            return time.time() - self.last_assistant_chunk_timestamp
        return None
    
    def time_since_last_tool_event(self) -> Optional[float]:
        if self.last_tool_event_timestamp:
            return time.time() - self.last_tool_event_timestamp
        return None
    
    def get_partial_tool_names(self) -> List[str]:
        return self.partial_tool_names.copy()


class FreeBuffFailureDetector:
    """Detects and reports observable failure conditions from FreeBuff events."""
    
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
    
    def check_session(self, session: FreeBuffSessionState) -> List[str]:
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
        ):
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
        
        # Connection/backend failures from observed errors
        for error in session.observed_errors:
            error_lower = error.lower()
            if "429" in error or "rate limit" in error_lower:
                failures.append(f"RATE_LIMIT_DETECTED: {error}")
            elif any(code in error for code in ["500", "502", "503", "504"]):
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


class FreeBuffObserver:
    """
    Observer for FreeBuff (Codebuff) Agent.
    
    Connects via HTTP API / WebSocket and observes events without
    interfering with FreeBuff's operation.
    """
    
    def __init__(
        self,
        connection_info: 'FreeBuffConnectionInfo',
        stale_threshold: float = 60.0,
        first_chunk_threshold: float = 30.0,
        chunk_gap_threshold: float = 15.0,
        tool_timeout_threshold: float = 120.0,
        on_event: Optional[Callable[[dict], None]] = None,
        on_failure: Optional[Callable[[str, dict], None]] = None,
    ):
        self.connection_info = connection_info
        self.stale_threshold = stale_threshold
        self.first_chunk_threshold = first_chunk_threshold
        self.chunk_gap_threshold = chunk_gap_threshold
        self.tool_timeout_threshold = tool_timeout_threshold
        self.on_event = on_event
        self.on_failure = on_failure
        
        self.sessions: Dict[str, FreeBuffSessionState] = {}
        self.parser = FreeBuffProtocolParser()
        self.failure_detector = FreeBuffFailureDetector(
            stale_threshold=stale_threshold,
            first_chunk_threshold=first_chunk_threshold,
            chunk_gap_threshold=chunk_gap_threshold,
            tool_timeout_threshold=tool_timeout_threshold,
            on_failure=on_failure,
        )
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._stale_check_task: Optional[asyncio.Task] = None
    
    async def connect(self) -> bool:
        """Connect to FreeBuff via WebSocket."""
        try:
            headers = {
                "Authorization": f"Bearer {self.connection_info.api_token}",
                "x-freebuff-acting-user-id": self.connection_info.api_token.split("_")[1] if "_" in self.connection_info.api_token else "unknown",
                "x-freebuff-model": "deepseek/deepseek-v4-flash",
            }
            
            self._session = aiohttp.ClientSession()
            
            self._ws = await self._session.ws_connect(
                self.connection_info.websocket_url,
                headers=headers,
                heartbeat=30,
            )
            
            self._running = True
            
            # Start receive loop
            asyncio.create_task(self._receive_loop())
            
            logger.info("FreeBuff observer connected via WebSocket")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to FreeBuff: {e}")
            await self._cleanup()
            return False
    
    async def _receive_loop(self):
        """Receive messages from FreeBuff WebSocket."""
        if not self._ws:
            return
        
        try:
            async for msg in self._ws:
                if not self._running:
                    break
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_message(data)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse FreeBuff message: {msg.data[:100]}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {self._ws.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("FreeBuff WebSocket closed")
                    break
        except Exception as e:
            logger.error(f"Error in FreeBuff receive loop: {e}")
        finally:
            self._running = False
    
    async def _handle_message(self, data: Dict[str, Any]):
        """Handle a parsed message from FreeBuff."""
        # Parse event
        event = self.parser.parse_websocket_message(data)
        if not event:
            return
        
        # Get or create session state
        session_id = event.session_id or "default"
        if session_id not in self.sessions:
            self.sessions[session_id] = FreeBuffSessionState(session_id=session_id)
        
        session = self.sessions[session_id]
        
        # Update session state
        session.update_event(event)
        
        # Emit event
        event_dict = {
            "type": event.event_type.value,
            "session_id": session_id,
            "timestamp": event.timestamp.isoformat(),
            "turn_phase": session.turn_phase.value,
            "safe_to_replay": session.is_safe_to_replay(),
        }
        
        if event.event_type in (FreeBuffEventType.MESSAGE_START, FreeBuffEventType.MESSAGE_CHUNK):
            event_dict["text_length"] = len(event.message_content) if event.message_content else 0
        elif event.event_type in (FreeBuffEventType.TOOL_CALL_START, FreeBuffEventType.TOOL_CALL_UPDATE,
                                  FreeBuffEventType.TOOL_CALL_END, FreeBuffEventType.TOOL_CALL_ERROR):
            event_dict["tool_name"] = event.tool_name
            event_dict["tool_call_id"] = event.tool_call_id
        
        if self.on_event:
            self.on_event(event_dict)
        
        # Check for failures
        failures = self.failure_detector.check_session(session)
        for failure in failures:
            self.failure_detector.report(session_id, failure, {"session_id": session_id})
    
    async def disconnect(self):
        """Disconnect from FreeBuff."""
        self._running = False
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        if self._session:
            await self._session.close()
            self._session = None
        
        logger.info("FreeBuff observer disconnected")
    
    async def _cleanup(self):
        """Cleanup connection resources."""
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None
    
    def get_session_state(self, session_id: str) -> Optional['FreeBuffSessionState']:
        """Get state for a session."""
        return self.sessions.get(session_id)
    
    def get_all_sessions(self) -> Dict[str, 'FreeBuffSessionState']:
        """Get all observed sessions."""
        return self.sessions.copy()
    
    def get_status(self) -> dict:
        """Get observer status summary."""
        return {
            "running": self._running,
            "connected": self._ws is not None and not self._ws.closed,
            "sessions_count": len(self.sessions),
            "stale_threshold": self.stale_threshold,
            "first_chunk_threshold": self.first_chunk_threshold,
            "chunk_gap_threshold": self.chunk_gap_threshold,
            "tool_timeout_threshold": self.tool_timeout_threshold,
        }


async def run_freebuff_observer(
    connection_info: 'FreeBuffConnectionInfo',
    stale_threshold: float = 60.0,
    first_chunk_threshold: float = 30.0,
    chunk_gap_threshold: float = 15.0,
    tool_timeout_threshold: float = 120.0,
    on_event: Optional[Callable[[dict], None]] = None,
    on_failure: Optional[Callable[[str, dict], None]] = None,
) -> FreeBuffObserver:
    """Convenience function to create and connect an observer to FreeBuff."""
    observer = FreeBuffObserver(
        connection_info=connection_info,
        stale_threshold=stale_threshold,
        first_chunk_threshold=first_chunk_threshold,
        chunk_gap_threshold=chunk_gap_threshold,
        tool_timeout_threshold=tool_timeout_threshold,
        on_event=on_event,
        on_failure=on_failure,
    )
    
    attached = await observer.connect()
    if not attached:
        raise ConnectionError("Failed to connect to FreeBuff")
    
    return observer