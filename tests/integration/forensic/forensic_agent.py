"""Forensic ACP Test Agent — Minimal deterministic agent for Guardian testing."""

import asyncio
import uuid
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import acp
from acp.schema import (
    InitializeRequest, InitializeResponse, NewSessionRequest, NewSessionResponse,
    LoadSessionRequest, LoadSessionResponse, ListSessionsRequest, ListSessionsResponse,
    PromptRequest, PromptResponse, AuthenticateRequest, AuthenticateResponse,
    SetSessionModeRequest, SetSessionModeResponse, SetSessionModelRequest, SetSessionModelResponse,
    SetSessionConfigOptionBooleanRequest, SetSessionConfigOptionSelectRequest,
    SetSessionConfigOptionResponse,
    CloseSessionRequest, CloseSessionResponse, ForkSessionRequest, ForkSessionResponse,
    ResumeSessionRequest, ResumeSessionResponse, CancelNotification,
    Implementation, ClientCapabilities, AgentCapabilities, SessionCapabilities,
    SessionModeState, SessionModelState, SessionListCapabilities, SessionForkCapabilities,
    SessionResumeCapabilities, SessionCloseCapabilities,
    PromptCapabilities, McpCapabilities, HttpMcpServer, McpServerHttp, McpServerSse, McpServerStdio,
    TextContentBlock,
    ToolCallStart, ToolCallProgress, ToolCallStatus, ToolKind, SessionNotification,
    SessionUpdate, ToolCall, ToolCallUpdate, ContentToolCallContent,
    AgentMessageChunk, AgentThoughtChunk, UserMessageChunk, AgentPlanUpdate,
    PlanEntry, PlanEntryPriority, PlanEntryStatus, AvailableCommandsUpdate,
    CurrentModeUpdate, ConfigOptionUpdate, SessionInfoUpdate, UsageUpdate,
    ModelInfo, SessionId, SessionMode, SessionConfigOption,
)


class ToolState(Enum):
    """Internal ground-truth tool state."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ToolExecution:
    """Ground-truth record of a tool execution."""
    session_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    state: ToolState = ToolState.PENDING
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    failed_at: Optional[float] = None
    cancelled_at: Optional[float] = None
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    _task: Optional[asyncio.Task] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "state": self.state.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failed_at": self.failed_at,
            "cancelled_at": self.cancelled_at,
            "duration_seconds": self.duration_seconds,
            "error_message": self.error_message,
        }


class ForensicAgent(acp.Agent):
    """Minimal ACP agent for forensic testing of Guardian."""
    
    def __init__(self, tool_duration: float = 2.0, should_fail: bool = False, fail_at_disconnect: bool = False):
        self.tool_duration = tool_duration
        self.should_fail = should_fail
        self.fail_at_disconnect = fail_at_disconnect
        
        # Ground-truth state (internal, not exposed via ACP)
        self._executions: Dict[str, ToolExecution] = {}
        self._current_session_id: Optional[str] = None
        self._current_turn_id: Optional[str] = None
        self._active_tool_execution: Optional[ToolExecution] = None
        self._disconnected = False
        
        # Session tracking
        self._sessions: Dict[str, Dict[str, Any]] = {}
        
        # Agent info
        self._agent_name = "forensic-test-agent"
        self._agent_version = "1.0.0"
        
        # Tool definition
        self._tool_name = "forensic_tool"
        self._tool_kind = ToolKind.EXECUTE
        
        # Connection for sending updates (AgentSideConnection)
        self._agent_conn: Optional[Any] = None
        
    def on_connect(self, conn: acp.Client) -> None:
        """Store AgentSideConnection for sending session updates."""
        # The conn passed to on_connect is an AgentSideConnection
        self._agent_conn = conn
        print(f"[ForensicAgent] ACP client connected")
    
    def get_ground_truth(self) -> List[Dict[str, Any]]:
        """Return all tool executions for verification."""
        return [e.to_dict() for e in self._executions.values()]
    
    def get_execution(self, tool_call_id: str) -> Optional[ToolExecution]:
        """Get a specific execution by tool_call_id."""
        return self._executions.get(tool_call_id)
    
    def is_tool_running(self) -> bool:
        """Check if a tool is currently running."""
        return self._active_tool_execution is not None and self._active_tool_execution.state == ToolState.RUNNING
    
    def disconnect_simulation(self):
        """Simulate ACP disconnect for testing."""
        self._disconnected = True
        print(f"[ForensicAgent] Simulated disconnect")
        if self._active_tool_execution and self._active_tool_execution.state == ToolState.RUNNING:
            if self.fail_at_disconnect:
                self._active_tool_execution.state = ToolState.FAILED
                self._active_tool_execution.failed_at = time.time()
                self._active_tool_execution.error_message = "Disconnect caused failure"
                self._active_tool_execution = None
    
    # ACP Agent methods
    
    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Optional[ClientCapabilities] = None,
        client_info: Optional[Implementation] = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        print(f"[ForensicAgent] Initialize: protocol_version={protocol_version}, client={client_info.name if client_info else 'unknown'}")
        
        return InitializeResponse(
            protocol_version=1,
            agent_info=Implementation(name=self._agent_name, version=self._agent_version),
            agent_capabilities=AgentCapabilities(
                load_session=False,
                prompt_capabilities=PromptCapabilities(image=False, audio=False, embedded_context=False),
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                    fork=SessionForkCapabilities(),
                    resume=SessionResumeCapabilities(),
                    close=SessionCloseCapabilities(),
                ),
                mcp_capabilities=McpCapabilities(http=False, sse=False),
            ),
            auth_methods=[],
        )
    
    async def authenticate(self, method_id: str, **kwargs: Any) -> Optional[AuthenticateResponse]:
        print(f"[ForensicAgent] Authenticate: method={method_id}")
        # Forensic agent accepts any auth method (test-only)
        return AuthenticateResponse()
    
    async def new_session(
        self,
        cwd: str,
        mcp_servers: Optional[List[McpServer]] = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        session_id = f"forensic_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        print(f"[ForensicAgent] New session: {session_id} (cwd={cwd})")
        
        self._current_session_id = session_id
        self._sessions[session_id] = {
            "cwd": cwd,
            "created_at": time.time(),
            "mcp_servers": mcp_servers or [],
            "history": [],
        }
        
        return NewSessionResponse(
            session_id=session_id,
            models=SessionModelState(
                available_models=[ModelInfo(model_id="forensic-model", name="Forensic Model")],
                current_model_id="forensic-model",
            ),
            modes=SessionModeState(
                current_mode_id="auto",
                available_modes=[
                    SessionMode(id="auto", name="Auto", description="Auto mode"),
                ],
            ),
        )
    
    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: Optional[List[McpServer]] = None,
        **kwargs: Any,
    ) -> Optional[LoadSessionResponse]:
        print(f"[ForensicAgent] Load session: {session_id}")
        if session_id not in self._sessions:
            return None
        self._current_session_id = session_id
        return LoadSessionResponse(
            session_id=session_id,
            models=SessionModelState(
                available_models=[ModelInfo(model_id="forensic-model", name="Forensic Model")],
                current_model_id="forensic-model",
            ),
            modes=SessionModeState(
                current_mode_id="auto",
                available_modes=[
                    SessionMode(id="auto", name="Auto", description="Auto mode"),
                ],
            ),
        )
    
    async def list_sessions(
        self,
        cursor: Optional[str] = None,
        cwd: Optional[str] = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        sessions = [
            {"session_id": sid, "cwd": info["cwd"], "title": f"Forensic session {sid}"}
            for sid, info in self._sessions.items()
        ]
        return ListSessionsResponse(sessions=sessions, next_cursor=None)
    
    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs: Any) -> Optional[SetSessionModeResponse]:
        print(f"[ForensicAgent] Set mode: {mode_id} for {session_id}")
        return SetSessionModeResponse()
    
    async def set_session_model(self, model_id: str, session_id: str, **kwargs: Any) -> Optional[SetSessionModelResponse]:
        print(f"[ForensicAgent] Set model: {model_id} for {session_id}")
        return SetSessionModelResponse()
    
    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> Optional[SetSessionConfigOptionResponse]:
        print(f"[ForensicAgent] Set config: {config_id}={value} for {session_id}")
        return SetSessionConfigOptionResponse(config_options=[])
    
    async def close_session(self, session_id: str, **kwargs: Any) -> Optional[CloseSessionResponse]:
        print(f"[ForensicAgent] Close session: {session_id}")
        if session_id in self._sessions:
            del self._sessions[session_id]
        return CloseSessionResponse()
    
    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: Optional[List[McpServer]] = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        print(f"[ForensicAgent] Fork session: {session_id}")
        new_session_id = f"{session_id}_fork_{int(time.time())}"
        return ForkSessionResponse(
            session_id=new_session_id,
            models=SessionModelState(
                available_models=[ModelInfo(model_id="forensic-model", name="Forensic Model")],
                current_model_id="forensic-model",
            ),
            modes=SessionModeState(
                current_mode_id="auto",
                available_modes=[SessionMode(id="auto", name="Auto", description="Auto mode")],
            ),
        )
    
    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: Optional[List[McpServer]] = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        print(f"[ForensicAgent] Resume session: {session_id}")
        if session_id not in self._sessions:
            # Create new
            await self.new_session(cwd, mcp_servers)
        self._current_session_id = session_id
        return ResumeSessionResponse(
            session_id=session_id,
            models=SessionModelState(
                available_models=[ModelInfo(model_id="forensic-model", name="Forensic Model")],
                current_model_id="forensic-model",
            ),
            modes=SessionModeState(
                current_mode_id="auto",
                available_modes=[SessionMode(id="auto", name="Auto", description="Auto mode")],
            ),
        )
    
    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        print(f"[ForensicAgent] Cancel: {session_id}")
        if self._active_tool_execution and self._active_tool_execution.state == ToolState.RUNNING:
            self._active_tool_execution.state = ToolState.CANCELLED
            self._active_tool_execution.cancelled_at = time.time()
            self._send_tool_cancelled()
            self._active_tool_execution = None
    
    async def prompt(
        self,
        prompt: List[TextContentBlock | Any],
        session_id: str,
        message_id: Optional[str] = None,
        **kwargs: Any,
    ) -> PromptResponse:
        """Process a prompt and deterministically trigger the forensic tool."""
        print(f"[ForensicAgent] Prompt on session {session_id}: {prompt}")
        
        if self._disconnected:
            print(f"[ForensicAgent] Rejecting prompt - disconnected")
            return PromptResponse(stop_reason="refusal")
        
        self._current_session_id = session_id
        self._current_turn_id = f"turn_{uuid.uuid4().hex[:8]}"
        
        # Extract text from prompt
        prompt_text = ""
        for block in prompt:
            if hasattr(block, 'text'):
                prompt_text += block.text
            elif isinstance(block, dict) and block.get('type') == 'text':
                prompt_text += block.get('text', '')
        
        # Always trigger the forensic tool (deterministic behavior)
        tool_call_id = f"tc_{uuid.uuid4().hex[:12]}"
        
        execution = ToolExecution(
            session_id=session_id,
            turn_id=self._current_turn_id,
            tool_call_id=tool_call_id,
            tool_name=self._tool_name,
        )
        self._executions[tool_call_id] = execution
        self._active_tool_execution = execution
        
        # Send TOOL_STARTED
        self._send_tool_started(execution)
        
        # Run the tool in background
        execution._task = asyncio.create_task(self._run_tool(execution))
        
        return PromptResponse(stop_reason="end_turn")
    
    async def _run_tool(self, execution: ToolExecution) -> None:
        """Execute the forensic tool with controlled duration."""
        execution.state = ToolState.RUNNING
        execution.started_at = time.time()
        print(f"[ForensicAgent] Tool STARTED: {execution.tool_call_id}")
        
        try:
            # Wait for the configured duration
            await asyncio.sleep(self.tool_duration)
            
            if self._disconnected and self.fail_at_disconnect:
                # Tool fails because of disconnect
                execution.state = ToolState.FAILED
                execution.failed_at = time.time()
                execution.error_message = "Failed due to disconnect"
                self._send_tool_failed(execution)
            else:
                # Tool completes successfully
                execution.state = ToolState.COMPLETED
                execution.completed_at = time.time()
                execution.duration_seconds = execution.completed_at - execution.started_at
                print(f"[ForensicAgent] Tool COMPLETED: {execution.tool_call_id} (duration={execution.duration_seconds:.2f}s)")
                self._send_tool_completed(execution)
        
        except asyncio.CancelledError:
            execution.state = ToolState.CANCELLED
            execution.cancelled_at = time.time()
            execution.duration_seconds = execution.cancelled_at - execution.started_at
            print(f"[ForensicAgent] Tool CANCELLED: {execution.tool_call_id}")
            self._send_tool_cancelled()
        except Exception as e:
            execution.state = ToolState.FAILED
            execution.failed_at = time.time()
            execution.error_message = str(e)
            print(f"[ForensicAgent] Tool FAILED: {execution.tool_call_id} - {e}")
            self._send_tool_failed(execution)
        finally:
            self._active_tool_execution = None

    def _send_tool_started(self, execution: ToolExecution) -> None:
                """Send TOOL_STARTED event via ACP."""
                if not self._agent_conn:
                    print(f"[ForensicAgent] No connection to send TOOL_STARTED")
                    return
        
                tool_call = ToolCallStart(
                    session_update="tool_call",
                    tool_call_id=execution.tool_call_id,
                    title=f"{self._tool_name}: duration={self.tool_duration}s",
                    kind=self._tool_kind,
                    status=ToolCallStatus.IN_PROGRESS,
                    content=[ContentToolCallContent(
                        type="text",
                        text=f"Running forensic tool for {self.tool_duration} seconds"
                    )],
                    locations=[],
                )
        
                notification = SessionNotification(
                    session_id=execution.session_id,
                    update=tool_call,
                )
        
                asyncio.create_task(self._send_notification(notification))
    
            def _send_tool_completed(self, execution: ToolExecution) -> None:
                """Send TOOL_COMPLETED event via ACP."""
                if not self._agent_conn:
                    return
        
                tool_call = ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id=execution.tool_call_id,
                    kind=self._tool_kind,
                    status=ToolCallStatus.COMPLETED,
                    content=[ContentToolCallContent(
                        type="text",
                        text=f"Tool completed successfully in {execution.duration_seconds:.2f}s"
                    )],
                )
        
                notification = SessionNotification(
                    session_id=execution.session_id,
                    update=tool_call,
                )
        
                asyncio.create_task(self._send_notification(notification))
    
            def _send_tool_failed(self, execution: ToolExecution) -> None:
                """Send TOOL_FAILED event via ACP."""
                if not self._agent_conn:
                    return
        
                tool_call = ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id=execution.tool_call_id,
                    kind=self._tool_kind,
                    status=ToolCallStatus.FAILED,
                    content=[ContentToolCallContent(
                        type="text",
                        text=f"Tool failed: {execution.error_message}"
                    )],
                )
        
                notification = SessionNotification(
                    session_id=execution.session_id,
                    update=tool_call,
                )
        
                asyncio.create_task(self._send_notification(notification))
    
            def _send_tool_cancelled(self) -> None:
                """Send TOOL_CANCELLED event via ACP."""
                if not self._agent_conn or not self._active_tool_execution:
                    return
        
                execution = self._active_tool_execution
                tool_call = ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id=execution.tool_call_id,
                    kind=self._tool_kind,
                    status=ToolCallStatus.FAILED,  # ACP doesn't have cancelled status
                    content=[ContentToolCallContent(
                        type="text",
                        text="Tool was cancelled"
                    )],
                )
        
                notification = SessionNotification(
                    session_id=execution.session_id,
                    update=tool_call,
                )
        
                asyncio.create_task(self._send_notification(notification))
    
            async def _send_notification(self, notification: SessionNotification) -> None:
                """Send a session update notification."""
                if self._agent_conn:
                    try:
                        await self._agent_conn.session_update(session_id=notification.session_id, update=notification.update)
                    except Exception as e:
                        print(f"[ForensicAgent] Failed to send notification: {e}")

            # Expose internal state for test harness
            async def _get_internal_state(self) -> Dict[str, Any]:
                return {
                    "sessions": self._sessions,
                    "executions": {k: v.to_dict() for k, v in self._executions.items()},
                    "current_session": self._current_session_id,
                    "current_turn": self._current_turn_id,
                    "disconnected": self._disconnected,
                }


        # Standalone runner
        async def run_forensic_agent(
            tool_duration: float = 2.0,
            should_fail: bool = False,
            fail_at_disconnect: bool = False,
            use_stdio: bool = True,
        ):
            """Run the forensic agent over stdio or TCP."""
            agent = ForensicAgent(
                tool_duration=tool_duration,
                should_fail=should_fail,
                fail_at_disconnect=fail_at_disconnect,
            )
    
            if use_stdio:
                await acp.run_agent(agent, use_unstable_protocol=True)
            else:
                # TCP mode - for future use
                pass


        if __name__ == "__main__":
            import sys
            tool_duration = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
            asyncio.run(run_forensic_agent(tool_duration=tool_duration))