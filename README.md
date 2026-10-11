# Agent Guardian

A safety-first reliability layer for AI coding agents — **Hermes, Goose, OpenCode, and FreeBuff**.

## Purpose

Agent Guardian provides observation, failure detection, and recovery for AI coding agents through a unified, agent-agnostic core. It implements the safety lifecycle:

**OBSERVE → CLASSIFY → VERIFY STATE → CHECK SAFETY → RECOVER ONLY WHEN PROVABLY SAFE**

Required invariants:
- `OBSERVE_ONLY` remains the default safety mode
- `AUTO_RECOVER` requires explicit opt-in
- `UNKNOWN` execution state blocks automatic recovery
- Tool execution that may have started blocks replay unless safety is affirmatively established
- State is revalidated immediately before every recovery action

## Quick Start

### Prerequisites

- Python 3.11+
- At least one supported agent: Hermes, Goose Desktop, OpenCode, or FreeBuff (Codebuff)

### Installation

```bash
cd C:\Projects\AgentGuardian
pip install -e . --break-system-packages
```

### Run the Observer (Read-Only)

```bash
# Observe Goose Desktop (auto-discovers backend)
python -m guardian observe goose

# Observe Hermes (in-process, requires running Hermes agent)
python -m guardian observe hermes

# Observe OpenCode (via ACP stdio subprocess)
python -m guardian observe opencode

# Observe FreeBuff (requires API token)
python -m guardian observe freebuff --api-token YOUR_TOKEN

# Verbose logging
python -m guardian observe goose -v
```

### Run Diagnostics

```bash
# Test Goose connection and ACP handshake
python -m guardian doctor

# Test with specific port
python -m guardian doctor --port 49684
```

### Manage Configuration

```bash
# Show current config
python -m guardian config show

# Save config to file
python -m guardian config save --path ~/.guardian/config.yaml
```

## Commands

### `observe` - Observe Agent Events (Read-Only)

```bash
python -m guardian observe {goose,hermes,opencode,freebuff} [OPTIONS]
```

**Goose options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--host` | 127.0.0.1 | Host to scan |
| `--port-range` | 10000-65535 | Port range to scan |
| `--port` | auto | Specific port (skips discovery) |
| `--token` | auto | ACP token |
| `--tls` | false | Use TLS (wss://) |
| `--stale-threshold` | 60 | Seconds before stale detection |
| `--discovery-timeout` | 30 | Max seconds for discovery |
| `--no-auto-reconnect` | false | Disable auto-reconnect |

**Hermes options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--stale-threshold` | 60 | Seconds before stale detection |
| `--first-chunk-threshold` | 30 | First chunk timeout |
| `--chunk-gap-threshold` | 15 | Gap between chunks timeout |
| `--tool-timeout-threshold` | 120 | Tool execution timeout |

**OpenCode options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--opencode-path` | opencode | Path to opencode executable |
| `--stale-threshold` | 60 | Seconds before stale detection |
| `--first-chunk-threshold` | 30 | First chunk timeout |
| `--chunk-gap-threshold` | 15 | Gap between chunks timeout |
| `--tool-timeout-threshold` | 120 | Tool execution timeout |

**FreeBuff options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--base-url` | https://codebuff.com | API base URL |
| `--api-token` | (required) | FreeBuff API token |
| `--websocket-url` | wss://codebuff.com/ws | WebSocket URL |
| `--stale-threshold` | 60 | Seconds before stale detection |
| `--first-chunk-threshold` | 30 | First chunk timeout |
| `--chunk-gap-threshold` | 15 | Gap between chunks timeout |
| `--tool-timeout-threshold` | 120 | Tool execution timeout |

### `watch` - Full State Machine with Recovery (Goose Only)

```bash
python -m guardian watch [OPTIONS]
```

Watch Goose with the full Guardian state machine. Recovery actions are available only in `auto_recover` mode (explicit opt-in).

### `doctor` - Diagnostics

```bash
python -m guardian doctor [OPTIONS]
```

Runs backend discovery, WebSocket connection test, and ACP handshake verification.

### `config` - Configuration Management

```bash
python -m guardian config {show,set,save} [OPTIONS]
```

## Supported Agents & Capabilities

| Capability | Hermes | Goose | OpenCode | FreeBuff (Codebuff) |
|------------|--------|-------|----------|----------|
| **Observation** | | | | |
| Stream chunks | ✅ | ✅ | ✅ | ✅ |
| Reasoning | ✅ | ❌ | ✅ | ❌ |
| Tool progress | ✅ | ✅ | ✅ | ✅ |
| Turn lifecycle | ✅ | ✅ | ✅ | ✅ |
| **Stream Recovery** | | | | |
| Reconnect stream | ✅ (simulated) | ✅ (ACP) | ✅ (subprocess) | ✅ (WS) |
| Cancel stream | ❌ | ❌ | ❌ | ❌ |
| **Session Recovery** | | | | |
| Reattach session | ❌ | ❌ | ❌ | ❌ |
| Fork session | ❌ | ✅ (ACP) | ✅ (ACP unstable) | ❌ |
| Load session | ❌ | ✅ (ACP) | ✅ (ACP) | ❌ (use previousRun) |
| **Provider Recovery** | | | | |
| Switch provider | ❌ | ❌ | ❌ | ❌ |
| Get provider list | ❌ | ❌ | ❌ | ❌ |
| **Agent Process** | | | | |
| Restart agent | ❌ | ❌ | ✅ (subprocess) | ❌ (SaaS) |
| Get agent status | ❌ | ✅ (HTTP) | ✅ (process) | ❌ |
| Is agent alive | ❌ | ✅ (WS) | ✅ (process) | ✅ (WS) |
| **Turn Control** | | | | |
| Cancel turn | ❌ | ✅ (ACP) | ✅ (ACP cancel) | ❌ |
| Retry request | ✅ (simulated) | ✅ (reattach) | ✅ (new session) | ✅ (previousRun) |
| Replay turn | ❌ | ✅ (load) | ✅ (load) | ❌ |
| **Introspection** | | | | |
| Partial tool names | ✅ | ❌ | ✅ (observer) | ❌ |
| Tool state | ❌ | ✅ (ACP) | ❌ | ❌ |
| Session state | ✅ | ✅ | ✅ | ✅ (run state) |
| Turn context | ✅ | ✅ | ✅ | ✅ |
| **Safety** | | | | |
| Verify session identity | ✅ | ✅ | ✅ | ✅ |
| Verify session exists | ❌ | ❌ | ✅ (listSessions) | ❌ |
| **Monitoring** | | | | |
| Detect disconnect | ✅ | ✅ | ✅ | ✅ |
| Detect crash | ❌ | ❌ | ✅ (process) | ✅ (connection) |
| Detect stale | ✅ | ✅ | ✅ | ✅ |

**Legend:** ✅ = Implemented and tested, ❌ = Not available in protocol/architecture, 🔶 = Partially implemented

## Evidence Levels

| Level | Meaning |
|-------|---------|
| **VERIFIED** | Implementation inspected + behavior supported by tests or runtime evidence |
| **MOCK-VERIFIED** | Demonstrated through controlled mock/fixture tests |
| **SIMULATED** | Only callback reattach or process restart — no real protocol recovery |
| **UNVERIFIED** | Insufficient evidence |
| **UNSAFE** | Violates a safety requirement |

### Current Evidence Status

| Adapter | Protocol | Evidence |
|---------|----------|----------|
| **Hermes** | In-process callbacks | VERIFIED (interface + mock) |
| **Goose** | ACP WebSocket | VERIFIED (interface + mock + forensic integration) |
| **OpenCode** | ACP stdio subprocess | VERIFIED (interface + mock + real ACP handshake) |
| **FreeBuff** | HTTP/WebSocket | DECLARED (interface + mock, blocked by missing credentials) |

## Safety Invariants — Enforced

| Invariant | Implementation |
|-----------|----------------|
| `OBSERVE_ONLY` default | `SafetyMode.OBSERVE_ONLY` in all adapters |
| `UNKNOWN` blocks recovery | `SafetyVerdict.AMBIGUOUS` → `RECOVERY_BLOCKED` |
| Tool started + no completion = replay blocked | `TurnContext.is_safe_to_replay()` checks `tool_calls_started > 0` |
| TOCTOU protection | `state_generation` comparison at planning + execution boundary |
| `AUTO_RECOVER` never auto-enabled | Explicit opt-in required |

## Project Structure

```
C:\Projects\AgentGuardian\
├── guardian/
│   ├── __init__.py           # Package exports
│   ├── __main__.py           # Entry point
│   ├── cli.py                # Command-line interface
│   ├── logging.py            # Secret-redacting logger
│   ├── core/                 # Agent-agnostic core
│   │   ├── state_machine.py  # GuardianStateMachine
│   │   ├── safety_gate.py    # Safety gate
│   │   ├── failure_classifier.py
│   │   ├── recovery_executor.py
│   │   ├── recovery_ladder.py
│   │   ├── recovery_budget.py
│   │   ├── recovery_planner.py
│   │   ├── ping_monitor.py   # Liveness detection
│   │   ├── adapter_capabilities.py  # AgentAdapter protocol
│   │   └── ...
│   ├── hermes/               # Hermes integration
│   │   ├── adapter.py        # HermesAdapter (AgentAdapter)
│   │   ├── observer.py       # HermesObserver (in-process)
│   │   └── ...
│   ├── goose/                # Goose Desktop integration
│   │   ├── adapter.py        # GooseAdapter (AgentAdapter)
│   │   ├── observer.py       # GooseObserver (ACP WebSocket)
│   │   ├── discovery.py      # Backend discovery
│   │   └── protocol.py       # ACP parsing
│   ├── opencode/             # OpenCode integration
│   │   ├── adapter.py        # OpenCodeAdapter (AgentAdapter)
│   │   ├── observer.py       # OpenCodeObserver (ACP stdio)
│   │   └── protocol.py       # ACP parsing
│   └── freebuff/             # FreeBuff integration
│       ├── adapter.py        # FreeBuffAdapter (AgentAdapter)
│       ├── observer.py       # FreeBuffObserver (WS)
│       └── protocol.py       # Event parsing
├── tests/
│   ├── test_core.py          # Core component tests
│   ├── test_fault_injection.py  # 38 fault scenarios
│   ├── test_hermes.py        # Hermes adapter tests
│   ├── test_ping_monitor.py  # Ping monitor tests
│   ├── test_protocol.py      # Protocol parsing tests
│   ├── test_recovery_executor.py
│   ├── test_state.py         # State tracking tests
│   └── test_utils.py         # Redaction, timestamp tests
│   └── integration/
│       └── forensic/
│           └── test_integration.py  # 14 real ACP tests
├── docs/
│   ├── GOOSE_OBSERVER.md
│   ├── HERMES_OBSERVER.md
│   ├── HERMES_RESEARCH.md
│   ├── RECOVERY_DESIGN.md
│   └── ...
├── pyproject.toml            # Packaging
├── requirements.txt          # Dependencies
├── pytest.ini               # Test config
└── README.md
```

## Testing

```bash
# Run all unit tests (256 tests)
python -m pytest tests/ -q --ignore=tests/integration

# Run forensic integration tests (14 tests, requires Goose + acp Python package)
python -m pytest tests/integration/forensic/test_integration.py -q

# Run specific test file
python -m pytest tests/test_protocol.py -v
```

## Packaging

```bash
# Build wheel
python -m build

# Install from local wheel
pip install dist/agent_guardian-0.1.0-py3-none-any.whl --break-system-packages
```

## License

Apache-2.0