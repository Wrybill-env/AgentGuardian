"""
OpenCode Observer.

Observes OpenCode events via ACP (stdio) or HTTP API.
"""

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from guardian.logging import get_logger
from guardian.core.types import (
    TurnPhase,
    FailureType,
    RecoveryAction,
)
from .protocol import (
    OCPAEvent,
    OCPAEventType,
    OCPAProtocolParser,
    OpenCodeConnectionInfo,
)

logger = get_logger("opencode.observer")


@dataclass
class OpenCodeSessionState:
    """Tracks state for an observed OpenCode session."""
    session_id: str
    directory: Optional[str] = None
    title: Optional[str] = None
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
    current_message_id: Optional[str] = None
    current_tool_call_id: Optional[str] = None
    
    def update_event(self, event: OCPAEvent, **kwargs):
        """Update state based on observed event."""
        self.last_event_timestamp = time.time()
        
        if event.event_type == OCPAEventType.SESSION_START:
            self.title = event.session_title
            self.directory = event.session_directory
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
        
        elif event.event_type in (OCPAEventType.AGENT_MESSAGE_CHUNK, OCPAEventType.USER_MESSAGE_CHUNK):
            self.assistant_output_observed = True
            self.last_assistant_chunk_timestamp = time.time()
            if self.first_assistant_chunk_timestamp is None:
                self.first_assistant_chunk_timestamp = time.time()
            if self.turn_phase in (TurnPhase.USER_MESSAGE_SENT, TurnPhase.IDLE):
                self.turn_phase = TurnPhase.ASSISTANT_STREAMING
            self.current_message_id = event.message_id
        
        elif event.event_type == OCPAEventType.AGENT_THOUGHT_CHUNK:
            if self.turn_phase in (TurnPhase.USER_MESSAGE_SENT, TurnPhase.IDLE):
                self.turn_phase = TurnPhase.ASSISTANT_STREAMING
            self.last_reasoning_timestamp = time.time()
        
        elif event.event_type == OCPAEventType.TOOL_CALL:
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
                    "kind": event.tool_kind,
                    "state": "pending"
                }
            if self.turn_phase in (TurnPhase.USER_MESSAGE_SENT, TurnPhase.ASSISTANT_STREAMING, TurnPhase.IDLE):
                self.turn_phase = TurnPhase.TOOL_EXECUTING
        
        elif event.event_type == OCPAEventType.TOOL_CALL_UPDATE:
            self.last_tool_event_timestamp = time.time()
            if event.tool_call_id and event.tool_call_id in self.tool_calls:
                if event.tool_arguments:
                    self.tool_calls[event.tool_call_id]["arguments"] = event.tool_arguments
                if event.tool_state:
                    self.tool_calls[event.tool_call_id]["state"] = event.tool_state
        
        elif event.event_type in (OCPAEventType.SESSION_END, OCPAEventType.SESSION_NOTIFICATION):
            # Check if this is a prompt completion (from response parsing)
            if event.event_type == OCPAEventType.SESSION_END:
                self.turn_phase = TurnPhase.TURN_ENDING
                self.completion_state = "completed"
                self.turn_count += 1
                # NOTE: Do NOT reset assistant_output_observed, tool_activity_observed,
                # partial_tool_names, or tool_calls here. These represent side effects
                # that occurred in this turn and must be preserved for safety evaluation
                # after the turn ends. They will be reset on the next SESSION_START.
                self.first_assistant_chunk_timestamp = None
                self.current_message_id = None
        
        elif event.event_type == OCPAEventType.ERROR:
            if event.error_message:
                self.observed_errors.append(event.error_message)
            self.completion_state = "error"
            self.turn_phase = TurnPhase.TURN_ENDING
        
        elif event.event_type == OCPAEventType.PLAN:
            # Plan update - could track todo state
            pass
        
        elif event.event_type == OCPAEventType.USAGE_UPDATE:
            # Usage update - could track token usage
            pass
    
    def is_safe_to_replay(self) -> bool:
        """Determine if current turn is safe to replay (no side effects observed)."""
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
    
    def get_partial_tool_names(self) -> List[str]:
        return self.partial_tool_names.copy()


class OpenCodeFailureDetector:
    """Detects and reports observable failure conditions from OpenCode events."""
    
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
    
    def check_session(self, session: OpenCodeSessionState) -> List[str]:
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


class OpenCodeObserver:
    """
    Observer for OpenCode Agent.
    
    Connects via ACP (stdio) or HTTP API and observes events without
    interfering with OpenCode's operation.
    """
    
    def __init__(
        self,
        connection_info: OpenCodeConnectionInfo,
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
        
        self.sessions: Dict[str, OpenCodeSessionState] = {}
        self.parser = OCPAProtocolParser()
        self.failure_detector = OpenCodeFailureDetector(
            stale_threshold=stale_threshold,
            first_chunk_threshold=first_chunk_threshold,
            chunk_gap_threshold=chunk_gap_threshold,
            tool_timeout_threshold=tool_timeout_threshold,
            on_failure=on_failure,
        )
        
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._stale_check_task: Optional[asyncio.Task] = None
        self._read_task: Optional[asyncio.Task] = None
    
    async def connect_acp(self, opencode_path: str = "opencode") -> bool:
        """Connect to OpenCode via ACP stdio subprocess."""
        try:
            # On Windows, opencode is a .cmd file, so we need to use shell=True or the .cmd extension
            import sys
            if sys.platform == "win32":
                # Use the full path to the .cmd file
                if not opencode_path.endswith(".cmd"):
                    opencode_path = opencode_path + ".cmd"
                self._process = subprocess.Popen(
                    [opencode_path, "acp"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    shell=True,  # Required on Windows for .cmd files
                )
            else:
                self._process = subprocess.Popen(
                    [opencode_path, "acp"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            
            self._running = True
            self._read_task = asyncio.create_task(self._read_loop())
            
            # Send initialize request
            init_msg = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {
                        "name": "agent-guardian",
                        "version": "0.1.0",
                    },
                },
            }
            await self._send_message(init_msg)
            
            logger.info("OpenCode ACP observer connected")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to OpenCode via ACP: {e}")
            return False
    
    async def connect_http(self) -> bool:
        """Connect to OpenCode via HTTP API."""
        # For HTTP mode, we would use the REST API and WebSocket
        # This is a placeholder for future implementation
        logger.warning("HTTP connection not yet implemented for OpenCode observer")
        return False
    
    async def _send_message(self, message: Dict[str, Any]) -> bool:
        """Send a message to the OpenCode ACP process."""
        if not self._process or not self._process.stdin:
            return False
        
        try:
            line = json.dumps(message) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
            return True
        except Exception as e:
            logger.error(f"Failed to send message to OpenCode: {e}")
            return False
    
    async def _read_loop(self):
        """Read messages from OpenCode stdout."""
        if not self._process or not self._process.stdout:
            return
        
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                line = await loop.run_in_executor(None, self._process.stdout.readline)
                if not line:
                    break
                
                line = line.strip()
                if not line:
                    continue
                
                try:
                    message = json.loads(line)
                    await self._handle_message(message)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse OpenCode message: {line[:100]}")
            except Exception as e:
                logger.error(f"Error in OpenCode read loop: {e}")
                break
    
    async def _handle_message(self, message: Dict[str, Any]):
        """Handle a parsed message from OpenCode."""
        # Handle response to our initialize request
        if "id" in message and message.get("id") == 1 and "result" in message:
            logger.info("OpenCode ACP initialized successfully")
            return
        
        # Parse event
        event = self.parser.parse_message(message)
        if not event:
            return
        
        # Get or create session state
        session_id = event.session_id
        if not session_id:
            # Try to get session_id from pending requests
            for pending in self.parser._pending_requests.values():
                params = pending.get("params", {})
                if "sessionId" in params:
                    session_id = params["sessionId"]
                    break
        if not session_id:
            session_id = "unknown"
        if session_id not in self.sessions:
            self.sessions[session_id] = OpenCodeSessionState(session_id=session_id)
        
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
        
        # Add event-specific fields
        if event.event_type in (OCPAEventType.AGENT_MESSAGE_CHUNK, OCPAEventType.USER_MESSAGE_CHUNK, OCPAEventType.AGENT_THOUGHT_CHUNK):
            event_dict["text_length"] = len(event.message_content) if event.message_content else 0
            event_dict["thinking"] = event.event_type == OCPAEventType.AGENT_THOUGHT_CHUNK
        elif event.event_type in (OCPAEventType.TOOL_CALL, OCPAEventType.TOOL_CALL_UPDATE):
            event_dict["tool_name"] = event.tool_name
            event_dict["tool_call_id"] = event.tool_call_id
            event_dict["tool_kind"] = event.tool_kind
            event_dict["tool_state"] = event.tool_state
        
        if self.on_event:
            self.on_event(event_dict)
        
        # Check for failures
        failures = self.failure_detector.check_session(session)
        for failure in failures:
            self.failure_detector.report(session_id, failure, {"session_id": session_id})
    
    async def disconnect(self):
        """Disconnect from OpenCode."""
        self._running = False
        
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        
        if self._stale_check_task and not self._stale_check_task.done():
            self._stale_check_task.cancel()
            try:
                await self._stale_check_task
            except asyncio.CancelledError:
                pass
        
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, self._process.wait),
                    timeout=5.0
                )
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            self._process = None
        
        logger.info("OpenCode observer disconnected")
    
    def get_session_state(self, session_id: str) -> Optional[OpenCodeSessionState]:
        """Get state for a session."""
        return self.sessions.get(session_id)
    
    def get_all_sessions(self) -> Dict[str, OpenCodeSessionState]:
        """Get all observed sessions."""
        return self.sessions.copy()
    
    def get_status(self) -> dict:
        """Get observer status summary."""
        return {
            "running": self._running,
            "connected": self._process is not None,
            "sessions_count": len(self.sessions),
            "stale_threshold": self.stale_threshold,
            "first_chunk_threshold": self.first_chunk_threshold,
            "chunk_gap_threshold": self.chunk_gap_threshold,
            "tool_timeout_threshold": self.tool_timeout_threshold,
        }


async def run_opencode_observer(
    connection_info: OpenCodeConnectionInfo,
    stale_threshold: float = 60.0,
    first_chunk_threshold: float = 30.0,
    chunk_gap_threshold: float = 15.0,
    tool_timeout_threshold: float = 120.0,
    on_event: Optional[Callable[[dict], None]] = None,
    on_failure: Optional[Callable[[str, dict], None]] = None,
) -> OpenCodeObserver:
    """Convenience function to create and connect an observer to OpenCode."""
    observer = OpenCodeObserver(
        connection_info=connection_info,
        stale_threshold=stale_threshold,
        first_chunk_threshold=first_chunk_threshold,
        chunk_gap_threshold=chunk_gap_threshold,
        tool_timeout_threshold=tool_timeout_threshold,
        on_event=on_event,
        on_failure=on_failure,
    )
    
    if connection_info.mode == "acp":
        attached = await observer.connect_acp()
    else:
        attached = await observer.connect_http()
    
    if not attached:
        raise ConnectionError("Failed to connect to OpenCode")
    
    return observer