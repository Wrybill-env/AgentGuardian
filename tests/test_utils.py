"""
Tests for utility functions: secret redaction, timestamp handling.
"""

import logging
import time
from io import StringIO

import pytest

from guardian.logging import setup_logging, get_logger


class TestSecretRedaction:
    """Tests for secret redaction in logs."""
    
    def test_redact_token_query_param(self):
        logger = setup_logging(level="DEBUG", redact_secrets=True)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logger.handlers[0].formatter)
        logger.addHandler(handler)
        
        logger.info("Connecting to ws://127.0.0.1:1234/acp?token=abc123secret")
        output = stream.getvalue()
        assert "token=***REDACTED***" in output
        assert "abc123secret" not in output
    
    def test_redact_secret_query_param(self):
        logger = setup_logging(level="DEBUG", redact_secrets=True)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logger.handlers[0].formatter)
        logger.addHandler(handler)
        
        logger.info("Connecting with secret=xyz789secret")
        output = stream.getvalue()
        assert "secret=***REDACTED***" in output
        assert "xyz789secret" not in output
    
    def test_redact_token_json(self):
        logger = setup_logging(level="DEBUG", redact_secrets=True)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logger.handlers[0].formatter)
        logger.addHandler(handler)
        
        logger.info('{"token": "my-secret-token", "other": "value"}')
        output = stream.getvalue()
        assert '"token": "***REDACTED***"' in output
        assert "my-secret-token" not in output
    
    def test_redact_secret_json(self):
        logger = setup_logging(level="DEBUG", redact_secrets=True)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logger.handlers[0].formatter)
        logger.addHandler(handler)
        
        logger.info('{"secret": "my-secret-value", "other": "value"}')
        output = stream.getvalue()
        assert '"secret": "***REDACTED***"' in output
        assert "my-secret-value" not in output
    
    def test_multiple_secrets_redacted(self):
        logger = setup_logging(level="DEBUG", redact_secrets=True)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logger.handlers[0].formatter)
        logger.addHandler(handler)
        
        logger.info('ws://host:port/acp?token=tok123&secret=sec456')
        output = stream.getvalue()
        assert "token=***REDACTED***" in output
        assert "secret=***REDACTED***" in output
        assert "tok123" not in output
        assert "sec456" not in output
    
    def test_non_secret_not_redacted(self):
        logger = setup_logging(level="DEBUG", redact_secrets=True)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logger.handlers[0].formatter)
        logger.addHandler(handler)
        
        logger.info('ws://host:port/acp?session=sess_123&user=john')
        output = stream.getvalue()
        assert "session=sess_123" in output
        assert "user=john" in output
    
    def test_redaction_disabled(self):
        logger = setup_logging(level="DEBUG", redact_secrets=False)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logger.handlers[0].formatter)
        logger.addHandler(handler)
        
        logger.info("token=should-not-be-redacted")
        output = stream.getvalue()
        assert "should-not-be-redacted" in output


class TestTimestampHandling:
    """Tests for timestamp handling in state tracking."""
    
    def test_time_functions_return_floats(self):
        from guardian.goose.observer import SessionState
        from guardian.goose.protocol import ACPEvent, ACPEventType
        from datetime import datetime
        
        session = SessionState(session_id="sess_test")
        
        # Initially should be 0 or very small
        elapsed = session.time_since_last_event()
        assert isinstance(elapsed, float)
        assert elapsed >= 0
    
    def test_assistant_chunk_timestamp(self):
        from guardian.goose.observer import SessionState
        from guardian.goose.protocol import ACPEvent, ACPEventType
        from datetime import datetime
        
        session = SessionState(session_id="sess_test")
        
        assert session.time_since_last_assistant_chunk() is None
        assert session.time_since_last_tool_event() is None
        
        event = ACPEvent(
            event_type=ACPEventType.ASSISTANT_CHUNK,
            timestamp=datetime.now(),
            session_id="sess_test",
        )
        session.update_event(event)
        
        time.sleep(0.01)
        elapsed = session.time_since_last_assistant_chunk()
        assert elapsed is not None
        assert elapsed >= 0.01
    
    def test_tool_event_timestamp(self):
        from guardian.goose.observer import SessionState
        from guardian.goose.protocol import ACPEvent, ACPEventType
        from datetime import datetime
        
        session = SessionState(session_id="sess_test")
        
        event = ACPEvent(
            event_type=ACPEventType.TOOL_CALL_STARTED,
            timestamp=datetime.now(),
            session_id="sess_test",
            tool_name="shell",
            tool_call_id="call_123",
        )
        session.update_event(event)
        
        time.sleep(0.01)
        elapsed = session.time_since_last_tool_event()
        assert elapsed is not None
        assert elapsed >= 0.01


class TestEventClassification:
    """Tests for event classification accuracy."""
    
    def test_event_types_exist(self):
        """Verify all expected event types exist."""
        from guardian.goose.protocol import ACPEventType
        
        expected = [
            "CONNECTED", "DISCONNECTED", "HANDSHAKE_SENT", "HANDSHAKE_RECEIVED",
            "SESSION_UPDATE", "SESSION_INFO_UPDATE", "CONFIG_OPTION_UPDATE", "CURRENT_MODE_UPDATE",
            "ASSISTANT_CHUNK", "ASSISTANT_MESSAGE", "USER_MESSAGE",
            "TOOL_CALL_STARTED", "TOOL_CALL_UPDATE", "TOOL_CALL_COMPLETED", "TOOL_CALL_FAILED",
            "USAGE_UPDATE",
            "TURN_COMPLETE", "TURN_MAX_TURNS", "TURN_ERROR", "TURN_CANCELLED",
            "PROVIDER_ERROR", "RATE_LIMIT", "NETWORK_ERROR", "SERVER_ERROR", "AUTHENTICATION_ERROR",
            "UNKNOWN",
        ]
        
        for e in expected:
            assert hasattr(ACPEventType, e), f"Missing event type: {e}"