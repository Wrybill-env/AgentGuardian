# Hermes Observer Documentation

## Overview

The Hermes Observer is a read-only diagnostic tool that connects to a running Hermes Agent instance via in-process callback hooks and observes events without interfering with Hermes' operation.

## How It Works

The observer integrates with Hermes Agent by wrapping its existing callback system:

| Callback | Purpose | What It Observes |
|----------|---------|------------------|
| `stream_delta_callback` | Assistant text chunks | Streaming text output |
| `reasoning_callback` | Reasoning/thinking chunks | Model reasoning process |
| `tool_progress_callback` | Tool start/progress/complete/output_risk | Tool execution lifecycle |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Strixora (Python)                                               │
│  ├─ HermesObserver                                              │
│  │   ├─ HermesSessionState (per-session tracking)              │
│  │   ├─ HermesFailureDetector (failure pattern detection)      │
│  │   └─ Callbacks (stream_delta, reasoning, tool_progress)     │
│  ├─ HermesAdapter (implements AgentAdapter interface)          │
│  ├─ failure_mapper (Hermes FailoverReason → Guardian FailureType)│
│  └─ safety_gate (HermesSafetyGate with explicit reasons)       │
└─────────────────────────────────────────────────────────────────┘
                              │
                    In-process callback hooks
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Hermes Agent (AIAgent)                                          │
│  ├─ stream_delta_callback (assistant text)                     │
│  ├─ reasoning_callback (thinking/reasoning)                    │
│  └─ tool_progress_callback (tool.started/progress/completed)   │
└─────────────────────────────────────────────────────────────────┘
```

## Observed Events

### Connection Events
| Event | Description |
|-------|-------------|
| `stream_start` | Stream began (turn phase → ASSISTANT_STREAMING) |
| `stream_end` | Stream completed (turn phase → TURN_COMPLETE) |

### Message Events
| Event | Description |
|-------|-------------|
| `assistant_chunk` | Assistant text streaming chunk |
| `reasoning_chunk` | Reasoning/thinking chunk |

### Tool Events
| Event | Description |
|-------|-------------|
| `tool_started` | Tool execution started (tool name, args) |
| `tool_progress` | Tool execution progress |
| `tool_completed` | Tool completed successfully (result preview) |
| `tool_output_risk` | Tool output risk assessment |

### Turn Completion Events
| Event | Description |
|-------|-------------|
| `turn_error` | Turn ended with error |
| `turn_cancelled` | Turn cancelled |

### Failure Events
| Event | Description |
|-------|-------------|
| `rate_limit` | Rate limit (429) detected |
| `server_error` | 5xx server error |
| `timeout` | Timeout (408, request timeout) |
| `connection_reset` | Connection reset/refused |
| `dns_failure` | DNS resolution failure |
| `auth_error` | Authentication/authorization error |
| `invalid_model` | Invalid model specified |
| `content_policy` | Content policy violation |
| `stale_stream` | No progress for threshold period |

## State Tracking

For each observed session, the observer tracks:

```python
HermesSessionState:
  session_id: str
  turn_id: Optional[str]
  turn_phase: TurnPhase           # IDLE, USER_MESSAGE_SENT, ASSISTANT_STREAMING, TOOL_EXECUTING, etc.
  assistant_output_observed: bool # Any assistant text chunks seen
  tool_activity_observed: bool    # Any tool calls seen
  partial_tool_names: List[str]   # Tool names from streaming tool_calls
  last_event_timestamp: float     # Unix timestamp of last event
  last_assistant_chunk_timestamp: Optional[float]
  last_reasoning_timestamp: Optional[float]
  last_tool_event_timestamp: Optional[float]
  first_assistant_chunk_timestamp: Optional[float]
  completion_state: Optional[str] # completed, error, cancelled, interrupted
  observed_errors: List[str]      # Accumulated error messages
  tool_calls: Dict[str, Dict]     # tool_call_id -> info
  turn_count: int                 # Number of completed turns
  retry_count: int                # Number of retries observed
  auto_recovery_cycles: int       # Auto-recovery cycles
  stale_detected: bool            # Whether stale stream detected
```

### Safety Check: `is_safe_to_replay()`

Returns `True` only if:
- No assistant output observed in current turn
- No tool activity observed in current turn

This is the PRIMARY safety gate for replay decisions.

## Failure Detection

The observer detects and reports the following conditions:

| Failure | Detection Method |
|---------|------------------|
| `POSSIBLE_STALE_STREAM` | No events for >60s (configurable) |
| `FIRST_CHUNK_TIMEOUT` | No chunks for >30s after stream start |
| `CHUNK_GAP` | No chunks for >15s between chunks |
| `TOOL_TIMEOUT` | Tool execution for >120s |
| `BACKEND_DISCONNECTED` | WebSocket/connection closed |
| `RATE_LIMIT_DETECTED` | 429 in error messages |
| `SERVER_ERROR_DETECTED` | 500, 502, 503, 504 in errors |
| `TIMEOUT_DETECTED` | "timeout" or 408 in errors |
| `CONNECTION_RESET_DETECTED` | Connection reset/refused patterns |
| `AUTHENTICATION_ERROR_DETECTED` | 401, 403, auth failures |
| `DNS_FAILURE_DETECTED` | DNS lookup failures |
| `INVALID_MODEL_DETECTED` | Model not found errors |
| `UNEXPECTED_TERMINATION` | Error/cancelled before any turn |

**Important:** Detection only — NO automatic recovery actions.

## What Cannot Currently Be Detected

| Limitation | Reason |
|------------|--------|
| Mid-stream stalls with periodic heartbeats | Need provider-specific knowledge |
| Partial tool execution before crash | No access to tool executor state |
| Provider-side retry exhaustion | Only see final error |
| Token expiry/rotation | Token is static per session |
| Multiple windows sharing session | Race conditions possible |

## How to Run the Observer

```bash
# Basic usage (requires running Hermes agent to attach to)
python -m guardian observe hermes

# With custom options
python -m guardian observe hermes --stale-threshold 30 --first-chunk-threshold 15 -v

# Observe Goose instead
python -m guardian observe goose
```

### Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--stale-threshold` | 60.0 | Seconds before reporting stale stream |
| `--first-chunk-threshold` | 30.0 | Seconds before reporting first chunk timeout |
| `--chunk-gap-threshold` | 15.0 | Seconds between chunks before reporting gap |
| `--tool-timeout-threshold` | 120.0 | Seconds before reporting tool timeout |
| `-v, --verbose` | false | Enable DEBUG logging |
| `--log-level` | INFO | Log level (DEBUG/INFO/WARNING/ERROR) |
| `--safety-mode` | observe_only | Safety mode (observe_only/dry_run/auto_recover) |

## Integration with Hermes

The observer attaches to a running Hermes AIAgent instance by replacing its callbacks:

```python
from guardian.hermes import HermesObserver, run_hermes_observer

# Attach to running agent
observer = await run_hermes_observer(
    agent=running_hermes_agent,
    stale_threshold=60.0,
    on_event=lambda event: print(f"Event: {event}"),
    on_failure=lambda failure, details: print(f"Failure: {failure}"),
)

# Observe events...
# Later, detach
observer.detach()
```

### Callback Integration Details

The observer wraps Hermes' existing callbacks:

```python
# Original Hermes callbacks (before observer attaches)
agent.stream_delta_callback = original_fn
agent.reasoning_callback = original_fn
agent.tool_progress_callback = original_fn

# After observer attaches
agent.stream_delta_callback = observer._on_stream_delta
agent.reasoning_callback = observer._on_reasoning_chunk
agent.tool_progress_callback = observer._on_tool_progress
```

The observer calls the original callbacks internally, so Hermes' normal operation is unaffected.

## Safety Semantics

### When Is Replay Safe?

Replay is ONLY safe if:
1. No assistant output observed in current turn (`assistant_output_observed == False`)
2. No tool activity observed in current turn (`tool_activity_observed == False`)
3. Turn hasn't completed (`phase not in (TURN_COMPLETE, TURN_ENDING)`)
4. No partial tool names recorded (`partial_tool_names == []`)

### When Is Recovery Safe?

| Recovery Action | Safety Requirement |
|-----------------|-------------------|
| `WAIT_AND_RETRY` | No side effects observed |
| `CANCEL_AND_REPLAY` | No side effects, active turn exists |
| `RECONNECT_ACP` | Session ID known (not applicable for Hermes) |
| `RELOAD_SESSION` | Session ID known |
| `FORK_SESSION` | Fork point before current turn's user message |
| `WAIT_FOR_BACKEND` | Always safe (no prompt replay) |
| `HUMAN_INTERVENTION` | Always safe (no automatic action) |

### Fail-Closed Principle

If state is ambiguous, Guardian **fails closed** — recovery is blocked and human intervention required.

## Known Limitations

1. **In-process only**: Observer must run in same process as Hermes agent
2. **No external attachment**: Cannot attach to Hermes running in separate process
3. **No ACP observation**: Hermes' ACP server is for IDEs, not used internally
4. **Single agent at a time**: Only observes one agent instance
5. **Read-only by design**: No recovery, no prompt resubmission, no session manipulation
6. **No persistent state**: Config only, not session state
7. **No structured logging output**: Text logs only

## Integration with Guardian Core

The Hermes integration follows the agent-agnostic architecture:

```
guardian/
├── core/                    # Agent-agnostic (ZERO Hermes imports)
│   ├── types.py
│   ├── failure_classifier.py
│   ├── safety_gate.py
│   ├── backoff.py
│   ├── state_machine.py
│   └── config.py
├── hermes/                  # Hermes-specific adapter
│   ├── observer.py
│   ├── adapter.py
│   ├── failure_mapper.py
│   └── safety_gate.py
├── goose/                   # Goose-specific adapter (isolated)
└── cli.py                   # Entry point
```

## Configuration

```yaml
# ~/.guardian/config.yaml
safety_mode: observe_only
stale_threshold: 60.0
first_chunk_threshold: 30.0
chunk_gap_threshold: 15.0
tool_timeout_threshold: 120.0
log_level: INFO
redact_secrets: true
```

Environment variables:
- `GUARDIAN_SAFETY_MODE`: safety_mode
- `GUARDIAN_STALE_THRESHOLD`: stale_threshold

## Why Recovery Is Not Safe Yet

The observer cannot safely perform recovery because:

1. **No idempotency guarantee**: Replaying a prompt after a tool call would re-execute the tool
2. **No session-level recovery protocol**: Hermes has no "resume from checkpoint" primitive
3. **Tool side effects are external**: File writes, shell commands, API calls have no rollback
4. **Partial state unknown**: Observer sees events but not backend internal state
5. **Race conditions**: Multiple concurrent prompts, steering, user corrections

## Architecture

```
Strixora (Python)
├── Core (agent-agnostic)
│   ├── FailureClassifier → maps errors to FailureType
│   ├── SafetyGate → checks replay safety
│   ├── BackoffStrategy → exponential backoff with jitter
│   ├── StateMachine → 13-state lifecycle
│   └── Config → YAML + env var config
├── Hermes Adapter
│   ├── HermesObserver → callback hooks + state tracking
│   ├── HermesAdapter → implements AgentAdapter interface
│   ├── failure_mapper → FailoverReason → FailureType
│   └── safety_gate → HermesSafetyGate with explicit reasons
└── CLI
    ├── guardian observe hermes
    ├── guardian observe goose
    ├── guardian watch hermes (not yet implemented)
    └── guardian doctor
```

---

*Documentation generated from Hermes Observer implementation v0.1.0*