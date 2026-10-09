"""
Read-only WebSocket observer for Goose Desktop ACP events.

Connects to the ACP WebSocket and observes events without sending
any commands that would modify Goose state.
"""

import asyncio
import json
import signal
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from ..logging import get_logger
from .protocol import (
    ACPEvent,
    ACPEventType,
    ACPProtocolParser,
    GooseConnectionInfo,
    StopReason,
)
from .discovery import discover_primary_goose, GooseConnectionInfo as DiscoveredInfo

logger = get_logger("observer")


@dataclass
class SessionState:
    """Tracks state for an observed session/run."""
    session_id: str
    run_id: Optional[str] = None
    connection_state: str = "connected"  # connected, disconnected, reconnecting
    assistant_output_observed: bool = False
    tool_activity_observed: bool = False
    last_event_timestamp: float = field(default_factory=time.time)
    last_assistant_chunk_timestamp: Optional[float] = None
    last_tool_event_timestamp: Optional[float] = None
    completion_state: Optional[str] = None  # completed, error, cancelled, max_turns
    observed_errors: List[str] = field(default_factory=list)
    tool_calls: Dict[str, Dict] = field(default_factory=dict)  # tool_call_id -> info
    partial_tool_names: Set[str] = field(default_factory=set)  # tool names with partial args
    turn_count: int = 0
    
    def update_event(self, event: ACPEvent):
        """Update state based on observed event."""
        self.last_event_timestamp = time.time()
        
        if event.event_type == ACPEventType.ASSISTANT_CHUNK:
            self.assistant_output_observed = True
            self.last_assistant_chunk_timestamp = time.time()
        
        elif event.event_type in (
            ACPEventType.TOOL_CALL_STARTED,
            ACPEventType.TOOL_CALL_UPDATE,
            ACPEventType.TOOL_CALL_COMPLETED,
            ACPEventType.TOOL_CALL_FAILED,
        ):
            self.tool_activity_observed = True
            self.last_tool_event_timestamp = time.time()
            if event.tool_call_id:
                self.tool_calls[event.tool_call_id] = {
                    "name": event.tool_name,
                    "status": event.event_type.value,
                    "timestamp": time.time(),
                }
                # Track partial tool names for tools that have started but not completed
                if event.event_type in (ACPEventType.TOOL_CALL_STARTED, ACPEventType.TOOL_CALL_UPDATE):
                    if event.tool_name:
                        self.partial_tool_names.add(event.tool_name)
                elif event.event_type in (ACPEventType.TOOL_CALL_COMPLETED, ACPEventType.TOOL_CALL_FAILED):
                    # Tool completed/failed - remove from partial
                    if event.tool_name:
                        self.partial_tool_names.discard(event.tool_name)
        
        elif event.event_type == ACPEventType.TURN_COMPLETE:
            self.completion_state = "completed"
            self.turn_count += 1
            # Reset per-turn flags
            self.assistant_output_observed = False
            self.tool_activity_observed = False
            self.partial_tool_names.clear()
        
        elif event.event_type == ACPEventType.TURN_MAX_TURNS:
            self.completion_state = "max_turns"
            self.turn_count += 1
        
        elif event.event_type == ACPEventType.TURN_ERROR:
            self.completion_state = "error"
            if event.error_message:
                self.observed_errors.append(event.error_message)
        
        elif event.event_type == ACPEventType.TURN_CANCELLED:
            self.completion_state = "cancelled"
        
        elif event.event_type in (
            ACPEventType.RATE_LIMIT,
            ACPEventType.SERVER_ERROR,
            ACPEventType.NETWORK_ERROR,
            ACPEventType.AUTHENTICATION_ERROR,
            ACPEventType.PROVIDER_ERROR,
        ):
            if event.error_message:
                self.observed_errors.append(event.error_message)
        
        elif event.event_type == ACPEventType.SESSION_UPDATE:
            # Track run ID if available
            if event.raw_message:
                params = event.raw_message.get("params", {})
                update = params.get("update", {})
                if "runId" in update:
                    self.run_id = update["runId"]
    
    def is_safe_to_replay(self) -> bool:
        """
        Determine if current turn is safe to replay (no side effects observed).
        
        This is for FUTURE recovery logic - currently only used for reporting.
        """
        return not self.assistant_output_observed and not self.tool_activity_observed
    
    def get_partial_tool_names(self) -> List[str]:
        """Get list of partial tool names for safety revalidation."""
        return list(self.partial_tool_names)
    
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


class FailureDetector:
    """Detects and reports observable failure conditions."""
    
    def __init__(
        self,
        stale_threshold: float = 60.0,
        on_failure: Optional[Callable[[str, dict], None]] = None
    ):
        self.stale_threshold = stale_threshold
        self.on_failure = on_failure
        self._reported_stale: Set[str] = set()  # session_ids that have been reported stale
    
    def check_session(self, session: SessionState) -> List[str]:
        """Check a session for failure conditions. Returns list of failure descriptions."""
        failures = []
        now = time.time()
        
        # Stale stream detection
        if session.connection_state == "connected":
            time_since_event = session.time_since_last_event()
            if time_since_event > self.stale_threshold:
                key = f"{session.session_id}:stale"
                if key not in self._reported_stale:
                    self._reported_stale.add(key)
                    failures.append(
                        f"POSSIBLE_STALE_STREAM: no observable event for {time_since_event:.0f}s"
                    )
        
        # Connection state failures
        if session.connection_state == "disconnected":
            failures.append("BACKEND_DISCONNECTED: WebSocket connection closed")
        
        # Observed errors
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
                "connection aborted", "broken pipe", "pipe closed"
            ]):
                failures.append(f"CONNECTION_RESET_DETECTED: {error}")
            elif "authentication" in error_lower or "401" in error or "403" in error:
                failures.append(f"AUTHENTICATION_ERROR_DETECTED: {error}")
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


class GooseObserver:
    """
    Read-only observer for Goose Desktop ACP WebSocket.
    
    Connects to the WebSocket, performs minimum handshake,
    and observes all events without sending any state-changing commands.
    """
    
    def __init__(
        self,
        connection_info: GooseConnectionInfo,
        stale_threshold: float = 60.0,
        on_event: Optional[Callable[[ACPEvent], None]] = None,
        on_failure: Optional[Callable[[str, dict], None]] = None,
        use_tls: bool = False,
    ):
        self.connection_info = connection_info
        self.stale_threshold = stale_threshold
        self.on_event = on_event
        self.on_failure = on_failure
        self.use_tls = use_tls
        
        self.parser = ACPProtocolParser()
        self.sessions: Dict[str, SessionState] = {}
        self.failure_detector = FailureDetector(stale_threshold, on_failure)
        
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._stale_check_task: Optional[asyncio.Task] = None
    
    async def connect(self, token: Optional[str] = None) -> bool:
        """
        Connect to the Goose ACP WebSocket.
        
        Performs minimum ACP handshake (initialize request) to establish
        a valid connection for receiving notifications.
        
        Args:
            token: Optional ACP token. If not provided, attempts connection without.
        """
        ws_url = self.connection_info.websocket_url
        if self.use_tls:
            # Convert ws:// to wss://
            ws_url = ws_url.replace("ws://", "wss://")
        if token and token != "UNKNOWN":
            # Add token to WebSocket URL
            parsed = urlparse(ws_url)
            query = parse_qs(parsed.query)
            query["token"] = [token]
            new_query = urlencode(query, doseq=True)
            ws_url = urlunparse(parsed._replace(query=new_query))
        
        logger.info(f"Connecting to Goose ACP at {self.connection_info.redacted_websocket_url()}")
        
        # Create SSL context for TLS connections
        ssl_context = None
        if self.use_tls:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        
        try:
            self._ws = await websockets.connect(
                ws_url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
                ssl=ssl_context,
            )
        except (WebSocketException, ConnectionRefusedError, OSError) as e:
            logger.error(f"Failed to connect to Goose WebSocket: {e}")
            return False
        
        logger.info("WebSocket connected, performing ACP handshake...")
        
        # Perform minimum ACP handshake
        if not await self._perform_handshake():
            await self.disconnect()
            return False
        
        self._running = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._stale_check_task = asyncio.create_task(self._stale_check_loop())
        
        logger.info("Goose observer connected and ready")
        return True
    
    async def _perform_handshake(self) -> bool:
        """
        Perform minimum ACP initialize handshake.
        
        This is REQUIRED by the ACP protocol to establish a connection
        that will receive sessionUpdate notifications. We send only the
        initialize request and wait for the response.
        """
        if not self._ws:
            return False
        
        # ACP Initialize request (per agent_client_protocol schema)
        initialize_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "elicitation": {"form": {}},
                    "_meta": {
                        "goose": {
                            "mcpHostCapabilities": ["filesystem.read", "filesystem.write", "shell.execute"],
                            "customNotifications": True,
                            "recipeParameterRequests": True,
                        }
                    }
                },
                "clientInfo": {
                    "name": "agent-guardian",
                    "version": "0.1.0"
                }
            }
        }
        
        try:
            await self._ws.send(json.dumps(initialize_request))
            logger.debug("Sent ACP initialize request")
            
            # Wait for initialize response
            response = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            response_data = json.loads(response)
            
            if "result" in response_data:
                logger.info("ACP handshake successful")
                return True
            elif "error" in response_data:
                logger.error(f"ACP handshake failed: {response_data['error']}")
                return False
            else:
                logger.warning(f"Unexpected handshake response: {response_data}")
                return False
                
        except asyncio.TimeoutError:
            logger.error("ACP handshake timed out")
            return False
        except (WebSocketException, json.JSONDecodeError) as e:
            logger.error(f"ACP handshake error: {e}")
            return False
    
    async def _receive_loop(self):
        """Main receive loop for WebSocket messages."""
        try:
            async for message in self._ws:
                await self._handle_message(message)
        except ConnectionClosed as e:
            logger.info(f"WebSocket connection closed: {e.code} {e.reason}")
            self._running = False
            self._mark_all_disconnected()
        except WebSocketException as e:
            logger.error(f"WebSocket error: {e}")
            self._running = False
            self._mark_all_disconnected()
        except Exception as e:
            logger.error(f"Unexpected error in receive loop: {e}")
            self._running = False
            self._mark_all_disconnected()
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        events = self.parser.parse_frame(message)
        
        for event in events:
            # Update session state
            if event.session_id:
                if event.session_id not in self.sessions:
                    self.sessions[event.session_id] = SessionState(session_id=event.session_id)
                session = self.sessions[event.session_id]
                session.update_event(event)
                
                # Check for failures
                failures = self.failure_detector.check_session(session)
                for failure in failures:
                    self.failure_detector.report(
                        event.session_id,
                        failure,
                        {
                            "session_id": event.session_id,
                            "event_type": event.event_type.value,
                            "details": event.metadata,
                        }
                    )
            
            # Call event callback
            if self.on_event:
                self.on_event(event)
            
            # Log event
            self._log_event(event)
    
    def _log_event(self, event: ACPEvent):
        """Log event in human-readable format."""
        session_part = f" session={event.session_id}" if event.session_id else ""
        
        # Build event description
        if event.event_type == ACPEventType.CONNECTED:
            desc = "CONNECTED"
        elif event.event_type == ACPEventType.DISCONNECTED:
            desc = "DISCONNECTED"
        elif event.event_type == ACPEventType.HANDSHAKE_SENT:
            desc = "HANDSHAKE_SENT"
        elif event.event_type == ACPEventType.HANDSHAKE_RECEIVED:
            desc = "HANDSHAKE_RECEIVED"
        elif event.event_type == ACPEventType.SESSION_UPDATE:
            desc = "SESSION_UPDATE"
        elif event.event_type == ACPEventType.SESSION_INFO_UPDATE:
            desc = "SESSION_INFO_UPDATE"
        elif event.event_type == ACPEventType.CONFIG_OPTION_UPDATE:
            desc = "CONFIG_OPTION_UPDATE"
        elif event.event_type == ACPEventType.CURRENT_MODE_UPDATE:
            desc = "CURRENT_MODE_UPDATE"
        elif event.event_type == ACPEventType.ASSISTANT_CHUNK:
            desc = "ASSISTANT_CHUNK"
        elif event.event_type == ACPEventType.ASSISTANT_MESSAGE:
            desc = "ASSISTANT_MESSAGE"
        elif event.event_type == ACPEventType.USER_MESSAGE:
            desc = "USER_MESSAGE"
        elif event.event_type == ACPEventType.TOOL_CALL_STARTED:
            desc = f"TOOL_CALL_STARTED tool={event.tool_name or 'unknown'}"
        elif event.event_type == ACPEventType.TOOL_CALL_UPDATE:
            desc = f"TOOL_CALL_UPDATE tool={event.tool_name or 'unknown'}"
        elif event.event_type == ACPEventType.TOOL_CALL_COMPLETED:
            desc = f"TOOL_CALL_COMPLETED tool={event.tool_name or 'unknown'}"
        elif event.event_type == ACPEventType.TOOL_CALL_FAILED:
            desc = f"TOOL_CALL_FAILED tool={event.tool_name or 'unknown'}"
        elif event.event_type == ACPEventType.USAGE_UPDATE:
            desc = "USAGE_UPDATE"
        elif event.event_type == ACPEventType.TURN_COMPLETE:
            desc = "TURN_COMPLETE"
        elif event.event_type == ACPEventType.TURN_MAX_TURNS:
            desc = "TURN_MAX_TURNS"
        elif event.event_type == ACPEventType.TURN_ERROR:
            desc = f"TURN_ERROR: {event.error_message or 'unknown'}"
        elif event.event_type == ACPEventType.TURN_CANCELLED:
            desc = "TURN_CANCELLED"
        elif event.event_type == ACPEventType.RATE_LIMIT:
            desc = f"RATE_LIMIT: {event.error_message or 'detected'}"
        elif event.event_type == ACPEventType.SERVER_ERROR:
            desc = f"SERVER_ERROR: {event.error_message or 'detected'}"
        elif event.event_type == ACPEventType.NETWORK_ERROR:
            desc = f"NETWORK_ERROR: {event.error_message or 'detected'}"
        elif event.event_type == ACPEventType.AUTHENTICATION_ERROR:
            desc = f"AUTHENTICATION_ERROR: {event.error_message or 'detected'}"
        elif event.event_type == ACPEventType.PROVIDER_ERROR:
            desc = f"PROVIDER_ERROR: {event.error_message or 'detected'}"
        else:
            desc = event.event_type.value.upper()
        
        logger.info(f"{desc}{session_part}")
    
    def _mark_all_disconnected(self):
        """Mark all sessions as disconnected."""
        for session in self.sessions.values():
            session.connection_state = "disconnected"
    
    async def _heartbeat_loop(self):
        """Send periodic pings to keep connection alive."""
        while self._running and self._ws:
            try:
                await asyncio.sleep(30)
                if self._ws and not self._ws.closed:
                    await self._ws.ping()
            except Exception:
                break
    
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
                        {"session_id": session.session_id, "details": {}}
                    )
    
    async def disconnect(self):
        """Disconnect from the WebSocket."""
        self._running = False
        
        for task in [self._receive_task, self._heartbeat_task, self._stale_check_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            logger.info("Disconnected from Goose WebSocket")
    
    def get_session_state(self, session_id: str) -> Optional[SessionState]:
        """Get state for a session."""
        return self.sessions.get(session_id)
    
    def get_all_sessions(self) -> Dict[str, SessionState]:
        """Get all observed sessions."""
        return self.sessions.copy()


async def run_observer(
    connection_info: GooseConnectionInfo,
    token: Optional[str] = None,
    stale_threshold: float = 60.0,
    on_event: Optional[Callable[[ACPEvent], None]] = None,
    on_failure: Optional[Callable[[str, dict], None]] = None,
    use_tls: bool = False,
) -> GooseObserver:
    """
    Convenience function to create and connect an observer.
    
    Returns the connected observer or raises exception on failure.
    """
    observer = GooseObserver(
        connection_info=connection_info,
        stale_threshold=stale_threshold,
        on_event=on_event,
        on_failure=on_failure,
        use_tls=use_tls,
    )
    
    connected = await observer.connect(token)
    if not connected:
        raise ConnectionError("Failed to connect to Goose backend")
    
    return observer