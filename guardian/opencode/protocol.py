"""
OpenCode Protocol Definitions.

Defines the ACP event types and parsing for OpenCode integration.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class OCPAEventType(Enum):
    """ACP event types from OpenCode."""
    # Session lifecycle
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_UPDATE = "session_update"
    
    # Message events
    MESSAGE_START = "message_start"
    MESSAGE_END = "message_end"
    MESSAGE_CHUNK = "message_chunk"
    MESSAGE_UPDATE = "message_update"
    
    # Tool events
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_UPDATE = "tool_call_update"
    TOOL_CALL_END = "tool_call_end"
    TOOL_CALL_ERROR = "tool_call_error"
    
    # Agent events
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    AGENT_THINKING = "agent_thinking"
    AGENT_THINKING_CHUNK = "agent_thinking_chunk"
    
    # Permission events
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESPONSE = "permission_response"
    
    # Error events
    ERROR = "error"
    
    # System events
    INITIALIZE = "initialize"
    SHUTDOWN = "shutdown"


@dataclass
class OCPAEvent:
    """Parsed ACP event from OpenCode."""
    event_type: OCPAEventType
    timestamp: datetime
    session_id: Optional[str] = None
    raw_message: Optional[Dict[str, Any]] = None
    
    # Session fields
    session_title: Optional[str] = None
    session_directory: Optional[str] = None
    
    # Message fields
    message_id: Optional[str] = None
    message_role: Optional[str] = None
    message_content: Optional[str] = None
    message_done: bool = False
    
    # Tool fields
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Optional[Dict[str, Any]] = None
    tool_result: Optional[Any] = None
    tool_error: Optional[str] = None
    tool_done: bool = False
    
    # Agent fields
    agent_name: Optional[str] = None
    thinking_content: Optional[str] = None
    
    # Error fields
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    
    # Permission fields
    permission_id: Optional[str] = None
    permission_tool: Optional[str] = None
    permission_result: Optional[bool] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class OCPAProtocolParser:
    """Parses JSON-RPC messages from OpenCode ACP stdio connection."""
    
    # ACP method to event type mapping
    METHOD_TO_EVENT = {
        # Session
        "session/new": OCPAEventType.SESSION_START,
        "session/close": OCPAEventType.SESSION_END,
        "session/update": OCPAEventType.SESSION_UPDATE,
        
        # Messages
        "message/start": OCPAEventType.MESSAGE_START,
        "message/end": OCPAEventType.MESSAGE_END,
        "message/chunk": OCPAEventType.MESSAGE_CHUNK,
        "message/update": OCPAEventType.MESSAGE_UPDATE,
        
        # Tools
        "tool/call": OCPAEventType.TOOL_CALL_START,
        "tool/update": OCPAEventType.TOOL_CALL_UPDATE,
        "tool/result": OCPAEventType.TOOL_CALL_END,
        "tool/error": OCPAEventType.TOOL_CALL_ERROR,
        
        # Agent
        "agent/start": OCPAEventType.AGENT_START,
        "agent/end": OCPAEventType.AGENT_END,
        "agent/thinking": OCPAEventType.AGENT_THINKING,
        "agent/thinking_chunk": OCPAEventType.AGENT_THINKING_CHUNK,
        
        # Permissions
        "permission/request": OCPAEventType.PERMISSION_REQUEST,
        "permission/response": OCPAEventType.PERMISSION_RESPONSE,
        
        # Errors
        "error": OCPAEventType.ERROR,
        
        # System
        "initialize": OCPAEventType.INITIALIZE,
        "shutdown": OCPAEventType.SHUTDOWN,
    }
    
    def __init__(self):
        self._session_id: Optional[str] = None
    
    @property
    def session_id(self) -> Optional[str]:
        return self._session_id
    
    def parse_message(self, data: Dict[str, Any]) -> Optional[OCPAEvent]:
        """Parse a JSON-RPC message into an OCPAEvent."""
        # Handle both request and notification formats
        method = data.get("method", "")
        params = data.get("params", {})
        
        # Extract session ID if present
        if "sessionId" in params:
            self._session_id = params["sessionId"]
        elif "session_id" in params:
            self._session_id = params["session_id"]
        
        event_type = self.METHOD_TO_EVENT.get(method)
        if event_type is None:
            # Unknown method - log but don't fail
            return None
        
        return self._create_event(event_type, method, params)
    
    def _create_event(self, event_type: OCPAEventType, method: str, params: Dict[str, Any]) -> OCPAEvent:
        """Create an OCPAEvent from parsed parameters."""
        event = OCPAEvent(
            event_type=event_type,
            timestamp=datetime.now(),
            session_id=self._session_id,
            raw_message={"method": method, "params": params},
        )
        
        # Populate fields based on event type
        if event_type in (OCPAEventType.SESSION_START, OCPAEventType.SESSION_UPDATE):
            event.session_title = params.get("title")
            event.session_directory = params.get("cwd") or params.get("directory")
        
        elif event_type in (OCPAEventType.MESSAGE_START, OCPAEventType.MESSAGE_CHUNK, 
                           OCPAEventType.MESSAGE_UPDATE, OCPAEventType.MESSAGE_END):
            event.message_id = params.get("messageId") or params.get("id")
            event.message_role = params.get("role")
            event.message_content = params.get("content") or params.get("text")
            event.message_done = event_type == OCPAEventType.MESSAGE_END
        
        elif event_type in (OCPAEventType.TOOL_CALL_START, OCPAEventType.TOOL_CALL_UPDATE,
                           OCPAEventType.TOOL_CALL_END, OCPAEventType.TOOL_CALL_ERROR):
            event.tool_call_id = params.get("callId") or params.get("id")
            event.tool_name = params.get("name") or params.get("tool")
            event.tool_arguments = params.get("arguments") or params.get("input")
            if event_type == OCPAEventType.TOOL_CALL_END:
                event.tool_result = params.get("result") or params.get("output")
                event.tool_done = True
            elif event_type == OCPAEventType.TOOL_CALL_ERROR:
                event.tool_error = params.get("error") or params.get("message")
        
        elif event_type in (OCPAEventType.AGENT_START, OCPAEventType.AGENT_END):
            event.agent_name = params.get("agent") or params.get("name")
        
        elif event_type in (OCPAEventType.AGENT_THINKING, OCPAEventType.AGENT_THINKING_CHUNK):
            event.thinking_content = params.get("content") or params.get("text")
        
        elif event_type == OCPAEventType.ERROR:
            event.error_code = params.get("code")
            event.error_message = params.get("message") or params.get("error")
        
        elif event_type == OCPAEventType.PERMISSION_REQUEST:
            event.permission_id = params.get("id")
            event.permission_tool = params.get("tool")
        
        elif event_type == OCPAEventType.PERMISSION_RESPONSE:
            event.permission_id = params.get("id")
            event.permission_result = params.get("result")
        
        return event


@dataclass
class OpenCodeConnectionInfo:
    """Connection information for OpenCode."""
    mode: str  # "acp" or "http"
    # ACP mode
    process: Optional[Any] = None
    # HTTP mode
    base_url: str = "http://127.0.0.1:4096"
    api_key: Optional[str] = None