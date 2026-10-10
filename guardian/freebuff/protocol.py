"""
FreeBuff Protocol Definitions.

Defines the HTTP API event types and parsing for FreeBuff (Codebuff) integration.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class FreeBuffEventType(Enum):
    """Event types from FreeBuff HTTP API."""
    # Session lifecycle
    SESSION_CREATE = "session_create"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_STATUS = "session_status"
    
    # Agent run lifecycle
    RUN_START = "run_start"
    RUN_END = "run_end"
    RUN_STATUS = "run_status"
    
    # Message events
    MESSAGE_START = "message_start"
    MESSAGE_CHUNK = "message_chunk"
    MESSAGE_END = "message_end"
    
    # Tool events
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_UPDATE = "tool_call_update"
    TOOL_CALL_END = "tool_call_end"
    TOOL_CALL_ERROR = "tool_call_error"
    
    # Error events
    ERROR = "error"
    
    # System events
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


@dataclass
class FreeBuffEvent:
    """Parsed event from FreeBuff HTTP API / WebSocket."""
    event_type: FreeBuffEventType
    timestamp: datetime
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None
    
    # Session fields
    session_status: Optional[str] = None
    
    # Run fields
    run_status: Optional[str] = None
    run_model: Optional[str] = None
    
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
    
    # Error fields
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class FreeBuffProtocolParser:
    """Parses events from FreeBuff HTTP API / WebSocket."""
    
    def parse_sse_event(self, data: str, session_id: str = None, run_id: str = None) -> Optional[FreeBuffEvent]:
        """Parse a Server-Sent Event from FreeBuff."""
        lines = data.strip().split('\n')
        event_type = None
        event_data = {}
        
        for line in lines:
            if line.startswith('event:'):
                event_type = line[6:].strip()
            elif line.startswith('data:'):
                try:
                    event_data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    event_data = {"raw": line[5:].strip()}
        
        if not event_type:
            return None
        
        return self._create_event(event_type, event_data, session_id, run_id)
    
    def parse_websocket_message(self, data: Dict[str, Any], session_id: str = None, run_id: str = None) -> Optional[FreeBuffEvent]:
        """Parse a WebSocket message from FreeBuff."""
        # FreeBuff WebSocket messages have a type field
        event_type = data.get("type") or data.get("event")
        if not event_type:
            return None
        
        return self._create_event(event_type, data, session_id, run_id)
    
    def _create_event(self, event_type: str, data: Dict[str, Any], session_id: str = None, run_id: str = None) -> Optional[FreeBuffEvent]:
        """Create a FreeBuffEvent from parsed data."""
        # Map event type string to enum
        type_map = {
            "session.created": FreeBuffEventType.SESSION_CREATE,
            "session.started": FreeBuffEventType.SESSION_START,
            "session.ended": FreeBuffEventType.SESSION_END,
            "session.status": FreeBuffEventType.SESSION_STATUS,
            "run.started": FreeBuffEventType.RUN_START,
            "run.ended": FreeBuffEventType.RUN_END,
            "run.status": FreeBuffEventType.RUN_STATUS,
            "message.start": FreeBuffEventType.MESSAGE_START,
            "message.chunk": FreeBuffEventType.MESSAGE_CHUNK,
            "message.end": FreeBuffEventType.MESSAGE_END,
            "tool.call.start": FreeBuffEventType.TOOL_CALL_START,
            "tool.call.update": FreeBuffEventType.TOOL_CALL_UPDATE,
            "tool.call.end": FreeBuffEventType.TOOL_CALL_END,
            "tool.call.error": FreeBuffEventType.TOOL_CALL_ERROR,
            "error": FreeBuffEventType.ERROR,
            "connected": FreeBuffEventType.CONNECTED,
            "disconnected": FreeBuffEventType.DISCONNECTED,
        }
        
        fb_type = type_map.get(event_type)
        if fb_type is None:
            return None
        
        event = FreeBuffEvent(
            event_type=fb_type,
            timestamp=datetime.now(),
            session_id=session_id,
            run_id=run_id,
            raw_data=data,
        )
        
        # Populate fields based on event type
        if fb_type in (FreeBuffEventType.SESSION_CREATE, FreeBuffEventType.SESSION_START,
                       FreeBuffEventType.SESSION_END, FreeBuffEventType.SESSION_STATUS):
            event.session_status = data.get("status")
        
        elif fb_type in (FreeBuffEventType.RUN_START, FreeBuffEventType.RUN_END, FreeBuffEventType.RUN_STATUS):
            event.run_status = data.get("status")
            event.run_model = data.get("model")
        
        elif fb_type in (FreeBuffEventType.MESSAGE_START, FreeBuffEventType.MESSAGE_CHUNK, FreeBuffEventType.MESSAGE_END):
            event.message_id = data.get("message_id") or data.get("id")
            event.message_role = data.get("role")
            event.message_content = data.get("content") or data.get("text")
            event.message_done = fb_type == FreeBuffEventType.MESSAGE_END
        
        elif fb_type in (FreeBuffEventType.TOOL_CALL_START, FreeBuffEventType.TOOL_CALL_UPDATE,
                         FreeBuffEventType.TOOL_CALL_END, FreeBuffEventType.TOOL_CALL_ERROR):
            event.tool_call_id = data.get("call_id") or data.get("id")
            event.tool_name = data.get("name") or data.get("tool")
            event.tool_arguments = data.get("arguments") or data.get("input")
            if fb_type == FreeBuffEventType.TOOL_CALL_END:
                event.tool_result = data.get("result") or data.get("output")
                event.tool_done = True
            elif fb_type == FreeBuffEventType.TOOL_CALL_ERROR:
                event.tool_error = data.get("error") or data.get("message")
        
        elif fb_type == FreeBuffEventType.ERROR:
            event.error_code = data.get("code")
            event.error_message = data.get("message") or data.get("error")
        
        return event


import json


@dataclass
class FreeBuffConnectionInfo:
    """Connection information for FreeBuff."""
    base_url: str = "https://codebuff.com"
    api_token: str = ""
    websocket_url: str = "wss://codebuff.com/ws"
    cost_mode: str = "free"  # "free" or "paid"