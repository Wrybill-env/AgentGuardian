"""
Maps Hermes-specific failure information to Guardian's generic FailureType system.

Uses actual Hermes error classes and enums discovered during research.
"""

from typing import Any, Dict, Optional, List
from guardian.core.types import FailureType, RecoveryAction

# Hermes FailoverReason enum (from agent/error_classifier.py)
HERMES_FAILOVER_REASONS = {
    "auth": "auth",
    "auth_permanent": "auth_permanent",
    "billing": "billing",
    "rate_limit": "rate_limit",
    "upstream_rate_limit": "upstream_rate_limit",
    "upstream_blocked": "upstream_blocked",
    "overloaded": "overloaded",
    "server_error": "server_error",
    "timeout": "timeout",
    "ssl_cert_verification": "ssl_cert_verification",
    "context_overflow": "context_overflow",
    "payload_too_large": "payload_too_large",
    "image_too_large": "image_too_large",
    "image_corrupt": "image_corrupt",
    "model_not_found": "model_not_found",
    "provider_policy_blocked": "provider_policy_blocked",
    "content_policy_blocked": "content_policy_blocked",
    "model_entitlement": "model_entitlement",
    "incomplete_response": "incomplete_response",
    "format_error": "format_error",
    "role_alternation": "role_alternation",
    "invalid_encrypted_content": "invalid_encrypted_content",
    "multimodal_tool_content_unsupported": "multimodal_tool_content_unsupported",
    "reasoning_mandatory": "reasoning_mandatory",
    "thinking_signature": "thinking_signature",
    "long_context_tier": "long_context_tier",
    "oauth_long_context_beta_forbidden": "oauth_long_context_beta_forbidden",
    "llama_cpp_grammar_pattern": "llama_cpp_grammar_pattern",
    "unknown": "unknown",
}


class HermesFailureType:
    """Hermes-specific failure type identifiers."""
    
    # Provider failures (transient)
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"  # 503/529
    SERVER_ERROR = "server_error"  # 500/502
    UPSTREAM_RATE_LIMIT = "upstream_rate_limit"
    UPSTREAM_BLOCKED = "upstream_blocked"  # WAF/CDN 403
    
    # Network failures (transient)
    TIMEOUT = "timeout"
    CONNECTION_RESET = "connection_reset"
    DNS_FAILURE = "dns_failure"
    SSL_CERT_VERIFICATION = "ssl_cert_verification"
    
    # Stream failures
    STREAM_STALE = "stream_stale"
    STREAM_TRUNCATED = "stream_truncated"
    FIRST_CHUNK_TIMEOUT = "first_chunk_timeout"
    CHUNK_GAP = "chunk_gap"
    
    # Tool failures
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_FAILURE = "tool_failure"
    
    # Auth/Config (permanent or credential-specific)
    AUTHENTICATION_FAILURE = "authentication_failure"
    AUTH_PERMANENT = "auth_permanent"
    BILLING = "billing"
    INVALID_MODEL = "invalid_model"
    INVALID_CONFIGURATION = "invalid_configuration"
    PROVIDER_POLICY_BLOCKED = "provider_policy_blocked"
    
    # Content policy
    CONTENT_POLICY_BLOCKED = "content_policy_blocked"
    MODEL_ENTITLEMENT = "model_entitlement"
    
    # Format/Protocol
    FORMAT_ERROR = "format_error"
    ROLE_ALTERNATION = "role_alternation"
    INVALID_ENCRYPTED_CONTENT = "invalid_encrypted_content"
    MULTIMODAL_TOOL_CONTENT_UNSUPPORTED = "multimodal_tool_content_unsupported"
    REASONING_MANDATORY = "reasoning_mandatory"
    
    # User action
    USER_CANCELLED = "user_cancelled"
    
    # Context
    CONTEXT_OVERFLOW = "context_overflow"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    IMAGE_TOO_LARGE = "image_too_large"
    IMAGE_CORRUPT = "image_corrupt"
    
    # Provider-specific
    THINKING_SIGNATURE = "thinking_signature"
    LONG_CONTEXT_TIER = "long_context_tier"
    OAUTH_LONG_CONTEXT_BETA_FORBIDDEN = "oauth_long_context_beta_forbidden"
    LLAMA_CPP_GRAMMAR_PATTERN = "llama_cpp_grammar_pattern"
    
    # Unknown
    UNKNOWN = "unknown"


# Mapping from Hermes FailoverReason to Guardian FailureType
HERMES_TO_GUARDIAN_FAILURE = {
    # Transient - retryable
    "rate_limit": FailureType.RATE_LIMIT,
    "overloaded": FailureType.TRANSIENT_PROVIDER,
    "server_error": FailureType.SERVER_ERROR,
    "upstream_rate_limit": FailureType.RATE_LIMIT,
    "upstream_blocked": FailureType.NETWORK_TRANSIENT,
    
    # Network - retryable
    "timeout": FailureType.TIMEOUT,
    "connection_reset": FailureType.CONNECTION_RESET,
    "dns_failure": FailureType.DNS_FAILURE,
    "ssl_cert_verification": FailureType.NETWORK_TRANSIENT,
    
    # Auth - generally not retryable without credential rotation
    "auth": FailureType.AUTHENTICATION_FAILURE,
    "auth_permanent": FailureType.AUTHENTICATION_FAILURE,
    "billing": FailureType.AUTHENTICATION_FAILURE,  # credential-specific
    
    # Model/Config - not retryable
    "model_not_found": FailureType.INVALID_MODEL,
    "provider_policy_blocked": FailureType.PERMISSION_FAILURE,
    "content_policy_blocked": FailureType.PERMISSION_FAILURE,
    "model_entitlement": FailureType.INVALID_MODEL,
    
    # Format - not retryable
    "format_error": FailureType.INVALID_CONFIGURATION,
    "role_alternation": FailureType.INVALID_CONFIGURATION,
    "invalid_encrypted_content": FailureType.INVALID_CONFIGURATION,
    "multimodal_tool_content_unsupported": FailureType.INVALID_CONFIGURATION,
    "reasoning_mandatory": FailureType.INVALID_CONFIGURATION,
    
    # Context - handled by compression
    "context_overflow": FailureType.STREAM_TRUNCATED,
    "payload_too_large": FailureType.STREAM_TRUNCATED,
    "image_too_large": FailureType.STREAM_TRUNCATED,
    "image_corrupt": FailureType.STREAM_TRUNCATED,
    
    # Provider-specific
    "thinking_signature": FailureType.INVALID_CONFIGURATION,
    "long_context_tier": FailureType.INVALID_MODEL,
    "oauth_long_context_beta_forbidden": FailureType.INVALID_MODEL,
    "llama_cpp_grammar_pattern": FailureType.INVALID_CONFIGURATION,
    
    # User
    "user_cancelled": FailureType.USER_CANCELLED,
    
    # Unknown
    "unknown": FailureType.UNKNOWN_FAILURE,
}


# Mapping for stream-specific failures (from observer)
STREAM_FAILURE_TO_GUARDIAN = {
    "first_chunk_timeout": FailureType.TIMEOUT,
    "chunk_gap": FailureType.STREAM_STALE,
    "tool_timeout": FailureType.TOOL_TIMEOUT,
    "stale_stream": FailureType.STREAM_STALE,
}


def classify_hermes_error(
    error: Any,
    error_message: str = "",
    failover_reason: Optional[str] = None,
    http_status: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Classify a Hermes error into Guardian's failure system.
    
    Args:
        error: The exception/error object
        error_message: String representation of the error
        failover_reason: Hermes FailoverReason if available
        http_status: HTTP status code if available
        context: Additional context (turn phase, tool activity, etc.)
    
    Returns:
        Dict with failure_type, retryable, replay_safe, recommended_action, confidence, details
    """
    error_message_lower = error_message.lower() if error_message else ""
    details = {"source": "hermes", "raw_error": error_message}
    
    if context:
        details.update(context)
    
    # 1. Try explicit failover_reason first (most reliable)
    if failover_reason and failover_reason in HERMES_TO_GUARDIAN_FAILURE:
        failure_type = HERMES_TO_GUARDIAN_FAILURE[failover_reason]
        return _build_classification(failure_type, failover_reason, details, error_message)
    
    # 2. Check HTTP status codes
    if http_status:
        if http_status == 429:
            return _build_classification(FailureType.RATE_LIMIT, "http_429", details, error_message)
        elif http_status == 408:
            return _build_classification(FailureType.TIMEOUT, "http_408", details, error_message)
        elif http_status in (500, 502, 503, 504):
            return _build_classification(FailureType.SERVER_ERROR, f"http_{http_status}", details, error_message)
        elif http_status in (401, 403):
            return _build_classification(FailureType.AUTHENTICATION_FAILURE, f"http_{http_status}", details, error_message)
        elif http_status == 404:
            return _build_classification(FailureType.INVALID_MODEL, "http_404", details, error_message)
        elif http_status == 413:
            return _build_classification(FailureType.STREAM_TRUNCATED, "http_413", details, error_message)
    
    # 3. Pattern matching on error message
    failure_type = _classify_by_message(error_message_lower)
    if failure_type != FailureType.UNKNOWN_FAILURE:
        return _build_classification(failure_type, "message_pattern", details, error_message)
    
    # 4. Check exception type
    failure_type = _classify_by_exception_type(error)
    if failure_type != FailureType.UNKNOWN_FAILURE:
        return _build_classification(failure_type, "exception_type", details, error_message)
    
    # 5. Default to unknown
    return _build_classification(FailureType.UNKNOWN_FAILURE, "unclassified", details, error_message)


def _classify_by_message(message: str) -> FailureType:
    """Classify by error message text patterns."""
    # Rate limit patterns
    if any(p in message for p in ["429", "rate limit", "too many requests", "throttled", "quota exceeded"]):
        return FailureType.RATE_LIMIT
    
    # Overloaded
    if any(p in message for p in ["overloaded", "temporarily overloaded", "service may be temporarily overloaded",
                                   "at capacity", "over capacity", "503", "529"]):
        return FailureType.TRANSIENT_PROVIDER
    
    # Server errors
    if any(p in message for p in ["500", "502", "504", "internal server error", "bad gateway", "gateway timeout"]):
        return FailureType.SERVER_ERROR
    
    # Timeout
    if any(p in message for p in ["timeout", "timed out", "deadline exceeded", "operation timed out",
                                   "request timed out", "upstream timed out"]):
        return FailureType.TIMEOUT
    
    # Connection reset
    if any(p in message for p in ["connection reset", "connection refused", "econnreset", "econnrefused",
                                   "broken pipe", "connection aborted", "connection lost", "pipe closed"]):
        return FailureType.CONNECTION_RESET
    
    # DNS
    if any(p in message for p in ["dns", "name or service not known", "nodename nor servname",
                                   "getaddrinfo failed", "eai_again"]):
        return FailureType.DNS_FAILURE
    
    # Auth
    if any(p in message for p in ["401", "403", "unauthorized", "forbidden", "invalid api key",
                                   "invalid token", "token expired", "authentication failed",
                                   "failed to extract accountid"]):
        return FailureType.AUTHENTICATION_FAILURE
    
    # Billing
    if any(p in message for p in ["insufficient credits", "insufficient quota", "credits exhausted",
                                   "billing", "payment required", "out of extra usage", "budget limit exceeded"]):
        return FailureType.AUTHENTICATION_FAILURE
    
    # Invalid model
    if any(p in message for p in ["model not found", "invalid model", "no such model", "unknown model",
                                   "unsupported model", "no endpoints found that support"]):
        return FailureType.INVALID_MODEL
    
    # Content policy
    if any(p in message for p in ["content policy", "content filter", "flagged", "violates our usage policies",
                                   "safety", "responsibleai", "cybersecurity risk"]):
        return FailureType.PERMISSION_FAILURE
    
    # Context overflow
    if any(p in message for p in ["context length", "context size", "maximum context", "token limit",
                                   "too many tokens", "max_tokens", "max_model_len", "exceeds the limit",
                                   "context window", "prompt is too long", "input is too long",
                                   "maximum allowed input length"]):
        return FailureType.STREAM_TRUNCATED
    
    # Payload too large
    if any(p in message for p in ["payload too large", "request too large", "413", "request entity too large",
                                   "request exceeds the maximum size"]):
        return FailureType.STREAM_TRUNCATED
    
    # Image errors
    if any(p in message for p in ["image exceeds", "image too large", "image_too_large", "image size exceeds",
                                   "dimensions exceed", "max allowed size", "patches after processing",
                                   "make sure your payload is below", "streamreadconstraints"]):
        return FailureType.STREAM_TRUNCATED
    
    if any(p in message for p in ["invalid png", "invalid jpeg", "base64 string of provided image cannot be decoded",
                                   "downloaded response does not contain a valid"]):
        return FailureType.STREAM_TRUNCATED
    
    # Format errors
    if any(p in message for p in ["invalid request", "malformed", "unexpected event order",
                                   "stream_options", "unsupported parameter", "unknown parameter"]):
        return FailureType.INVALID_CONFIGURATION
    
    # Role alternation
    if any(p in message for p in ["roles must alternate", "role must alternate", "consecutive user messages",
                                   "consecutive messages with the same role", "same role in a row"]):
        return FailureType.INVALID_CONFIGURATION
    
    # Tool content
    if any(p in message for p in ["tool message content must be a string", "expected string, got list",
                                   "tool_call.content must be string", "tool.content.str",
                                   "chatcompletionrequesttoolmessagecontent"]):
        return FailureType.INVALID_CONFIGURATION
    
    # Reasoning mandatory
    if "reasoning is mandatory" in message:
        return FailureType.INVALID_CONFIGURATION
    
    # Provider policy blocked
    if any(p in message for p in ["no endpoints available matching your guardrail",
                                   "no endpoints available matching your data policy"]):
        return FailureType.PERMISSION_FAILURE
    
    # Thinking signature
    if any(p in message for p in ["thinking", "signature", "cannot be modified", "must remain as they were"]):
        return FailureType.INVALID_CONFIGURATION
    
    # Long context tier
    if any(p in message for p in ["long context", "extra usage", "1m context", "1M context"]):
        return FailureType.INVALID_MODEL
    
    # Llama.cpp grammar
    if "grammar" in message and "pattern" in message:
        return FailureType.INVALID_CONFIGURATION
    
    # Upstream blocked (WAF/CDN)
    if any(p in message for p in ["blocked", "cloudflare", "cf-browser-verification", "cf-challenge",
                                   "enable javascript", "challenge-error-text"]):
        return FailureType.NETWORK_TRANSIENT
    
    return FailureType.UNKNOWN_FAILURE


def _classify_by_exception_type(error: Any) -> FailureType:
    """Classify by exception class name."""
    if error is None:
        return FailureType.UNKNOWN_FAILURE
    
    error_type = type(error).__name__.lower()
    error_module = type(error).__module__.lower()
    
    # httpx exceptions
    if "httpx" in error_module:
        if "timeout" in error_type:
            return FailureType.TIMEOUT
        if "connect" in error_type:
            return FailureType.CONNECTION_RESET
        if "remote" in error_type:
            return FailureType.CONNECTION_RESET
    
    # asyncio
    if "asyncio" in error_module:
        if "timeout" in error_type:
            return FailureType.TIMEOUT
        if "cancel" in error_type:
            return FailureType.USER_CANCELLED
    
    # ssl
    if "ssl" in error_module:
        return FailureType.NETWORK_TRANSIENT
    
    # Provider SDK exceptions
    if any(p in error_module for p in ["openai", "anthropic", "google", "cohere", "mistral", "groq", "together"]):
        if "rate" in error_type or "429" in error_type:
            return FailureType.RATE_LIMIT
        if "auth" in error_type or "401" in error_type or "403" in error_type:
            return FailureType.AUTHENTICATION_FAILURE
        if "notfound" in error_type or "404" in error_type:
            return FailureType.INVALID_MODEL
        if "badrequest" in error_type or "400" in error_type:
            return FailureType.INVALID_CONFIGURATION
        if "internal" in error_type or "500" in error_type or "502" in error_type or "503" in error_type:
            return FailureType.SERVER_ERROR
        if "timeout" in error_type:
            return FailureType.TIMEOUT
    
    return FailureType.UNKNOWN_FAILURE


def _build_classification(
    failure_type: FailureType,
    source: str,
    details: Dict[str, Any],
    raw_error: str,
) -> Dict[str, Any]:
    """Build a standardized classification result."""
    
    # Determine retryability and safety based on failure type
    retryable = failure_type in {
        FailureType.RATE_LIMIT,
        FailureType.TRANSIENT_PROVIDER,
        FailureType.SERVER_ERROR,
        FailureType.TIMEOUT,
        FailureType.CONNECTION_RESET,
        FailureType.DNS_FAILURE,
        FailureType.NETWORK_TRANSIENT,
        FailureType.STREAM_STALE,
    }
    
    # Replay safe ONLY if no side effects (checked separately by safety gate)
    # This is the base classification - safety gate will adjust based on turn context
    replay_safe = failure_type in {
        FailureType.RATE_LIMIT,
        FailureType.TRANSIENT_PROVIDER,
        FailureType.SERVER_ERROR,
        FailureType.TIMEOUT,
        FailureType.CONNECTION_RESET,
        FailureType.DNS_FAILURE,
        FailureType.NETWORK_TRANSIENT,
        FailureType.STREAM_STALE,
        FailureType.STREAM_TRUNCATED,
    }
    
    # Recommended action
    if failure_type == FailureType.RATE_LIMIT:
        action = RecoveryAction.WAIT_AND_RETRY
    elif failure_type in {FailureType.TRANSIENT_PROVIDER, FailureType.SERVER_ERROR, FailureType.TIMEOUT,
                          FailureType.CONNECTION_RESET, FailureType.DNS_FAILURE, FailureType.NETWORK_TRANSIENT}:
        action = RecoveryAction.WAIT_AND_RETRY
    elif failure_type == FailureType.STREAM_STALE:
        action = RecoveryAction.CANCEL_AND_REPLAY
    elif failure_type == FailureType.STREAM_TRUNCATED:
        action = RecoveryAction.CANCEL_AND_REPLAY
    elif failure_type in {FailureType.AUTHENTICATION_FAILURE, FailureType.INVALID_MODEL,
                          FailureType.INVALID_CONFIGURATION, FailureType.PERMISSION_FAILURE,
                          FailureType.USER_CANCELLED}:
        action = RecoveryAction.HUMAN_INTERVENTION
    elif failure_type in {FailureType.BACKEND_DISCONNECT, FailureType.BACKEND_CRASH}:
        action = RecoveryAction.WAIT_FOR_BACKEND
    else:
        action = RecoveryAction.NO_SAFE_RECOVERY
    
    # Confidence
    confidence = 0.9 if source != "message_pattern" and source != "unclassified" else 0.6
    if source == "unclassified":
        confidence = 0.1
    
    return {
        "failure_type": failure_type.value,
        "retryable": retryable,
        "replay_safe": replay_safe,
        "requires_human": not retryable,
        "recommended_action": action.value,
        "confidence": confidence,
        "details": {**details, "classification_source": source},
        "raw_error": raw_error,
    }


def map_hermes_failure(
    error: Any = None,
    error_message: str = "",
    failover_reason: Optional[str] = None,
    http_status: Optional[int] = None,
    turn_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Main entry point for mapping Hermes failures.
    
    Args:
        error: Exception object
        error_message: Error message string
        failover_reason: Hermes FailoverReason enum value
        http_status: HTTP status code
        turn_context: Current turn context (for replay safety)
    
    Returns:
        Classification dict compatible with Guardian's FailureClassification
    """
    context = {"turn_context": turn_context} if turn_context else {}
    
    result = classify_hermes_error(
        error=error,
        error_message=error_message,
        failover_reason=failover_reason,
        http_status=http_status,
        context=context,
    )
    
    # If turn context provided, adjust replay_safe
    if turn_context:
        has_assistant_output = turn_context.get("assistant_chunks_received", 0) > 0
        has_tool_activity = turn_context.get("tool_calls_started", 0) > 0
        if has_assistant_output or has_tool_activity:
            result["replay_safe"] = False
            result["details"]["replay_safe_overridden"] = "turn_context_has_side_effects"
    
    return result