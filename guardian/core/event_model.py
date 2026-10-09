"""
Unified event model for Agent Guardian.

Provides a generic event taxonomy that works across different agents
(ACP events, Hermes callbacks, etc.) with structured evidence tracking.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
import time


class EventCategory(Enum):
    """High-level event categories."""
    CONNECTION = "connection"
    STREAM = "stream"
    MESSAGE = "message"
    TOOL = "tool"
    TURN = "turn"
    FAILURE = "failure"
    RECOVERY = "recovery"
    DIAGNOSTIC = "diagnostic"


class StreamEventType(Enum):
    """Stream-specific event types (agent-agnostic)."""
    STREAM_STARTED = "stream_started"
    FIRST_CHUNK_RECEIVED = "first_chunk_received"
    CHUNK_RECEIVED = "chunk_received"
    REASONING_RECEIVED = "reasoning_received"
    CHUNK_GAP_DETECTED = "chunk_gap_detected"
    FIRST_CHUNK_TIMEOUT = "first_chunk_timeout"
    STREAM_STALLED = "stream_stalled"
    STREAM_ENDED = "stream_ended"
    STREAM_ERROR = "stream_error"


class ToolEventType(Enum):
    """Tool-specific event types (agent-agnostic)."""
    TOOL_STARTED = "tool_started"
    TOOL_PROGRESS = "tool_progress"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    TOOL_TIMEOUT = "tool_timeout"
    PARTIAL_TOOL_DETECTED = "partial_tool_detected"


class TurnEventType(Enum):
    """Turn lifecycle event types (agent-agnostic)."""
    USER_MESSAGE_SENT = "user_message_sent"
    ASSISTANT_STREAMING = "assistant_streaming"
    TOOL_EXECUTING = "tool_executing"
    TURN_COMPLETING = "turn_completing"
    TURN_COMPLETED = "turn_completed"
    TURN_ERROR = "turn_error"
    TURN_CANCELLED = "turn_cancelled"
    TURN_MAX_TURNS = "turn_max_turns"


class ConnectionEventType(Enum):
    """Connection lifecycle event types."""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    RECONNECTED = "reconnected"
    HANDSHAKE_COMPLETE = "handshake_complete"


class FailureEventType(Enum):
    """Failure detection event types."""
    RATE_LIMIT_DETECTED = "rate_limit_detected"
    SERVER_ERROR_DETECTED = "server_error_detected"
    NETWORK_ERROR_DETECTED = "network_error_detected"
    TIMEOUT_DETECTED = "timeout_detected"
    CONNECTION_RESET_DETECTED = "connection_reset_detected"
    DNS_FAILURE_DETECTED = "dns_failure_detected"
    AUTH_ERROR_DETECTED = "auth_error_detected"
    STALE_STREAM_DETECTED = "stale_stream_detected"
    BACKEND_DISCONNECT_DETECTED = "backend_disconnect_detected"
    BACKEND_CRASH_DETECTED = "backend_crash_detected"
    UNKNOWN_FAILURE_DETECTED = "unknown_failure_detected"


class RecoveryEventType(Enum):
    """Recovery action event types."""
    RECOVERY_PLANNED = "recovery_planned"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETED = "recovery_completed"
    RECOVERY_FAILED = "recovery_failed"
    RECOVERY_CYCLE_STARTED = "recovery_cycle_started"
    RECOVERY_CYCLE_COMPLETED = "recovery_cycle_completed"
    BACKOFF_STARTED = "backoff_started"
    BACKOFF_COMPLETED = "backoff_completed"


@dataclass
class BaseEvent:
    """Base event with common fields."""
    timestamp: float = field(default_factory=time.time)
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    turn_id: Optional[str] = None
    source: str = "unknown"  # e.g., "acp", "hermes_callback", "monitor"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamEvent(BaseEvent):
    """Stream-related event with timing evidence."""
    event_type: StreamEventType = StreamEventType.CHUNK_RECEIVED
    chunk_index: int = 0
    chunk_text: Optional[str] = None
    reasoning_text: Optional[str] = None
    is_first_chunk: bool = False
    time_since_last_chunk: Optional[float] = None
    time_since_stream_start: Optional[float] = None
    visible_text_delivered: bool = False  # Non-whitespace text reached consumer


@dataclass
class ToolEvent(BaseEvent):
    """Tool execution event with state evidence."""
    event_type: ToolEventType = ToolEventType.TOOL_STARTED
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    output: Optional[str] = None
    error: Optional[str] = None
    is_partial: bool = False  # Streaming tool call not yet complete
    time_since_tool_start: Optional[float] = None


@dataclass
class TurnEvent(BaseEvent):
    """Turn lifecycle event."""
    event_type: TurnEventType = TurnEventType.USER_MESSAGE_SENT
    user_prompt: Optional[str] = None
    stop_reason: Optional[str] = None
    error_message: Optional[str] = None
    assistant_chunks_received: int = 0
    tool_calls_started: int = 0
    tool_calls_completed: int = 0
    tool_calls_failed: int = 0


@dataclass
class ConnectionEvent(BaseEvent):
    """Connection lifecycle event."""
    event_type: ConnectionEventType = ConnectionEventType.CONNECTED
    host: Optional[str] = None
    port: Optional[int] = None
    protocol: Optional[str] = None  # "acp", "websocket", "in-process"


@dataclass
class FailureEvent(BaseEvent):
    """Failure detection event with classification evidence."""
    event_type: FailureEventType = FailureEventType.UNKNOWN_FAILURE_DETECTED
    error_message: Optional[str] = None
    http_status: Optional[int] = None
    stop_reason: Optional[str] = None
    failure_type: Optional[str] = None  # Guardian FailureType value
    retryable: bool = False
    confidence: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryEvent(BaseEvent):
    """Recovery action event."""
    event_type: RecoveryEventType = RecoveryEventType.RECOVERY_PLANNED
    action: Optional[str] = None  # RecoveryAction value
    failure_type: Optional[str] = None
    attempt_number: int = 0
    cycle_number: int = 0
    delay_seconds: float = 0.0
    success: Optional[bool] = None
    safety_verdict: Optional[str] = None
    blocking_factors: List[str] = field(default_factory=list)


# Union type for all events
AgentEvent = StreamEvent | ToolEvent | TurnEvent | ConnectionEvent | FailureEvent | RecoveryEvent


def create_stream_event(
    event_type: StreamEventType,
    session_id: str,
    **kwargs
) -> StreamEvent:
    """Factory for stream events."""
    return StreamEvent(
        event_type=event_type,
        session_id=session_id,
        **kwargs
    )


def create_tool_event(
    event_type: ToolEventType,
    session_id: str,
    tool_name: str,
    **kwargs
) -> ToolEvent:
    """Factory for tool events."""
    return ToolEvent(
        event_type=event_type,
        session_id=session_id,
        tool_name=tool_name,
        **kwargs
    )


def create_turn_event(
    event_type: TurnEventType,
    session_id: str,
    **kwargs
) -> TurnEvent:
    """Factory for turn events."""
    return TurnEvent(
        event_type=event_type,
        session_id=session_id,
        **kwargs
    )


def create_failure_event(
    event_type: FailureEventType,
    session_id: str,
    error_message: str = "",
    **kwargs
) -> FailureEvent:
    """Factory for failure events."""
    return FailureEvent(
        event_type=event_type,
        session_id=session_id,
        error_message=error_message,
        **kwargs
    )


def create_recovery_event(
    event_type: RecoveryEventType,
    session_id: str,
    action: str,
    **kwargs
) -> RecoveryEvent:
    """Factory for recovery events."""
    return RecoveryEvent(
        event_type=event_type,
        session_id=session_id,
        action=action,
        **kwargs
    )