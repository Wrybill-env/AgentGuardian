# Agent Guardian

A read-only watchdog/supervisor for Goose Desktop on Windows.

## Purpose

Agent Guardian observes Goose Desktop's ACP (Agent Client Protocol) events to detect transient failures (rate limits, network errors, stream stalls, backend crashes) — **without** interfering with Goose's operation.

**Current Status**: MVP Read-Only Observer (prototype)

## Quick Start

### Prerequisites

- Python 3.11+
- Goose Desktop running locally

### Installation

```bash
cd C:\Projects\AgentGuardian
pip install -r requirements.txt
```

### Run the Observer

```bash
# Auto-discovers Goose Desktop backend
python -m guardian observe

# With options
python -m guardian observe --stale-threshold 30 -v
```

### Expected Output

```
[19:12:01] INFO guardian: Starting Goose backend discovery...
[19:12:02] INFO guardian: Found Goose backend at 127.0.0.1:12345
[19:12:02] INFO guardian: Status URL: http://127.0.0.1:12345/status
[19:12:02] INFO guardian: WebSocket URL: ws://127.0.0.1:12345/acp?token=***REDACTED***

[Guardian] Connected to Goose at 127.0.0.1:12345
[Guardian] Observing ACP events (Ctrl+C to stop)...

[19:12:03] CONNECTED
[19:12:03] HANDSHAKE_SENT
[19:12:03] HANDSHAKE_RECEIVED
[19:12:05] SESSION_UPDATE session=sess_abc123
[19:12:10] ASSISTANT_CHUNK session=sess_abc123
[19:12:12] TOOL_CALL_STARTED session=sess_abc123 tool=shell
[19:12:15] TOOL_CALL_COMPLETED session=sess_abc123 tool=shell
[19:12:18] TURN_COMPLETE session=sess_abc123
```

## Commands

### `observe` - Observe Goose Events

```bash
python -m guardian observe [OPTIONS]

Options:
  --host HOST                 Host to scan (default: 127.0.0.1)
  --port-range START-END      Port range to scan (default: 10000-65535)
  --token TOKEN               ACP token (if known)
  --stale-threshold SECONDS   Stale stream threshold (default: 60)
  --discovery-timeout SECONDS Discovery timeout (default: 30)
  -v, --verbose               Verbose logging
  --log-level LEVEL           Log level (DEBUG/INFO/WARNING/ERROR)
```

## How It Works

1. **Discovery**: Scans localhost ports for Goose's `/status` endpoint
2. **Connection**: Connects to ACP WebSocket (`ws://host:port/acp?token=secret`)
3. **Handshake**: Sends minimum ACP `initialize` request
4. **Observation**: Receives and classifies all `sessionUpdate` notifications
5. **State Tracking**: Maintains per-session state (tools, output, errors)
6. **Failure Detection**: Reports stale streams, errors, disconnects

## Safety Guarantees

- **Zero outbound mutations**: Never sends prompts, cancellations, or session changes
- **Read-only WebSocket**: Only receives, never sends after handshake
- **Secret redaction**: Tokens/secrets never logged in plaintext
- **No recovery**: Detection only, no automatic actions

## Project Structure

```
C:\Projects\AgentGuardian\
├── guardian/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py              # Command-line interface
│   ├── logging.py          # Secret-redacting logger
│   └── goose/
│       ├── __init__.py
│       ├── discovery.py    # Backend discovery via /status
│       ├── observer.py     # WebSocket observer + state tracking
│       └── protocol.py     # ACP frame parsing & classification
├── tests/
│   ├── test_protocol.py    # ACP parsing tests
│   ├── test_state.py       # State tracking & failure detection tests
│   └── test_utils.py       # Redaction, timestamp tests
├── docs/
│   └── GOOSE_OBSERVER.md   # Detailed documentation
├── requirements.txt
├── pytest.ini
└── README.md
```

## Documentation

See [docs/GOOSE_OBSERVER.md](docs/GOOSE_OBSERVER.md) for detailed documentation.

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_protocol.py -v
```

## Roadmap

- [x] MVP Read-Only Observer (current)
- [ ] WebSocket reconnection with backoff
- [ ] Multiple backend support
- [ ] TLS support (`goose serve --tls`)
- [ ] Safe fork-based recovery (requires user confirmation)
- [ ] OpenCode adapter
- [ ] Multi-agent framework

## License

Apache-2.0