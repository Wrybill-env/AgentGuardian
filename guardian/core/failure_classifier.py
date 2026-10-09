"""
Failure classification for Guardian.

Centralized logic for classifying observed errors/events into
actionable failure types with recovery recommendations.
"""

import re
from typing import Optional
from dataclasses import dataclass

from .types import (
    FailureType,
    RecoveryAction,
    FailureClassification,
    TurnContext,
)


@dataclass
class FailurePattern:
    """Pattern for matching failure types."""
    failure_type: FailureType
    patterns: list[str]  # Regex patterns to match
    http_status_codes: list[int] = None
    stop_reasons: list[str] = None
    retryable: bool = False
    replay_safe: bool = False
    requires_human: bool = True
    recommended_action: RecoveryAction = RecoveryAction.HUMAN_INTERVENTION
    confidence: float = 0.8


# Failure patterns ordered by specificity (most specific first)
FAILURE_PATTERNS = [
    # Rate limiting - highest specificity
    FailurePattern(
        failure_type=FailureType.RATE_LIMIT,
        patterns=[
            r"429",
            r"rate.?limit",
            r"too.?many.?requests",
            r"quota.?exceeded",
            r"throttl",
        ],
        http_status_codes=[429],
        retryable=True,
        replay_safe=True,  # Only safe if no tools/assistant output yet
        requires_human=False,
        recommended_action=RecoveryAction.WAIT_AND_RETRY,
        confidence=0.95,
    ),
    
    # Overloaded provider (503/529) - Hermes-specific pattern
    FailurePattern(
        failure_type=FailureType.OVERLOADED,
        patterns=[
            r"overloaded",
            r"529",
            r"temporarily.?unavailable",
            r"capacity.?exceeded",
        ],
        http_status_codes=[529],
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.WAIT_AND_RETRY,
        confidence=0.9,
    ),
    
    # Unknown/ambiguous errors - higher priority than server errors
    FailurePattern(
        failure_type=FailureType.UNKNOWN_FAILURE,
        patterns=[
            r"unknown.?error",
            r"ambiguous",
            r"unexpected.?error",
            r"unclassified",
        ],
        http_status_codes=[],
        retryable=False,
        replay_safe=False,
        requires_human=True,
        recommended_action=RecoveryAction.NO_SAFE_RECOVERY,
        confidence=0.9,
    ),
    
    # Network transient failures
    FailurePattern(
        failure_type=FailureType.NETWORK_TRANSIENT,
        patterns=[
            r"network.?error",
            r"connection.?failed",
            r"connect.?timeout",
            r"dns.?error",
            r"name.?resolution.?failed",
            r"temporary.?failure",
            r"try.?again",
        ],
        http_status_codes=[],
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.WAIT_AND_RETRY,
        confidence=0.85,
    ),
    
    # Transient provider failures (generic provider-side transient errors)
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
        http_status_codes=[500, 502, 503, 504],
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.WAIT_AND_RETRY,
        confidence=0.8,
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
        http_status_codes=[],
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.RECONNECT_ACP,
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
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.WAIT_AND_RETRY,
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
        ],
        http_status_codes=[408],
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.WAIT_AND_RETRY,
        confidence=0.85,
    ),
    
    # Server errors (5xx) - higher confidence than TRANSIENT_PROVIDER to take precedence
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
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.WAIT_AND_RETRY,
        confidence=0.85,
    ),
    
    # Authentication failures
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
        retryable=False,  # Don't retry auth failures automatically
        replay_safe=False,
        requires_human=True,
        recommended_action=RecoveryAction.HUMAN_INTERVENTION,
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
        retryable=False,
        replay_safe=False,
        requires_human=True,
        recommended_action=RecoveryAction.HUMAN_INTERVENTION,
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
        retryable=False,
        replay_safe=False,
        requires_human=True,
        recommended_action=RecoveryAction.HUMAN_INTERVENTION,
        confidence=0.9,
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
        retryable=True,
        replay_safe=True,  # Only if no tools/assistant output
        requires_human=False,
        recommended_action=RecoveryAction.CANCEL_AND_REPLAY,
        confidence=0.7,
    ),
    
    # Stream truncated (incomplete response)
        FailurePattern(
            failure_type=FailureType.STREAM_TRUNCATED,
            patterns=[
                r"stream.?truncated",
                r"incomplete.?response",
                r"partial.?response",
                r"truncated.?stream",
                r"connection.?closed.?unexpectedly",
            ],
            retryable=True,
            replay_safe=True,
            requires_human=False,
            recommended_action=RecoveryAction.CANCEL_AND_REPLAY,
            confidence=0.75,
        ),

        # First chunk timeout
        FailurePattern(
            failure_type=FailureType.FIRST_CHUNK_TIMEOUT,
            patterns=[
                r"first.?chunk.?timeout",
                r"no.?first.?chunk",
                r"waiting.?for.?first.?chunk",
            ],
            retryable=True,
            replay_safe=True,
            requires_human=False,
            recommended_action=RecoveryAction.CANCEL_AND_REPLAY,
            confidence=0.7,
        ),

        # Chunk gap
        FailurePattern(
            failure_type=FailureType.CHUNK_GAP,
            patterns=[
                r"chunk.?gap",
                r"gap.?between.?chunks",
                r"no.?chunks.?for",
            ],
            retryable=True,
            replay_safe=True,
            requires_human=False,
            recommended_action=RecoveryAction.CANCEL_AND_REPLAY,
            confidence=0.7,
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
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.RECONNECT_ACP,
        confidence=0.85,
    ),
    
    # Backend crash (process exit)
    FailurePattern(
        failure_type=FailureType.BACKEND_CRASH,
        patterns=[
            r"process.?exited",
            r"process.?crashed",
            r"child.?process.?died",
            r"backend.?died",
        ],
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.WAIT_FOR_BACKEND,
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
        retryable=True,
        replay_safe=True,
        requires_human=False,
        recommended_action=RecoveryAction.WAIT_FOR_BACKEND,
        confidence=0.8,
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
        retryable=False,
        replay_safe=False,
        requires_human=True,
        recommended_action=RecoveryAction.HUMAN_INTERVENTION,
        confidence=0.95,
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
        retryable=False,  # Tool may have had side effects
        replay_safe=False,
        requires_human=True,
        recommended_action=RecoveryAction.HUMAN_INTERVENTION,
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
        retryable=False,  # Tool may have had side effects before timeout
        replay_safe=False,
        requires_human=True,
        recommended_action=RecoveryAction.HUMAN_INTERVENTION,
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
        retryable=False,
        replay_safe=False,
        requires_human=True,
        recommended_action=RecoveryAction.HUMAN_INTERVENTION,
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
        retryable=False,
        replay_safe=False,
        requires_human=True,
        recommended_action=RecoveryAction.HUMAN_INTERVENTION,
        confidence=0.9,
    ),
]


class FailureClassifier:
    """
    Classifies observed failures into actionable types.
    
    Uses multiple signals:
    - Error message text (regex patterns)
    - HTTP status codes
    - ACP stop reasons
    - Turn context (phase, tool activity, etc.)
    """
    
    def __init__(self, custom_patterns: list = None):
        self.patterns = FAILURE_PATTERNS.copy()
        if custom_patterns:
            self.patterns.extend(custom_patterns)
        # Sort by confidence descending
        self.patterns.sort(key=lambda p: p.confidence, reverse=True)
    
    def classify(
        self,
        error_message: Optional[str] = None,
        http_status: Optional[int] = None,
        stop_reason: Optional[str] = None,
        turn_context: Optional[TurnContext] = None,
    ) -> FailureClassification:
        """
        Classify a failure based on all available signals.
        
        Returns a FailureClassification with recommended action.
        """
        # Combine all signals into searchable text
        signals = []
        if error_message:
            signals.append(error_message.lower())
        if http_status:
            signals.append(str(http_status))
        if stop_reason:
            signals.append(stop_reason.lower())
        if turn_context and turn_context.error_message:
            signals.append(turn_context.error_message.lower())
        
        signal_text = " ".join(signals)
        
        best_match = None
        best_confidence = 0.0
        
        for pattern in self.patterns:
            confidence = 0.0
            
            # Check regex patterns
            if error_message:
                for regex in pattern.patterns:
                    if re.search(regex, error_message, re.IGNORECASE):
                        confidence = max(confidence, pattern.confidence)
                        break
            
            # Check HTTP status
            if http_status and pattern.http_status_codes:
                if http_status in pattern.http_status_codes:
                    confidence = max(confidence, pattern.confidence)
            
            # Check stop reasons
            if stop_reason and pattern.stop_reasons:
                if stop_reason.lower() in [s.lower() for s in pattern.stop_reasons]:
                    confidence = max(confidence, pattern.confidence)
            
            if confidence > best_confidence:
                best_confidence = confidence
                best_match = pattern
        
        if best_match is None:
            # Unknown failure - fail closed
            return FailureClassification(
                failure_type=FailureType.UNKNOWN_FAILURE,
                retryable=False,
                replay_safe=False,
                requires_human=True,
                recommended_action=RecoveryAction.NO_SAFE_RECOVERY,
                confidence=0.0,
                details={"reason": "No pattern matched"},
                raw_error=error_message,
            )
        
        # Adjust replay_safe based on turn context
        replay_safe = best_match.replay_safe
        if turn_context and not turn_context.is_safe_to_replay():
            replay_safe = False
        
        # If not replay_safe, can't do WAIT_AND_RETRY or CANCEL_AND_REPLAY
        recommended_action = best_match.recommended_action
        if not replay_safe and recommended_action in (
            RecoveryAction.WAIT_AND_RETRY,
            RecoveryAction.CANCEL_AND_REPLAY,
            RecoveryAction.RELOAD_SESSION,
            RecoveryAction.FORK_SESSION,
        ):
            recommended_action = RecoveryAction.NO_SAFE_RECOVERY
        
        return FailureClassification(
            failure_type=best_match.failure_type,
            retryable=best_match.retryable,
            replay_safe=replay_safe,
            requires_human=best_match.requires_human,
            recommended_action=recommended_action,
            confidence=best_confidence,
            details={
                "matched_patterns": [p.failure_type.value for p in self.patterns 
                                   if self._pattern_matches(p, error_message, http_status, stop_reason)],
            },
            raw_error=error_message,
        )
    
    def _pattern_matches(
        self,
        pattern: FailurePattern,
        error_message: Optional[str],
        http_status: Optional[int],
        stop_reason: Optional[str],
    ) -> bool:
        """Check if a pattern matches any signal."""
        if error_message:
            for regex in pattern.patterns:
                if re.search(regex, error_message, re.IGNORECASE):
                    return True
        if http_status and pattern.http_status_codes:
            if http_status in pattern.http_status_codes:
                return True
        if stop_reason and pattern.stop_reasons:
            if stop_reason.lower() in [s.lower() for s in pattern.stop_reasons]:
                return True
        return False
    
    def classify_from_acp_event(
        self,
        event_type: str,
        event_data: dict,
        turn_context: Optional[TurnContext] = None,
    ) -> Optional[FailureClassification]:
        """
        Classify failure from ACP event data.
        
        Returns None if no failure detected.
        """
        error_message = None
        http_status = None
        stop_reason = None
        
        # Extract error from event data
        if "error" in event_data:
            error_message = str(event_data["error"])
        if "message" in event_data:
            error_message = str(event_data["message"])
        if "http_status" in event_data:
            http_status = event_data.get("http_status")
        if "stop_reason" in event_data:
            stop_reason = event_data.get("stop_reason")
        elif event_type == "TURN_ERROR":
            stop_reason = "error"
        elif event_type == "TURN_CANCELLED":
            stop_reason = "cancelled"
        
        # Check if this is actually a failure
        failure_events = {
            "RATE_LIMIT", "SERVER_ERROR", "NETWORK_ERROR", 
            "AUTHENTICATION_ERROR", "PROVIDER_ERROR", "TOOL_FAILED",
            "TURN_ERROR", "TURN_CANCELLED", "TURN_MAX_TURNS"
        }
        
        if event_type not in failure_events:
            return None
        
        return self.classify(
            error_message=error_message,
            http_status=http_status,
            stop_reason=stop_reason,
            turn_context=turn_context,
        )