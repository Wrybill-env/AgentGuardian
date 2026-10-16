"""
Core types and enums for Guardian state machine.

This is the single source of truth for all core type definitions.
"""

from enum import Enum
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List


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
class FailureEvidence:
    """Evidence supporting a failure classification."""
    # Source signals
    error_message: Optional[str] = None
    http_status: Optional[int] = None
    stop_reason: Optional[str] = None
    exception_type: Optional[str] = None

    # Stream state at failure
    stream_phase: Optional[str] = None
    chunks_received: int = 0
    visible_output: bool = False
    reasoning_only: bool = False

    # Tool state at failure
    tools_started: List[str] = field(default_factory=list)
    tools_completed: List[str] = field(default_factory=list)
    tools_failed: List[str] = field(default_factory=list)
    partial_tool_names: List[str] = field(default_factory=list)
    tool_active: bool = False

    # Timing
    time_since_last_event: Optional[float] = None
    time_since_first_chunk: Optional[float] = None
    time_since_last_chunk: Optional[float] = None
    time_since_tool_start: Optional[float] = None

    # Turn context
    turn_phase: Optional[str] = None
    turn_id: Optional[str] = None
    session_id: Optional[str] = None

    # Additional context
    retry_count: int = 0
    auto_recovery_cycles: int = 0
    provider: Optional[str] = None
    model: Optional[str] = None


@dataclass
class RecoveryHint:
    """Recovery hints derived from failure classification."""
    retryable: bool = False
    recommended_actions: List[str] = field(default_factory=list)  # RecoveryAction values
    requires_human: bool = True
    max_recovery_cycles: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    respect_retry_after: bool = True
    max_retry_after_seconds: float = 120.0
    provider_fallback_possible: bool = False
    session_reattach_possible: bool = False
    stream_reconnect_possible: bool = False


# Mapping from FailureType to FailureCategory
FAILURE_TYPE_TO_CATEGORY = {
    FailureType.RATE_LIMIT: FailureCategory.TRANSIENT_PROVIDER,
    FailureType.TRANSIENT_PROVIDER: FailureCategory.TRANSIENT_PROVIDER,
    FailureType.OVERLOADED: FailureCategory.TRANSIENT_PROVIDER,
    FailureType.SERVER_ERROR: FailureCategory.SERVER_ERROR,
    FailureType.NETWORK_TRANSIENT: FailureCategory.NETWORK,
    FailureType.CONNECTION_RESET: FailureCategory.NETWORK,
    FailureType.DNS_FAILURE: FailureCategory.NETWORK,
    FailureType.TIMEOUT: FailureCategory.NETWORK,
    FailureType.STREAM_STALE: FailureCategory.STREAM,
    FailureType.STREAM_TRUNCATED: FailureCategory.STREAM,
    FailureType.FIRST_CHUNK_TIMEOUT: FailureCategory.STREAM,
    FailureType.CHUNK_GAP: FailureCategory.STREAM,
    FailureType.BACKEND_DISCONNECT: FailureCategory.BACKEND,
    FailureType.BACKEND_CRASH: FailureCategory.BACKEND,
    FailureType.BACKEND_RESTART: FailureCategory.BACKEND,
    FailureType.AUTHENTICATION_FAILURE: FailureCategory.AUTH_CONFIG,
    FailureType.INVALID_CONFIGURATION: FailureCategory.AUTH_CONFIG,
    FailureType.INVALID_MODEL: FailureCategory.AUTH_CONFIG,
    FailureType.TOOL_FAILURE: FailureCategory.TOOL,
    FailureType.TOOL_TIMEOUT: FailureCategory.TOOL,
    FailureType.PERMISSION_FAILURE: FailureCategory.PERMISSION,
    FailureType.CONTENT_POLICY_BLOCKED: FailureCategory.PERMISSION,
    FailureType.USER_CANCELLED: FailureCategory.USER,
    FailureType.UNKNOWN_FAILURE: FailureCategory.UNKNOWN,
}


# Default recovery hints per failure type
DEFAULT_RECOVERY_HINTS: Dict[FailureType, RecoveryHint] = {
    FailureType.RATE_LIMIT: RecoveryHint(
        retryable=True,
        recommended_actions=["wait_and_retry", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=5,
        base_backoff_seconds=5.0,
        max_backoff_seconds=120.0,
        respect_retry_after=True,
        max_retry_after_seconds=120.0,
        stream_reconnect_possible=True,
    ),
    FailureType.TRANSIENT_PROVIDER: RecoveryHint(
        retryable=True,
        recommended_actions=["wait_and_retry", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=5,
        base_backoff_seconds=5.0,
        max_backoff_seconds=120.0,
        respect_retry_after=True,
        max_retry_after_seconds=120.0,
        stream_reconnect_possible=True,
        provider_fallback_possible=True,
    ),
    FailureType.OVERLOADED: RecoveryHint(
        retryable=True,
        recommended_actions=["wait_and_retry", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=5,
        base_backoff_seconds=15.0,
        max_backoff_seconds=60.0,
        respect_retry_after=True,
        max_retry_after_seconds=120.0,
        stream_reconnect_possible=True,
        provider_fallback_possible=True,
    ),
    FailureType.SERVER_ERROR: RecoveryHint(
        retryable=True,
        recommended_actions=["wait_and_retry", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=2.0,
        max_backoff_seconds=60.0,
        respect_retry_after=True,
        max_retry_after_seconds=120.0,
        stream_reconnect_possible=True,
    ),
    FailureType.NETWORK_TRANSIENT: RecoveryHint(
        retryable=True,
        recommended_actions=["wait_and_retry", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=1.0,
        max_backoff_seconds=30.0,
        stream_reconnect_possible=True,
    ),
    FailureType.CONNECTION_RESET: RecoveryHint(
        retryable=True,
        recommended_actions=["reconnect_stream", "wait_and_retry"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=1.0,
        max_backoff_seconds=30.0,
        stream_reconnect_possible=True,
    ),
    FailureType.DNS_FAILURE: RecoveryHint(
        retryable=True,
        recommended_actions=["wait_and_retry"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=2.0,
        max_backoff_seconds=60.0,
    ),
    FailureType.TIMEOUT: RecoveryHint(
        retryable=True,
        recommended_actions=["wait_and_retry", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=2.0,
        max_backoff_seconds=60.0,
        stream_reconnect_possible=True,
    ),
    FailureType.STREAM_STALE: RecoveryHint(
        retryable=True,
        recommended_actions=["cancel_and_replay", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=5.0,
        max_backoff_seconds=60.0,
        stream_reconnect_possible=True,
    ),
    FailureType.STREAM_TRUNCATED: RecoveryHint(
        retryable=True,
        recommended_actions=["cancel_and_replay", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=2.0,
        max_backoff_seconds=60.0,
        stream_reconnect_possible=True,
    ),
    FailureType.FIRST_CHUNK_TIMEOUT: RecoveryHint(
        retryable=True,
        recommended_actions=["cancel_and_replay", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=5.0,
        max_backoff_seconds=60.0,
        stream_reconnect_possible=True,
    ),
    FailureType.CHUNK_GAP: RecoveryHint(
        retryable=True,
        recommended_actions=["cancel_and_replay", "reconnect_stream"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=5.0,
        max_backoff_seconds=60.0,
        stream_reconnect_possible=True,
    ),
    FailureType.BACKEND_DISCONNECT: RecoveryHint(
        retryable=True,
        recommended_actions=["reconnect_stream", "reattach_session", "wait_for_backend"],
        requires_human=False,
        max_recovery_cycles=5,
        base_backoff_seconds=5.0,
        max_backoff_seconds=120.0,
        stream_reconnect_possible=True,
        session_reattach_possible=True,
    ),
    FailureType.BACKEND_CRASH: RecoveryHint(
        retryable=True,
        recommended_actions=["wait_for_backend", "reattach_session"],
        requires_human=False,
        max_recovery_cycles=5,
        base_backoff_seconds=10.0,
        max_backoff_seconds=300.0,
        session_reattach_possible=True,
    ),
    FailureType.BACKEND_RESTART: RecoveryHint(
        retryable=True,
        recommended_actions=["wait_for_backend", "reattach_session"],
        requires_human=False,
        max_recovery_cycles=3,
        base_backoff_seconds=5.0,
        max_backoff_seconds=60.0,
        session_reattach_possible=True,
    ),
    FailureType.AUTHENTICATION_FAILURE: RecoveryHint(
        retryable=False,
        recommended_actions=["human_intervention"],
        requires_human=True,
    ),
    FailureType.INVALID_CONFIGURATION: RecoveryHint(
        retryable=False,
        recommended_actions=["human_intervention"],
        requires_human=True,
    ),
    FailureType.INVALID_MODEL: RecoveryHint(
        retryable=False,
        recommended_actions=["human_intervention", "switch_provider"],
        requires_human=True,
        provider_fallback_possible=True,
    ),
    FailureType.TOOL_FAILURE: RecoveryHint(
        retryable=False,
        recommended_actions=["human_intervention"],
        requires_human=True,
    ),
    FailureType.TOOL_TIMEOUT: RecoveryHint(
        retryable=False,
        recommended_actions=["human_intervention"],
        requires_human=True,
    ),
    FailureType.PERMISSION_FAILURE: RecoveryHint(
        retryable=False,
        recommended_actions=["human_intervention"],
        requires_human=True,
    ),
    FailureType.CONTENT_POLICY_BLOCKED: RecoveryHint(
        retryable=False,
        recommended_actions=["human_intervention"],
        requires_human=True,
    ),
    FailureType.USER_CANCELLED: RecoveryHint(
        retryable=False,
        recommended_actions=["human_intervention"],
        requires_human=True,
    ),
    FailureType.UNKNOWN_FAILURE: RecoveryHint(
        retryable=False,
        recommended_actions=["human_intervention"],
        requires_human=True,
    ),
}


@dataclass
class FailureClassification:
    """Result of failure classification with recovery hints."""
    failure_type: FailureType
    retryable: bool
    replay_safe: bool
    requires_human: bool
    recommended_action: RecoveryAction
    confidence: float  # 0.0 - 1.0
    details: dict = field(default_factory=dict)
    raw_error: Optional[str] = None
    # Extended fields for recovery planner compatibility
    category: FailureCategory = FailureCategory.UNKNOWN
    recovery_hints: Optional['RecoveryHint'] = None
    evidence: Optional['FailureEvidence'] = None
    matched_patterns: list = field(default_factory=list)
    timestamp: Optional[datetime] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if self.recovery_hints is None:
            # Provide a minimal default recovery hint
            self.recovery_hints = DEFAULT_RECOVERY_HINTS.get(self.failure_type, DEFAULT_RECOVERY_HINTS[FailureType.UNKNOWN_FAILURE])
        if self.evidence is None:
            self.evidence = FailureEvidence()
        if self.category == FailureCategory.UNKNOWN and self.failure_type != FailureType.UNKNOWN_FAILURE:
            self.category = FAILURE_TYPE_TO_CATEGORY.get(self.failure_type, FailureCategory.UNKNOWN)


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
    #
    # CONTRACT: the ONLY legitimate writer is persistence-backed reconstruction
    # (guardian.core.persistence.create_turn_context_from_session), which sets it
    # True only when validated persisted state was loaded for this session.
    # Agent adapters MUST NOT set this flag: a live session cannot prove that no
    # restart/reattach gap occurred, and adapter self-assertion of freshness is
    # not independent evidence. Replay actions (WAIT_AND_RETRY /
    # CANCEL_AND_REPLAY) are denied by the safety gate unless this is True.
    verified_fresh: bool = False
    # Evaluated freshness evidence backing verified_fresh (diagnostics/provenance).
    # Populated by guardian.core.freshness.apply_freshness. Never set by adapters.
    freshness_evidence: Optional[Any] = None
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