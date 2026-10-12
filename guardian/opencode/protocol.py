"""
OpenCode Protocol Definitions.

Defines the ACP event types and parsing for OpenCode integration.
Based on the official OpenCode ACP specification (protocol version 1).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import json


class OCPAEventType(Enum):
    """ACP event types from OpenCode (protocol version 1)."""
    # Session lifecycle
    SESSION_START = "session_start"       # Our internal tracking
    SESSION_END = "session_end"           # Our internal tracking
    SESSION_UPDATE = "session_update"     # Generic session update notification
    
    # ACP Protocol Methods (Client -> Agent)
    INITIALIZE = "initialize"
    NEW_SESSION = "newSession"
    LOAD_SESSION = "loadSession"
    LIST_SESSIONS = "listSessions"
    FORK_SESSION = "forkSession"
    RESUME_SESSION = "resumeSession"
    CLOSE_SESSION = "closeSession"
    DELETE_SESSION = "deleteSession"
    PROMPT = "prompt"
    CANCEL = "cancel"
    SET_SESSION_MODE = "setSessionMode"
    SET_SESSION_MODEL = "setSessionModel"
    SET_SESSION_CONFIG_OPTION = "setSessionConfigOption"
    AUTHENTICATE = "authenticate"
    
    # ACP Protocol Notifications (Agent -> Client) 
    SESSION_NOTIFICATION = "sessionNotification"  # Contains sessionUpdate
    # sessionUpdate types:
    AGENT_MESSAGE_CHUNK = "agent_message_chunk"
    AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
    USER_MESSAGE_CHUNK = "user_message_chunk"
    TOOL_CALL = "tool_call"
    TOOL_CALL_UPDATE = "tool_call_update"
    PLAN = "plan"
    USAGE_UPDATE = "usage_update"
    AVAILABLE_COMMANDS_UPDATE = "available_commands_update"
    
    # Error events
    ERROR = "error"


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
    tool_kind: Optional[str] = None  # execute, edit, search, read, fetch, other
    tool_arguments: Optional[Dict[str, Any]] = None
    tool_result: Optional[Any] = None
    tool_error: Optional[str] = None
    tool_done: bool = False
    tool_state: Optional[str] = None  # pending, in_progress, completed, failed
    
    # Agent fields
    agent_name: Optional[str] = None
    thinking_content: Optional[str] = None
    
    # Error fields
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    
    # Plan/todo fields
    plan_entries: Optional[List[Dict]] = None
    
    # Usage fields
    usage_input_tokens: Optional[int] = None
    usage_output_tokens: Optional[int] = None
    usage_total_cost_usd: Optional[float] = None


@dataclass
class OpenCodeConnectionInfo:
    """Connection information for OpenCode."""
    mode: str = "acp"  # "acp" (stdio) or "http"
    opencode_path: str = "opencode"
    base_url: str = "http://127.0.0.1:4096"
    api_key: Optional[str] = None
    
    # ACP stdio specific
    stdio_process: Optional[Any] = None


class OCPAProtocolParser:
    """Parses ACP JSON-RPC messages from OpenCode."""
    
    def __init__(self):
        self._pending_requests: Dict[int, Dict] = {}
        self._request_id = 0
    
    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id
    
    def create_initialize_request(self, client_name: str = "agent-guardian", client_version: str = "0.1.0") -> Dict:
        """Create ACP initialize request."""
        return {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "elicitation": {"form": {}},
                    "_meta": {
                        "terminal-auth": True
                    }
                },
                "clientInfo": {
                    "name": client_name,
                    "version": client_version
                }
            }
        }
    
    def create_new_session_request(self, cwd: str, mcp_servers: List = None) -> Dict:
        """Create ACP newSession request."""
        return {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "session/new",
            "params": {
                "cwd": cwd,
                "mcpServers": mcp_servers or []
            }
        }
    
    def create_load_session_request(self, session_id: str, cwd: str, mcp_servers: List = None) -> Dict:
        """Create ACP loadSession request."""
        return {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "loadSession",
            "params": {
                "sessionId": session_id,
                "cwd": cwd,
                "mcpServers": mcp_servers or []
            }
        }
    
    def create_prompt_request(self, session_id: str, prompt: str) -> Dict:
        """Create ACP prompt request."""
        return {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [
                    {
                        "type": "text",
                        "text": prompt
                    }
                ],
                "cwd": "C:/Projects/AgentGuardian"
            }
        }
    
    def create_cancel_request(self, session_id: str) -> Dict:
        """Create ACP cancel notification (notification, no id)."""
        return {
            "jsonrpc": "2.0",
            "method": "cancel",
            "params": {
                "sessionId": session_id
            }
        }
    
    def create_fork_session_request(self, session_id: str, cwd: str) -> Dict:
        """Create ACP forkSession request (unstable)."""
        return {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "forkSession",
            "params": {
                "sessionId": session_id,
                "cwd": cwd
            }
        }
    
    def create_resume_session_request(self, session_id: str, cwd: str) -> Dict:
        """Create ACP resumeSession request."""
        return {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "resumeSession",
            "params": {
                "sessionId": session_id,
                "cwd": cwd
            }
        }
    
    def create_close_session_request(self, session_id: str) -> Dict:
        """Create ACP closeSession request."""
        return {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "closeSession",
            "params": {
                "sessionId": session_id
            }
        }
    
    def parse_message(self, data) -> Optional[OCPAEvent]:
        """Parse a JSON-RPC message from OpenCode."""
        # Handle both string and already-parsed dict
        if isinstance(data, dict):
            msg = data
        else:
            try:
                msg = json.loads(data.strip())
            except json.JSONDecodeError:
                return None
        
        # Handle response
        if "id" in msg and ("result" in msg or "error" in msg):
            return self._parse_response(msg)
        
        # Handle notification
        if "method" in msg and "id" not in msg:
            return self._parse_notification(msg)
        
        # Handle request (shouldn't happen in observer mode)
        if "method" in msg and "id" in msg:
            return self._parse_request(msg)
        
        return None
    
    def _parse_response(self, msg: Dict) -> Optional[OCPAEvent]:
        """Parse JSON-RPC response."""
        request_id = msg.get("id")
        pending = self._pending_requests.pop(request_id, None)
        
        if "error" in msg:
            error = msg["error"]
            return OCPAEvent(
                event_type=OCPAEventType.ERROR,
                timestamp=datetime.now(),
                session_id=pending.get("session_id") if pending else None,
                raw_message=msg,
                error_code=str(error.get("code", "")),
                error_message=error.get("message", "Unknown error")
            )
        
        result = msg.get("result", {})
        
        # Initialize response
        if pending and pending.get("method") == "initialize":
            return OCPAEvent(
                event_type=OCPAEventType.INITIALIZE,
                timestamp=datetime.now(),
                session_id=None,
                raw_message=msg,
                message_content=f"Initialized: {result.get('agentInfo', {}).get('name', 'OpenCode')} v{result.get('agentInfo', {}).get('version', 'unknown')}"
            )
        
        # New session response
        if pending and pending.get("method") == "session/new":
            session_id = result.get("sessionId", "")
            return OCPAEvent(
                event_type=OCPAEventType.SESSION_START,
                timestamp=datetime.now(),
                session_id=session_id,
                raw_message=msg,
                session_title=result.get("sessionId", ""),
                session_directory=pending.get("cwd", "")
            )
        
        # Load session response
        if pending and pending.get("method") == "loadSession":
            session_id = pending.get("params", {}).get("sessionId", "")
            return OCPAEvent(
                event_type=OCPAEventType.SESSION_START,
                timestamp=datetime.now(),
                session_id=session_id,
                raw_message=msg
            )
        
        # Prompt response (completion)
        if pending and pending.get("method") == "prompt":
            return OCPAEvent(
                event_type=OCPAEventType.SESSION_END,
                timestamp=datetime.now(),
                session_id=pending.get("params", {}).get("sessionId", ""),
                raw_message=msg,
                message_content=f"Usage: {result.get('usage', {})}"
            )
        
        return None
    
    def _parse_notification(self, msg: Dict) -> Optional[OCPAEvent]:
        """Parse JSON-RPC notification from OpenCode."""
        method = msg.get("method", "")
        params = msg.get("params", {})
        
        # Session notification with sessionUpdate (OpenCode uses "session/update")
        if method in ("sessionNotification", "session/update"):
            update = params.get("update", {})
            session_id = params.get("sessionId", "")
            
            # OpenCode format: update.sessionUpdate indicates the type
            session_update_type = update.get("sessionUpdate", "")
            
            # Map sessionUpdate types to update fields
            if session_update_type == "agent_message_chunk":
                chunk = update.get("agentMessageChunk") or update.get("agent_message_chunk")
                if chunk:
                    return OCPAEvent(
                        event_type=OCPAEventType.AGENT_MESSAGE_CHUNK,
                        timestamp=datetime.now(),
                        session_id=session_id,
                        raw_message=msg,
                        message_id=chunk.get("messageId"),
                        message_role="assistant",
                        message_content=chunk.get("content", ""),
                        message_done=chunk.get("done", False)
                    )
            
            if session_update_type == "agent_thought_chunk":
                chunk = update.get("agentThoughtChunk") or update.get("agent_thought_chunk")
                if chunk:
                    return OCPAEvent(
                        event_type=OCPAEventType.AGENT_THOUGHT_CHUNK,
                        timestamp=datetime.now(),
                        session_id=session_id,
                        raw_message=msg,
                        thinking_content=chunk.get("content", "")
                    )
            
            if session_update_type == "user_message_chunk":
                chunk = update.get("userMessageChunk") or update.get("user_message_chunk")
                if chunk:
                    return OCPAEvent(
                        event_type=OCPAEventType.USER_MESSAGE_CHUNK,
                        timestamp=datetime.now(),
                        session_id=session_id,
                        raw_message=msg,
                        message_id=chunk.get("messageId"),
                        message_role="user",
                        message_content=chunk.get("content", ""),
                        message_done=chunk.get("done", False)
                    )
            
            if session_update_type == "tool_call":
                tool = update.get("toolCall") or update.get("tool_call")
                if tool:
                    return OCPAEvent(
                        event_type=OCPAEventType.TOOL_CALL,
                        timestamp=datetime.now(),
                        session_id=session_id,
                        raw_message=msg,
                        tool_call_id=tool.get("toolCallId"),
                        tool_name=tool.get("toolName"),
                        tool_kind=tool.get("kind"),
                        tool_arguments=tool.get("arguments", {}),
                        tool_state="pending"
                    )
            
            if session_update_type == "tool_call_update":
                tool_update = update.get("toolCallUpdate") or update.get("tool_call_update")
                if tool_update:
                    return OCPAEvent(
                        event_type=OCPAEventType.TOOL_CALL_UPDATE,
                        timestamp=datetime.now(),
                        session_id=session_id,
                        raw_message=msg,
                        tool_call_id=tool_update.get("toolCallId"),
                        tool_state=tool_update.get("state"),
                        tool_result=tool_update.get("result"),
                        tool_error=tool_update.get("error"),
                        tool_done=tool_update.get("state") in ("completed", "failed")
                    )
            
            if session_update_type == "plan":
                plan = update.get("plan")
                if plan:
                    return OCPAEvent(
                        event_type=OCPAEventType.PLAN,
                        timestamp=datetime.now(),
                        session_id=session_id,
                        raw_message=msg,
                        plan_entries=plan
                    )
            
            if session_update_type == "usage_update":
                usage = update.get("usageUpdate") or update.get("usage_update")
                if usage:
                    return OCPAEvent(
                        event_type=OCPAEventType.USAGE_UPDATE,
                        timestamp=datetime.now(),
                        session_id=session_id,
                        raw_message=msg,
                        usage_input_tokens=usage.get("inputTokens"),
                        usage_output_tokens=usage.get("outputTokens"),
                        usage_total_cost_usd=usage.get("totalCostUsd")
                    )
            
            if session_update_type == "available_commands_update":
                return OCPAEvent(
                    event_type=OCPAEventType.AVAILABLE_COMMANDS_UPDATE,
                    timestamp=datetime.now(),
                    session_id=session_id,
                    raw_message=msg
                )
            
            # Fallback: check for legacy field names (without sessionUpdate)
            if "agentMessageChunk" in update:
                chunk = update["agentMessageChunk"]
                return OCPAEvent(
                    event_type=OCPAEventType.AGENT_MESSAGE_CHUNK,
                    timestamp=datetime.now(),
                    session_id=session_id,
                    raw_message=msg,
                    message_id=chunk.get("messageId"),
                    message_role="assistant",
                    message_content=chunk.get("content", ""),
                    message_done=chunk.get("done", False)
                )
            
            if "agentThoughtChunk" in update:
                chunk = update["agentThoughtChunk"]
                return OCPAEvent(
                    event_type=OCPAEventType.AGENT_THOUGHT_CHUNK,
                    timestamp=datetime.now(),
                    session_id=session_id,
                    raw_message=msg,
                    thinking_content=chunk.get("content", "")
                )
            
            if "userMessageChunk" in update:
                chunk = update["userMessageChunk"]
                return OCPAEvent(
                    event_type=OCPAEventType.USER_MESSAGE_CHUNK,
                    timestamp=datetime.now(),
                    session_id=session_id,
                    raw_message=msg,
                    message_id=chunk.get("messageId"),
                    message_role="user",
                    message_content=chunk.get("content", ""),
                    message_done=chunk.get("done", False)
                )
            
            if "toolCall" in update:
                tool = update["toolCall"]
                return OCPAEvent(
                    event_type=OCPAEventType.TOOL_CALL,
                    timestamp=datetime.now(),
                    session_id=session_id,
                    raw_message=msg,
                    tool_call_id=tool.get("toolCallId"),
                    tool_name=tool.get("toolName"),
                    tool_kind=tool.get("kind"),
                    tool_arguments=tool.get("arguments", {}),
                    tool_state="pending"
                )
            
            if "toolCallUpdate" in update:
                tool_update = update["toolCallUpdate"]
                return OCPAEvent(
                    event_type=OCPAEventType.TOOL_CALL_UPDATE,
                    timestamp=datetime.now(),
                    session_id=session_id,
                    raw_message=msg,
                    tool_call_id=tool_update.get("toolCallId"),
                    tool_state=tool_update.get("state"),
                    tool_result=tool_update.get("result"),
                    tool_error=tool_update.get("error"),
                    tool_done=tool_update.get("state") in ("completed", "failed")
                )
            
            if "plan" in update:
                return OCPAEvent(
                    event_type=OCPAEventType.PLAN,
                    timestamp=datetime.now(),
                    session_id=session_id,
                    raw_message=msg,
                    plan_entries=update["plan"]
                )
            
            if "usageUpdate" in update:
                usage = update["usageUpdate"]
                return OCPAEvent(
                    event_type=OCPAEventType.USAGE_UPDATE,
                    timestamp=datetime.now(),
                    session_id=session_id,
                    raw_message=msg,
                    usage_input_tokens=usage.get("inputTokens"),
                    usage_output_tokens=usage.get("outputTokens"),
                    usage_total_cost_usd=usage.get("totalCostUsd")
                )
            
            if "availableCommandsUpdate" in update:
                return OCPAEvent(
                    event_type=OCPAEventType.AVAILABLE_COMMANDS_UPDATE,
                    timestamp=datetime.now(),
                    session_id=session_id,
                    raw_message=msg
                )
        
        return None
    
    def _parse_request(self, msg: Dict) -> Optional[OCPAEvent]:
        """Parse incoming request (for completeness)."""
        return None
    
    def track_request(self, request: Dict):
        """Track a sent request for response correlation."""
        request_id = request.get("id")
        if request_id:
            self._pending_requests[request_id] = {
                "method": request.get("method"),
                "params": request.get("params", {}),
                "session_id": request.get("params", {}).get("sessionId") if "params" in request else None
            }