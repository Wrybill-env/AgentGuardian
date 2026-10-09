"""
Tests for Hermes observer, adapter, failure mapper, and safety gate.
"""

import time
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from guardian.hermes.observer import (
    HermesObserver,
    HermesSessionState,
    HermesFailureDetector,
)
from guardian.hermes.adapter import HermesAdapter, HermesAdapterConfig
from guardian.hermes.failure_mapper import (
    map_hermes_failure,
    classify_hermes_error,
    HERMES_TO_GUARDIAN_FAILURE,
)
from guardian.hermes.safety_gate import (
    HermesSafetyGate,
    HermesSafetyVerdict,
    HermesSafetyReason,
    HermesSafetyCheckResult,
)
from guardian.core.types import (
    TurnContext,
    TurnPhase,
    FailureType,
    RecoveryAction,
    SafetyMode,
)


class TestHermesSessionState:
    """Tests for HermesSessionState."""
    
    def setup_method(self):
        self.session = HermesSessionState(session_id="sess_test")
    
    def test_initial_state(self):
        assert self.session.session_id == "sess_test"
        assert self.session.turn_phase == TurnPhase.IDLE
        assert self.session.assistant_output_observed is False
        assert self.session.tool_activity_observed is False
        assert self.session.partial_tool_names == []
        assert self.session.completion_state is None
        assert self.session.observed_errors == []
        assert self.session.turn_count == 0
    
    def test_assistant_chunk_sets_flags(self):
        self.session.update_event("assistant_chunk")
        
        assert self.session.assistant_output_observed is True
        assert self.session.last_assistant_chunk_timestamp is not None
        assert self.session.first_assistant_chunk_timestamp is not None
        assert self.session.turn_phase == TurnPhase.ASSISTANT_STREAMING
    
    def test_reasoning_chunk(self):
        self.session.update_event("reasoning_chunk")
        
        assert self.session.last_reasoning_timestamp is not None
        assert self.session.turn_phase == TurnPhase.ASSISTANT_STREAMING
    
    def test_tool_started_sets_flag(self):
        self.session.update_event("tool_started", tool_name="shell")
        
        assert self.session.tool_activity_observed is True
        assert self.session.last_tool_event_timestamp is not None
        assert "shell" in self.session.partial_tool_names
        assert self.session.turn_phase == TurnPhase.TOOL_EXECUTING
    
    def test_tool_progress(self):
        self.session.update_event("tool_progress", tool_name="shell")
        
        assert self.session.last_tool_event_timestamp is not None
    
    def test_stream_start(self):
        self.session.update_event("stream_start")
        
        assert self.session.turn_phase == TurnPhase.ASSISTANT_STREAMING
        assert self.session.first_assistant_chunk_timestamp is None
    
    def test_stream_end_resets_flags(self):
        # Simulate a turn with assistant output and tool
        self.session.update_event("assistant_chunk")
        self.session.update_event("tool_started", tool_name="shell")
        
        # End stream
        self.session.update_event("stream_end")
        
        assert self.session.completion_state == "completed"
        assert self.session.turn_count == 1
        # Flags should be reset for next turn
        assert self.session.assistant_output_observed is False
        assert self.session.tool_activity_observed is False
        assert self.session.partial_tool_names == []
        assert self.session.first_assistant_chunk_timestamp is None
    
    def test_turn_error(self):
        self.session.update_event("turn_error", error_message="Rate limit exceeded")
        
        assert self.session.completion_state == "error"
        assert "Rate limit exceeded" in self.session.observed_errors
    
    def test_turn_cancelled(self):
        self.session.update_event("turn_cancelled")
        
        assert self.session.completion_state == "cancelled"
    
    def test_is_safe_to_replay_no_activity(self):
        assert self.session.is_safe_to_replay() is True
    
    def test_is_safe_to_replay_with_assistant(self):
        self.session.update_event("assistant_chunk")
        assert self.session.is_safe_to_replay() is False
    
    def test_is_safe_to_replay_with_tool(self):
        self.session.update_event("tool_started", tool_name="shell")
        assert self.session.is_safe_to_replay() is False
    
    def test_time_functions(self):
        time.sleep(0.01)
        elapsed = self.session.time_since_last_event()
        assert elapsed >= 0.01
        
        self.session.update_event("assistant_chunk")
        time.sleep(0.01)
        elapsed = self.session.time_since_last_assistant_chunk()
        assert elapsed >= 0.01
        
        self.session.update_event("tool_started", tool_name="shell")
        time.sleep(0.01)
        elapsed = self.session.time_since_last_tool_event()
        assert elapsed >= 0.01


class TestHermesFailureDetector:
    """Tests for HermesFailureDetector."""
    
    def setup_method(self):
        self.detector = HermesFailureDetector(stale_threshold=60.0)
        self.session = HermesSessionState(session_id="sess_test")
    
    def test_no_failures_initially(self):
        failures = self.detector.check_session(self.session)
        assert failures == []
    
    def test_stale_stream_detected(self):
        self.session.turn_phase = TurnPhase.ASSISTANT_STREAMING
        # Stream started 100s ago, first chunk arrived 100s ago, but no events for 70s
        self.session.last_event_timestamp = time.time() - 70
        self.session.first_assistant_chunk_timestamp = time.time() - 100
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "POSSIBLE_STALE_STREAM" in failures[0]
        assert "70" in failures[0]
    
    def test_first_chunk_timeout(self):
        self.session.turn_phase = TurnPhase.ASSISTANT_STREAMING
        self.session.last_event_timestamp = time.time() - 40  # stream started 40s ago
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "FIRST_CHUNK_TIMEOUT" in failures[0]
    
    def test_chunk_gap_timeout(self):
        self.session.turn_phase = TurnPhase.ASSISTANT_STREAMING
        self.session.first_assistant_chunk_timestamp = time.time() - 100
        self.session.last_assistant_chunk_timestamp = time.time() - 20
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "CHUNK_GAP" in failures[0]
    
    def test_tool_timeout(self):
        self.session.turn_phase = TurnPhase.TOOL_EXECUTING
        self.session.last_tool_event_timestamp = time.time() - 130
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "TOOL_TIMEOUT" in failures[0]
    
    def test_rate_limit_detected(self):
        self.session.observed_errors.append("429 Too Many Requests")
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "RATE_LIMIT_DETECTED" in failures[0]
    
    def test_server_error_detected(self):
        for error_msg in ["500 Internal Server Error", "502 Bad Gateway", "503 Service Unavailable", "504 Gateway Timeout"]:
            session = HermesSessionState(session_id="sess_test")
            session.observed_errors.append(error_msg)
            
            failures = self.detector.check_session(session)
            assert len(failures) == 1
            assert "SERVER_ERROR_DETECTED" in failures[0]
    
    def test_connection_reset_detected(self):
        for error_msg in ["Connection reset by peer", "Connection refused", "ECONNRESET", "broken pipe"]:
            session = HermesSessionState(session_id="sess_test")
            session.observed_errors.append(error_msg)
            
            failures = self.detector.check_session(session)
            assert len(failures) == 1
            assert "CONNECTION_RESET_DETECTED" in failures[0]
    
    def test_auth_error_detected(self):
        for error_msg in ["401 Unauthorized", "403 Forbidden", "Authentication failed"]:
            session = HermesSessionState(session_id="sess_test")
            session.observed_errors.append(error_msg)
            
            failures = self.detector.check_session(session)
            assert len(failures) == 1
            assert "AUTHENTICATION_ERROR_DETECTED" in failures[0]
    
    def test_dns_failure_detected(self):
        self.session.observed_errors.append("DNS lookup failed: name or service not known")
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "DNS_FAILURE_DETECTED" in failures[0]
    
    def test_stale_not_reported_twice(self):
        self.session.turn_phase = TurnPhase.ASSISTANT_STREAMING
        self.session.last_event_timestamp = time.time() - 70
        self.session.first_assistant_chunk_timestamp = time.time() - 100
        
        failures1 = self.detector.check_session(self.session)
        failures2 = self.detector.check_session(self.session)
        
        assert len(failures1) == 1
        assert len(failures2) == 0
    
    def test_custom_callback(self):
        callback_calls = []
        
        def callback(failure, details):
            callback_calls.append((failure, details))
        
        detector = HermesFailureDetector(stale_threshold=60.0, on_failure=callback)
        session = HermesSessionState(session_id="sess_test")
        session.turn_phase = TurnPhase.ASSISTANT_STREAMING
        session.last_event_timestamp = time.time() - 70
        session.first_assistant_chunk_timestamp = time.time() - 100
        
        failures = detector.check_session(session)
        for failure in failures:
            detector.report(session.session_id, failure, {"session_id": session.session_id})
        
        assert len(callback_calls) == 1
        failure, details = callback_calls[0]
        assert "POSSIBLE_STALE_STREAM" in failure


class TestHermesFailureMapper:
    """Tests for Hermes failure mapping."""
    
    def test_rate_limit_mapping(self):
        result = map_hermes_failure(
            error_message="429 Too Many Requests",
            failover_reason="rate_limit",
        )
        
        assert result["failure_type"] == FailureType.RATE_LIMIT.value
        assert result["retryable"] is True
        assert result["replay_safe"] is True
        assert result["recommended_action"] == RecoveryAction.WAIT_AND_RETRY.value
    
    def test_overloaded_mapping(self):
        result = map_hermes_failure(
            error_message="Service temporarily overloaded",
            failover_reason="overloaded",
        )
        
        assert result["failure_type"] == FailureType.TRANSIENT_PROVIDER.value
        assert result["retryable"] is True
    
    def test_server_error_mapping(self):
        for reason in ["server_error", "http_500", "http_502", "http_503"]:
            result = map_hermes_failure(
                error_message="Internal Server Error",
                failover_reason=reason,
            )
            assert result["failure_type"] == FailureType.SERVER_ERROR.value
    
    def test_timeout_mapping(self):
        result = map_hermes_failure(
            error_message="Request timed out",
            failover_reason="timeout",
        )
        
        assert result["failure_type"] == FailureType.TIMEOUT.value
        assert result["retryable"] is True
    
    def test_connection_reset_mapping(self):
        result = map_hermes_failure(
            error_message="Connection reset by peer",
            failover_reason="connection_reset",
        )
        
        assert result["failure_type"] == FailureType.CONNECTION_RESET.value
        assert result["retryable"] is True
    
    def test_auth_failure_mapping(self):
        for reason in ["auth", "auth_permanent", "http_401", "http_403"]:
            result = map_hermes_failure(
                error_message="Unauthorized",
                failover_reason=reason,
            )
            assert result["failure_type"] == FailureType.AUTHENTICATION_FAILURE.value
            assert result["retryable"] is False
    
    def test_invalid_model_mapping(self):
        result = map_hermes_failure(
            error_message="Model not found",
            failover_reason="model_not_found",
        )
        
        assert result["failure_type"] == FailureType.INVALID_MODEL.value
        assert result["retryable"] is False
    
    def test_content_policy_blocked_mapping(self):
        result = map_hermes_failure(
            error_message="Content policy violation",
            failover_reason="content_policy_blocked",
        )
        
        assert result["failure_type"] == FailureType.PERMISSION_FAILURE.value
        assert result["retryable"] is False
    
    def test_context_overflow_mapping(self):
        for reason in ["context_overflow", "payload_too_large", "image_too_large"]:
            result = map_hermes_failure(
                error_message="Context length exceeded",
                failover_reason=reason,
            )
            assert result["failure_type"] == FailureType.STREAM_TRUNCATED.value
    
    def test_unknown_failure(self):
        result = map_hermes_failure(
            error_message="Some completely unknown error",
        )
        
        assert result["failure_type"] == FailureType.UNKNOWN_FAILURE.value
        assert result["retryable"] is False
    
    def test_replay_safe_blocked_by_turn_context(self):
        result = map_hermes_failure(
            error_message="429 Too Many Requests",
            failover_reason="rate_limit",
            turn_context={
                "assistant_chunks_received": 5,
                "tool_calls_started": 0,
            },
        )
        
        assert result["replay_safe"] is False
        assert result["details"]["replay_safe_overridden"] == "turn_context_has_side_effects"
    
    def test_tool_activity_blocks_replay(self):
        result = map_hermes_failure(
            error_message="500 Internal Server Error",
            failover_reason="server_error",
            turn_context={
                "assistant_chunks_received": 0,
                "tool_calls_started": 1,
            },
        )
        
        assert result["replay_safe"] is False
        assert result["details"]["replay_safe_overridden"] == "turn_context_has_side_effects"


class TestHermesSafetyGate:
    """Tests for HermesSafetyGate."""
    
    def setup_method(self):
        self.gate = HermesSafetyGate(safety_mode=SafetyMode.OBSERVE_ONLY)
    
    def test_observe_only_blocks_all_recovery(self):
        turn_context = TurnContext(session_id="sess_test")
        
        result = self.gate.check_recovery_action_safety(
            RecoveryAction.WAIT_AND_RETRY,
            turn_context,
        )
        
        assert result.verdict == HermesSafetyVerdict.UNSAFE
        assert result.reason == HermesSafetyReason.BLOCKED_OBSERVE_ONLY
    
    def test_dry_run_allows_all(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.DRY_RUN)
        turn_context = TurnContext(session_id="sess_test")
        turn_context.assistant_chunks_received = 5
        
        result = gate.check_recovery_action_safety(
            RecoveryAction.WAIT_AND_RETRY,
            turn_context,
        )
        
        assert result.verdict == HermesSafetyVerdict.SAFE
        assert "DRY_RUN" in result.message
    
    def test_auto_recover_checks_safety(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        
        # Safe - no activity
        result = gate.check_recovery_action_safety(
            RecoveryAction.WAIT_AND_RETRY,
            turn_context,
        )
        assert result.verdict == HermesSafetyVerdict.SAFE
        
        # Unsafe - assistant output observed
        turn_context.assistant_chunks_received = 1
        result = gate.check_recovery_action_safety(
            RecoveryAction.WAIT_AND_RETRY,
            turn_context,
        )
        assert result.verdict == HermesSafetyVerdict.UNSAFE
        assert result.reason == HermesSafetyReason.UNSAFE_ASSISTANT_OUTPUT
    
    def test_replay_safety_blocks_after_assistant(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        turn_context.assistant_chunks_received = 1
        
        result = gate.check_replay_safety(turn_context)
        
        assert result.verdict == HermesSafetyVerdict.UNSAFE
        assert result.reason == HermesSafetyReason.UNSAFE_ASSISTANT_OUTPUT
    
    def test_replay_safety_blocks_after_tool(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        turn_context.tool_calls_started = 1
        
        result = gate.check_replay_safety(turn_context)
        
        assert result.verdict == HermesSafetyVerdict.UNSAFE
        assert result.reason == HermesSafetyReason.UNSAFE_TOOL_EXECUTION
    
    def test_replay_safety_blocks_turn_completed(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        turn_context.phase = TurnPhase.TURN_COMPLETE
        
        result = gate.check_replay_safety(turn_context)
        
        assert result.verdict == HermesSafetyVerdict.UNSAFE
        assert result.reason == HermesSafetyReason.UNSAFE_TURN_COMPLETED
    
    def test_replay_safety_blocks_with_partial_tool_names(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        
        stream_state = {"partial_tool_names": ["shell", "write"]}
        
        result = gate.check_replay_safety(turn_context, stream_state=stream_state)
        
        assert result.verdict == HermesSafetyVerdict.UNSAFE
        assert any("Partial tool activity" in f for f in result.blocking_factors)
    
    def test_reconnect_acp_safety(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        
        # Safe with session ID
        turn_context = TurnContext(session_id="sess_test")
        result = gate.check_recovery_action_safety(
            RecoveryAction.RECONNECT_ACP,
            turn_context,
        )
        assert result.verdict == HermesSafetyVerdict.SAFE
        
        # Ambiguous without session ID
        turn_context = TurnContext(session_id="")
        result = gate.check_recovery_action_safety(
            RecoveryAction.RECONNECT_ACP,
            turn_context,
        )
        assert result.verdict == HermesSafetyVerdict.AMBIGUOUS
    
    def test_wait_for_backend_always_safe(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        turn_context.assistant_chunks_received = 10
        turn_context.tool_calls_started = 5
        
        result = gate.check_recovery_action_safety(
            RecoveryAction.WAIT_FOR_BACKEND,
            turn_context,
        )
        assert result.verdict == HermesSafetyVerdict.SAFE
    
    def test_human_intervention_always_safe(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        turn_context.assistant_chunks_received = 100
        turn_context.tool_calls_started = 50
        
        result = gate.check_recovery_action_safety(
            RecoveryAction.HUMAN_INTERVENTION,
            turn_context,
        )
        assert result.verdict == HermesSafetyVerdict.SAFE
    
    def test_no_safe_recovery_blocked(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        
        result = gate.check_recovery_action_safety(
            RecoveryAction.NO_SAFE_RECOVERY,
            turn_context,
        )
        assert result.verdict == HermesSafetyVerdict.UNSAFE
    
    def test_evaluate_recovery_decision(self):
        gate = HermesSafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        
        # Mock failure classification
        class MockClassification:
            recommended_action = RecoveryAction.WAIT_AND_RETRY
        
        result = gate.evaluate_recovery_decision(MockClassification(), turn_context)
        assert result.verdict == HermesSafetyVerdict.SAFE
        
        # With assistant output - should be unsafe
        turn_context.assistant_chunks_received = 1
        result = gate.evaluate_recovery_decision(MockClassification(), turn_context)
        assert result.verdict == HermesSafetyVerdict.UNSAFE


class TestHermesObserver:
    """Tests for HermesObserver."""
    
    def setup_method(self):
        self.observer = HermesObserver(
            stale_threshold=60.0,
            first_chunk_threshold=30.0,
            chunk_gap_threshold=15.0,
            tool_timeout_threshold=120.0,
        )
    
    def test_attach_requires_agent(self):
        # Without an agent, attach should handle gracefully
        mock_agent = MagicMock()
        mock_agent.session_id = "sess_test"
        mock_agent.stream_delta_callback = None
        mock_agent.reasoning_callback = None
        mock_agent.tool_progress_callback = None
        mock_agent.tool_start_callback = None
        mock_agent.tool_complete_callback = None
        
        # The observer wraps callbacks, so we just test it doesn't crash
        result = self.observer.attach(mock_agent)
        assert result is True
        assert self.observer._agent is mock_agent
        assert self.observer._running is True
    
    def test_detach_restores_callbacks(self):
        mock_agent = MagicMock()
        mock_agent.session_id = "sess_test"
        mock_agent.stream_delta_callback = "original_delta"
        mock_agent.reasoning_callback = "original_reasoning"
        mock_agent.tool_progress_callback = "original_tool"
        
        self.observer.attach(mock_agent)
        self.observer.detach()
        
        assert mock_agent.stream_delta_callback == "original_delta"
        assert mock_agent.reasoning_callback == "original_reasoning"
        assert mock_agent.tool_progress_callback == "original_tool"
    
    def test_stream_delta_callback(self):
        mock_agent = MagicMock()
        mock_agent.session_id = "sess_test"
        
        self.observer.attach(mock_agent)
        self.observer._on_stream_delta("Hello world")
        
        session = self.observer.get_session_state("sess_test")
        assert session is not None
        assert session.assistant_output_observed is True
        assert session.turn_phase == TurnPhase.ASSISTANT_STREAMING
    
    def test_reasoning_callback(self):
        mock_agent = MagicMock()
        mock_agent.session_id = "sess_test"
        
        self.observer.attach(mock_agent)
        self.observer._on_reasoning_chunk("Let me think...")
        
        session = self.observer.get_session_state("sess_test")
        assert session is not None
        assert session.last_reasoning_timestamp is not None
    
    def test_tool_progress_callback(self):
        mock_agent = MagicMock()
        mock_agent.session_id = "sess_test"
        
        self.observer.attach(mock_agent)
        self.observer._on_tool_progress("tool.started", "shell", {"command": "ls"})
        
        session = self.observer.get_session_state("sess_test")
        assert session is not None
        assert session.tool_activity_observed is True
        assert "shell" in session.partial_tool_names
        assert session.turn_phase == TurnPhase.TOOL_EXECUTING
    
    def test_tool_completed_callback(self):
        mock_agent = MagicMock()
        mock_agent.session_id = "sess_test"
        
        self.observer.attach(mock_agent)
        self.observer._on_tool_progress("tool.completed", "shell", result="output")
        
        session = self.observer.get_session_state("sess_test")
        assert session is not None
        assert session.last_tool_event_timestamp is not None
    
    def test_get_status(self):
        mock_agent = MagicMock()
        mock_agent.session_id = "sess_test"
        
        self.observer.attach(mock_agent)
        # Trigger an event to create a session
        self.observer._on_stream_delta("Hello")
        
        status = self.observer.get_status()
        assert status["running"] is True
        assert status["attached"] is True
        assert status["sessions_count"] == 1


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])