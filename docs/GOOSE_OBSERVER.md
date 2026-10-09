# Goose Observer Documentation

## Overview

The Goose Observer is a read-only diagnostic tool that connects to a running Goose Desktop instance via the ACP (Agent Client Protocol) WebSocket and observes events without interfering with Goose's operation.

## How Discovery Works

The observer discovers the Goose backend by scanning localhost ports for the `/status` health endpoint:

1. Scans ports 10000-65535 on 127.0.0.1
2. Sends HTTP GET to `http://127.0.0.1:<port>/status`
3. If response is "ok", identifies it as a Goose backend
4. Constructs WebSocket URL: `ws://127.0.0.1:<port>/acp?token=<secret>`

The ACP token is generated per-lease by Goose Desktop's Electron main process. The observer attempts to connect with the token if known, or without token for discovery.

## How the ACP Connection Works

### WebSocket Connection

The observer connects to the ACP WebSocket endpoint:
```
ws://127.0.0.1:<port>/acp?token=<secret>
```

### ACP Handshake (Minimum Required)

Upon connection, the observer sends the minimum required ACP initialize request:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "agent/initialize",
  "params": {
    "protocolVersion": 1,
    "clientCapabilities": {
      "elicitation": {"form": {}},
      "_meta": {
        "goose": {
          "mcpHostCapabilities": ["filesystem.read", "filesystem.write", "shell.execute"],
          "customNotifications": true,
          "recipeParameterRequests": true
        }
      }
    },
    "clientInfo": {
      "name": "agent-guardian",
      "version": "0.1.0"
    }
  }
}
```

This handshake is REQUIRED by the ACP protocol to establish a connection that will receive `sessionUpdate` notifications. The observer sends NO other requests.

### No State-Changing Commands

The observer NEVER sends:
- `agent/session/prompt` (user messages)
- `agent/session/cancel` (cancellation)
- `agent/session/fork` (session forking)
- `agent/session/load` (session loading)
- Any other mutation commands

## Observed Messages/Events

The observer classifies and logs the following ACP events:

### Connection Events
| Event | Description |
|-------|-------------|
| `CONNECTED` | WebSocket connection established |
| `DISCONNECTED` | WebSocket connection closed |
| `HANDSHAKE_SENT` | ACP initialize request sent |
| `HANDSHAKE_RECEIVED` | ACP initialize response received |

### Session Events
| Event | Description |
|-------|-------------|
| `SESSION_UPDATE` | Generic session update |
| `SESSION_INFO_UPDATE` | Session metadata changed (title, cwd, etc.) |
| `CONFIG_OPTION_UPDATE` | Model/provider/config changed |
| `CURRENT_MODE_UPDATE` | Goose mode changed |

### Message Events
| Event | Description |
|-------|-------------|
| `ASSISTANT_CHUNK` | Assistant text streaming chunk |
| `ASSISTANT_MESSAGE` | Complete assistant message |
| `USER_MESSAGE` | User message sent (observed, not sent by us) |
| `TOOL_CALL_STARTED` | Tool execution started |
| `TOOL_CALL_UPDATE` | Tool execution progress |
| `TOOL_CALL_COMPLETED` | Tool completed successfully |
| `TOOL_CALL_FAILED` | Tool failed |

### Usage Events
| Event | Description |
|-------|-------------|
| `USAGE_UPDATE` | Token usage update |

### Turn Completion Events
| Event | Description |
|-------|-------------|
| `TURN_COMPLETE` | Turn ended normally (stopReason=end_turn) |
| `TURN_MAX_TURNS` | Max turns reached |
| `TURN_ERROR` | Turn ended with error (stopReason=error) |
| `TURN_CANCELLED` | Turn cancelled (stopReason=cancelled) |

### Error Events
| Event | Description |
|-------|-------------|
| `RATE_LIMIT` | HTTP 429 detected |
| `SERVER_ERROR` | HTTP 5xx detected |
| `NETWORK_ERROR` | Connection reset, timeout, DNS failure |
| `AUTHENTICATION_ERROR` | HTTP 401/403 |
| `PROVIDER_ERROR` | Other provider errors |

## State Tracking

For each observed session, the observer tracks:

```python
SessionState:
  session_id: str
  run_id: Optional[str]           # ACP run ID if available
  connection_state: str           # connected, disconnected, reconnecting
  assistant_output_observed: bool # Any assistant text chunks seen
  tool_activity_observed: bool    # Any tool calls seen
  last_event_timestamp: float     # Unix timestamp of last event
  last_assistant_chunk_timestamp: Optional[float]
  last_tool_event_timestamp: Optional[float]
  completion_state: Optional[str] # completed, error, cancelled, max_turns
  observed_errors: List[str]      # Accumulated error messages
  tool_calls: Dict[str, Dict]     # tool_call_id -> {name, status, timestamp}
  turn_count: int                 # Number of completed turns
```

### Safety Check: `is_safe_to_replay()`

Returns `True` only if:
- No assistant output observed in current turn
- No tool activity observed in current turn

This is for FUTURE recovery logic - currently only used for reporting.

## Failure Detection

The observer detects and reports the following conditions:

| Failure | Detection Method |
|---------|------------------|
| `POSSIBLE_STALE_STREAM` | No events for >60s (configurable) |
| `BACKEND_DISCONNECTED` | WebSocket connection closed |
| `RATE_LIMIT_DETECTED` | 429 in error messages |
| `SERVER_ERROR_DETECTED` | 500, 502, 503, 504 in errors |
| `TIMEOUT_DETECTED` | "timeout" or 408 in errors |
| `CONNECTION_RESET_DETECTED` | Connection reset/refused patterns |
| `AUTHENTICATION_ERROR_DETECTED` | 401, 403, auth failures |
| `UNEXPECTED_TERMINATION` | Error/cancelled before any turn |

**Important:** Detection only - NO automatic recovery actions.

## What Cannot Currently Be Detected

| Limitation | Reason |
|------------|--------|
| Mid-stream stalls with periodic heartbeats | Need provider-specific knowledge |
| Partial tool execution before crash | No access to tool executor state |
| Provider-side retry exhaustion | Only see final error |
| Token expiry/rotation | Token is static per lease |
| Multiple windows sharing lease | Race conditions possible |

## Why Recovery Is Not Safe Yet

The observer cannot safely perform recovery because:

1. **No idempotency guarantee**: Replaying a prompt after a tool call would re-execute the tool
2. **No session-level recovery protocol**: ACP has no "resume from checkpoint" primitive
3. **Tool side effects are external**: MCP servers, filesystem, shell commands have no rollback
4. **Partial state unknown**: Observer sees events but not backend internal state
5. **Race conditions**: Multiple windows, concurrent prompts, steering

## How to Run the Observer

```bash
# Basic usage (auto-discovers Goose)
python -m guardian observe

# With custom options
python -m guardian observe --host 127.0.0.1 --port-range 10000-20000 --stale-threshold 30

# With known token
python -m guardian observe --token YOUR_ACP_TOKEN

# Verbose logging
python -m guardian observe -v
```

### Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | 127.0.0.1 | Host to scan |
| `--port-range` | 10000-65535 | Port range to scan |
| `--token` | auto | ACP token (if known) |
| `--stale-threshold` | 60.0 | Seconds before stale detection |
| `--discovery-timeout` | 30.0 | Max seconds for discovery |
| `-v, --verbose` | false | Enable DEBUG logging |
| `--log-level` | INFO | Log level (DEBUG/INFO/WARNING/ERROR) |

## Known Limitations

1. **Token Discovery**: The ACP token is generated by Goose Desktop's Electron process and not easily discoverable without Electron context. The observer attempts connection without token first.

2. **Port Scan Speed**: Scanning 55,000+ ports takes time. Default concurrent limit is 50.

3. **Single Backend**: Currently connects to first discovered backend. Multiple Goose instances not supported.

4. **Read-Only by Design**: No recovery, no prompt resubmission, no session manipulation.

5. **ACP Protocol Version**: Assumes ACP v1. Future versions may require updates.

6. **WebSocket Reconnection**: Not implemented - exits on disconnect.

7. **TLS Support**: Not tested with `goose serve --tls`.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Agent Guardian (Python)                                     │
│  ├─ discovery.py    → Port scan + /status check             │
│  ├─ observer.py     → WebSocket + event processing          │
│  ├─ protocol.py     → ACP frame parsing + classification    │
│  └─ cli.py          → Command-line interface                │
└─────────────────────────────────────────────────────────────┘
                              │
                    ACP v1 over WebSocket
                    ws://127.0.0.1:<port>/acp?token=<secret>
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Goose Desktop                                               │
│  ├─ Electron Main    → Spawns goose.exe serve              │
│  ├─ Renderer (React) → ACP WebSocket client                │
│  └─ goose.exe serve  → ACP Server (Rust)                   │
└─────────────────────────────────────────────────────────────┘
```