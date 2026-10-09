"""Debug script to test ACP parser with error events."""
import json
from guardian.goose.protocol import ACPProtocolParser, ACPEventType

def make_session_update(session_id: str, update_type: str, **kwargs) -> str:
    update = {"type": update_type}
    update.update(kwargs)
    return json.dumps({
        "jsonrpc": "2.0",
        "method": "sessionUpdate",
        "params": {
            "sessionId": session_id,
            "update": update
        }
    })

def make_rate_limit_error(session_id: str, error_msg: str = "Rate limit exceeded", run_id: str = None) -> str:
    return make_session_update(session_id, "Error",
        error={"code": -32603, "message": error_msg},
        runId=run_id
    )

def make_server_error(session_id: str, error_msg: str = "Internal server error", http_status: int = 500, run_id: str = None) -> str:
    return make_session_update(session_id, "Error",
        error={"code": -32603, "message": f"{error_msg} (HTTP {http_status})"},
        runId=run_id
    )

def make_auth_error(session_id: str, run_id: str = None) -> str:
    return make_session_update(session_id, "Error",
        error={"code": -32603, "message": "401 Unauthorized"},
        runId=run_id
    )

def make_invalid_model_error(session_id: str, run_id: str = None) -> str:
    return make_session_update(session_id, "Error",
        error={"code": -32603, "message": "Model not found"},
        runId=run_id
    )

parser = ACPProtocolParser()

# Test rate limit error
rate_limit_json = make_rate_limit_error("test_session", "429 Too Many Requests")
print("Rate limit error JSON:")
print(rate_limit_json)

events = parser.parse_frame(rate_limit_json)
for event in events:
    print(f"Event type: {event.event_type}")
    print(f"Session ID: {event.session_id}")
    print(f"Error message: {event.error_message}")
    print(f"Raw message: {event.raw_message}")
    print()

# Test server error
server_error_json = make_server_error("test_session", "Internal server error", 500)
print("Server error JSON:")
print(server_error_json)

events = parser.parse_frame(server_error_json)
for event in events:
    print(f"Event type: {event.event_type}")
    print(f"Session ID: {event.session_id}")
    print(f"Error message: {event.error_message}")
    print(f"Raw message: {event.raw_message}")
    print()

# Test auth error
auth_error_json = make_auth_error("test_session")
print("Auth error JSON:")
print(auth_error_json)

events = parser.parse_frame(auth_error_json)
for event in events:
    print(f"Event type: {event.event_type}")
    print(f"Session ID: {event.session_id}")
    print(f"Error message: {event.error_message}")
    print(f"Raw message: {event.raw_message}")
    print()

# Test invalid model error
invalid_model_json = make_invalid_model_error("test_session")
print("Invalid model error JSON:")
print(invalid_model_json)

events = parser.parse_frame(invalid_model_json)
for event in events:
    print(f"Event type: {event.event_type}")
    print(f"Session ID: {event.session_id}")
    print(f"Error message: {event.error_message}")
    print(f"Raw message: {event.raw_message}")
    print()