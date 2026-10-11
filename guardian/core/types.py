"""
Core types and enums for Guardian state machine.

This is the single source of truth for all core type definitions.
"""

from enum import Enum
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime


class GuardianState(Enum):
    """High-level Guardian lifecycle states."""
    IDLE = "idle"                    # Not connected to any agent
    OBSERVING = "observing"          # Connected, watching events
    STREAMING = "streaming"          # Assistant output actively streaming
    TOOL_ACTIVE = "tool_active"      # Tool execution in progress
    COMPLETING = "completing"        # Turn ending (waiting for final events)
    COMPLETED = "completed"          # Turn completed successfully
    POSSIBLE_FAILURE = "possible_failure"  # Failure detected, assessing
    RECOVERY_WAIT = "recovery_wait"  # Waiting before recovery attempt
    RECOVERY_ATTEMPT = "recovery_attempt"  # Actively attempting recovery
    VERIFYING = "verifying"          # Verifying recovery result
    RECOVERED = "recovered"          # Recovery successful
    FAILED_PERMANENTLY = "failed_permanently"  # No safe recovery possible
    UNKNOWN_UNSAFE = "unknown_unsafe"  # Ambiguous state, fail closed


class SafetyVerdict(Enum):
    """Result of safety check."""
    SAFE = "safe"                          # Recovery allowed
    UNSAFE = "unsafe"                      # Recovery blocked
    AMBIGUOUS = "ambiguous"                # Cannot determine, fail closed


class FailureCategory(Enum):
    """High-level failure categories."""
    TRANSIENT_PROVIDER = "transient_provider"      # Rate limit, overload, temporary provider issues
    SERVER_ERROR = "server_error"                   # 5xx errors
    NETWORK = "network"                             # Connection, DNS, timeout
    STREAM = "stream"                               # Stale, truncated, gaps
    BACKEND = "backend"                             # Disconnect, crash, restart
    AUTH_CONFIG = "auth_config"                     # Auth, config, model errors
    TOOL = "tool"                                   # Tool execution failures
    PERMISSION = "permission"                       # Access denied, content policy
    USER = "user"                                   # Cancellation
    UNKNOWN = "unknown"                             # Unclassifiable


class FailureType(Enum):
    """Specific failure types with recovery hints."""
    # Transient provider failures
    RATE_LIMIT = "rate_limit"
    TRANSIENT_PROVIDER = "transient_provider"
    OVERLOADED = "overloaded"
    
    # Server failures
    SERVER_ERROR = "server_error"
    
    # Network failures
    NETWORK_TRANSIENT = "network_transient"
    CONNECTION_RESET = "connection_reset"
    DNS_FAILURE = "dns_failure"
    TIMEOUT = "timeout"
    
    # Stream failures
    STREAM_STALE = "stream_stale"
    STREAM_TRUNCATED = "stream_truncated"
    FIRST_CHUNK_TIMEOUT = "first_chunk_timeout"
    CHUNK_GAP = "chunk_gap"
    
    # Backend failures
    BACKEND_DISCONNECT = "backend_disconnect"
    BACKEND_CRASH = "backend_crash"
    BACKEND_RESTART = "backend_restart"
    
    # Auth/Config failures
    AUTHENTICATION_FAILURE = "authentication_failure"
    INVALID_CONFIGURATION = "invalid_configuration"
    INVALID_MODEL = "invalid_model"
    
    # Permission/Tool failures
    PERMISSION_FAILURE = "permission_failure"
    TOOL_FAILURE = "tool_failure"
    TOOL_TIMEOUT = "tool_timeout"
    CONTENT_POLICY_BLOCKED = "content_policy_blocked"
    
    # User actions
    USER_CANCELLED = "user_cancelled"
    
    # Unknown
    UNKNOWN_FAILURE = "unknown_failure"


class RecoveryAction(Enum):
    """Possible recovery actions."""
    NONE = "none"                          # No action needed
    WAIT_AND_RETRY = "wait_and_retry"      # Wait with backoff, then retry
    CANCEL_AND_REPLAY = "cancel_and_replay"  # Cancel in-flight, replay prompt
    RECONNECT_STREAM = "reconnect_stream"  # Re-establish streaming connection
    RECONNECT_ACP = "reconnect_acp"        # Re-establish ACP connection (legacy alias)
    REATTACH_SESSION = "reattach_session"  # Load session from backend
    RELOAD_SESSION = "reload_session"      # Load session from backend (legacy alias)
    FORK_SESSION = "fork_session"          # Fork at safe point
    SWITCH_PROVIDER = "switch_provider"    # Switch to different provider
    WAIT_FOR_BACKEND = "wait_for_backend"  # Wait for backend to restart
    RESTART_AGENT = "restart_agent"        # Restart agent process
    HUMAN_INTERVENTION = "human_intervention"  # Require human decision
    NO_SAFE_RECOVERY = "no_safe_recovery"  # Cannot recover safely


class SafetyMode(Enum):
    """Guardian safety operating modes."""
    OBSERVE_ONLY = "observe_only"      # Never perform recovery
    DRY_RUN = "dry_run"                # Detect what WOULD happen, but don't act
    AUTO_RECOVER = "auto_recover"      # Perform verified-safe recovery only


class TurnPhase(Enum):
    """Phase of current turn."""
    IDLE = "idle"                      # No active turn
    USER_MESSAGE_SENT = "user_message_sent"  # Prompt sent, waiting for response
    ASSISTANT_STREAMING = "assistant_streaming"  # Assistant chunks arriving
    TOOL_EXECUTING = "tool_executing"  # Tool running
    TOOL_COMPLETING = "tool_completing"  # Tool done, waiting for result
    TURN_ENDING = "turn_ending"        # Stop reason received, finalizing
    TURN_COMPLETE = "turn_complete"    # Turn finished
    TURN_ERROR = "turn_error"          # Turn ended with error
    TURN_CANCELLED = "turn_cancelled"  # Turn cancelled


class StreamPhase(Enum):
    """Stream lifecycle phases."""
    IDLE = "idle"
    STARTING = "starting"
    WAITING_FIRST_CHUNK = "waiting_first_chunk"
    STREAMING = "streaming"
    STALLED = "stalled"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


class StaleReason(Enum):
    """Reasons for stream being considered stale."""
    FIRST_CHUNK_TIMEOUT = "first_chunk_timeout"
    CHUNK_GAP = "chunk_gap"
    TOOL_TIMEOUT = "tool_timeout"
    GENERAL_STALE = "general_stale"


class SideEffectCategory(Enum):
    """Categories of observed side effects."""
    NO_EVIDENCE = "no_evidence"
    ASSISTANT_CHUNKS = "assistant_chunks"
    VISIBLE_OUTPUT = "visible_output"
    REASONING_ONLY = "reasoning_only"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    PARTIAL_TOOL_ARGS = "partial_tool_args"
    UNKNOWN_TOOL_STATE = "unknown_tool_state"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


class LadderDelaySource(Enum):
    """Source of the delay value."""
    SCHEDULE = "schedule"           # From configured schedule
    RETRY_AFTER = "retry_after"     # From provider Retry-After header
    JITTERED_SCHEDULE = "jittered_schedule"  # Schedule with jitter applied


class BudgetExhaustedReason(Enum):
    """Reasons why recovery budget was exhausted."""
    MAX_ATTEMPTS = "max_attempts"
    MAX_CYCLES = "max_cycles"
    MAX_TOTAL_TIME = "max_total_time"
    MAX_BACKOFF_DELAY = "max_backoff_delay"
    PERMANENT_FAILURE = "permanent_failure"
    SAFETY_BLOCKED = "safety_blocked"
    CANCELLED = "cancelled"


class AdapterCapability(Enum):
    """Capabilities an agent adapter may provide."""
    # Observation
    OBSERVE_STREAM = "observe_stream"
    OBSERVE_REASONING = "observe_reasoning"
    OBSERVE_TOOL_PROGRESS = "observe_tool_progress"
    OBSERVE_TURN_LIFECYCLE = "observe_turn_lifecycle"
    
    # Stream recovery
    RECONNECT_STREAM = "reconnect_stream"
    CANCEL_STREAM = "cancel_stream"
    
    # Session recovery
    REATTACH_SESSION = "reattach_session"
    FORK_SESSION = "fork_session"
    LOAD_SESSION = "load_session"
    SAVE_SESSION = "save_session"
    
    # Provider recovery
    SWITCH_PROVIDER = "switch_provider"
    GET_PROVIDER_LIST = "get_provider_list"
    
    # Agent process
    RESTART_AGENT = "restart_agent"
    GET_AGENT_STATUS = "get_agent_status"
    IS_AGENT_ALIVE = "is_agent_alive"
    
    # Turn control
    CANCEL_TURN = "cancel_turn"
    RETRY_REQUEST = "retry_request"
    REPLAY_TURN = "replay_turn"
    WAIT_FOR_BACKEND = "wait_for_backend"
    
    # Introspection
    GET_PARTIAL_TOOL_NAMES = "get_partial_tool_names"
    GET_TOOL_STATE = "get_tool_state"
    GET_SESSION_STATE = "get_session_state"
    GET_TURN_CONTEXT = "get_turn_context"
    
    # Safety
    VERIFY_SESSION_IDENTITY = "verify_session_identity"
    VERIFY_SESSION_EXISTS = "verify_session_exists"
    
    # Monitoring
    DETECT_DISCONNECT = "detect_disconnect"
    DETECT_CRASH = "detect_crash"
    DETECT_STALE = "detect_stale"


@dataclass
class FailureClassification:
    """Result of failure classification."""
    failure_type: FailureType
    retryable: bool
    replay_safe: bool
    requires_human: bool
    recommended_action: RecoveryAction
    confidence: float  # 0.0 - 1.0
    details: dict = field(default_factory=dict)
    raw_error: Optional[str] = None


@dataclass
class TurnContext:
    """Complete context for current turn."""
    session_id: str
    run_id: Optional[str] = None
    phase: TurnPhase = TurnPhase.IDLE
    assistant_chunks_received: int = 0
    tool_calls_started: int = 0
    tool_calls_completed: int = 0
    tool_calls_failed: int = 0
    last_event_timestamp: float = field(default_factory=lambda: __import__('time').time())
    last_assistant_chunk_time: Optional[float] = None
    last_tool_event_time: Optional[float] = None
    first_chunk_time: Optional[float] = None
    user_prompt: Optional[str] = None
    stop_reason: Optional[str] = None
    error_message: Optional[str] = None
    # State generation for TOCTOU protection
    state_generation: int = 0
    # Verified fresh flag - True only if prior execution state has been verified
    # (e.g., via persistence). Defaults to False; without persistence, we cannot
    # verify no restart occurred, so we must treat state as potentially ambiguous.
    verified_fresh: bool = False
    # Deduplication tracking
    _seen_tool_call_ids: set = field(default_factory=set)
    _completed_tool_call_ids: set = field(default_factory=set)
    _failed_tool_call_ids: set = field(default_factory=set)

    def has_assistant_output(self) -> bool:
        return self.assistant_chunks_received > 0

    def has_tool_activity(self) -> bool:
        return self.tool_calls_started > 0

    def is_safe_to_replay(self) -> bool:
        """Return True only if NO side effects have been observed."""
        return not self.has_assistant_output() and not self.has_tool_activity()

    def increment_generation(self) -> int:
        """Increment state generation for TOCTOU protection."""
        self.state_generation += 1
        return self.state_generation


@dataclass
class RecoveryAttempt:
    """Record of a recovery attempt."""
    attempt_number: int
    failure_type: FailureType
    action_taken: RecoveryAction
    timestamp: datetime
    delay_seconds: float
    success: bool
    details: dict = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class GuardianMetrics:
    """Runtime metrics for monitoring."""
    total_turns: int = 0
    successful_turns: int = 0
    failed_turns: int = 0
    recovery_attempts: int = 0
    successful_recoveries: int = 0
    failed_recoveries: int = 0
    total_downtime_seconds: float = 0.0
    last_recovery_time: Optional[datetime] = None