"""
Tests for ACP protocol parsing and event classification.
"""

import json
from datetime import datetime

import pytest

from guardian.goose.protocol import (
    ACPEvent,
    ACPEventType,
    ACPProtocolParser,
    StopReason,
    classify_http_error,
)


class TestACPProtocolParser:
    """Tests for ACPProtocolParser."""
    
    def setup_method(self):
        self.parser = ACPProtocolParser()
    
    def test_parse_initialize_request(self):
        """Test parsing of ACP initialize request."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "agent/initialize",
            "params": {"protocolVersion": 1}
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.HANDSHAKE_SENT
        assert event.metadata["method"] == "agent/initialize"
        assert event.metadata["request_id"] == 1
    
    def test_parse_session_update_notification(self):
        """Test parsing of sessionUpdate notification."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "type": "SessionInfoUpdate",
                    "sessionInfo": {"title": "Test Session"}
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.SESSION_INFO_UPDATE
        assert event.session_id == "sess_123"
        assert event.metadata["update_type"] == "SessionInfoUpdate"
    
    def test_parse_assistant_chunk(self):
        """Test parsing of assistant message chunk."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "type": "MessageUpdate",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Hello"}]
                    }
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.ASSISTANT_CHUNK
        assert event.session_id == "sess_123"
    
    def test_parse_tool_call_started(self):
        """Test parsing of tool call start."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "type": "MessageUpdate",
                    "message": {
                        "role": "assistant",
                        "content": [{
                            "type": "tool_call",
                            "id": "call_123",
                            "name": "shell",
                            "arguments": {"command": "ls"}
                        }]
                    }
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.TOOL_CALL_STARTED
        assert event.tool_name == "shell"
        assert event.tool_call_id == "call_123"
    
    def test_parse_tool_call_update(self):
        """Test parsing of tool call update."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "type": "ToolCallUpdate",
                    "toolCall": {
                        "id": "call_123",
                        "name": "shell",
                        "status": "executing"
                    }
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.TOOL_CALL_UPDATE
        assert event.tool_name == "shell"
        assert event.tool_call_id == "call_123"
    
    def test_parse_tool_call_completed(self):
        """Test parsing of tool call completion."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "type": "ToolCallUpdate",
                    "toolCall": {
                        "id": "call_123",
                        "name": "shell",
                        "status": "completed"
                    }
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.TOOL_CALL_COMPLETED
    
    def test_parse_tool_call_failed(self):
        """Test parsing of tool call failure."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "type": "ToolCallUpdate",
                    "toolCall": {
                        "id": "call_123",
                        "name": "shell",
                        "status": "failed"
                    }
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.TOOL_CALL_FAILED
    
    def test_parse_turn_complete(self):
        """Test parsing of turn completion."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "type": "MessageUpdate",
                    "message": {"role": "assistant", "content": []},
                    "stopReason": "end_turn"
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.TURN_COMPLETE
        assert event.stop_reason == StopReason.END_TURN
    
    def test_parse_turn_max_turns(self):
        """Test parsing of max turns."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "stopReason": "max_turns"
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.TURN_MAX_TURNS
        assert event.stop_reason == StopReason.MAX_TURNS
    
    def test_parse_turn_error(self):
        """Test parsing of turn error."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "stopReason": "error",
                    "error": "Provider returned 429"
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.TURN_ERROR
        assert event.stop_reason == StopReason.ERROR
        assert "429" in event.error_message
    
    def test_parse_turn_cancelled(self):
        """Test parsing of turn cancellation."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": "sessionUpdate",
            "params": {
                "sessionId": "sess_123",
                "update": {
                    "stopReason": "cancelled"
                }
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.TURN_CANCELLED
        assert event.stop_reason == StopReason.CANCELLED
    
    def test_parse_error_response(self):
        """Test parsing of error response."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32603,
                "message": "Rate limit exceeded: 429 Too Many Requests"
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.RATE_LIMIT
        assert "429" in event.error_message
    
    def test_parse_server_error_response(self):
        """Test parsing of server error response."""
        frame = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32603,
                "message": "Internal server error: 500"
            }
        })
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == ACPEventType.SERVER_ERROR
    
    def test_parse_batch_frames(self):
        """Test parsing batch of frames."""
        frames = [
            {"jsonrpc": "2.0", "method": "sessionUpdate", "params": {"sessionId": "s1", "update": {"type": "SessionInfoUpdate"}}},
            {"jsonrpc": "2.0", "method": "sessionUpdate", "params": {"sessionId": "s2", "update": {"type": "SessionInfoUpdate"}}},
        ]
        frame = json.dumps(frames)
        
        events = self.parser.parse_frame(frame)
        assert len(events) == 2
        assert events[0].session_id == "s1"
        assert events[1].session_id == "s2"


class TestClassifyHTTPError:
    """Tests for HTTP error classification."""
    
    def test_rate_limit(self):
        assert classify_http_error(429) == ACPEventType.RATE_LIMIT
    
    def test_request_timeout(self):
        assert classify_http_error(408) == ACPEventType.NETWORK_ERROR
    
    def test_server_errors(self):
        for code in [500, 502, 503, 504]:
            assert classify_http_error(code) == ACPEventType.SERVER_ERROR
    
    def test_auth_errors(self):
        assert classify_http_error(401) == ACPEventType.AUTHENTICATION_ERROR
        assert classify_http_error(403) == ACPEventType.AUTHENTICATION_ERROR
    
    def test_other_errors(self):
        assert classify_http_error(400) == ACPEventType.PROVIDER_ERROR
        assert classify_http_error(404) == ACPEventType.PROVIDER_ERROR


class TestStopReason:
    """Tests for StopReason enum."""
    
    def test_values(self):
        assert StopReason.END_TURN.value == "end_turn"
        assert StopReason.MAX_TURNS.value == "max_turns"
        assert StopReason.ERROR.value == "error"
        assert StopReason.CANCELLED.value == "cancelled"