"""
Tests for session state tracking and failure detection.
"""

import time
from datetime import datetime

import pytest

from guardian.goose.observer import SessionState, FailureDetector
from guardian.goose.protocol import ACPEvent, ACPEventType, StopReason


class TestSessionState:
    """Tests for SessionState tracking."""
    
    def setup_method(self):
        self.session = SessionState(session_id="sess_test")
    
    def test_initial_state(self):
        assert self.session.session_id == "sess_test"
        assert self.session.connection_state == "connected"
        assert self.session.assistant_output_observed is False
        assert self.session.tool_activity_observed is False
        assert self.session.completion_state is None
        assert self.session.observed_errors == []
        assert self.session.tool_calls == {}
        assert self.session.turn_count == 0
    
    def test_assistant_chunk_sets_flag(self):
        event = ACPEvent(
            event_type=ACPEventType.ASSISTANT_CHUNK,
            timestamp=datetime.now(),
            session_id="sess_test",
        )
        self.session.update_event(event)
        
        assert self.session.assistant_output_observed is True
        assert self.session.last_assistant_chunk_timestamp is not None
    
    def test_tool_call_started_sets_flag(self):
        event = ACPEvent(
            event_type=ACPEventType.TOOL_CALL_STARTED,
            timestamp=datetime.now(),
            session_id="sess_test",
            tool_name="shell",
            tool_call_id="call_123",
        )
        self.session.update_event(event)
        
        assert self.session.tool_activity_observed is True
        assert self.session.last_tool_event_timestamp is not None
        assert "call_123" in self.session.tool_calls
        assert self.session.tool_calls["call_123"]["name"] == "shell"
    
    def test_tool_call_completed(self):
        event = ACPEvent(
            event_type=ACPEventType.TOOL_CALL_COMPLETED,
            timestamp=datetime.now(),
            session_id="sess_test",
            tool_name="shell",
            tool_call_id="call_123",
        )
        self.session.update_event(event)
        
        assert self.session.tool_calls["call_123"]["status"] == "tool_call_completed"
    
    def test_turn_complete_resets_flags(self):
        # First, simulate assistant output and tool call
        self.session.update_event(ACPEvent(
            event_type=ACPEventType.ASSISTANT_CHUNK,
            timestamp=datetime.now(),
            session_id="sess_test",
        ))
        self.session.update_event(ACPEvent(
            event_type=ACPEventType.TOOL_CALL_STARTED,
            timestamp=datetime.now(),
            session_id="sess_test",
            tool_name="shell",
            tool_call_id="call_123",
        ))
        
        # Then complete turn
        event = ACPEvent(
            event_type=ACPEventType.TURN_COMPLETE,
            timestamp=datetime.now(),
            session_id="sess_test",
        )
        self.session.update_event(event)
        
        assert self.session.completion_state == "completed"
        assert self.session.turn_count == 1
        # Flags should be reset for next turn
        assert self.session.assistant_output_observed is False
        assert self.session.tool_activity_observed is False
    
    def test_turn_max_turns(self):
        event = ACPEvent(
            event_type=ACPEventType.TURN_MAX_TURNS,
            timestamp=datetime.now(),
            session_id="sess_test",
        )
        self.session.update_event(event)
        
        assert self.session.completion_state == "max_turns"
        assert self.session.turn_count == 1
    
    def test_turn_error(self):
        event = ACPEvent(
            event_type=ACPEventType.TURN_ERROR,
            timestamp=datetime.now(),
            session_id="sess_test",
            error_message="Rate limit exceeded",
        )
        self.session.update_event(event)
        
        assert self.session.completion_state == "error"
        assert "Rate limit exceeded" in self.session.observed_errors
    
    def test_turn_cancelled(self):
        event = ACPEvent(
            event_type=ACPEventType.TURN_CANCELLED,
            timestamp=datetime.now(),
            session_id="sess_test",
        )
        self.session.update_event(event)
        
        assert self.session.completion_state == "cancelled"
    
    def test_provider_error_recorded(self):
        event = ACPEvent(
            event_type=ACPEventType.RATE_LIMIT,
            timestamp=datetime.now(),
            session_id="sess_test",
            error_message="429 Too Many Requests",
        )
        self.session.update_event(event)
        
        assert "429 Too Many Requests" in self.session.observed_errors
    
    def test_is_safe_to_replay_no_activity(self):
        """Safe to replay when no assistant output or tool activity."""
        assert self.session.is_safe_to_replay() is True
    
    def test_is_safe_to_replay_with_assistant(self):
        """Not safe to replay if assistant output observed."""
        self.session.update_event(ACPEvent(
            event_type=ACPEventType.ASSISTANT_CHUNK,
            timestamp=datetime.now(),
            session_id="sess_test",
        ))
        assert self.session.is_safe_to_replay() is False
    
    def test_is_safe_to_replay_with_tool(self):
        """Not safe to replay if tool activity observed."""
        self.session.update_event(ACPEvent(
            event_type=ACPEventType.TOOL_CALL_STARTED,
            timestamp=datetime.now(),
            session_id="sess_test",
            tool_name="shell",
            tool_call_id="call_123",
        ))
        assert self.session.is_safe_to_replay() is False
    
    def test_time_since_last_event(self):
        time.sleep(0.01)  # Small delay
        elapsed = self.session.time_since_last_event()
        assert elapsed >= 0.01
    
    def test_time_since_last_assistant_chunk(self):
        assert self.session.time_since_last_assistant_chunk() is None
        
        self.session.update_event(ACPEvent(
            event_type=ACPEventType.ASSISTANT_CHUNK,
            timestamp=datetime.now(),
            session_id="sess_test",
        ))
        time.sleep(0.01)
        elapsed = self.session.time_since_last_assistant_chunk()
        assert elapsed >= 0.01


class TestFailureDetector:
    """Tests for FailureDetector."""
    
    def setup_method(self):
        self.detector = FailureDetector(stale_threshold=60.0)
        self.session = SessionState(session_id="sess_test")
    
    def test_no_failures_initially(self):
        failures = self.detector.check_session(self.session)
        assert failures == []
    
    def test_stale_stream_detected(self):
        # Simulate old event
        self.session.last_event_timestamp = time.time() - 70
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "POSSIBLE_STALE_STREAM" in failures[0]
        assert "70" in failures[0]  # seconds
    
    def test_stale_stream_not_reported_twice(self):
        self.session.last_event_timestamp = time.time() - 70
        
        failures1 = self.detector.check_session(self.session)
        failures2 = self.detector.check_session(self.session)
        
        assert len(failures1) == 1
        assert len(failures2) == 0  # Already reported
    
    def test_backend_disconnected(self):
        self.session.connection_state = "disconnected"
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "BACKEND_DISCONNECTED" in failures[0]
    
    def test_rate_limit_detected(self):
        self.session.observed_errors.append("429 Too Many Requests")
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "RATE_LIMIT_DETECTED" in failures[0]
    
    def test_server_error_detected(self):
        for error_msg in ["500 Internal Server Error", "502 Bad Gateway", "503 Service Unavailable", "504 Gateway Timeout"]:
            session = SessionState(session_id="sess_test")
            session.observed_errors.append(error_msg)
            
            failures = self.detector.check_session(session)
            assert len(failures) == 1
            assert "SERVER_ERROR_DETECTED" in failures[0]
    
    def test_timeout_detected(self):
        self.session.observed_errors.append("Request timeout after 30s")
        
        failures = self.detector.check_session(self.session)
        assert len(failures) == 1
        assert "TIMEOUT_DETECTED" in failures[0]
    
    def test_connection_reset_detected(self):
        for error_msg in ["Connection reset by peer", "Connection refused", "ECONNRESET"]:
            session = SessionState(session_id="sess_test")
            session.observed_errors.append(error_msg)
            
            failures = self.detector.check_session(session)
            assert len(failures) == 1
            assert "CONNECTION_RESET_DETECTED" in failures[0]
    
    def test_auth_error_detected(self):
        for error_msg in ["401 Unauthorized", "403 Forbidden", "Authentication failed"]:
            session = SessionState(session_id="sess_test")
            session.observed_errors.append(error_msg)
            
            failures = self.detector.check_session(session)
            assert len(failures) == 1
            assert "AUTHENTICATION_ERROR_DETECTED" in failures[0]
    
    def test_unexpected_termination(self):
        session = SessionState(session_id="sess_test")
        session.completion_state = "error"
        session.turn_count = 0
        
        failures = self.detector.check_session(session)
        assert len(failures) == 1
        assert "UNEXPECTED_TERMINATION" in failures[0]
    
    def test_custom_callback(self):
        callback_calls = []
        
        def callback(failure, details):
            callback_calls.append((failure, details))
        
        detector = FailureDetector(stale_threshold=60.0, on_failure=callback)
        session = SessionState(session_id="sess_test")
        session.last_event_timestamp = time.time() - 70
        
        # check_session returns failures, report calls callback
        failures = detector.check_session(session)
        for failure in failures:
            detector.report(session.session_id, failure, {"session_id": session.session_id})
        
        assert len(callback_calls) == 1
        failure, details = callback_calls[0]
        assert "POSSIBLE_STALE_STREAM" in failure
        assert details["session_id"] == "sess_test"