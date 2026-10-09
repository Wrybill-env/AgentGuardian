# Agent Guardian - Recovery Design

**Version:** 0.1.0
**Status:** MVP Design Complete
**Related:** `docs/OVERNIGHT_BUILD_REPORT.md`, `docs/GOOSE_EVENT_VALIDATION.md`

---

## 1. Guardian Architecture

### 1.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        AGENT GUARDIAN                           │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────┐ │
│  │   Goose Adapter │    │  OpenCode Adptr │    │  Future...  │ │
│  │  (agent-agnostic│    │   (planned)     │    │  adapters   │ │
│  │   interface)    │    │                 │    │             │ │
│  └────────┬────────┘    └────────┬────────┘    └──────┬──────┘ │
│           │                      │                    │        │
│           └──────────────────────┼────────────────────┘        │
│                                  ▼                             │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │                  GUARDIAN CORE                          │  │
│  │  ┌─────────────┐ ┌─────────────┐ ┌──────────────────┐  │  │
│  │  │ Failure     │ │ Safety      │ │ State Machine    │  │  │
│  │  │ Classifier  │ │ Gate        │ │ (13 states)      │  │  │
│  │  └──────┬──────┘ └──────┬──────┘ └────────┬─────────┘  │  │
│  │         │               │                  │            │  │
│  │         ▼               ▼                  ▼            │  │
│  │  ┌──────────────────────────────────────────────────┐  │  │
│  │  │           BACKOFF STRATEGY                       │  │  │
│  │  │  (Exponential, Jitter, Retry-After, Max Attempts)│  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Core Principles

1. **Agent-Agnostic Core**: All recovery logic lives in `guardian/core/` with no Goose-specific code
2. **Adapter Pattern**: Each agent (Goose, OpenCode, etc.) implements a thin adapter
3. **Fail-Closed**: Every ambiguous decision defaults to blocking recovery
4. **Explicit Safety Gates**: No recovery action executes without passing the SafetyGate
5. **Observability First**: Every decision is logged with reasoning

### 1.3 Module Boundaries

```
guardian/
├── core/                    # Agent-agnostic (ZERO Goose imports)
│   ├── types.py             # Shared enums, dataclasses
│   ├── failure_classifier.py
│   ├── safety_gate.py
│   ├── backoff.py
│   ├── state_machine.py
│   └── config.py
├── goose/                   # Goose-specific adapter
│   ├── discovery.py
│   ├── observer.py
│   ├── protocol.py
│   └── adapter.py           # Bridges Goose → Core
└── cli.py                   # Entry point
```

---

## 2. Goose Adapter Boundary

### 2.1 Adapter Responsibilities

The Goose adapter (`guardian/goose/adapter.py`) is the **only** place where Goose-specific logic lives:

| Responsibility | Implementation |
|----------------|----------------|
| ACP event → Guardian event translation | `_handle_acp_event()` maps 25+ ACP events |
| Recovery action execution | 6 concrete action implementations |
| WebSocket connection management | Via `GooseObserver` |
| Session/run ID tracking | Extracted from `sessionUpdate` notifications |

### 2.2 Adapter Interface (Conceptual)

```python
class AgentAdapter(ABC):
    """Interface all agents must implement."""
    
    @abstractmethod
    async def connect(self) -> bool: ...
    
    @abstractmethod
    async def disconnect(self): ...
    
    @abstractmethod
    def on_event(self, callback: Callable[[ACPEvent], None]): ...
    
    @abstractmethod
    def on_failure(self, callback: Callable[[str, dict], None]): ...
    
    @abstractmethod
    async def execute_recovery(self, action: RecoveryAction, context: dict) -> bool: ...
    
    @property
    @abstractmethod
    def session_id(self) -> Optional[str]: ...
    
    @property
    @abstractmethod
    def run_id(self) -> Optional[str]: ...
```

### 2.3 ACP Events Handled

| ACP Event | Guardian Event | Critical Data |
|-----------|----------------|---------------|
| `initialize` response | `HANDSHAKE_RECEIVED` | agentCapabilities, agentInfo |
| `sessionUpdate` (MessageUpdate, assistant) | `ASSISTANT_CHUNK` | chunk text, tool_calls |
| `sessionUpdate` (MessageUpdate, tool_call) | `TOOL_CALL_STARTED` | tool_name, tool_call_id |
| `sessionUpdate` (ToolCallUpdate) | `TOOL_CALL_UPDATE/COMPLETED/FAILED` | status, output, error |
| `sessionUpdate` (stopReason) | `TURN_COMPLETE/ERROR/CANCELLED/MAX_TURNS` | stopReason |
| `sessionUpdate` (RateLimit/ServerError/etc) | `FAILURE_DETECTED` | error details |
| `sessionUpdate` (runId) | Internal tracking | runId |

---

## 3. Core Watchdog State Machine

### 3.1 States (13)

```
IDLE → OBSERVING ↔ STREAMING ↔ TOOL_ACTIVE
                          ↓
                     COMPLETING → COMPLETED
                          ↓
                   POSSIBLE_FAILURE
                          ↓
              RECOVERY_WAIT → RECOVERY_ATTEMPT
                          ↓
                     VERIFYING → RECOVERED
                          ↓
                   FAILED_PERMANENTLY
                          ↓
                   UNKNOWN_UNSAFE
```

### 3.2 Valid Transitions

| From | Event | To |
|------|-------|----|
| IDLE | CONNECTED | OBSERVING |
| OBSERVING | USER_MESSAGE_SENT | STREAMING |
| STREAMING | TOOL_STARTED | TOOL_ACTIVE |
| TOOL_ACTIVE | TOOL_COMPLETED | STREAMING |
| STREAMING | TURN_COMPLETE | COMPLETED |
| ANY | FAILURE_DETECTED | POSSIBLE_FAILURE |
| POSSIBLE_FAILURE | BACKOFF_COMPLETE | RECOVERY_ATTEMPT |
| RECOVERY_ATTEMPT | RECOVERY_COMPLETE | VERIFYING |
| VERIFYING | VERIFICATION_PASSED | RECOVERED |
| VERIFYING | VERIFICATION_FAILED | POSSIBLE_FAILURE |

### 3.3 Terminal States

| State | Meaning |
|-------|---------|
| COMPLETED | Turn finished successfully |
| RECOVERED | Recovery successful, ready for next turn |
| FAILED_PERMANENTLY | No safe recovery possible |
| UNKNOWN_UNSAFE | Ambiguous state, human required |

---

## 4. Failure Classification

### 4.1 20 Failure Types

| Category | Types |
|----------|-------|
| **Provider** | RATE_LIMIT, TRANSIENT_PROVIDER, SERVER_ERROR |
| **Network** | NETWORK_TRANSIENT, CONNECTION_RESET, DNS_FAILURE, TIMEOUT |
| **Stream** | STREAM_STALE, STREAM_TRUNCATED |
| **Backend** | BACKEND_DISCONNECT, BACKEND_CRASH, BACKEND_RESTART |
| **Auth/Config** | AUTHENTICATION_FAILURE, INVALID_CONFIGURATION, INVALID_MODEL |
| **Tool** | TOOL_FAILURE, TOOL_TIMEOUT |
| **Permission** | PERMISSION_FAILURE |
| **User** | USER_CANCELLED |
| **Unknown** | UNKNOWN_FAILURE |

### 4.2 Classification Signals

| Signal | Source | Weight |
|--------|--------|--------|
| Error message regex | ACP error fields, HTTP response | High |
| HTTP status code | Provider HTTP response | High |
| ACP stop_reason | `sessionUpdate.stopReason` | High |
| Turn context | Assistant chunks, tool calls | Critical for replay safety |

### 4.3 Classification Output

```python
@dataclass
class FailureClassification:
    failure_type: FailureType
    retryable: bool
    replay_safe: bool        # Adjusted by turn context!
    requires_human: bool
    recommended_action: RecoveryAction
    confidence: float        # 0.0 - 1.0
    details: dict
    raw_error: str
```

### 4.4 Confidence Scoring

Patterns sorted by confidence (highest first). First match wins.

---

## 5. Transient vs Permanent Failures

| Failure Type | Retryable | Replay Safe | Human Required |
|--------------|-----------|-------------|----------------|
| RATE_LIMIT | ✅ | ✅* | ❌ |
| TRANSIENT_PROVIDER | ✅ | ✅* | ❌ |
| SERVER_ERROR (5xx) | ✅ | ✅* | ❌ |
| NETWORK_TRANSIENT | ✅ | ✅* | ❌ |
| CONNECTION_RESET | ✅ | ✅* | ❌ |
| DNS_FAILURE | ✅ | ✅* | ❌ |
| TIMEOUT | ✅ | ✅* | ❌ |
| STREAM_STALE | ✅ | ✅* | ❌ |
| STREAM_TRUNCATED | ✅ | ✅* | ❌ |
| BACKEND_DISCONNECT | ✅ | ✅* | ❌ |
| BACKEND_CRASH | ✅ | ✅* | ❌ |
| BACKEND_RESTART | ✅ | ✅* | ❌ |
| AUTHENTICATION_FAILURE | ❌ | ❌ | ✅ |
| INVALID_CONFIGURATION | ❌ | ❌ | ✅ |
| INVALID_MODEL | ❌ | ❌ | ✅ |
| USER_CANCELLED | ❌ | ❌ | ✅ |
| TOOL_FAILURE | ❌ | ❌ | ✅ |
| TOOL_TIMEOUT | ❌ | ❌ | ✅ |
| PERMISSION_FAILURE | ❌ | ❌ | ✅ |
| UNKNOWN_FAILURE | ❌ | ❌ | ✅ |

*Replay safe ONLY if no assistant output AND no tool activity observed in current turn.

---

## 6. Stale Stream Detection

### 6.1 Detection Logic

```python
def _check_stale(self) -> bool:
    if not self.turn_context:
        return False
    if self.state not in (STREAMING, TOOL_ACTIVE, OBSERVING):
        return False
    elapsed = time.time() - self.turn_context.last_event_timestamp
    return elapsed > self.stale_threshold  # Default: 60s
```

### 6.2 Phase-Aware Thresholds (Future)

| Phase | Threshold |
|-------|-----------|
| ASSISTANT_STREAMING | 30s |
| TOOL_EXECUTING | 120s (configurable per tool) |
| TURN_ENDING | 10s |

### 6.3 Response

| Detection | Classification | Action |
|-----------|----------------|--------|
| Stale during streaming | STREAM_STALE | CANCEL_AND_REPLAY (if safe) |
| Stale during tool | STREAM_STALE | HUMAN_INTERVENTION (tool may be running) |
| Stale idle | STREAM_STALE | WAIT_AND_RETRY |

---

## 7. Backoff Strategy

### 7.1 Configuration

```python
@dataclass
class BackoffConfig:
    initial_delay: float = 1.0      # seconds
    max_delay: float = 60.0         # seconds
    multiplier: float = 2.0         # exponential
    jitter_type: JitterType = FULL  # FULL/EQUAL/DECORRELATED/NONE
    max_attempts: int = 3           # 0 = unlimited with indefinite_retry
    indefinite_retry: bool = False
    respect_retry_after: bool = True
    max_retry_after: float = 300.0  # cap on Retry-After
```

### 7.2 Jitter Types

| Type | Formula | Use Case |
|------|---------|----------|
| NONE | `delay` | Testing |
| FULL | `random(0, delay)` | AWS standard, prevents thundering herd |
| EQUAL | `delay/2 + random(0, delay/2)` | Balanced |
| DECORRELATED | `random(base, prev_delay * 3)` | High contention |

### 7.3 Retry-After Integration

- Server `Retry-After` header honored when `respect_retry_after=True`
- Capped at `max_retry_after` (default 300s)
- Overrides exponential backoff for that attempt

### 7.4 Predefined Configs

| Config | Initial | Max | Mult | Jitter | Attempts |
|--------|---------|-----|------|--------|----------|
| DEFAULT | 1s | 60s | 2.0 | FULL | 3 |
| CONSERVATIVE | 2s | 120s | 2.0 | FULL | 5 |
| AGGRESSIVE | 0.5s | 30s | 1.5 | EQUAL | 3 |
| INDEFINITE | 5s | 300s | 2.0 | DECORRELATED | ∞ |

---

## 8. Recovery States

| State | Trigger | Behavior |
|-------|---------|----------|
| POSSIBLE_FAILURE | Failure detected | Classify, check safety, start backoff |
| RECOVERY_WAIT | Backoff started | Wait for `backoff.next_delay()` |
| RECOVERY_ATTEMPT | Backoff complete | Execute `recommended_action` via adapter |
| VERIFYING | Recovery complete | Check success via observable state |
| RECOVERED | Verification passed | Resume OBSERVING |
| FAILED_PERMANENTLY | Max attempts exceeded | Human intervention required |

---

## 9. Session/Run Identity

### 9.1 Identity Sources

| Identifier | Source | Stability |
|------------|--------|-----------|
| `session_id` | ACP `sessionId` in all notifications | Stable per chat |
| `run_id` | ACP `runId` in `sessionUpdate` | Per-turn |
| `tool_call_id` | ACP `toolCallUpdate.id` | Per tool invocation |

### 9.2 Identity Preservation

| Recovery Action | Identity Preserved? |
|-----------------|---------------------|
| WAIT_AND_RETRY | ✅ Same session/run |
| CANCEL_AND_REPLAY | ✅ Same session, new run |
| RECONNECT_ACP | ✅ Same session (if load works) |
| RELOAD_SESSION | ✅ Same session |
| FORK_SESSION | ⚠️ New session, forked from point |
| WAIT_FOR_BACKEND | ✅ Same session |

---

## 10. Tool Side-Effect Safety

### 10.1 Safety Gate Logic

```python
def is_safe_to_replay(self) -> bool:
    """ONLY safe if NO side effects observed."""
    return not self.assistant_output_observed and not self.tool_activity_observed
```

### 10.2 Side Effect Categories

| Category | Examples | Detectable? |
|----------|----------|-------------|
| File writes | `developer__write`, `edit` | ✅ Tool started |
| Shell commands | `developer__shell`, `bash` | ✅ Tool started |
| MCP tool calls | Any MCP tool | ✅ Tool started |
| API calls | HTTP via tools | ✅ Tool started |
| Sub-agent spawn | `summon`, delegation | ✅ Tool started |
| Assistant text | Streaming chunks | ✅ Chunks received |

### 10.3 Safety Rule

> **NEVER replay if `assistant_output_observed` OR `tool_activity_observed`**

---

## 11. Replay Safety

### 11.1 Replay Conditions

| Condition | Safe? |
|-----------|-------|
| No assistant chunks, no tools | ✅ YES |
| Assistant chunks only | ❌ NO |
| Tools started (any status) | ❌ NO |
| Turn completed | ❌ NO (already done) |
| Turn cancelled | ❌ NO (may have partial effects) |

### 11.2 Fork-Based Alternative

When replay unsafe but recovery needed:
1. `agent/session/fork` at timestamp before failure
2. Replay in forked session
3. User chooses which branch to continue

---

## 12. Verification After Recovery

### 12.1 Verification Criteria

| Action | Verification |
|--------|--------------|
| WAIT_AND_RETRY | New turn completes with `TURN_COMPLETE` |
| CANCEL_AND_REPLAY | New turn starts, completes without same error |
| RECONNECT_ACP | Handshake succeeds, session loads |
| RELOAD_SESSION | `agent/session/load` returns success |
| FORK_SESSION | Fork returns new session ID |
| WAIT_FOR_BACKEND | `/status` returns `ok`, handshake succeeds |

### 12.2 Verification Failure

- Increments recovery attempt counter
- If max attempts reached → FAILED_PERMANENTLY
- Else → back to POSSIBLE_FAILURE for next backoff cycle

---

## 13. Crash/Disconnect Handling

### 13.1 Backend Crash Detection

| Signal | Detection |
|--------|-----------|
| WebSocket close | `ConnectionClosed` exception |
| Process exit | Observer process monitor |
| `/status` fails | Periodic health check |

### 13.2 Crash Response Flow

```
BACKEND_CRASH detected
       │
       ▼
Transition to POSSIBLE_FAILURE
       │
       ▼
Classify as BACKEND_CRASH (retryable, replay_safe)
       │
       ▼
Start backoff (WAIT_FOR_BACKEND action)
       │
       ▼
Poll /status until "ok"
       │
       ▼
Reconnect ACP WebSocket
       │
       ▼
Load session (agent/session/load)
       │
       ▼
Verify session loaded → RECOVERED
```

---

## 14. Logging & Diagnostics

### 14.1 Structured Logging

```json
{
  "timestamp": "2026-10-03T10:30:45.123Z",
  "level": "INFO",
  "component": "state_machine",
  "event": "STATE_TRANSITION",
  "from": "STREAMING",
  "to": "POSSIBLE_FAILURE",
  "trigger": "FAILURE_DETECTED",
  "details": {
    "failure_type": "RATE_LIMIT",
    "retryable": true,
    "replay_safe": false,
    "action": "WAIT_AND_RETRY",
    "session_id": "sess_abc123",
    "turn_phase": "ASSISTANT_STREAMING",
    "assistant_chunks": 5,
    "tools_started": 0
  }
}
```

### 14.2 Secret Redaction

All logs automatically redact:
- ACP tokens (`token=...`)
- API keys (`secret=...`)
- JSON `"token": "..."`
- JSON `"secret": "..."`

---

## 15. Configuration

### 15.1 Configuration Sources (Priority)

1. Default values (code)
2. YAML config file (`~/.guardian/config.yaml`)
3. Environment variables (`GUARDIAN_*`)

### 15.2 Key Settings

```yaml
safety_mode: observe_only          # observe_only | dry_run | auto_recover
discovery_host: "127.0.0.1"
discovery_port_range: [10000, 65535]
stale_threshold: 60.0              # seconds
backoff:
  initial_delay: 1.0
  max_delay: 60.0
  multiplier: 2.0
  jitter_type: FULL
  max_attempts: 3
  respect_retry_after: true
log_level: INFO
redact_secrets: true
```

### 15.3 Environment Variables

| Variable | Config Path |
|----------|-------------|
| `GUARDIAN_SAFETY_MODE` | safety_mode |
| `GUARDIAN_STALE_THRESHOLD` | stale_threshold |
| `GUARDIAN_GOOSE_TOKEN` | goose_token |
| `GUARDIAN_BACKOFF_INITIAL` | backoff.initial_delay |
| `GUARDIAN_BACKOFF_MAX` | backoff.max_delay |

---

## 16. Shutdown Behavior

### 16.1 Graceful Shutdown

1. Signal received (SIGINT/SIGTERM)
2. Stop accepting new events
3. Cancel all async tasks (backoff, stale check, heartbeat)
4. Disconnect WebSocket gracefully
5. Save config/state if persistence enabled
6. Exit

### 16.2 Forced Shutdown

- Second SIGINT → immediate exit
- No state saved
- WebSocket closed abruptly

---

## 17. Failure Scenarios (Tested)

| Scenario | Classification | Recovery | Safe? |
|----------|----------------|----------|-------|
| 429 during streaming | RATE_LIMIT | WAIT_AND_RETRY | Only if no tools |
| 500 after tool start | SERVER_ERROR | HUMAN_INTERVENTION | ❌ |
| Connection reset idle | CONNECTION_RESET | RECONNECT_ACP | ✅ |
| Backend crash mid-tool | BACKEND_CRASH | WAIT_FOR_BACKEND | ❌ (tool may have run) |
| 429 before first chunk | RATE_LIMIT | WAIT_AND_RETRY | ✅ |
| Stream stalls 90s | STREAM_STALE | CANCEL_AND_REPLAY | Only if no tools |
| User clicks Stop | USER_CANCELLED | HUMAN_INTERVENTION | N/A |
| Auth token expired | AUTHENTICATION_FAILURE | HUMAN_INTERVENTION | ❌ |
| Invalid model | INVALID_MODEL | HUMAN_INTERVENTION | ❌ |

---

## 18. Race Conditions

### 18.1 Identified Races

| Race | Mitigation |
|------|------------|
| Tool starts during cancel | Check tool_activity before cancel |
| Turn ends during backoff | State machine ignores stale events |
| Multiple failures same turn | Only first failure triggers recovery |
| Session fork during turn | Fork timestamp before current turn |

### 18.2 Synchronization

- Single-threaded async event loop
- State machine transitions atomic
- Turn context updated before event dispatch

---

## 19. Duplicate Execution Risks

### 19.1 Risk Matrix

| Scenario | Duplicate Risk | Mitigation |
|----------|----------------|------------|
| Replay after tool | HIGH | Blocked by safety gate |
| Replay after chunks | HIGH | Blocked by safety gate |
| Fork then replay both | MEDIUM | User chooses branch |
| Cancel + replay | LOW | Cancel sent first |
| Backend crash + reload | LOW | Session load is idempotent |

### 19.2 Duplicate Prevention

1. **Safety Gate** - Primary barrier
2. **Turn Context** - Tracks all side effects
3. **Fork Alternative** - Preserves original session
4. **Human-in-loop** - Required for ambiguous cases

---

## 20. Tests Required Before Enabling AUTO_RECOVER

| Test | Status |
|------|--------|
| Normal completion (TEST 1) | ⏳ Pending live validation |
| Tool execution (TEST 2) | ⏳ Pending live validation |
| Multiple turns (TEST 3) | ⏳ Pending live validation |
| Cancellation (TEST 4) | ⏳ Pending live validation |
| Natural failure observation (TEST 5) | ⏳ Pending live validation |
| Rate limit with no tools | ✅ Unit test |
| Rate limit with tools | ✅ Unit test (blocked) |
| 500 before tools | ✅ Unit test (allowed) |
| 500 after tools | ✅ Unit test (blocked) |
| Connection reset | ✅ Unit test |
| Backend crash | ✅ Unit test |
| Stale stream | ✅ Unit test |
| Stale with tool active | ✅ Unit test (blocked) |
| Cancellation | ✅ Unit test |
| Auth failure | ✅ Unit test |
| Invalid model | ✅ Unit test |
| Max attempts reached | ✅ Unit test |
| Exponential backoff | ✅ Unit test |
| Retry-After override | ✅ Unit test |
| Jitter types | ✅ Unit test |
| Safety gate all modes | ✅ Unit test |
| State machine transitions | ✅ Unit test |
| Turn context tracking | ✅ Unit test |
| Failure pattern coverage | ✅ Unit test |

---

## 21. Fault Injection Test Harness (Planned)

```python
class FaultInjector:
    """Inject faults into Goose ACP stream for testing."""
    
    async def inject_429(self, after_chunks: int = 0): ...
    async def inject_500(self, after_tool: bool = False): ...
    async def inject_timeout(self): ...
    async def inject_connection_reset(self): ...
    async def inject_stale_stream(self, duration: float): ...
    async def inject_backend_crash(self): ...
    async def inject_auth_failure(self): ...
```

---

## 22. Observability Requirements

### 22.1 Required Log Events

| Event | Fields |
|-------|--------|
| STATE_TRANSITION | from, to, trigger, session_id, turn_phase |
| FAILURE_DETECTED | type, retryable, replay_safe, action, confidence |
| RECOVERY_ATTEMPT | action, failure_type, attempt_number, delay |
| RECOVERY_RESULT | success, verification, details |
| SAFETY_CHECK | verdict, action, blocking_factors |
| BACKOFF | attempt, delay, total_waited, retry_after |

### 22.2 Metrics

- `guardian_turns_total{result="success|failure"}`
- `guardian_recovery_attempts_total{action,result}`
- `guardian_backoff_duration_seconds{attempt}`
- `guardian_stale_detections_total`
- `guardian_safety_checks_total{verdict}`

---

*End of Recovery Design Document*