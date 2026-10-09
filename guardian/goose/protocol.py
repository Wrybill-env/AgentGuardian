"""
ACP (Agent Client Protocol) message parsing and classification.

This module handles parsing of ACP JSON-RPC 2.0 frames and classifying
them into observable event types for the Guardian observer.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class ACPEventType(Enum):
    """Types of observable ACP events."""
    # Connection events
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    HANDSHAKE_SENT = "handshake_sent"
    HANDSHAKE_RECEIVED = "handshake_received"
    
    # Session events
    SESSION_UPDATE = "session_update"
    SESSION_INFO_UPDATE = "session_info_update"
    CONFIG_OPTION_UPDATE = "config_option_update"
    CURRENT_MODE_UPDATE = "current_mode_update"
    
    # Message events
    ASSISTANT_CHUNK = "assistant_chunk"
    ASSISTANT_MESSAGE = "assistant_message"
    USER_MESSAGE = "user_message"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_UPDATE = "tool_call_update"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"
    
    # Usage events
    USAGE_UPDATE = "usage_update"
    
    # Turn completion
    TURN_COMPLETE = "turn_complete"
    TURN_MAX_TURNS = "turn_max_turns"
    TURN_ERROR = "turn_error"
    TURN_CANCELLED = "turn_cancelled"
    
    # Errors
    PROVIDER_ERROR = "provider_error"
    RATE_LIMIT = "rate_limit"
    NETWORK_ERROR = "network_error"
    SERVER_ERROR = "server_error"
    AUTHENTICATION_ERROR = "authentication_error"
    
    # Other
    UNKNOWN = "unknown"


class StopReason(Enum):
    """ACP StopReason values (from agent_client_protocol schema)."""
    END_TURN = "end_turn"
    MAX_TURNS = "max_turns"
    ERROR = "error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class GooseConnectionInfo:
    """Information about a discovered Goose backend connection."""
    host: str
    port: int
    status_url: str
    websocket_url: str
    token: str
    
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"
    
    def redacted_websocket_url(self) -> str:
        """Return websocket URL with token redacted."""
        return re.sub(r'token=[^&\s]+', 'token=***REDACTED***', self.websocket_url)


@dataclass
class ACPEvent:
    """Parsed ACP event with classification."""
    event_type: ACPEventType
    timestamp: datetime
    session_id: Optional[str] = None
    raw_message: Optional[Dict[str, Any]] = None
    # Event-specific fields
    message: Optional[str] = None
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    stop_reason: Optional[StopReason] = None
    error_message: Optional[str] = None
    http_status: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ACPProtocolParser:
    """Parse and classify ACP JSON-RPC frames."""
    
    # Method patterns for classification
    NOTIFICATION_METHODS = {
        "session/update": ACPEventType.SESSION_UPDATE,
        "sessionUpdate": ACPEventType.SESSION_UPDATE,
        "agent/sessionUpdate": ACPEventType.SESSION_UPDATE,
    }
    
    REQUEST_METHODS = {
        "agent/initialize": ACPEventType.HANDSHAKE_SENT,
        "agent/session/prompt": ACPEventType.USER_MESSAGE,
        "agent/session/cancel": ACPEventType.TURN_CANCELLED,
    }
    
    RESPONSE_METHODS = {}  # Responses don't have method, they have id
    
    def __init__(self):
        self._pending_requests: Dict[int, str] = {}  # id -> method
    
    def parse_frame(self, frame: str) -> List[ACPEvent]:
        """Parse a WebSocket frame and return classified events."""
        events = []
        try:
            data = json.loads(frame)
        except json.JSONDecodeError:
            return events
        
        # JSON-RPC 2.0 can be single object or array
        messages = data if isinstance(data, list) else [data]
        
        for msg in messages:
            event = self._parse_message(msg)
            if event:
                events.append(event)
        
        return events
    
    def _parse_message(self, msg: Dict[str, Any]) -> Optional[ACPEvent]:
        """Parse a single JSON-RPC message."""
        # Request
        if "method" in msg and "id" in msg:
            return self._parse_request(msg)
        # Notification (no id)
        elif "method" in msg and "id" not in msg:
            return self._parse_notification(msg)
        # Response
        elif "id" in msg and ("result" in msg or "error" in msg):
            return self._parse_response(msg)
        
        return None
    
    def _parse_request(self, msg: Dict[str, Any]) -> Optional[ACPEvent]:
        """Parse a JSON-RPC request."""
        method = msg.get("method", "")
        params = msg.get("params", {})
        msg_id = msg.get("id")
        
        # Track pending request for response correlation
        if msg_id is not None:
            self._pending_requests[msg_id] = method
        
        session_id = params.get("sessionId")
        
        event_type = self.REQUEST_METHODS.get(method, ACPEventType.UNKNOWN)
        
        return ACPEvent(
            event_type=event_type,
            timestamp=datetime.now(),
            session_id=session_id,
            raw_message=msg,
            metadata={"method": method, "request_id": msg_id}
        )
    
    def _parse_notification(self, msg: Dict[str, Any]) -> Optional[ACPEvent]:
        """Parse a JSON-RPC notification (server push)."""
        method = msg.get("method", "")
        params = msg.get("params", {})
        
        session_id = params.get("sessionId") or params.get("session_id")
        
        # Handle sessionUpdate notifications - must check BEFORE generic NOTIFICATION_METHODS
        if method in ("session/update", "sessionUpdate", "agent/sessionUpdate"):
            return self._parse_session_update(params, session_id, msg)
        
        event_type = self.NOTIFICATION_METHODS.get(method, ACPEventType.UNKNOWN)
        
        return ACPEvent(
            event_type=event_type,
            timestamp=datetime.now(),
            session_id=session_id,
            raw_message=msg,
            metadata={"method": method}
        )
    
    def _parse_session_update(self, params: Dict[str, Any], session_id: Optional[str], raw: Dict[str, Any]) -> ACPEvent:
        """Parse a sessionUpdate notification."""
        update = params.get("update", {})
        # ACP protocol uses "sessionUpdate" field for the update type
        update_type = update.get("type") or update.get("updateType") or update.get("sessionUpdate") or update.get("session_update")
        
        # Determine specific event type from update
        event_type = ACPEventType.SESSION_UPDATE
        
        if update_type == "SessionInfoUpdate":
            event_type = ACPEventType.SESSION_INFO_UPDATE
        elif update_type == "ConfigOptionUpdate":
            event_type = ACPEventType.CONFIG_OPTION_UPDATE
        elif update_type == "CurrentModeUpdate":
            event_type = ACPEventType.CURRENT_MODE_UPDATE
        elif update_type == "UsageUpdate":
            event_type = ACPEventType.USAGE_UPDATE
        elif update_type == "MessageUpdate":
            # Check if it's assistant or tool-related
            message = update.get("message", {})
            role = message.get("role")
            content = message.get("content", [])
            
            if role == "assistant":
                # Check for tool calls in content
                has_tool_call = any(
                    c.get("type") == "tool_call" or c.get("type") == "toolCall"
                    for c in content
                )
                if has_tool_call:
                    event_type = ACPEventType.TOOL_CALL_STARTED
                else:
                    event_type = ACPEventType.ASSISTANT_CHUNK
            elif role == "user":
                event_type = ACPEventType.USER_MESSAGE
        elif update_type == "ToolCallUpdate":
            event_type = ACPEventType.TOOL_CALL_UPDATE
            # Check status
            tool_call = update.get("toolCall", {})
            status = tool_call.get("status")
            if status == "completed":
                event_type = ACPEventType.TOOL_CALL_COMPLETED
            elif status == "failed":
                event_type = ACPEventType.TOOL_CALL_FAILED
        # Handle actual ACP protocol tool call events (lowercase with underscore)
        elif update_type == "tool_call":
            event_type = ACPEventType.TOOL_CALL_STARTED
        elif update_type == "tool_call_update":
            event_type = ACPEventType.TOOL_CALL_UPDATE
            # Check status
            status = update.get("status")
            if status == "completed":
                event_type = ACPEventType.TOOL_CALL_COMPLETED
            elif status == "failed":
                event_type = ACPEventType.TOOL_CALL_FAILED
        
        # Extract tool info if present
        tool_name = None
        tool_call_id = None
        if update_type in ("ToolCallUpdate", "MessageUpdate", "tool_call", "tool_call_update"):
            tool_call = update.get("toolCall", {})
            # For tool_call and tool_call_update, tool info is at top level
            if not tool_call and update_type in ("tool_call", "tool_call_update"):
                tool_call = {
                    "name": update.get("title"),
                    "id": update.get("toolCallId") or update.get("tool_call_id"),
                    "status": update.get("status"),
                    "rawInput": update.get("rawInput"),
                }
            if not tool_call and update_type == "MessageUpdate":
                # Check content for tool calls
                for c in update.get("message", {}).get("content", []):
                    if c.get("type") in ("tool_call", "toolCall"):
                        tool_call = c
                        break
            
            if tool_call:
                tool_name = tool_call.get("name") or tool_call.get("toolName") or tool_call.get("title")
                tool_call_id = tool_call.get("id") or tool_call.get("toolCallId") or tool_call.get("tool_call_id")
        
        # Check for stop reason FIRST (takes priority over error)
        stop_reason = None
        stop_reason_str = update.get("stopReason") or params.get("stopReason")
        has_stop_reason = bool(stop_reason_str)
        if stop_reason_str:
            try:
                stop_reason = StopReason(stop_reason_str.lower())
            except ValueError:
                stop_reason = StopReason.UNKNOWN
            
            # Map stop reason to event type
            if stop_reason == StopReason.END_TURN:
                event_type = ACPEventType.TURN_COMPLETE
            elif stop_reason == StopReason.MAX_TURNS:
                event_type = ACPEventType.TURN_MAX_TURNS
            elif stop_reason == StopReason.ERROR:
                event_type = ACPEventType.TURN_ERROR
            elif stop_reason == StopReason.CANCELLED:
                event_type = ACPEventType.TURN_CANCELLED
        
        # Check for error in update (capture error message even if stop reason exists)
        error_message = None
        if update_type == "Error" or "error" in update:
            error_message = str(update.get("error") or update.get("message", ""))
            if not has_stop_reason:
                event_type = ACPEventType.PROVIDER_ERROR
        
        return ACPEvent(
            event_type=event_type,
            timestamp=datetime.now(),
            session_id=session_id,
            raw_message=raw,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            stop_reason=stop_reason,
            error_message=error_message,
            metadata={"update_type": update_type}
        )
    
    def _parse_response(self, msg: Dict[str, Any]) -> Optional[ACPEvent]:
        """Parse a JSON-RPC response."""
        msg_id = msg.get("id")
        method = self._pending_requests.pop(msg_id, "unknown") if msg_id is not None else "unknown"
        
        error = msg.get("error")
        event_type = ACPEventType.UNKNOWN
        if error:
            error_code = error.get("code", 0)
            error_msg = error.get("message", "")
            
            # Classify error
            event_type = ACPEventType.PROVIDER_ERROR
            if error_code == -32603:  # Internal error often used for provider errors
                if "429" in error_msg or "rate limit" in error_msg.lower():
                    event_type = ACPEventType.RATE_LIMIT
                elif "500" in error_msg or "502" in error_msg or "503" in error_msg or "504" in error_msg:
                    event_type = ACPEventType.SERVER_ERROR
                elif "timeout" in error_msg.lower() or "408" in error_msg:
                    event_type = ACPEventType.NETWORK_ERROR
                elif "connection" in error_msg.lower() or "reset" in error_msg.lower():
                    event_type = ACPEventType.NETWORK_ERROR
        else:
            # Successful response
            event_type = ACPEventType.SESSION_UPDATE
        
        return ACPEvent(
            event_type=event_type,
            timestamp=datetime.now(),
            raw_message=msg,
            error_message=str(error) if error else None,
            metadata={"method": method, "request_id": msg_id, "error_code": error.get("code") if error else None}
        )


def classify_http_error(status_code: int) -> ACPEventType:
    """Classify HTTP status code into event type."""
    if status_code == 429:
        return ACPEventType.RATE_LIMIT
    elif status_code == 408:
        return ACPEventType.NETWORK_ERROR
    elif 500 <= status_code < 600:
        return ACPEventType.SERVER_ERROR
    elif status_code == 401 or status_code == 403:
        return ACPEventType.AUTHENTICATION_ERROR
    return ACPEventType.PROVIDER_ERROR