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
    FailureEvidence,
    RecoveryHint,
    FAILURE_TYPE_TO_CATEGORY,
    DEFAULT_RECOVERY_HINTS,
)


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
