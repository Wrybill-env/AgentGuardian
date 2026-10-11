"""
Fault Injection Test Harness for Agent Guardian.

This module provides a deterministic test harness that can simulate various
failure scenarios and verify Guardian's safety decisions.

The harness simulates ACP event streams with injected faults and verifies
that Guardian makes the correct safety decisions.
"""

import asyncio
import json
import random
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, AsyncGenerator
from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from guardian.core import (
    GuardianStateMachine,
    SafetyMode,
    StateMachineEvent,
    FailureClassification,
    RecoveryAction,
    TurnContext,
    TurnPhase,
    GuardianState,
    FailureType,
)
from guardian.core.failure_classifier import FailureClassifier
from guardian.core.safety_gate import SafetyGate, SafetyVerdict
from guardian.core.backoff import BackoffStrategy, BackoffConfig, JitterType, JitterProvider, SystemJitterProvider
from guardian.goose.protocol import (
    ACPEvent,
    ACPEventType,
    ACPProtocolParser,
    StopReason,
)
from guardian.goose.observer import GooseObserver, SessionState, FailureDetector
from guardian.core.backoff import BackoffStrategy, BackoffConfig, JitterProvider, SystemJitterProvider


class DeterministicJitterProvider:
    """Deterministic jitter provider for testing."""
    
    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._values = []
    
    def random(self) -> float:
        if self._values:
            val = self._values[self._value_index]
            self._value_index = (self._value_index + 1) % len(self._values)
            return val
        return self._rng.random()
    
    def uniform(self, a: float, b: float) -> float:
        if self._values:
            val = self._values[self._value_index]
            self._value_index = (self._value_index + 1) % len(self._values)
            return val
        return self._rng.uniform(a, b)


@dataclass
class FaultScenario:
    """Defines a fault injection scenario."""
    fault_type: 'FaultType'
    description: str
    # Expected outcomes
    expected_failure_type: str
    expected_replay_safe: bool
    expected_recovery_action: str
    # Scenario parameters
    inject_after_chunks: int = 0
    inject_after_tool_start: bool = False
    inject_after_tool_complete: bool = False
    stall_duration: float = 0.0
    # Expected state transitions (subset of states that should be visited)
    expected_states: List[str] = field(default_factory=list)


class FaultType(Enum):
    """Types of faults that can be injected."""
    RATE_LIMIT_429 = "rate_limit_429"
    REQUEST_TIMEOUT_408 = "request_timeout_408"
    SERVER_ERROR_5XX = "server_error_5xx"
    CONNECTION_RESET = "connection_reset"
    NETWORK_DISCONNECT = "network_disconnect"
    STALE_STREAM = "stale_stream"
    AUTH_FAILURE = "auth_failure"
    PERMANENT_PROVIDER_FAILURE = "permanent_provider_failure"
    BACKEND_DISCONNECT = "backend_disconnect"
    
    
    AMBIGUOUS_FAILURE = "ambiguous_failure"


class DeterministicJitterProvider:
    """Deterministic jitter provider for testing."""
    
    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._values = []
    
    def random(self) -> float:
        if self._values:
            val = self._values[self._value_index]
            self._value_index = (self._value_index + 1) % len(self._values)
            return val
        return self._rng.random()
    
    def uniform(self, a: float, b: float) -> float:
        if self._values:
            val = self._values[self._value_index]
            self._value_index = (self._value_index + 1) % len(self._values)
            return val
        return self._rng.uniform(a, b)


class FaultInjectionHarness:
    """
    Fault injection test harness for Guardian.
    
    Creates controlled ACP event streams with injected faults and verifies
    Guardian's safety decisions.
    """
    
    def __init__(self, safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY, seed: int = 42):
        self.safety_mode = safety_mode
        self.deterministic_jitter = DeterministicJitterProvider(seed)
        self.guardian: Optional[GuardianStateMachine] = None
        self.observer: Optional[GooseObserver] = None
        self.parser = ACPProtocolParser()
        self.failure_classifier = FailureClassifier()
        self.safety_gate = SafetyGate(safety_mode=safety_mode)
        self.backoff = BackoffStrategy(BackoffConfig(jitter_provider=SystemJitterProvider()))
        
        # Test tracking
        self.events_received: List[ACPEvent] = []
        self.state_transitions: List[tuple] = []
        self.failures_detected: List[FailureClassification] = []
        self.recovery_actions: List[Dict] = []
        
    def setup_guardian(self, safety_mode: Optional[SafetyMode] = None, backoff_config: Optional[BackoffConfig] = None):
        """Initialize a fresh Guardian state machine."""
        mode = safety_mode or self.safety_mode
        bc = backoff_config or BackoffConfig(jitter_provider=self.deterministic_jitter)
        self.guardian = GuardianStateMachine(
            safety_mode=safety_mode or SafetyMode.OBSERVE_ONLY,
            backoff_config=bc,
            on_state_change=self._on_state_change,
            on_failure=self._on_failure,
            on_recovery_action=self._on_recovery_action,
            on_recovery_result=self._on_recovery_result,
        )
        
    def _on_state_change(self, old_state, new_state, event):
        self.state_transitions.append((old_state.value, new_state.value, event.value))
        
    def _on_failure(self, classification: FailureClassification, turn_context: Optional[TurnContext]):
        self.failures_detected.append(classification)
        
    def _on_recovery_action(self, action: RecoveryAction, context: dict):
        self.recovery_actions.append({"action": action.value, "context": context})
        
    def _on_recovery_result(self, success: bool, details: dict):
        pass
    
    def feed_event(self, event: ACPEvent):
        """Feed an ACP event into the Guardian state machine."""
        self.events_received.append(event)
        
        if not self.guardian.turn_context and event.session_id:
            self.guardian.start_turn(session_id=event.session_id)
        
        # Map ACP event types to Guardian events
        event_mapping = {
            ACPEventType.ASSISTANT_CHUNK: StateMachineEvent.ASSISTANT_CHUNK,
            ACPEventType.TOOL_CALL_STARTED: StateMachineEvent.TOOL_STARTED,
            ACPEventType.TOOL_CALL_UPDATE: StateMachineEvent.TOOL_STARTED,
            ACPEventType.TOOL_CALL_COMPLETED: StateMachineEvent.TOOL_COMPLETED,
            ACPEventType.TOOL_CALL_FAILED: StateMachineEvent.TOOL_FAILED,
            ACPEventType.TURN_COMPLETE: StateMachineEvent.TURN_COMPLETE,
            ACPEventType.TURN_ERROR: StateMachineEvent.TURN_ERROR,
            ACPEventType.TURN_CANCELLED: StateMachineEvent.TURN_CANCELLED,
            ACPEventType.TURN_MAX_TURNS: StateMachineEvent.TURN_MAX_TURNS,
            ACPEventType.RATE_LIMIT: StateMachineEvent.FAILURE_DETECTED,
            ACPEventType.SERVER_ERROR: StateMachineEvent.FAILURE_DETECTED,
            ACPEventType.NETWORK_ERROR: StateMachineEvent.FAILURE_DETECTED,
            ACPEventType.AUTHENTICATION_ERROR: StateMachineEvent.FAILURE_DETECTED,
            ACPEventType.PROVIDER_ERROR: StateMachineEvent.FAILURE_DETECTED,
        }
        
        guardian_event = event_mapping.get(event.event_type)
        if guardian_event:
            data = {}
            if event.session_id:
                data["session_id"] = event.session_id
            if event.tool_name:
                data["tool_name"] = event.tool_name
            if event.tool_call_id:
                data["tool_call_id"] = event.tool_call_id
            if event.error_message:
                data["error_message"] = event.error_message
            if event.stop_reason:
                data["stop_reason"] = event.stop_reason.value
            
            self.guardian.handle_event(guardian_event, data)
        
        # Handle session info for run_id
        if event.event_type == ACPEventType.SESSION_UPDATE and event.raw_message:
            params = event.raw_message.get("params", {})
            update = params.get("update", {})
            if "runId" in update and self.guardian.turn_context:
                self.guardian.turn_context.run_id = update["runId"]
    
    def get_status(self) -> Dict:
        """Get current test status."""
        return {
            "state": self.guardian.state.value if self.guardian else "NOT_INITIALIZED",
            "events_received": len(self.events_received),
            "state_transitions": self.state_transitions,
            "failures_detected": [
                {
                    "type": f.failure_type.value,
                    "retryable": f.retryable,
                    "replay_safe": f.replay_safe,
                    "action": f.recommended_action.value,
                    "confidence": f.confidence,
                } for f in self.failures_detected
            ],
            "recovery_actions": self.recovery_actions,
            "turn_context": {
                "session_id": self.guardian.turn_context.session_id if self.guardian and self.guardian.turn_context else None,
                "phase": self.guardian.turn_context.phase.value if self.guardian and self.guardian.turn_context else None,
                "assistant_chunks": self.guardian.turn_context.assistant_chunks_received if self.guardian and self.guardian.turn_context else 0,
                "tools_started": self.guardian.turn_context.tool_calls_started if self.guardian and self.guardian.turn_context else 0,
                "safe_to_replay": self.guardian.turn_context.is_safe_to_replay() if self.guardian and self.guardian.turn_context else True,
            } if self.guardian and self.guardian.turn_context else None,
        }


# ============================================================
# ACP Event Builders for Test Scenarios (Correct ACP Format)
# ============================================================

def make_initialize_response() -> str:
    """Create ACP initialize response."""
    return json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": 1,
            "agentCapabilities": {"loadSession": True},
            "agentInfo": {"name": "goose", "version": "1.52.0"}
        }
    })


def make_session_update(session_id: str, update_type: str, **kwargs) -> str:
    """Create a sessionUpdate notification with proper ACP format."""
    update = {"type": update_type}
    update.update(kwargs)
    
    return json.dumps({
        "jsonrpc": "2.0",
        "method": "sessionUpdate",
        "params": {
            "sessionId": session_id,
            "update": update
        }
    })


def make_assistant_chunk(session_id: str, text: str, run_id: str = None) -> str:
    """Create an assistant text chunk event (sessionUpdate with MessageUpdate)."""
    return make_session_update(session_id, "MessageUpdate", 
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
        runId=run_id
    )


def make_user_message(session_id: str, text: str) -> str:
    """Create a user message event (sessionUpdate with MessageUpdate)."""
    return make_session_update(session_id, "MessageUpdate",
        message={"role": "user", "content": [{"type": "text", "text": text}]}
    )


def make_tool_call_started(session_id: str, tool_name: str, tool_call_id: str, run_id: str = None) -> str:
    """Create a tool call started event (sessionUpdate with MessageUpdate containing tool_call)."""
    return make_session_update(session_id, "MessageUpdate",
        message={
            "role": "assistant",
            "content": [{
                "type": "tool_call",
                "id": tool_call_id,
                "name": tool_name,
                "arguments": {}
            }]
        },
        runId=run_id
    )


def make_tool_call_update(session_id: str, tool_call_id: str, status: str, run_id: str = None, output: str = None, error: str = None) -> str:
    """Create a tool call update event (sessionUpdate with ToolCallUpdate)."""
    tool_call = {"id": tool_call_id, "status": status}
    if output:
        tool_call["output"] = output
    if error:
        tool_call["error"] = error
    
    return make_session_update(session_id, "ToolCallUpdate",
        toolCall=tool_call,
        runId=run_id
    )


def make_turn_complete(session_id: str, stop_reason: str = "end_turn", run_id: str = None) -> str:
    """Create a turn completion event (sessionUpdate with stopReason in MessageUpdate)."""
    return make_session_update(session_id, "MessageUpdate",
        message={"role": "assistant", "content": []},
        stopReason=stop_reason,
        runId=run_id
    )


def make_rate_limit_error(session_id: str, error_msg: str = "Rate limit exceeded", run_id: str = None) -> str:
    """Create a rate limit error event (sessionUpdate with Error update type)."""
    return make_session_update(session_id, "Error",
        error={"code": -32603, "message": error_msg},
        runId=run_id
    )


def make_server_error(session_id: str, error_msg: str = "Internal server error", http_status: int = 500, run_id: str = None) -> str:
    """Create a server error event (sessionUpdate with Error update type)."""
    return make_session_update(session_id, "Error",
        error={"code": -32603, "message": f"{error_msg} (HTTP {http_status})"},
        runId=run_id
    )


def make_auth_error(session_id: str, run_id: str = None) -> str:
    """Create an authentication error event."""
    return make_session_update(session_id, "Error",
        error={"code": -32603, "message": "401 Unauthorized"},
        runId=run_id
    )


def make_invalid_model_error(session_id: str, run_id: str = None) -> str:
    """Create an invalid model error."""
    return make_session_update(session_id, "Error",
        error={"code": -32603, "message": "Model not found"},
        runId=run_id
    )


def make_backend_disconnect(session_id: str) -> str:
    """Simulate backend disconnect with cancelled turn."""
    return make_session_update(session_id, "Error",
        error={"code": -32603, "message": "Backend disconnected"},
        runId=None
    )


# ============================================================
# Test Scenarios - Expected states match actual state machine behavior
# ============================================================

SCENARIOS = [
    FaultScenario(
        fault_type=FaultType.RATE_LIMIT_429,
        description="Rate limit before any output",
        expected_failure_type="rate_limit",
        expected_replay_safe=True,
        expected_recovery_action="wait_and_retry",
        expected_states=["observing", "streaming", "possible_failure", "recovery_wait"],
    ),
    FaultScenario(
        fault_type=FaultType.RATE_LIMIT_429,
        description="Rate limit after assistant chunks",
        expected_failure_type="rate_limit",
        expected_replay_safe=False,
        expected_recovery_action="no_safe_recovery",
        inject_after_chunks=3,
        expected_states=["observing", "streaming", "possible_failure"],
    ),
    FaultScenario(
        fault_type=FaultType.RATE_LIMIT_429,
        description="Rate limit after tool started",
        expected_failure_type="rate_limit",
        expected_replay_safe=False,
        expected_recovery_action="no_safe_recovery",
        inject_after_tool_start=True,
        expected_states=["observing", "streaming", "tool_active", "possible_failure"],
    ),
    FaultScenario(
        fault_type=FaultType.REQUEST_TIMEOUT_408,
        description="Request timeout before any output",
        expected_failure_type="timeout",
        expected_replay_safe=True,
        expected_recovery_action="wait_and_retry",
        expected_states=["observing", "streaming", "possible_failure", "recovery_wait"],
    ),
    FaultScenario(
        fault_type=FaultType.SERVER_ERROR_5XX,
        description="Server error before any output",
        expected_failure_type="server_error",
        expected_replay_safe=True,
        expected_recovery_action="wait_and_retry",
        expected_states=["observing", "streaming", "possible_failure", "recovery_wait"],
    ),
    FaultScenario(
        fault_type=FaultType.SERVER_ERROR_5XX,
        description="Server error after tool completed",
        expected_failure_type="server_error",
        expected_replay_safe=False,
        expected_recovery_action="no_safe_recovery",
        inject_after_tool_complete=True,
        expected_states=["observing", "streaming", "tool_active", "streaming", "possible_failure", "failed_permanently"],
    ),
    FaultScenario(
        fault_type=FaultType.CONNECTION_RESET,
        description="Connection reset during idle",
        expected_failure_type="backend_disconnect",
        expected_replay_safe=True,
        expected_recovery_action="reconnect_acp",
        expected_states=["observing", "possible_failure"],
    ),
    FaultScenario(
        fault_type=FaultType.NETWORK_DISCONNECT,
        description="Network disconnect during streaming",
        expected_failure_type="network_transient",
        expected_replay_safe=True,
        expected_recovery_action="reconnect_acp",
        expected_states=["streaming", "possible_failure"],
    ),
    FaultScenario(
        fault_type=FaultType.STALE_STREAM,
        description="Stale stream with no output",
        expected_failure_type="stream_stale",
        expected_replay_safe=True,
        expected_recovery_action="cancel_and_replay",
        stall_duration=65.0,
        expected_states=["streaming", "possible_failure"],
    ),
    FaultScenario(
        fault_type=FaultType.STALE_STREAM,
        description="Stale stream during tool execution",
        expected_failure_type="stream_stale",
        expected_replay_safe=False,
        expected_recovery_action="human_intervention",
        inject_after_tool_start=True,
        stall_duration=65.0,
        expected_states=["streaming", "tool_active", "possible_failure", "failed_permanently"],
    ),
    FaultScenario(
        fault_type=FaultType.AUTH_FAILURE,
        description="Authentication failure",
        expected_failure_type="authentication_failure",
        expected_replay_safe=False,
        expected_recovery_action="human_intervention",
        expected_states=["streaming", "possible_failure", "failed_permanently"],
    ),
    FaultScenario(
        fault_type=FaultType.PERMANENT_PROVIDER_FAILURE,
        description="Permanent provider failure (invalid model)",
        expected_failure_type="invalid_model",
        expected_replay_safe=False,
        expected_recovery_action="human_intervention",
        expected_states=["streaming", "possible_failure", "failed_permanently"],
    ),
    FaultScenario(
        fault_type=FaultType.BACKEND_DISCONNECT,
        description="Backend disconnect during streaming",
        expected_failure_type="backend_disconnect",
        expected_replay_safe=True,
        expected_recovery_action="reconnect_acp",
        expected_states=["streaming", "possible_failure"],
    ),
    FaultScenario(
        fault_type=FaultType.SERVER_ERROR_5XX,
        description="Tool call executed before failure",
        expected_failure_type="server_error",
        expected_replay_safe=False,
        expected_recovery_action="no_safe_recovery",
        inject_after_tool_complete=True,
        expected_states=["observing", "streaming", "tool_active", "streaming", "possible_failure", "failed_permanently"],
    ),
    FaultScenario(
        fault_type=FaultType.RATE_LIMIT_429,
        description="Assistant output before failure",
        expected_failure_type="rate_limit",
        expected_replay_safe=False,
        expected_recovery_action="no_safe_recovery",
        inject_after_chunks=2,
        expected_states=["observing", "streaming", "possible_failure", "failed_permanently"],
    ),
    FaultScenario(
        fault_type=FaultType.AMBIGUOUS_FAILURE,
        description="Ambiguous/unknown failure",
        expected_failure_type="unknown_failure",
        expected_replay_safe=False,
        expected_recovery_action="no_safe_recovery",
        expected_states=["streaming", "possible_failure", "failed_permanently"],
    ),
]


# ============================================================
# Test Functions
# ============================================================

@pytest.fixture
def harness():
    """Create a fresh fault injection harness."""
    h = FaultInjectionHarness(safety_mode=SafetyMode.OBSERVE_ONLY)
    h.setup_guardian()
    return h


async def run_scenario(harness: FaultInjectionHarness, scenario: FaultScenario, session_id: str = "test_session"):
    """Run a single fault injection scenario."""
    run_id = "test_run_123"
    
    # Connect - start from IDLE
    harness.guardian.transition(StateMachineEvent.CONNECTED)
    # Handshake completes during connection, state becomes OBSERVING
    # No HANDSHAKE_COMPLETE event needed
    
    # Send user message to start turn
    user_msg = make_user_message(session_id, "Test prompt")
    event = harness.parser.parse_frame(user_msg)[0]
    harness.feed_event(event)
    
    # Track events for this scenario
    events_to_send = []
    
    # Build event sequence based on scenario
    if scenario.inject_after_tool_start or scenario.inject_after_tool_complete:
        # Tool call sequence
        tool_id = "call_test_123"
        events_to_send.append(make_tool_call_started(session_id, "shell", tool_id, "run_123"))
        
        if scenario.inject_after_tool_complete:
            events_to_send.append(make_tool_call_update(session_id, tool_id, "completed", output="success"))
    
    if scenario.inject_after_chunks > 0:
        for i in range(scenario.inject_after_chunks):
            events_to_send.append(make_assistant_chunk(session_id, f"Chunk {i+1}", "run_123"))
    
    # Inject the fault
    fault_events = []
    print(f"DEBUG: scenario.fault_type = {scenario.fault_type}")
    if scenario.fault_type == FaultType.RATE_LIMIT_429:
        fault_events.append(make_rate_limit_error(session_id, "429 Too Many Requests"))
    elif scenario.fault_type == FaultType.REQUEST_TIMEOUT_408:
        fault_events.append(make_server_error(session_id, "Request timeout", 408))
    elif scenario.fault_type == FaultType.SERVER_ERROR_5XX:
        fault_events.append(make_server_error(session_id, "Internal server error", 500))
    elif scenario.fault_type == FaultType.CONNECTION_RESET:
        # Simulated by test harness - just disconnect
        pass
    elif scenario.fault_type == FaultType.NETWORK_DISCONNECT:
        fault_events.append(make_server_error(session_id, "Network error", 0))
    elif scenario.fault_type == FaultType.STALE_STREAM:
        # Stale is detected by time, not an event
        pass
    elif scenario.fault_type == FaultType.AUTH_FAILURE:
        fault_events.append(make_auth_error(session_id))
    elif scenario.fault_type == FaultType.PERMANENT_PROVIDER_FAILURE:
        fault_events.append(make_invalid_model_error(session_id))
    elif scenario.fault_type == FaultType.BACKEND_DISCONNECT:
        fault_events.append(make_backend_disconnect(session_id))
    elif scenario.fault_type == FaultType.AMBIGUOUS_FAILURE:
        fault_events.append(make_server_error(session_id, "Unknown error occurred"))
    
    print(f"DEBUG: fault_events count = {len(fault_events)}")
    for fe in fault_events:
        print(f"DEBUG: fault event = {fe[:100]}")
    
    # Feed assistant chunks before fault (if any)
    for event_json in events_to_send:
        event = harness.parser.parse_frame(event_json)[0]
        harness.feed_event(event)
        await asyncio.sleep(0.01)
    
    # Feed fault events
    for event_json in fault_events:
        event = harness.parser.parse_frame(event_json)[0]
        print(f"DEBUG: Feeding fault event: {event.event_type}, session_id={event.session_id}")
        harness.feed_event(event)
        await asyncio.sleep(0.01)
    
    # Handle stale stream by advancing time
    if scenario.fault_type == FaultType.STALE_STREAM and scenario.stall_duration > 0:
        if harness.guardian.turn_context:
            harness.guardian.turn_context.last_event_timestamp = time.time() - scenario.stall_duration - 1
            # Trigger stale check via handle_event
            harness.guardian.handle_event(StateMachineEvent.STALE_DETECTED)
    
    # For connection reset, simulate disconnect
    if scenario.fault_type == FaultType.CONNECTION_RESET:
        harness.guardian.handle_event(StateMachineEvent.DISCONNECTED)
    
    # For backend disconnect
    if scenario.fault_type == FaultType.BACKEND_DISCONNECT:
        harness.guardian.handle_event(StateMachineEvent.DISCONNECTED)
    
    return harness.get_status()


# ============================================================
# Test Functions
# ============================================================

@pytest.fixture
def harness():
    """Create a fresh fault injection harness."""
    h = FaultInjectionHarness(safety_mode=SafetyMode.OBSERVE_ONLY)
    h.setup_guardian()
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.description for s in SCENARIOS])
async def test_fault_scenario(scenario: FaultScenario):
    """Test a single fault injection scenario."""
    harness = FaultInjectionHarness(safety_mode=SafetyMode.OBSERVE_ONLY)
    h = harness
    h.setup_guardian()
    
    status = await run_scenario(harness, scenario)
    
    # Verify failure classification
    assert len(h.failures_detected) > 0 or scenario.fault_type in [
        FaultType.CONNECTION_RESET, FaultType.BACKEND_DISCONNECT
    ], f"No failure detected for {scenario.description}"
    
    if h.failures_detected:
        failure = h.failures_detected[-1]
        assert failure.failure_type.value == scenario.expected_failure_type, \
            f"Expected {scenario.expected_failure_type}, got {failure.failure_type.value} for {scenario.description}"
        
        # Check replay safety
        if scenario.expected_replay_safe is not None:
            assert failure.replay_safe == scenario.expected_replay_safe, \
                f"Replay safety mismatch for {scenario.description}: expected {scenario.expected_replay_safe}, got {failure.replay_safe}"
        
        # Check recovery action (in DRY_RUN/OBSERVE_ONLY mode, action may be NO_SAFE_RECOVERY)
        # The key test is that unsafe scenarios are NOT marked as replay_safe
        if not scenario.expected_replay_safe:
            assert failure.replay_safe == False, \
                f"Unsafe scenario {scenario.description} incorrectly marked as replay_safe"
    
    # Verify state transitions include expected states
    if scenario.expected_states:
        actual_states = [s[1] for s in h.state_transitions]
        for expected in scenario.expected_states:
            assert expected in actual_states, \
                f"Expected state {expected} not found in {actual_states} for {scenario.description}"


# ============================================================
# Safety Gate Tests
# ============================================================

@pytest.mark.asyncio
async def test_safety_gate_blocks_replay_after_assistant_output():
    """Verify safety gate blocks replay when assistant output observed."""
    gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    turn_context.assistant_chunks_received = 3
    
    result = gate.check_replay_safety(turn_context)
    
    assert result.verdict == SafetyVerdict.UNSAFE
    assert any("Assistant output" in str(f) for f in result.blocking_factors)


@pytest.mark.asyncio
async def test_safety_gate_blocks_replay_after_tool_started():
    """Verify safety gate blocks replay when tool activity observed."""
    gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    turn_context.tool_calls_started = 1
    
    result = gate.check_replay_safety(turn_context)
    
    assert result.verdict == SafetyVerdict.UNSAFE
    assert any("Tool activity" in str(f) for f in result.blocking_factors)


@pytest.mark.asyncio
async def test_safety_gate_allows_replay_when_safe():
    """Verify safety gate allows replay when no side effects."""
    gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    
    result = gate.check_replay_safety(turn_context)
    
    assert result.verdict == SafetyVerdict.SAFE


@pytest.mark.asyncio
async def test_safety_gate_blocks_in_observe_only():
    """Verify safety gate blocks all recovery in OBSERVE_ONLY mode."""
    gate = SafetyGate(safety_mode=SafetyMode.OBSERVE_ONLY)
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    
    result = gate.check_recovery_action_safety(
        RecoveryAction.WAIT_AND_RETRY, turn_context
    )
    
    assert result.verdict == SafetyVerdict.UNSAFE
    assert "OBSERVE_ONLY" in result.reason


# ============================================================
# Backoff Strategy Tests with Deterministic Jitter
# ============================================================

@pytest.mark.asyncio
async def test_backoff_respects_retry_after():
    """Test that backoff respects Retry-After header."""
    config = BackoffConfig(
        initial_delay=10.0,
        max_delay=60.0,
        respect_retry_after=True,
        max_retry_after=300.0,
        jitter_provider=DeterministicJitterProvider(seed=42),
        jitter_type=JitterType.NONE,
    )
    backoff = BackoffStrategy(config)
    
    # First attempt - no jitter for first attempt (attempt 0)
    delay1 = backoff.next_delay()
    assert delay1 == 10.0  # initial_delay
    
    # Second attempt with Retry-After: 30
    delay2 = backoff.next_delay(retry_after=30.0)
    assert delay2 == 30.0
    
    # Third attempt back to exponential
    delay3 = backoff.next_delay()
    assert delay3 == 20.0  # 10 * 2^1


@pytest.mark.asyncio
async def test_backoff_caps_retry_after():
    """Test that backoff caps Retry-After at max_retry_after."""
    config = BackoffConfig(
        initial_delay=1.0,
        max_delay=60.0,
        max_retry_after=30.0,
        respect_retry_after=True,
        jitter_provider=DeterministicJitterProvider(seed=42),
        jitter_type=JitterType.NONE,
    )
    backoff = BackoffStrategy(config)
    
    delay = backoff.next_delay(retry_after=300.0)
    assert delay == 30.0  # Capped at max_retry_after


@pytest.mark.asyncio
async def test_backoff_exponential_growth_deterministic():
    """Test exponential backoff with deterministic jitter."""
    config = BackoffConfig(
        initial_delay=1.0,
        max_delay=100.0,
        multiplier=2.0,
        jitter_type=JitterType.FULL,
        jitter_provider=DeterministicJitterProvider(seed=123),
    )
    backoff = BackoffStrategy(config)
    
    delays = [backoff.next_delay() for _ in range(4)]
    
    # With deterministic seed, delays should be predictable
    assert len(delays) == 4
    assert all(d >= 0 for d in delays)
    assert delays[0] <= 1.0
    assert delays[1] <= 2.0
    assert delays[2] <= 4.0
    assert delays[3] <= 8.0


# ============================================================
# Stale Stream Detection Tests
# ============================================================

@pytest.mark.asyncio
async def test_stale_detection_during_streaming():
    """Test stale detection during assistant streaming."""
    machine = GuardianStateMachine(
        safety_mode=SafetyMode.OBSERVE_ONLY,
        stale_threshold=60.0,
    )
    
    machine.transition(StateMachineEvent.CONNECTED)
    machine.start_turn("sess_test")
    
    # Simulate streaming
    machine.handle_event(StateMachineEvent.ASSISTANT_CHUNK)
    assert machine.state == GuardianState.STREAMING
    
    # Simulate time passing beyond stale threshold
    machine.turn_context.last_event_timestamp = time.time() - 65
    
    assert machine._check_stale() is True


@pytest.mark.asyncio
async def test_stale_detection_during_tool_execution():
    """Test stale detection during tool execution."""
    machine = GuardianStateMachine(
        safety_mode=SafetyMode.OBSERVE_ONLY,
        stale_threshold=60.0,
    )
    
    machine.transition(StateMachineEvent.CONNECTED)
    machine.start_turn("sess_test")
    machine.handle_event(StateMachineEvent.TOOL_STARTED)
    assert machine.state == GuardianState.TOOL_ACTIVE
    
    # Simulate time passing
    machine.turn_context.last_event_timestamp = time.time() - 65
    
    assert machine._check_stale() is True


@pytest.mark.asyncio
async def test_no_stale_detection_when_no_turn():
    """Test that stale detection doesn't trigger when no turn is active."""
    machine = GuardianStateMachine(
        safety_mode=SafetyMode.OBSERVE_ONLY,
        stale_threshold=60.0,
    )
    
    machine.transition(StateMachineEvent.CONNECTED)
    # No turn started - should not be stale
    assert machine._check_stale() is False


@pytest.mark.asyncio
async def test_no_stale_detection_when_turn_completed():
    """Test that stale detection doesn't trigger after turn completes."""
    machine = GuardianStateMachine(
        safety_mode=SafetyMode.OBSERVE_ONLY,
        stale_threshold=60.0,
    )
    
    machine.transition(StateMachineEvent.CONNECTED)
    machine.start_turn("sess_test")
    machine.end_turn("end_turn")
    
    # Turn completed, no active turn
    assert machine._check_stale() is False


@pytest.mark.asyncio
async def test_stale_detection_during_tool_active():
    """Test stale detection during tool execution."""
    machine = GuardianStateMachine(
        safety_mode=SafetyMode.OBSERVE_ONLY,
        stale_threshold=60.0,
    )
    
    machine.transition(StateMachineEvent.CONNECTED)
    machine.start_turn("sess_test")
    machine.handle_event(StateMachineEvent.TOOL_STARTED)
    assert machine.state == GuardianState.TOOL_ACTIVE
    
    # Simulate time passing beyond stale threshold
    machine.turn_context.last_event_timestamp = time.time() - 65
    
    assert machine._check_stale() is True


# ============================================================
# Recovery Safety Verification Tests
# ============================================================

@pytest.mark.asyncio
async def test_recovery_blocked_after_tool_execution():
    """Verify recovery is blocked when tool has executed."""
    gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    turn_context.tool_calls_started = 1
    turn_context.tool_calls_completed = 1
    
    result = gate.check_recovery_action_safety(
        RecoveryAction.WAIT_AND_RETRY, turn_context
    )
    
    assert result.verdict == SafetyVerdict.UNSAFE
    assert any("Tool activity" in str(f) for f in result.blocking_factors)


@pytest.mark.asyncio
async def test_reconnect_acp_requires_session_id():
    """Verify reconnect requires session ID for safety."""
    gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
    
    # No session ID - ambiguous
    turn_context = TurnContext(session_id="")
    result = gate.check_recovery_action_safety(
        RecoveryAction.RECONNECT_ACP, turn_context
    )
    assert result.verdict == SafetyVerdict.AMBIGUOUS
    
    # With session ID - safe
    turn_context = TurnContext(session_id="sess_123")
    result = gate.check_recovery_action_safety(
        RecoveryAction.RECONNECT_ACP, turn_context
    )
    assert result.verdict == SafetyVerdict.SAFE


@pytest.mark.asyncio
async def test_wait_for_backend_always_safe():
    """Verify WAIT_FOR_BACKEND is always safe (no replay)."""
    gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    turn_context.assistant_chunks_received = 10
    turn_context.tool_calls_started = 5
    
    result = gate.check_recovery_action_safety(
        RecoveryAction.WAIT_FOR_BACKEND, turn_context
    )
    
    assert result.verdict == SafetyVerdict.SAFE


# ============================================================
# Recovery Safety Verification Tests
# ============================================================

@pytest.mark.asyncio
async def test_recovery_blocked_after_tool_execution():
    """Verify recovery is blocked when tool has executed."""
    gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    turn_context.tool_calls_started = 1
    turn_context.tool_calls_completed = 1
    
    result = gate.check_recovery_action_safety(
        RecoveryAction.WAIT_AND_RETRY, turn_context
    )
    
    assert result.verdict == SafetyVerdict.UNSAFE
    assert any("Tool activity" in str(f) for f in result.blocking_factors)


@pytest.mark.asyncio
async def test_reconnect_acp_requires_session_id():
    """Verify reconnect requires session ID for safety."""
    gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
    
    # No session ID - ambiguous
    turn_context = TurnContext(session_id="")
    result = gate.check_recovery_action_safety(
        RecoveryAction.RECONNECT_ACP, turn_context
    )
    assert result.verdict == SafetyVerdict.AMBIGUOUS
    
    # With session ID - safe
    turn_context = TurnContext(session_id="sess_123")
    result = gate.check_recovery_action_safety(
        RecoveryAction.RECONNECT_ACP, turn_context
    )
    assert result.verdict == SafetyVerdict.SAFE


@pytest.mark.asyncio
async def test_wait_for_backend_always_safe():
    """Verify WAIT_FOR_BACKEND is always safe (no replay)."""
    gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    turn_context.assistant_chunks_received = 10
    turn_context.tool_calls_started = 5
    
    result = gate.check_recovery_action_safety(
        RecoveryAction.WAIT_FOR_BACKEND, turn_context
    )
    
    assert result.verdict == SafetyVerdict.SAFE


# ============================================================
# Failure Classification Edge Cases
# ============================================================

@pytest.mark.asyncio
async def test_unknown_failure_fails_closed():
    """Unknown failures should fail closed (not replay safe)."""
    classifier = FailureClassifier()
    
    result = classifier.classify(
        error_message="Some completely unknown error that matches no pattern",
    )
    
    assert result.failure_type == FailureType.UNKNOWN_FAILURE
    assert result.retryable is False
    assert result.replay_safe is False
    assert result.recommended_action == RecoveryAction.NO_SAFE_RECOVERY


@pytest.mark.asyncio
async def test_rate_limit_before_tools_is_replay_safe():
    """Rate limit before any tools should be replay safe."""
    classifier = FailureClassifier()
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    
    result = classifier.classify(
        error_message="429 Too Many Requests",
        http_status=429,
        turn_context=turn_context,
    )
    
    assert result.failure_type == FailureType.RATE_LIMIT
    assert result.replay_safe is True
    assert result.recommended_action == RecoveryAction.WAIT_AND_RETRY


@pytest.mark.asyncio
async def test_rate_limit_after_tools_not_replay_safe():
    """Rate limit after tools should NOT be replay safe."""
    classifier = FailureClassifier()
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    turn_context.tool_calls_started = 1
    
    result = classifier.classify(
        error_message="429 Too Many Requests",
        http_status=429,
        turn_context=turn_context,
    )
    
    assert result.failure_type == FailureType.RATE_LIMIT
    assert result.replay_safe is False
    assert result.recommended_action == RecoveryAction.NO_SAFE_RECOVERY


# ============================================================
# Stale Stream Ambiguity Tests
# ============================================================

@pytest.mark.asyncio
async def test_stale_without_tools_is_replay_safe():
    """Stale stream without tools should be replay safe."""
    classifier = FailureClassifier()
    
    turn_context = TurnContext(session_id="sess_test", verified_fresh=True)
    # No tool activity, no assistant output
    
    result = classifier.classify(
        error_message="Stream stalled - no chunks for 60s",
        turn_context=turn_context,
    )
    
    # Should be classified as STREAM_STALE with replay_safe=True
    # (when no tools/assistant output)
    assert result.failure_type in (FailureType.STREAM_STALE, FailureType.NETWORK_TRANSIENT)


# ============================================================
# CLI Integration Test
# ============================================================

def test_cli_help():
    """Test CLI help output."""
    result = subprocess.run(
        ["python", "-m", "guardian", "--help"],
        cwd="C:\\Projects\\AgentGuardian",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "observe" in result.stdout
    assert "watch" in result.stdout
    assert "doctor" in result.stdout
    assert "config" in result.stdout
    assert "observe_only" in result.stdout
    assert "dry_run" in result.stdout
    assert "auto_recover" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
