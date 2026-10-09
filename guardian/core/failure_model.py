"""
Generic failure model for Agent Guardian.

Defines failure taxonomy with evidence-based classification, separate from safety decisions.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime

from .types import (
    FailureCategory,
    FailureType,
    RecoveryAction,
    FailureClassification,
    TurnPhase,
    StreamPhase,
    StaleReason,
    SideEffectCategory,
    LadderDelaySource,
    BudgetExhaustedReason,
    AdapterCapability,
)

# Mapping from FailureType to FailureCategory (kept for backward compatibility)
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
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_message": self.error_message,
            "http_status": self.http_status,
            "stop_reason": self.stop_reason,
            "exception_type": self.exception_type,
            "stream_phase": self.stream_phase,
            "chunks_received": self.chunks_received,
            "visible_output": self.visible_output,
            "reasoning_only": self.reasoning_only,
            "tools_started": self.tools_started,
            "tools_completed": self.tools_completed,
            "tools_failed": self.tools_failed,
            "partial_tool_names": self.partial_tool_names,
            "tool_active": self.tool_active,
            "time_since_last_event": self.time_since_last_event,
            "time_since_first_chunk": self.time_since_first_chunk,
            "time_since_last_chunk": self.time_since_last_chunk,
            "time_since_tool_start": self.time_since_tool_start,
            "turn_phase": self.turn_phase,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "retry_count": self.retry_count,
            "auto_recovery_cycles": self.auto_recovery_cycles,
            "provider": self.provider,
            "model": self.model,
        }


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
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "retryable": self.retryable,
            "recommended_actions": self.recommended_actions,
            "requires_human": self.requires_human,
            "max_recovery_cycles": self.max_recovery_cycles,
            "base_backoff_seconds": self.base_backoff_seconds,
            "max_backoff_seconds": self.max_backoff_seconds,
            "respect_retry_after": self.respect_retry_after,
            "max_retry_after_seconds": self.max_retry_after_seconds,
            "provider_fallback_possible": self.provider_fallback_possible,
            "session_reattach_possible": self.session_reattach_possible,
            "stream_reconnect_possible": self.stream_reconnect_possible,
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
    """Complete failure classification with evidence and recovery hints."""
    failure_type: FailureType
    category: FailureCategory
    confidence: float  # 0.0 - 1.0
    evidence: FailureEvidence
    recovery_hints: RecoveryHint
    matched_patterns: List[str] = field(default_factory=list)
    raw_error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def is_retryable(self) -> bool:
        return self.recovery_hints.retryable
    
    @property
    def requires_human(self) -> bool:
        return self.recovery_hints.requires_human
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure_type": self.failure_type.value,
            "category": self.category.value,
            "confidence": self.confidence,
            "evidence": self.evidence.to_dict(),
            "recovery_hints": self.recovery_hints.to_dict(),
            "matched_patterns": self.matched_patterns,
            "raw_error": self.raw_error,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class FailurePattern:
    """Pattern for matching failures."""
    failure_type: FailureType
    patterns: List[str] = field(default_factory=list)  # Regex patterns
    http_status_codes: List[int] = field(default_factory=list)
    stop_reasons: List[str] = field(default_factory=list)
    exception_types: List[str] = field(default_factory=list)
    confidence: float = 0.8
    recovery_hints: Optional[RecoveryHint] = None  # Override defaults


# Generic failure patterns (agent-agnostic)
GENERIC_FAILURE_PATTERNS = [
    # Rate limiting - highest specificity
    FailurePattern(
        failure_type=FailureType.RATE_LIMIT,
        patterns=[
            r"429",
            r"rate.?limit",
            r"too.?many.?requests",
            r"quota.?exceeded",
            r"throttl",
            r"resource.?exhausted",
        ],
        http_status_codes=[429],
        confidence=0.95,
    ),
    
    # Transient provider (overloaded)
    FailurePattern(
        failure_type=FailureType.OVERLOADED,
        patterns=[
            r"overloaded",
            r"503",
            r"529",
            r"service.?unavailable",
            r"temporarily.?unavailable",
            r"capacity.?exceeded",
        ],
        http_status_codes=[503, 529],
        confidence=0.9,
    ),
    
    # Generic transient provider
    FailurePattern(
        failure_type=FailureType.TRANSIENT_PROVIDER,
        patterns=[
            r"provider.?error",
            r"provider.?temporary",
            r"provider.?unavailable",
            r"upstream.?error",
            r"upstream.?timeout",
            r"provider.?failed",
            r"llm.?error",
            r"model.?error",
        ],
        http_status_codes=[500, 502, 504],
        confidence=0.8,
    ),
    
    # Server errors (5xx)
    FailurePattern(
        failure_type=FailureType.SERVER_ERROR,
        patterns=[
            r"500",
            r"502",
            r"503",
            r"504",
            r"internal.?server.?error",
            r"bad.?gateway",
            r"service.?unavailable",
            r"gateway.?timeout",
            r"server.?error",
        ],
        http_status_codes=[500, 502, 503, 504],
        confidence=0.85,
    ),
    
    # Connection reset
    FailurePattern(
        failure_type=FailureType.CONNECTION_RESET,
        patterns=[
            r"connection.?reset",
            r"connection.?refused",
            r"econnreset",
            r"econnrefused",
            r"broken.?pipe",
            r"pipe.?closed",
            r"connection.?aborted",
            r"remote.?host.?closed",
        ],
        confidence=0.9,
    ),
    
    # DNS failure
    FailurePattern(
        failure_type=FailureType.DNS_FAILURE,
        patterns=[
            r"dns.?lookup.?failed",
            r"name.?or.?service.?not.?known",
            r"nodename.?nor.?servname.?provided",
            r"getaddrinfo.?failed",
        ],
        confidence=0.9,
    ),
    
    # Timeout
    FailurePattern(
        failure_type=FailureType.TIMEOUT,
        patterns=[
            r"timeout",
            r"timed.?out",
            r"408",
            r"request.?timeout",
            r"read.?timeout",
            r"write.?timeout",
            r"connection.?timeout",
        ],
        http_status_codes=[408],
        confidence=0.85,
    ),
    
    # Network transient
    FailurePattern(
        failure_type=FailureType.NETWORK_TRANSIENT,
        patterns=[
            r"network.?error",
            r"connection.?failed",
            r"connect.?timeout",
            r"temporary.?failure",
            r"try.?again",
        ],
        confidence=0.85,
    ),
    
    # Stream stale (no progress)
    FailurePattern(
        failure_type=FailureType.STREAM_STALE,
        patterns=[
            r"stale",
            r"no.?progress",
            r"no.?chunks",
            r"no.?output",
            r"stream.?stalled",
        ],
        confidence=0.7,
    ),
    
    # Stream truncated
    FailurePattern(
        failure_type=FailureType.STREAM_TRUNCATED,
        patterns=[
            r"stream.?truncated",
            r"incomplete.?response",
            r"partial.?response",
            r"truncated.?stream",
            r"connection.?closed.?unexpectedly",
        ],
        confidence=0.75,
    ),
    
    # Backend disconnect
    FailurePattern(
        failure_type=FailureType.BACKEND_DISCONNECT,
        patterns=[
            r"websocket.?closed",
            r"connection.?lost",
            r"disconnected",
            r"session.?ended",
        ],
        stop_reasons=["disconnected", "cancelled"],
        confidence=0.85,
    ),
    
    # Backend crash
    FailurePattern(
        failure_type=FailureType.BACKEND_CRASH,
        patterns=[
            r"process.?exited",
            r"process.?crashed",
            r"child.?process.?died",
            r"backend.?died",
        ],
        confidence=0.8,
    ),
    
    # Backend restart
    FailurePattern(
        failure_type=FailureType.BACKEND_RESTART,
        patterns=[
            r"backend.?restart",
            r"backend.?restarted",
            r"service.?restarted",
            r"process.?restarted",
            r"server.?restarted",
        ],
        confidence=0.8,
    ),
    
    # Auth failures
    FailurePattern(
        failure_type=FailureType.AUTHENTICATION_FAILURE,
        patterns=[
            r"401",
            r"403",
            r"unauthorized",
            r"forbidden",
            r"authentication.?failed",
            r"invalid.?token",
            r"token.?expired",
            r"credentials.?invalid",
            r"permission.?denied",
        ],
        http_status_codes=[401, 403],
        confidence=0.95,
    ),
    
    # Invalid configuration
    FailurePattern(
        failure_type=FailureType.INVALID_CONFIGURATION,
        patterns=[
            r"invalid.?config",
            r"configuration.?error",
            r"missing.?required",
            r"invalid.?parameter",
            r"bad.?request",
            r"400",
        ],
        http_status_codes=[400],
        confidence=0.8,
    ),
    
    # Invalid model
    FailurePattern(
        failure_type=FailureType.INVALID_MODEL,
        patterns=[
            r"model.?not.?found",
            r"invalid.?model",
            r"unknown.?model",
            r"model.?does.?not.?exist",
        ],
        confidence=0.9,
    ),
    
    # Tool failure
    FailurePattern(
        failure_type=FailureType.TOOL_FAILURE,
        patterns=[
            r"tool.?failed",
            r"tool.?error",
            r"execution.?failed",
            r"command.?failed",
        ],
        confidence=0.8,
    ),
    
    # Tool timeout
    FailurePattern(
        failure_type=FailureType.TOOL_TIMEOUT,
        patterns=[
            r"tool.?timeout",
            r"tool.?timed.?out",
            r"execution.?timeout",
            r"command.?timeout",
        ],
        confidence=0.85,
    ),
    
    # Permission failure
    FailurePattern(
        failure_type=FailureType.PERMISSION_FAILURE,
        patterns=[
            r"permission.?denied",
            r"access.?denied",
            r"not.?authorized",
            r"forbidden",
        ],
        confidence=0.9,
    ),
    
    # Content policy blocked
    FailurePattern(
        failure_type=FailureType.CONTENT_POLICY_BLOCKED,
        patterns=[
            r"content.?policy",
            r"safety.?filter",
            r"blocked.?by.?policy",
            r"violation",
        ],
        confidence=0.9,
    ),
    
    # User cancellation
    FailurePattern(
        failure_type=FailureType.USER_CANCELLED,
        patterns=[
            r"cancelled",
            r"user.?cancel",
            r"stop.?requested",
        ],
        stop_reasons=["cancelled", "user_cancelled"],
        confidence=0.95,
    ),
]


class FailureClassifier:
    """
    Classifies observed failures into actionable types with recovery hints.
    
    Uses multiple signals:
    - Error message text (regex patterns)
    - HTTP status codes
    - Stop reasons
    - Exception types
    - Stream/tool state evidence (for context, NOT for replay safety)
    """
    
    def __init__(self, custom_patterns: List[FailurePattern] = None):
        self.patterns = GENERIC_FAILURE_PATTERNS.copy()
        if custom_patterns:
            self.patterns.extend(custom_patterns)
        # Sort by confidence descending
        self.patterns.sort(key=lambda p: p.confidence, reverse=True)
    
    def classify(
        self,
        evidence: FailureEvidence,
    ) -> FailureClassification:
        """
        Classify a failure based on all available evidence.
        
        Returns a FailureClassification with recovery hints.
        Replay safety is NOT determined here - that's a separate safety check.
        """
        # Combine all signals into searchable text
        signals = []
        if evidence.error_message:
            signals.append(evidence.error_message.lower())
        if evidence.http_status:
            signals.append(str(evidence.http_status))
        if evidence.stop_reason:
            signals.append(evidence.stop_reason.lower())
        if evidence.exception_type:
            signals.append(evidence.exception_type.lower())
        
        signal_text = " ".join(signals)
        
        best_match = None
        best_confidence = 0.0
        matched_pattern_names = []
        
        for pattern in self.patterns:
            confidence = 0.0
            
            # Check regex patterns
            if evidence.error_message:
                for regex in pattern.patterns:
                    import re
                    if re.search(regex, evidence.error_message, re.IGNORECASE):
                        confidence = max(confidence, pattern.confidence)
                        break
            
            # Check HTTP status
            if evidence.http_status and pattern.http_status_codes:
                if evidence.http_status in pattern.http_status_codes:
                    confidence = max(confidence, pattern.confidence)
            
            # Check stop reasons
            if evidence.stop_reason and pattern.stop_reasons:
                if evidence.stop_reason.lower() in [s.lower() for s in pattern.stop_reasons]:
                    confidence = max(confidence, pattern.confidence)
            
            # Check exception types
            if evidence.exception_type and pattern.exception_types:
                if evidence.exception_type.lower() in [s.lower() for s in pattern.exception_types]:
                    confidence = max(confidence, pattern.confidence)
            
            if confidence > best_confidence:
                best_confidence = confidence
                best_match = pattern
        
        if best_match is None:
            # Unknown failure - fail closed
            return FailureClassification(
                failure_type=FailureType.UNKNOWN_FAILURE,
                category=FailureCategory.UNKNOWN,
                confidence=0.0,
                evidence=evidence,
                recovery_hints=DEFAULT_RECOVERY_HINTS[FailureType.UNKNOWN_FAILURE],
                matched_patterns=[],
                raw_error=evidence.error_message,
            )
        
        # Use pattern's recovery hints or defaults
        recovery_hints = best_match.recovery_hints or DEFAULT_RECOVERY_HINTS.get(
            best_match.failure_type,
            DEFAULT_RECOVERY_HINTS[FailureType.UNKNOWN_FAILURE]
        )
        
        category = FAILURE_TYPE_TO_CATEGORY.get(best_match.failure_type, FailureCategory.UNKNOWN)
        
        return FailureClassification(
            failure_type=best_match.failure_type,
            category=category,
            confidence=best_confidence,
            evidence=evidence,
            recovery_hints=recovery_hints,
            matched_patterns=[best_match.failure_type.value],
            raw_error=evidence.error_message,
        )
    
    def classify_from_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        stream_state: Optional[Dict] = None,
        tool_state: Optional[Dict] = None,
        turn_state: Optional[Dict] = None,
    ) -> Optional[FailureClassification]:
        """
        Classify failure from event data.
        
        Returns None if no failure detected.
        """
        # Build evidence from event
        evidence = FailureEvidence(
            error_message=event_data.get("error_message"),
            http_status=event_data.get("http_status"),
            stop_reason=event_data.get("stop_reason"),
            exception_type=event_data.get("exception_type"),
        )
        
        # Add stream evidence
        if stream_state:
            evidence.stream_phase = stream_state.get("phase")
            evidence.chunks_received = stream_state.get("chunks_received", 0)
            evidence.visible_output = stream_state.get("visible_output", False)
            evidence.reasoning_only = stream_state.get("reasoning_only", False)
            evidence.time_since_last_event = stream_state.get("time_since_last_event")
            evidence.time_since_first_chunk = stream_state.get("time_since_first_chunk")
            evidence.time_since_last_chunk = stream_state.get("time_since_last_chunk")
        
        # Add tool evidence
        if tool_state:
            evidence.tools_started = tool_state.get("tools_started", [])
            evidence.tools_completed = tool_state.get("tools_completed", [])
            evidence.tools_failed = tool_state.get("tools_failed", [])
            evidence.partial_tool_names = tool_state.get("partial_tool_names", [])
            evidence.tool_active = tool_state.get("tool_active", False)
            evidence.time_since_tool_start = tool_state.get("time_since_tool_start")
        
        # Add turn evidence
        if turn_state:
            evidence.turn_phase = turn_state.get("phase")
            evidence.turn_id = turn_state.get("turn_id")
            evidence.session_id = turn_state.get("session_id")
            evidence.retry_count = turn_state.get("recovery_attempts", 0)
            evidence.auto_recovery_cycles = turn_state.get("recovery_cycles", 0)
        
        # Check if this is actually a failure event
        failure_events = {
            "rate_limit", "server_error", "network_error", "provider_error",
            "timeout", "connection_reset", "dns_failure",
            "auth_error", "auth_failure", "config_error", "model_error",
            "tool_failed", "tool_timeout", "permission_denied",
            "turn_error", "turn_cancelled", "turn_max_turns",
            "stale_stream", "first_chunk_timeout", "chunk_gap", "tool_timeout",
            "backend_disconnect", "backend_crash", "backend_restart",
        }
        
        if event_type.lower() not in failure_events and event_type not in ["FAILURE_DETECTED", "STALE_DETECTED"]:
            return None
        
        return self.classify(evidence)