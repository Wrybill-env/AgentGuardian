"""Forensic Test Harness — Controls the forensic agent and runs experiments."""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import acp
from acp.schema import (
    InitializeRequest, NewSessionRequest, PromptRequest,
    Implementation, ClientCapabilities, AgentCapabilities,
    SessionCapabilities, SessionListCapabilities, PromptCapabilities,
    McpCapabilities, SessionModeState, SessionModelState,
    SessionForkCapabilities, SessionResumeCapabilities,
    SessionCloseCapabilities, SessionDeleteCapabilities,
    ModelInfo, SessionId,
)


class DisconnectPoint(Enum):
    """When to inject disconnect during the experiment."""
    BEFORE_TOOL = "before_tool"
    DURING_TOOL = "during_tool"
    AFTER_COMPLETION = "after_completion"
    WHILE_RUNNING = "while_running"


@dataclass
class ExperimentResult:
    """Results of a single experiment."""
    name: str
    disconnect_point: DisconnectPoint
    tool_duration: float
    should_fail: bool
    fail_at_disconnect: bool
    
    # Ground truth (from forensic agent)
    actual_tool_state: str = ""
    tool_started_at: Optional[float] = None
    tool_completed_at: Optional[float] = None
    tool_failed_at: Optional[float] = None
    tool_cancelled_at: Optional[float] = None
    tool_duration_actual: float = 0.0
    tool_error: Optional[str] = None
    
    # ACP observed state
    acp_tool_started_received: bool = False
    acp_tool_completed_received: bool = False
    acp_tool_failed_received: bool = False
    acp_tool_cancelled_received: bool = False
    acp_disconnect_time: Optional[float] = None
    
    # Guardian observed state (to be filled by Guardian tests)
    guardian_partial_tool_names: List[str] = field(default_factory=list)
    guardian_generation_at_disconnect: int = 0
    guardian_safety_verdict: str = ""
    guardian_recovery_action: str = ""
    
    # Metadata
    session_id: str = ""
    turn_id: str = ""
    tool_call_id: str = ""
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


class ForensicTestHarness:
    """Harness for running controlled ACP experiments."""
    
    def __init__(self, agent_process=None):
        self.agent_process = agent_process
        self.agent_ws = None
        self.session_id = ""
        self.results: List[ExperimentResult] = []
        self._acp_messages: List[Dict] = []
        self._message_queue: asyncio.Queue = asyncio.Queue()
        
    async def connect_stdio(self, agent_cmd: List[str]):
        """Connect to agent via stdio using ACP."""
        print(f"[Harness] Starting agent: {' '.join(agent_cmd)}")
        
        # Use acp.spawn_agent_process for stdio transport
        self.agent_transport = await acp.spawn_agent_process(
            *agent_cmd,
            env=None,
            cwd=None,
        )
        self.agent_conn, self.agent_process = self.agent_transport
        
        # Initialize
        init_req = InitializeRequest(
            protocol_version=1,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="forensic-harness", version="1.0"),
        )
        init_resp = await self.agent_conn.send_request(init_req.model_dump())
        print(f"[Harness] Initialize: {init_resp}")
        
        # Create session
        session_req = NewSessionRequest(
            mcp_servers=[],
            cwd="C:/tmp/goose-test",
        )
        session_resp = await self.agent_conn.send_request(session_req.model_dump())
        self.session_id = session_resp.get("session_id") or session_resp.get("result", {}).get("session_id")
        print(f"[Harness] Session: {self.session_id}")
        
        # Start listening for notifications
        self._listen_task = asyncio.create_task(self._listen_for_notifications())
        
    async def _listen_for_notifications(self):
        """Listen for ACP session updates."""
        try:
            async for notification in self.agent_conn.notifications():
                self._acp_messages.append({
                    "timestamp": time.time(),
                    "data": notification,
                })
                await self._message_queue.put(notification)
        except Exception as e:
            print(f"[Harness] Notification listener error: {e}")
    
    async def run_experiment(
        self,
        name: str,
        disconnect_point: DisconnectPoint,
        tool_duration: float = 3.0,
        should_fail: bool = False,
        fail_at_disconnect: bool = False,
    ) -> ExperimentResult:
        """Run a single forensic experiment."""
        print(f"\n[Harness] ===== Experiment: {name} =====")
        print(f"[Harness] Disconnect point: {disconnect_point.value}")
        print(f"[Harness] Tool duration: {tool_duration}s")
        
        result = ExperimentResult(
            name=name,
            disconnect_point=disconnect_point,
            tool_duration=tool_duration,
            should_fail=should_fail,
            fail_at_disconnect=fail_at_disconnect,
        )
        
        # Send prompt that triggers the forensic tool
        prompt_req = PromptRequest(
            session_id=self.session_id,
            prompt=[{"type": "text", "text": f"Run the forensic tool for {tool_duration} seconds"}],
        )
        
        prompt_task = asyncio.create_task(
            self.agent_conn.send_request(prompt_req.model_dump())
        )
        
        # Wait for tool start event
        tool_started = await self._wait_for_tool_start()
        if tool_started:
            result.acp_tool_started_received = True
            result.tool_call_id = tool_started.get("tool_call_id", "")
            result.tool_started_at = time.time()
            print(f"[Harness] Tool started: {result.tool_call_id}")
        
        # Inject disconnect at the specified point
        if disconnect_point == DisconnectPoint.BEFORE_TOOL:
            await self._inject_disconnect()
            result.acp_disconnect_time = time.time()
        
        elif disconnect_point == DisconnectPoint.DURING_TOOL:
            # Wait a bit for tool to be running
            await asyncio.sleep(0.5)
            await self._inject_disconnect()
            result.acp_disconnect_time = time.time()
        
        elif disconnect_point == DisconnectPoint.WHILE_RUNNING:
            # Wait for tool to be mid-execution
            await asyncio.sleep(tool_duration / 2)
            await self._inject_disconnect()
            result.acp_disconnect_time = time.time()
        
        # Wait for completion or timeout
        completion_event = await self._wait_for_completion(timeout=tool_duration + 5.0)
        
        if completion_event:
            if completion_event.get("status") == "completed":
                result.acp_tool_completed_received = True
                result.tool_completed_at = time.time()
            elif completion_event.get("status") == "failed":
                result.acp_tool_failed_received = True
                result.tool_failed_at = time.time()
            elif completion_event.get("status") == "cancelled":
                result.acp_tool_cancelled_received = True
                result.tool_cancelled_at = time.time()
        
        # For AFTER_COMPLETION, disconnect after tool finishes
        if disconnect_point == DisconnectPoint.AFTER_COMPLETION:
            await asyncio.sleep(0.5)
            await self._inject_disconnect()
            result.acp_disconnect_time = time.time()
        
        result.completed_at = time.time()
        
        # Try to get ground truth from agent (if it exposes it)
        # This would require a special method or separate channel
        
        self.results.append(result)
        print(f"[Harness] Experiment complete: {result}")
        return result
    
    async def _wait_for_tool_start(self, timeout: float = 10.0) -> Optional[Dict]:
        """Wait for TOOL_STARTED event."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
                if isinstance(msg, dict) and msg.get("sessionUpdate") == "tool_call":
                    return msg
                elif hasattr(msg, "update") and hasattr(msg.update, "sessionUpdate"):
                    if msg.update.sessionUpdate == "tool_call":
                        return msg.model_dump() if hasattr(msg, "model_dump") else msg
            except asyncio.TimeoutError:
                continue
        return None
    
    async def _wait_for_completion(self, timeout: float = 30.0) -> Optional[Dict]:
        """Wait for TOOL_COMPLETED/FAILED/CANCELLED event."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
                if isinstance(msg, dict) and msg.get("sessionUpdate") == "tool_call_update":
                    status = msg.get("status")
                    if status in ("completed", "failed", "cancelled"):
                        return msg
                elif hasattr(msg, "update") and hasattr(msg.update, "sessionUpdate"):
                    if msg.update.sessionUpdate == "tool_call_update":
                        status = getattr(msg.update, "status", None)
                        if status in ("completed", "failed", "cancelled"):
                            return msg.model_dump() if hasattr(msg, "model_dump") else msg
            except asyncio.TimeoutError:
                continue
        return None
    
    async def _inject_disconnect(self):
        """Inject ACP disconnect by closing the connection."""
        print(f"[Harness] Injecting ACP disconnect...")
        if self.agent_conn:
            await self.agent_conn.close()
            self.agent_conn = None
    
    async def reconnect(self, agent_cmd: List[str]):
        """Reconnect to a new agent instance (simulating session restore)."""
        print(f"[Harness] Reconnecting...")
        await self.connect_stdio(agent_cmd)
    
    def get_acp_events(self) -> List[Dict]:
        """Get all captured ACP events."""
        return self._acp_messages
    
    async def cleanup(self):
        """Clean up resources."""
        if self._listen_task:
            self._listen_task.cancel()
        if self.agent_conn:
            await self.agent_conn.close()
        if self.agent_process:
            self.agent_process.terminate()


async def run_forensic_experiments():
    """Run all forensic experiments."""
    
    # The agent command - we'll use our forensic agent
    import sys
    agent_cmd = [
        sys.executable,
        "C:/Projects/AgentGuardian/tests/integration/forensic/forensic_agent.py",
        "3.0",  # 3 second tool duration
    ]
    
    harness = ForensicTestHarness()
    
    try:
        await harness.connect_stdio(agent_cmd)
        
        experiments = [
            ("Normal", DisconnectPoint.AFTER_COMPLETION, 2.0, False, False),
            ("Disconnect Before Tool", DisconnectPoint.BEFORE_TOOL, 2.0, False, False),
            ("Disconnect During Tool", DisconnectPoint.DURING_TOOL, 3.0, False, False),
            ("Disconnect While Running", DisconnectPoint.WHILE_RUNNING, 4.0, False, False),
            ("Disconnect + Tool Fails", DisconnectPoint.DURING_TOOL, 3.0, False, True),
            ("Complete Then Disconnect", DisconnectPoint.AFTER_COMPLETION, 2.0, False, False),
        ]
        
        for name, dp, duration, should_fail, fail_at_disc in experiments:
            try:
                result = await harness.run_experiment(
                    name=name,
                    disconnect_point=dp,
                    tool_duration=duration,
                    should_fail=should_fail,
                    fail_at_disconnect=fail_at_disc,
                )
                print(f"[Harness] Result: {result.name} - ACP events: start={result.acp_tool_started_received}, complete={result.acp_tool_completed_received}, failed={result.acp_tool_failed_received}")
            except Exception as e:
                print(f"[Harness] Experiment {name} failed: {e}")
                import traceback
                traceback.print_exc()
        
        # Print summary
        print("\n[Harness] ===== EXPERIMENT SUMMARY =====")
        for r in harness.results:
            print(f"  {r.name}:")
            print(f"    ACP: start={r.acp_tool_started_received}, complete={r.acp_tool_completed_received}, failed={r.acp_tool_failed_received}")
            print(f"    Disconnect: {r.disconnect_point.value} at t={r.acp_disconnect_time}")
        
        return harness.results
        
    finally:
        await harness.cleanup()


if __name__ == "__main__":
    asyncio.run(run_forensic_experiments())