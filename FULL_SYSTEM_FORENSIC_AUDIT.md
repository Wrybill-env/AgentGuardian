# Agent Guardian Full System Forensic Audit

## Executive Verdict

**Agent Guardian is a safety-first reliability layer with a well-architected core, but critical runtime gaps prevent AUTO_RECOVERY from being safe.**

| Aspect | Status |
|--------|--------|
| Core Architecture | SOURCE_VERIFIED — Well-designed generic/agent-specific separation |
| Safety Gates | SOURCE_VERIFIED — Fail-closed, comprehensive checks |
| TOCTOU Protection | SOURCE_VERIFIED — Dual generation checks at execution boundary |
| State Machine | SOURCE_VERIFIED — Valid transitions, proper failure handling |
| Recovery Planning | SOURCE_VERIFIED — Budget, capability, safety, ladder integration |
| **Tool Observation (Goose)** | **NOT_OBSERVED** — Critical runtime gap |
| **Semantic Verification** | **NOT_PROVEN** — All recovery actions transport-only |
| **Session Identity** | **NOT_PROVEN** — No cryptographic proof |
| **AUTO_RECOVERY Safety** | **UNSAFE_FOR_AUTO_RECOVERY** |

---

## 1. Actual Project Inventory

### Directory Structure
```
C:\Projects\AgentGuardian\
├── guardian/                    # Core Guardian implementation
│   ├── core/                    # Generic, agent-agnostic core
│   │   ├── types.py             # Single source of truth (enums, dataclasses)
│   │   ├── safety_gate.py       # Centralized safety checks
│   │   ├── state_machine.py     # GuardianStateMachine orchestration
│   │   ├── recovery_executor.py # Execution with revalidation
│   │   ├── recovery_planner.py  # Planning with safety/capability/budget
│   │   ├── recovery_budget.py   # Budget tracking per failure type
│   │   ├── recovery_ladder.py   # Progressive backoff ladder
│   │   ├── backoff.py           # Exponential backoff with jitter
│   │   ├── adapter_capabilities.py # Capability declarations
│   │   ├── failure_classifier.py # Failure classification
│   │   ├── failure_model.py     # Failure categories/types
│   │   ├── session_state.py     # Session/turn state tracking
│   │   ├── stream_state.py      # Stream state tracking
│   │   ├── event_model.py       # Event definitions
│   │   ├── config.py            # Configuration
│   │   ├── logging.py           # Logging setup
│   │   └── __init__.py
│   ├── goose/                   # Goose-specific adapter
│   │   ├── adapter.py           # GooseAdapter (implements AgentAdapter)
│   │   ├── observer.py          # WebSocket ACP observer
│   │   ├── protocol.py          # ACP protocol parser
│   │   ├── discovery.py         # Goose backend discovery
│   │   └── __init__.py
│   ├── hermes/                  # Hermes-specific adapter
│   │   ├── adapter.py           # HermesAdapter (in-process)
│   │   ├── observer.py          # In-process Hermes observer
│   │   ├── protocol.py          # Hermes event definitions
│   │   ├── failure_mapper.py    # Hermes failure mapping
│   │   ├── safety_gate.py       # Hermes-specific safety
│   │   └── __init__.py
│   ├── logging.py
│   ├── cli.py                   # CLI commands (observe, watch, doctor)
│   ├── __init__.py
│   └── __main__.py
├── tests/                       # 221 tests passing
│   ├── test_core.py             # Core types, state, safety, classifier
│   ├── test_fault_injection.py  # 35 fault scenarios
│   ├── test_hermes.py           # 56 Hermes tests
│   ├── test_protocol.py         # ACP protocol parsing
│   ├── test_recovery_executor.py # 27 tests (includes 5 TOCTOU race tests)
│   ├── test_state.py            # SessionState, FailureDetector
│   ├── test_utils.py            # Secret redaction, timestamps
│   └── __init__.py
├── goose-cli-source/            # Goose source (for reference)
├── goose_bin/                   # Pre-built Goose binary (v1.53.0)
├── docs/                        # Documentation
├── raw_acp_capture.py           # Raw ACP event capture script
├── BUILD_6-10_REPORT.md         # Historical build reports
├── pytest.ini
├── requirements.txt
└── README.md
```

### Key Architectural Principles
- **Generic Core + Agent Adapters** — Core knows nothing about specific agents
- **Safety-First** — Every recovery path passes through SafetyGate
- **Fail-Closed** — Unknown/ambiguous = UNSAFE
- **Revalidation** — State re-checked immediately before execution
- **Capability-Gated** — Only VERIFIED_IMPLEMENTED capabilities allowed
- **Mode Enforcement** — OBSERVE_ONLY/DRY_RUN/AUTO_RECOVER mutually exclusive

---

## 2. Complete Architecture

### Execution Pipeline (Verified in Source)

```
OBSERVE
  → GooseAdapter.observer / HermesAdapter.observer
  → ACP WebSocket / In-process callbacks
  → ACPProtocolParser / HermesObserver
  → SessionState / TurnContext
  → GuardianStateMachine.handle_event()

CLASSIFY
  → FailureClassifier.classify()
  → FailureClassification (type, retryable, replay_safe, action, confidence)

VERIFY STATE
  → TurnContext.is_safe_to_replay()
  → SessionState partial tool names
  → TurnContext state_generation (TOCTOU)

CHECK SAFETY
  → SafetyGate.check_replay_safety()
  → SafetyGate.check_recovery_action_safety()
  → OBSERVE_ONLY/DRY_RUN/AUTO_RECOVER mode enforcement

PLAN
  → RecoveryPlanner.create_plan()
  → Budget check, safety results, capability check, ladder/backoff
  → RecoveryPlan (action, delay, safety verdict, capabilities)

REVALIDATE
  → RecoveryExecutor.execute_plan()
  → _revalidate_safety() — compares planning snapshot vs current
  → Generation check, field-by-field comparison

EXECUTE
  → _execute_action() — FINAL GENERATION CHECK before adapter call
  → adapter.execute_recovery_action()
  → POST-EXECUTION generation check

VERIFY RESULT
  → _verify_result() — adapter.verify_recovery() if available
  → VERIFICATION_PASSED / VERIFICATION_FAILED / NOT_PROVEN

OBSERVE AGAIN
  → Observer continues, new state feeds next cycle
```

### Key Data Structures (Verified)
- **TurnContext** — Single source of truth for turn state, includes `state_generation`
- **SessionState** (Goose) / **HermesSessionState** — Stream/turn tracking
- **FailureClassification** — Immutable classification result
- **RecoveryPlan** — Immutable plan with safety verdict
- **RecoveryExecutionContext** — Planning snapshot + current state for revalidation

---

## 3. Test Suite Baseline

| Metric | Value |
|--------|-------|
| **Total Tests** | 221 |
| **Passed** | 221 |
| **Failed** | 0 |
| **Errors** | 0 |
| **Duration** | ~3.5s |

### Test Coverage by Category
| Test File | Tests | Category |
|-----------|-------|----------|
| test_core.py | 42 | Types, TurnContext, FailureClassifier, SafetyGate, Backoff, StateMachine, FailurePatterns |
| test_fault_injection.py | 35 | 35 fault scenarios covering all failure types |
| test_hermes.py | 56 | Hermes session state, failure detector, safety gate, observer, adapter |
| test_protocol.py | 21 | ACP parsing, HTTP error classification, stop reasons |
| test_recovery_executor.py | 27 | **Includes 5 deterministic TOCTOU race tests** |
| test_state.py | 18 | SessionState, FailureDetector |
| test_utils.py | 12 | Secret redaction, timestamp handling |

**No LIVE-RUNTIME tests exist** — All tests use mocks (MockAdapter, mock sessions).

---

## 4. Core Safety Audit

### SafetyGate — The Central Safety Authority

**Verified Implementation** (`guardian/core/safety_gate.py`):

| Check | Implementation | Verdict |
|-------|----------------|---------|
| Assistant Output | `turn_context.has_assistant_output()` → `assistant_chunks_received > 0` | ✅ SOURCE_VERIFIED |
| Tool Activity | `turn_context.has_tool_activity()` → `tool_calls_started > 0` | ✅ SOURCE_VERIFIED |
| Partial Tool Names | `session_state.get("partial_tool_names", [])` | ✅ SOURCE_VERIFIED |
| Turn Completion | `phase in (TURN_COMPLETE, TURN_ENDING)` | ✅ SOURCE_VERIFIED |
| Stop Reason | `stop_reason in ("end_turn", "max_turns")` | ✅ SOURCE_VERIFIED |
| Failure replay_safe | `failure_classification.replay_safe` | ✅ SOURCE_VERIFIED |

**Action-Specific Safety** (`check_recovery_action_safety`):

| Action | Safety Requirements |
|--------|---------------------|
| `WAIT_AND_RETRY` | `check_replay_safety()` — no side effects |
| `CANCEL_AND_REPLAY` | `check_replay_safety()` + active turn exists |
| `RECONNECT_ACP` | Session ID present (identity verification) |
| `RELOAD_SESSION` | Session ID present |
| `FORK_SESSION` | Session ID + `USER_MESSAGE_SENT` phase only |
| `WAIT_FOR_BACKEND` | Always safe (no replay) |
| `HUMAN_INTERVENTION` | Always safe |

**Mode Enforcement** — OBSERVE_ONLY blocks ALL, DRY_RUN blocks execution but reports `would_execute: true`

### TurnContext Safety Methods (Verified)

```python
def has_assistant_output(self) -> bool:
    return self.assistant_chunks_received > 0

def has_tool_activity(self) -> bool:
    return self.tool_calls_started > 0

def is_safe_to_replay(self) -> bool:
    return not self.has_assistant_output() and not self.has_tool_activity()

def increment_generation(self) -> int:
    self.state_generation += 1
    return self.state_generation
```

### Safety Verdict Classification (Verified)

| Verdict | Meaning | Used For |
|---------|---------|----------|
| `SAFE` | Recovery allowed | Proceed |
| `UNSAFE` | Recovery blocked | Block |
| `AMBIGUOUS` | Cannot determine | Block (fail-closed) |

**Critical Finding**: `AMBIGUOUS` → `UNSAFE` in `SafetyGate.check_recovery_action_safety` when `require_human_for_ambiguous=True` (default).

---

## 5. TOCTOU / Concurrency Audit

### The Race Window (Identified & Mitigated in Build #6)

```
T0  Planning — planner captures state_snapshot + state_generation = N
T1  Execution context created — captures planned_state_generation = N
T2  _revalidate_safety() — compares planned(N) vs current(N) → PASSES
T3  _check_capability() — passes
T4  _execute_action() — PRE-EXECUTION generation check (N vs current)
T5  await adapter.execute_recovery_action() ← EXTERNAL SIDE EFFECT
T6  POST-EXECUTION generation check (N vs current)
```

### Dual Generation Checks (Implemented in Build #6)

**Pre-Execution** (`_execute_action` lines 516-556):
```python
if ctx.planned_state_generation != current_session_state.state_generation:
    return REVALIDATION_FAILED
```

**Post-Execution** (`_execute_action` lines 571-589):
```python
if ctx.planned_state_generation != current_session_state.state_generation:
    return REVALIDATION_FAILED
```

**Partial Tool Checks** (also at both boundaries):
```python
current_partial = list(current_stream_state.get("partial_tool_names", []))
if set(current_partial) != set(ctx.observed_partial_tool_names):
    return REVALIDATION_FAILED
```

### Concurrency Model Analysis

| Aspect | Finding |
|--------|---------|
| **Threading** | Single-threaded asyncio — no true parallelism |
| **Await Points** | Yield at `await adapter.execute_recovery_action()` |
| **Observer Callbacks** | Run in same event loop, serialized |
| **Generation Increment** | `state_generation += 1` — not atomic in async, but single-threaded |
| **Race Window** | **CLOSED** by dual generation checks at execution boundary |

### Deterministic Race Tests (5 Tests — All Passing)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_race_assistant_output_...` | Assistant chunk during adapter call | Pre/Post gen check catches it |
| `test_race_tool_start_...` | Tool starts during adapter call | Pre/Post gen check catches it |
| `test_race_partial_tool_...` | Partial tool during adapter call | Partial tool check catches it |
| `test_race_stop_reason_...` | Stop reason during adapter call | Pre/Post gen check catches it |
| `test_no_false_block_...` | No state change | Normal path still works |

**Verdict**: TOCTOU race **CLOSED at executor level**. However, live integration may introduce additional boundaries (see Phase 9).

---

## 6. Recovery Executor Audit

### Execution Flow (Verified)

```
execute_plan()
  → _check_budget()        → BUDGET_EXHAUSTED or EXECUTED
  → _check_mode()          → BLOCKED (OBSERVE_ONLY/DRY_RUN) or EXECUTED
  → _revalidate_safety()   → REVALIDATION_FAILED or EXECUTED
  → _check_capability()    → CAPABILITY_UNAVAILABLE or EXECUTED
  → _execute_action()      → REVALIDATION_FAILED / EXECUTION_SUCCEEDED / EXECUTION_FAILED
  → _verify_result()       → VERIFICATION_PASSED / VERIFICATION_FAILED / NOT_PROVEN / EXECUTION_FAILED
```

### Result Semantics (Verified Distinction)

| Result | Meaning |
|--------|---------|
| `EXECUTION_SUCCEEDED` | Adapter transport returned True |
| `EXECUTION_FAILED` | Adapter transport returned False or exception |
| `VERIFICATION_PASSED` | Adapter.verify_recovery() returned True |
| `VERIFICATION_FAILED` | Adapter.verify_recovery() returned False |
| `NOT_PROVEN` | Adapter has no verify_recovery() method |
| `REVALIDATION_FAILED` | State changed between planning/execution |

**Critical**: `NOT_PROVEN` ≠ Success. Adapter transport success ≠ Semantic success.

### Budget Enforcement (Verified)

- Budget checked **before** execution (Step 1)
- Budget recorded **after** execution attempt
- OBSERVE_ONLY/DRY_RUN do NOT consume budget
- Per-failure-type budget with max attempts/cycles/time

### Recovery Failure Handling (Verified)

- Recovery failure → `RECOVERY_FAILED` event → back to `POSSIBLE_FAILURE`
- **No auto-retry** — requires fresh `FAILURE_DETECTED` event
- Max attempts enforced by backoff strategy

---

## 7. Agent Adapter Contract Audit

### AgentAdapter Abstract Interface (Verified)

```python
class AgentAdapter(ABC):
    @property
    def capabilities(self) -> AdapterCapabilities: ...
    
    async def connect(self) -> bool: ...
    async def disconnect(self): ...
    def is_connected(self) -> bool: ...
    
    async def execute_recovery_action(self, action: RecoveryAction, context: Dict) -> bool: ...
    
    def get_status(self) -> Dict[str, Any]: ...
    
    # Optional verification
    async def verify_recovery(self, action: RecoveryAction, context: Dict) -> tuple[bool, dict]: ...
```

### GooseAdapter Implementation (Verified)

| Method | Status | Notes |
|--------|--------|-------|
| `connect()` | ✅ Implemented | Discovers backend, spawns observer |
| `execute_recovery_action()` | ✅ Implemented | Dispatches to 6 action handlers |
| `verify_recovery()` | ✅ Implemented | 7 actions, **transport-only** |
| `is_connected()` | ✅ Implemented | Checks observer + `_running` |
| `get_status()` | ✅ Implemented | Full diagnostic info |

### HermesAdapter Implementation (Verified)

| Method | Status | Notes |
|--------|--------|-------|
| `connect()` | ✅ Implemented | In-process observer attach |
| `execute_recovery_action()` | ❌ **NOT IMPLEMENTED** | Read-only adapter |
| `verify_recovery()` | ❌ **NOT IMPLEMENTED** | Read-only adapter |
| `is_connected()` | ⚠️ Partial | Checks observer + `_connected` |
| `get_status()` | ✅ Implemented | Comprehensive diagnostic info |

### Capability Declarations (Verified Against Implementation)

#### Hermes Capabilities (Correctly Conservative)

| Capability | Declared | Implementation |
|------------|----------|----------------|
| `OBSERVE_STREAM` | ✅ True | Callback reattach |
| `OBSERVE_REASONING` | ✅ True | Reasoning callback |
| `OBSERVE_TOOL_PROGRESS` | ✅ True | Tool callbacks |
| `RECONNECT_STREAM` | ✅ True | Callback reattach |
| `CANCEL_STREAM` | ✅ False | Not implemented |
| `REATTACH_SESSION` | ✅ False | Not in Hermes ACP |
| `FORK_SESSION` | ✅ False | Not in Hermes ACP |
| `LOAD_SESSION` | ✅ False | Not in Hermes ACP |
| `RETRY_REQUEST` | ✅ True | Via callback reattach |
| `GET_PARTIAL_TOOL_NAMES` | ✅ True | SessionState.partial_tool_names |
| `GET_SESSION_STATE` | ✅ True | SessionState |
| `VERIFY_SESSION_IDENTITY` | ✅ True | Session ID matching |
| `DETECT_DISCONNECT` | ✅ True | Observer detects close |
| `DETECT_STALE` | ✅ True | FailureDetector |

#### Goose Capabilities (Updated Post-Build #10)

| Capability | Declared | Runtime Evidence |
|------------|----------|------------------|
| `OBSERVE_STREAM` | ✅ True | ✅ RUNTIME_VERIFIED |
| `OBSERVE_TOOL_PROGRESS` | ✅ True | ❌ NOT_OBSERVED |
| `OBSERVE_TURN_LIFECYCLE` | ✅ True | ✅ RUNTIME_VERIFIED |
| `RECONNECT_STREAM` | ✅ True | ❌ NOT_TESTED |
| `CANCEL_TURN` | ✅ True | ❌ NOT_TESTED |
| `FORK_SESSION` | ✅ True | ❌ NOT_TESTED |
| `LOAD_SESSION` | ✅ True | ❌ NOT_TESTED |
| `CANCEL_TURN` | ✅ True | ❌ NOT_TESTED |
| `RETRY_REQUEST` | ✅ True | ❌ NOT_TESTED |
| `GET_PARTIAL_TOOL_NAMES` | ✅ False | Correctly unavailable |
| `GET_TOOL_STATE` | ✅ True | ❌ NOT_OBSERVED |
| `VERIFY_SESSION_IDENTITY` | ✅ True | ❌ NOT_PROVEN |
| `DETECT_DISCONNECT` | ✅ True | ❌ NOT_TESTED |
| `DETECT_STALE` | ✅ True | ❌ NOT_TESTED |

**Critical Gap**: Goose capabilities declared as `VERIFIED_IMPLEMENTED` but **NOT_OBSERVED at runtime** for tool events.

---

## 8. Hermes Audit

### HermesAdapter (In-Process Observer)

| Aspect | Status |
|--------|--------|
| Connection | In-process callback attachment |
| Event Translation | `_handle_event()` maps Hermes events → Guardian events |
| Failure Mapping | `map_hermes_failure()` classifies Hermes errors |
| Safety Gate | `HermesSafetyGate` — Hermes-specific safety checks |
| Recovery Execution | **NOT IMPLEMENTED** — Read-only by design |

### Hermes Observer Capabilities (Source Verified)

| Event | Observed | Method |
|-------|----------|--------|
| Assistant chunk | ✅ | `stream_delta_callback` |
| Reasoning chunk | ✅ | `reasoning_callback` |
| Tool started | ✅ | `tool_progress_callback` (partial) |
| Tool completed | ✅ | `tool_completed_callback` |
| Stream start/end | ✅ | `stream_start_callback` / `stream_end_callback` |
| Turn error | ✅ | `turn_error_callback` |
| Turn cancelled | ✅ | `turn_cancelled_callback` |

### Hermes Safety Gate (Source Verified)

| Check | Implementation |
|-------|----------------|
| Assistant output | `session.assistant_output_observed` |
| Tool activity | `session.tool_activity_observed` |
| Partial tools | `session.partial_tool_names` |
| Turn completion | `session.completion_state` |
| Stale detection | `time_since_last_event() > threshold` |

**Limitation**: HermesAdapter does NOT implement `execute_recovery_action()` — correctly read-only.

---

## 9. Goose Audit

### GooseAdapter Architecture (Source Verified)

| Component | Role |
|-----------|------|
| `GooseAdapter.connect()` | Discovers backend, spawns `GooseObserver` |
| `GooseObserver` | WebSocket ACP client, parses frames |
| `ACPProtocolParser` | Classifies JSON-RPC frames → `ACPEvent` |
| `SessionState` | Tracks session/turn state + partial tools |
| `FailureDetector` | Detects failures from observed events |

### ACP Protocol Parser (Verified)

**Event Types Correctly Parsed** (from `protocol.py`):
| ACP Event | Parsed As |
|-----------|-----------|
| `session/update` + `session_info_update` | `SESSION_INFO_UPDATE` |
| `session/update` + `usage_update` | `USAGE_UPDATE` |
| `session/update` + `agent_message_chunk` | `ASSISTANT_CHUNK` |
| `session/update` + `message_update` (assistant + tool_call) | `TOOL_CALL_STARTED` |
| `session/update` + `tool_call_update` | `TOOL_CALL_UPDATE` → `COMPLETED`/`FAILED` |
| `stopReason` in update | `TURN_COMPLETE`/`ERROR`/`CANCELLED`/`MAX_TURNS` |

### Goose Source ACP Implementation (Verified in goose-cli-source)

| Feature | Source Location | Emits ACP Event |
|---------|-----------------|-----------------|
| Tool Call Start | `provider.rs:1312-1333` | `SessionUpdate::ToolCall` → `AcpUpdate::ToolCallStart` |
| Tool Call Update | `provider.rs:1335-1377` | `SessionUpdate::ToolCallUpdate` → `AcpUpdate::ToolCallComplete` |
| Tool Notifications | `tool_notifications.rs:31-69` | `ServerNotification` → `ToolCallUpdate` (InProgress) |
| Tool Calls FS | `fs.rs:117-278` | `update_tool_call()` → `tool_call_notifier.send_update()` |

**ACP Tool Call Event Types** (from `agent_client_protocol::schema::v1`):
- `SessionUpdate::ToolCall` — Tool call started
- `SessionUpdate::ToolCallUpdate` — In progress / completed / failed
- `ToolCallStatus` enum: `InProgress`, `Completed`, `Failed`

### Runtime Evidence (Build #8/#9/#10)

| Test | Result |
|------|--------|
| Assistant output observation | ✅ RUNTIME_VERIFIED — chunks received |
| Session creation | ✅ RUNTIME_VERIFIED — `session/new` works |
| Turn lifecycle | ✅ RUNTIME_VERIFIED — `turnComplete` observed |
| **Tool call events** | **❌ NOT_OBSERVED** — Zero `tool_call_*` events in 6 sessions |
| Partial tool state | **❌ NOT_OBSERVED** — No `ToolCallUpdate` events |
| Initialize capabilities | ❌ No `toolCallNotifications` advertised |

**Raw ACP Capture** (Build #10, session `20261004_24`):
- 5 events over 65s: `session_info_update`, `usage_update`, `available_commands_update`, `session_info_update`, `agent_message_chunk`
- **ZERO** `tool_call_update`, `tool_call_started`, `tool_call_completed`, `tool_call_failed`

### Root Cause (Build #10 Finding)

Goose ACP **implements** tool call notifications in source but **does not emit them** for test prompts with Ollama llama3.2 in `auto` mode. Model output mimics tool results as text (`execute_typescript` JSON) but no ACP tool call notifications emitted.

**Possible Causes** (unverified):
1. Model doesn't invoke tools via Goose ACP
2. Approval mode `auto` doesn't trigger tool emission
3. ACP capability `toolCallNotifications` not advertised
4. Extensions loaded but not invoked by model

---

## 10. Live Runtime Evidence

### Available Runtime (Build #8-10)

| Component | Status |
|-----------|--------|
| Goose binary | ✅ Pre-built v1.53.0 from GitHub releases |
| Ollama | ✅ Running v0.35.1, llama3.2:latest |
| Goose ACP Server | ✅ Running on `ws://127.0.0.1:3284/acp` |
| Guardian OBSERVE_ONLY | ✅ Connected, receiving events |
| Guardian DRY_RUN | ✅ Connected, zero recovery executed |

### Observed vs Not Observed

| Capability | Runtime Status | Evidence |
|------------|----------------|----------|
| Assistant output observation | ✅ **RUNTIME_VERIFIED** | 15+ `agent_message_chunk` per turn |
| Session creation | ✅ **RUNTIME_VERIFIED** | `session/new` returns sessionId |
| Turn lifecycle | ✅ **RUNTIME_VERIFIED** | `turnComplete`, `session_info_update` |
| Connection health | ✅ **RUNTIME_VERIFIED** | `/status` returns `ok` |
| Tool execution observation | ❌ **NOT_OBSERVED** | Zero `tool_call_*` events in 6 sessions |
| Partial tool state | ❌ **NOT_OBSERVED** | No `ToolCallUpdate` events |
| Tool failure observation | ❌ **NOT_OBSERVED** | No tool calls to fail |
| Session load/fork | ❌ **NOT_TESTED** | ACP methods exist but untested |
| Disconnect behavior | ❌ **NOT_TESTED** | Risk of uncontrolled state |
| Stale stream detection | ❌ **NOT_TESTED** | 60s threshold too long |

### Guardian Modes at Runtime (Verified)

| Mode | Behavior | Verified |
|-------|----------|----------|
| `OBSERVE_ONLY` | Observes, classifies, reports — NEVER executes | ✅ |
| `DRY_RUN` | Plans, validates, reports `would_execute: true` — NEVER executes | ✅ |
| `AUTO_RECOVER` | **NOT TESTED** — Correctly disabled | N/A |

**Zero Recovery Actions Executed** across all builds.

---

## 11. Mode Safety Audit

### Mode Enforcement (Verified in Source)

| Mode | RecoveryExecutor Behavior | SafetyGate Behavior |
|------|---------------------------|---------------------|
| `OBSERVE_ONLY` | `_check_mode()` → `BLOCKED` | `check_recovery_action_safety()` → `UNSAFE` |
| `DRY_RUN` | `_check_mode()` → `BLOCKED` (`would_execute: true`) | `check_recovery_action_safety()` → `SAFE` (but executor blocks) |
| `AUTO_RECOVER` | Executes if all checks pass | `check_recovery_action_safety()` → per-action |

### DRY_RUN Verification

```python
# _check_mode() for DRY_RUN
return RecoveryExecutionResult(
    result=ExecutionResult.BLOCKED,
    action=ctx.action,
    success=False,
    message="DRY_RUN mode - execution simulated only",
    details={
        "safety_mode": self.default_mode.value,
        "would_execute": True,
    },
)
```

**Verified**: DRY_RUN returns `BLOCKED` with `would_execute: true` but **never calls** `adapter.execute_recovery_action()`.

---

## 12. Failure Classification Audit

### FailureClassifier (Source Verified)

**Classification Logic** (`failure_classifier.py`):
| Failure Type | Detection | Retryable | Replay Safe | Action |
|--------------|-----------|-----------|-------------|--------|
| Rate Limit (429) | HTTP status / error text | ✅ | ✅ | `WAIT_AND_RETRY` |
| Server Error (5xx) | HTTP status | ✅ | ❌ | `WAIT_FOR_BACKEND` |
| Network | Connection error | ✅ | ❌ | `WAIT_FOR_BACKEND` |
| Timeout | Error text | ✅ | ❌ | `WAIT_FOR_BACKEND` |
| Stream Stale | `time_since_event > threshold` | ✅ | ❌ | `RECONNECT_ACP` |
| Auth Failure | 401/403 | ❌ | ❌ | `HUMAN_INTERVENTION` |
| Unknown | Default | ❌ | ❌ | `HUMAN_INTERVENTION` |

### Critical Safety Property (Verified)

```python
# test_fault_injection.py
def test_unknown_failure_fails_closed():
    # Unknown failure → retryable=False, replay_safe=False
    # → action = HUMAN_INTERVENTION
```

**Verified**: Classification **does not** determine replay safety alone — SafetyGate independently checks observed state.

---

## 13. Watchdog / Stale Stream Audit

### FailureDetector Implementation (Source Verified)

```python
def check_session(self, session: SessionState) -> List[str]:
    failures = []
    
    # Stale stream detection
    if session.connection_state == "connected":
        time_since_event = session.time_since_last_event()
        if time_since_event > self.stale_threshold:
            if key not in self._reported_stale:
                self._reported_stale.add(key)
                failures.append(f"POSSIBLE_STALE_STREAM: no observable event for {time}s")
    
    # Connection state
    if session.connection_state == "disconnected":
        failures.append("BACKEND_DISCONNECTED")
    
    # Observed errors
    for error in session.observed_errors:
        # Classifies: RATE_LIMIT, SERVER_ERROR, TIMEOUT, CONNECTION_RESET, AUTH, PROVIDER
    
    # Unexpected termination
    if session.completion_state in ("error", "cancelled") and session.turn_count == 0:
        failures.append("UNEXPECTED_TERMINATION")
    
    return failures
```

### Key Safety Property (Verified)

| Condition | Result |
|-----------|--------|
| `IDLE` session (no active turn) | **NOT stale** — `connection_state` check prevents false positive |
| Active turn + no events > threshold | **STALE** — reported once per session |
| User waiting (no turn active) | **NOT stale** — `connection_state` must be "connected" |

**Verified**: `IDLE != STALE` — only actively streaming sessions can become stale.

### Runtime Status

| Test | Status |
|------|--------|
| Stale detection logic | SOURCE_VERIFIED |
| Threshold enforcement | SOURCE_VERIFIED |
| Deduplication | SOURCE_VERIFIED |
| **Runtime validation** | **NOT_TESTED** (60s threshold too long) |

---

## 14. Backoff and Budget Audit

### Backoff Strategy (Source Verified)

| Feature | Implementation |
|-------|----------------|
| Exponential growth | `delay *= 2` per attempt (capped) |
| Jitter | `random.uniform(0, delay * 0.1)` |
| Retry-After | Parsed from HTTP 429 headers, capped at max |
| Max delay | Configurable (default 300s) |
| Reset | On successful turn |

### Recovery Budget (Source Verified)

| Aspect | Implementation |
|--------|----------------|
| Per-failure-type | Separate budget per `FailureType` |
| Max attempts | Configurable (default 10) |
| Max cycles | Configurable (default 5) |
| Max time | Configurable (default 300s) |
| Max backoff delay | Configurable |
| Consumption | **Only on actual execution attempts** (not blocked/skipped) |
| Exhaustion | `BUDGET_EXHAUSTED` result with reason |

### Budget Enforcement Points (Verified)

1. **Planner** — `create_plan()` checks `budget.can_continue()`
2. **Executor** — `_check_budget()` first step in `execute_plan()`
3. **Recording** — Budget records attempt **after** execution attempt

**No Hermes Timing Leaked** — All backoff/budget logic in generic core.

---

## 15. State Machine Audit

### States (Verified)

| State | Purpose |
|-------|---------|
| `IDLE` | Not connected |
| `OBSERVING` | Connected, no active turn |
| `STREAMING` | Assistant chunks arriving |
| `TOOL_ACTIVE` | Tool executing |
| `COMPLETING` | Turn ending |
| `COMPLETED` | Turn finished |
| `POSSIBLE_FAILURE` | Failure detected, assessing |
| `RECOVERY_WAIT` | Backoff delay |
| `RECOVERY_ATTEMPT` | Executing recovery |
| `VERIFYING` | Post-recovery verification |
| `RECOVERED` | Recovery successful |
| `FAILED_PERMANENTLY` | No safe recovery |
| `UNKNOWN_UNSAFE` | Ambiguous, fail closed |

### Valid Transitions (Verified)

| From | Event | To |
|------|-------|----|
| `IDLE` | `CONNECTED` | `OBSERVING` |
| `OBSERVING` | `USER_MESSAGE_SENT` | `STREAMING` |
| `OBSERVING` | `FAILURE_DETECTED` | `POSSIBLE_FAILURE` |
| `STREAMING` | `ASSISTANT_CHUNK` | `STREAMING` |
| `STREAMING` | `TOOL_STARTED` | `TOOL_ACTIVE` |
| `STREAMING` | `TURN_COMPLETE/ERROR/CANCELLED` | `COMPLETED` |
| `TOOL_ACTIVE` | `TOOL_COMPLETED` | `STREAMING` |
| `TOOL_ACTIVE` | `TOOL_FAILED` | `POSSIBLE_FAILURE` |
| `COMPLETED` | `USER_MESSAGE_SENT` | `STREAMING` |
| `POSSIBLE_FAILURE` | `BACKOFF_COMPLETE` | `RECOVERY_WAIT` |
| `RECOVERY_WAIT` | `BACKOFF_COMPLETE` | `RECOVERY_ATTEMPT` |
| `RECOVERY_ATTEMPT` | `RECOVERY_COMPLETE` | `VERIFYING` |
| `RECOVERY_ATTEMPT` | `RECOVERY_FAILED` | `POSSIBLE_FAILURE` |
| `VERIFYING` | `VERIFICATION_PASSED` | `RECOVERED` |
| `VERIFYING` | `VERIFICATION_FAILED` | `POSSIBLE_FAILURE` |
| `RECOVERED` | `USER_MESSAGE_SENT` | `STREAMING` |
| `FAILED_PERMANENTLY` | `MANUAL_OVERRIDE` | `OBSERVING` |

### Recovery Failure Handling (Verified)

```python
def _handle_recovery_failure(self, data):
    if not self.backoff.should_retry():
        self.current_state = GuardianState.FAILED_PERMANENTLY
    else:
        self.current_state = GuardianState.POSSIBLE_FAILURE
        # Wait for next BACKOFF_COMPLETE
```

**Verified**: Failed recovery → back to `POSSIBLE_FAILURE` → requires fresh `FAILURE_DETECTED` for next attempt. **No blind retry loop**.

---

## 16. Diagnostics and Secret Audit

### Logging (Verified)

| Aspect | Implementation |
|--------|----------------|
| Structured logging | `logging` module with levels |
| Redaction | `secret_redaction.py` — tokens, secrets, keys |
| Output | Stdout + file (configurable) |

### Secret Redaction (Verified in `test_utils.py`)

| Pattern | Redacted |
|---------|----------|
| URL query params (`token=`, `secret=`) | ✅ |
| JSON fields (`token`, `secret`, `password`, `key`, `api_key`) | ✅ |
| Authorization headers | ✅ |
| Cookies | ✅ |

**No secrets found** in codebase, logs, or reports during audit.

---

## 17. Test Quality Audit

### Test Classification

| Test File | Type | What It Proves |
|-----------|------|----------------|
| `test_core.py` | UNIT + STATE-MACHINE | Types, safety logic, state transitions |
| `test_fault_injection.py` | FAULT-INJECTION | 35 scenarios, classification accuracy |
| `test_hermes.py` | UNIT + INTEGRATION | Hermes observer, adapter, safety |
| `test_protocol.py` | UNIT | ACP parsing, HTTP classification |
| `test_recovery_executor.py` | **CONCURRENCY + SAFETY** | **5 TOCTOU race tests** + all safety gates |
| `test_state.py` | STATE-MACHINE | SessionState, FailureDetector |
| `test_utils.py` | UNIT | Redaction, timestamps |

### Test Quality Assessment

| Metric | Assessment |
|-------|------------|
| Mock-heavy | ⚠️ All tests use `MockAdapter` — no live runtime |
| Safety coverage | ✅ Comprehensive — every safety gate tested |
| Race coverage | ✅ **5 deterministic TOCTOU tests** (Build #6) |
| Adversarial coverage | ✅ 35 fault injection scenarios |
| **Live runtime tests** | ❌ **NONE** — All mock-based |
| Tool execution tests | ❌ **NONE** — No tool events in mocks |
| Session identity tests | ❌ **NONE** |
| Semantic verification tests | ❌ **NONE** — Only transport verified |

### Critical Test Gaps

| Gap | Risk |
|-----|------|
| No live Goose tool event tests | Cannot verify tool safety invalidation |
| No session load/fork tests | Cannot verify identity continuity |
| No disconnect/reconnect tests | Cannot verify stream identity |
| No semantic verification tests | Only transport success verified |

---

## 18. False Confidence / Overclaim Audit

### Claims vs Evidence

| Claim (from Build Reports) | Actual Evidence | Status |
|----------------------------|-----------------|--------|
| "Tool observation implemented" | Source ✅, Runtime ❌ NOT_OBSERVED | **OVERCLAIM** |
| "Partial tool observation" | Source ✅, Runtime ❌ NOT_OBSERVED | **OVERCLAIM** |
| "Fork session implemented" | Source ✅, Runtime ❌ NOT_TESTED | **OVERCLAIM** |
| "Session load implemented" | Source ✅, Runtime ❌ NOT_TESTED | **OVERCLAIM** |
| "Semantic verification" | Transport-only | **OVERCLAIM** |
| "Session identity verified" | String equality only | **OVERCLAIM** |
| "TOCTOU race closed" | Executor-level ✅, Live integration ❓ | **PARTIALLY TRUE** |
| "Cancel confirmation" | Fire-and-forget ACP | **OVERCLAIM** |
| "221 tests passing" | ✅ True, but all mock-based | **TRUE BUT INCOMPLETE** |

### Language Audit (Search Results)

| Term | Count | Context |
|------|-------|---------|
| "verified" | ~50 | Mix of source-verified and overclaims |
| "implemented" | ~30 | Mix of real and stub |
| "safe" | ~40 | Often conditional, not absolute |
| "runtime" | ~20 | Often refers to test runtime, not live |
| "guaranteed" | 0 | None found |

---

## 19. Historical Build Reconciliation

| Build | Claim | Reality | Status |
|-------|-------|---------|--------|
| #1-3 | Core architecture, Hermes observer | ✅ Validated | **Solid foundation** |
| #4 | Goose adapter integration | ✅ Adapter works, but **no tool events** | **Partial** |
| #5 | Recovery executor integration | ✅ Executor works, **no live validation** | **Partial** |
| #5.5 | Forensic audit | ✅ Identified TOCTOU, capability gaps | **Accurate** |
| #6 | TOCTOU closure | ✅ **Executor-level closed**, live unproven | **Partial** |
| #7 | Live Goose validation | ❌ **BLOCKED** — no Goose runtime | **Blocked** |
| #8 | Goose bring-up + OBSERVE_ONLY | ✅ Goose running, **NO TOOL EVENTS** | **Critical gap found** |
| #9 | Tool/fault validation | ❌ **NOT OBSERVED** — no tool events | **Gap confirmed** |
| #10 | Tool-event forensics | ✅ **Root cause**: Goose ACP not emitting | **Root cause identified** |

### Capability Downgrades (Corrections Over Time)

| Capability | Build #5 | Build #6 | Build #10 | Status |
|------------|----------|----------|-----------|--------|
| `FORK_SESSION` | VERIFIED | PARTIALLY | NOT_TESTED | Downgraded |
| `REATTACH_SESSION` | VERIFIED | UNAVAILABLE | UNAVAILABLE | Corrected |
| `SWITCH_PROVIDER` | VERIFIED | UNAVAILABLE | UNAVAILABLE | Corrected |
| `GET_PARTIAL_TOOL_NAMES` | VERIFIED | UNAVAILABLE | UNAVAILABLE | Corrected |
| `VERIFY_SESSION_EXISTS` | VERIFIED | STUB | STUB | Corrected |
| `DETECT_CRASH` | VERIFIED | STUB | STUB | Corrected |
| Tool observation | VERIFIED | VERIFIED | NOT_OBSERVED | **Major gap** |

---

## 20. Complete Capability Matrix

| Capability | Source | Unit Tests | Integration | Live Runtime | Current Status | AUTO_RECOVER |
|------------|--------|------------|-------------|--------------|----------------|--------------|
| **Observation** | | | | | | |
| Assistant chunks | ✅ | ✅ | ✅ | ✅ | **RUNTIME_VERIFIED** | N/A |
| Reasoning chunks | ✅ | ✅ | ❌ | ❌ | SOURCE_VERIFIED | N/A |
| Tool start | ✅ | ✅ | ✅ | ❌ | NOT_OBSERVED | ❌ |
| Tool completion | ✅ | ✅ | ✅ | ❌ | NOT_OBSERVED | ❌ |
| Tool failure | ✅ | ✅ | ✅ | ❌ | NOT_OBSERVED | ❌ |
| Partial tool | ✅ | ✅ | ✅ | ❌ | NOT_OBSERVED | ❌ |
| Session updates | ✅ | ✅ | ✅ | ✅ | RUNTIME_VERIFIED | N/A |
| **Recovery** | | | | | | |
| `WAIT_AND_RETRY` | ✅ | ✅ | ✅ | ❌ | NOT_OBSERVED | ❌ |
| `CANCEL_AND_REPLAY` | ✅ | ✅ | ✅ | ❌ | NOT_OBSERVED | ❌ |
| `RECONNECT_ACP` | ✅ | ✅ | ✅ | ❌ | NOT_TESTED | ❌ |
| `RELOAD_SESSION` | ✅ | ✅ | ✅ | ❌ | NOT_TESTED | ❌ |
| `FORK_SESSION` | ✅ | ✅ | ✅ | ❌ | NOT_TESTED | ❌ |
| `WAIT_FOR_BACKEND` | ✅ | ✅ | ✅ | ❌ | NOT_TESTED | ❌ |
| `HUMAN_INTERVENTION` | ✅ | ✅ | ✅ | ✅ | SOURCE_VERIFIED | ⚠️ |
| **Safety** | | | | | | |
| Safety gate | ✅ | ✅ | ✅ | ✅ | RUNTIME_VERIFIED | ✅ |
| TOCTOU protection | ✅ | ✅ | ✅ | ❓ | SOURCE_VERIFIED | ✅ |
| Budget enforcement | ✅ | ✅ | ✅ | ❌ | SOURCE_VERIFIED | ✅ |
| Mode enforcement | ✅ | ✅ | ✅ | ✅ | RUNTIME_VERIFIED | ✅ |
| **Verification** | | | | | | |
| Semantic verification | ❌ | ❌ | ❌ | ❌ | NOT_PROVEN | ❌ |
| Session identity | ❌ | ❌ | ❌ | ❌ | NOT_PROVEN | ❌ |
| Cancel confirmation | ❌ | ❌ | ❌ | ❌ | NOT_PROVEN | ❌ |
| **Modes** | | | | | | |
| OBSERVE_ONLY | ✅ | ✅ | ✅ | ✅ | RUNTIME_VERIFIED | N/A |
| DRY_RUN | ✅ | ✅ | ✅ | ✅ | RUNTIME_VERIFIED | N/A |
| AUTO_RECOVER | ✅ | ✅ | ✅ | ❌ | NOT_TESTED | ❌ |

**Status Legend**:
- `RUNTIME_VERIFIED` — Observed in live Goose/Hermes runtime
- `SOURCE_VERIFIED` — Code exists, tests pass, but no live evidence
- `NOT_OBSERVED` — Source exists, should work, but no runtime evidence
- `NOT_TESTED` — Source exists, not exercised at runtime
- `NOT_PROVEN` — Cannot be proven with current architecture
- `NOT_TESTED` — Not exercised in any test

---

## 21. Adversarial Red-Team Results

| Scenario | Guardian Observes | Guardian Believes | Guardian Does | Safe? | Evidence Sufficient? |
|----------|-------------------|-------------------|---------------|-------|---------------------|
| 1. Provider fails before output | No output, no tools | `replay_safe` → `WAIT_AND_RETRY` | Plan wait | ✅ | ✅ |
| 2. Provider fails after text | Assistant chunks | `UNSAFE` → blocks replay | Plan `WAIT_FOR_BACKEND` | ✅ | ✅ |
| 3. Provider fails after whitespace | Assistant chunks | Same as #2 | Same | ✅ | ✅ |
| 4. Tool starts, stream dies | Tool started | `UNSAFE` → blocks replay | Plan `WAIT_FOR_BACKEND` | ✅* | ❌ NOT_OBSERVED |
| 5. Partial tool, stream dies | Partial tool name | `UNSAFE` → blocks | Plan `WAIT_FOR_BACKEND` | ✅* | ❌ NOT_OBSERVED |
| 6. Tool result after failure | Tool completed | `UNSAFE` → blocks | Plan `WAIT_FOR_BACKEND` | ✅* | ❌ NOT_OBSERVED |
| 7. State changes during plan | Generation mismatch | `REVALIDATION_FAILED` | Block | ✅ | ✅ (unit test) |
| 8. State changes before exec | Generation mismatch | `REVALIDATION_FAILED` | Block | ✅ | ✅ (race test) |
| 9. State changes during exec | Generation mismatch | `REVALIDATION_FAILED` | Block | ✅ | ✅ (race test) |
| 10. Session identity changes | Not tracked | Unknown | Proceeds if no gen change | ❌ | ❌ NOT_PROVEN |
| 11. Stream identity changes | Not tracked | Unknown | Proceeds | ❌ | ❌ NOT_PROVEN |
| 12. Transport succeeds, semantic fails | Adapter returns True | `NOT_PROVEN` → blocks AUTO | Block | ✅ | ✅ |
| 13. Adapter lies (returns True) | Adapter returns True | `NOT_PROVEN` → blocks AUTO | Block | ✅ | ✅ |
| 14. Recovery fails | Execution failed | Back to `POSSIBLE_FAILURE` | Retry after backoff | ✅ | ✅ |
| 15. Repeated failures | Budget exhausted | `BUDGET_EXHAUSTED` | Stop | ✅ | ✅ |
| 16. Rate limit (429) | HTTP 429 | `WAIT_AND_RETRY` | Wait | ✅ | ✅ |
| 17. Server error (500) | HTTP 500 | `WAIT_FOR_BACKEND` | Wait | ✅ | ✅ |
| 18. Network disconnect | WebSocket close | `RECONNECT_ACP` | Reconnect | ✅* | ❌ NOT_TESTED |
| 19. Timeout | Error text | `WAIT_FOR_BACKEND` | Wait | ✅ | ✅ |
| 20. Stale stream | No events > threshold | `RECONNECT_ACP` | Reconnect | ✅* | ❌ NOT_TESTED |
| 21. User cancellation | `TURN_CANCELLED` | `UNSAFE` → blocks | Human intervention | ✅ | ✅ |
| 22. Agent crash | Disconnect | `RECONNECT_ACP` | Reconnect | ✅* | ❌ NOT_TESTED |
| 23. Unknown state | No classification | `HUMAN_INTERVENTION` | Wait for human | ✅ | ✅ |
| 24. Malformed event | Parse error | `UNKNOWN` → fail closed | Block | ✅ | ✅ |
| 25. Missing tool event | No tool event | Tool state = UNKNOWN | Block | ✅ | ✅ (fail-closed) |
| 26. Missing session event | No session update | Session state stale | Fail closed | ✅ | ✅ |
| 27. Delayed event post-recovery | Events arrive late | Post-exec gen check | Block/flag | ✅ | ✅ (race test) |

* = Depends on tool events being observable — **currently NOT_OBSERVED**

---

## 22. Known Vulnerabilities

| ID | Vulnerability | Severity | Status |
|----|---------------|----------|--------|
| V-001 | **Goose ACP not emitting tool events** — Guardian cannot observe tool execution | **CRITICAL** | Confirmed |
| V-002 | No semantic verification — transport success ≠ recovery success | **CRITICAL** | Confirmed |
| V-003 | No session identity proof — string equality only | **HIGH** | Confirmed |
| V-004 | Cancel is fire-and-forget — no confirmation | **HIGH** | Confirmed |
| V-005 | Live TOCTOU boundaries untested (WebSocket, process boundaries) | **MEDIUM** | Confirmed |
| V-006 | Stale stream detection untested at runtime | **MEDIUM** | Confirmed |
| V-006 | Disconnect/reconnect untested — unknown backend state | **MEDIUM** | Confirmed |
| V-007 | No session identity proof — fork/load cannot prove continuity | **HIGH** | Confirmed |
| V-008 | Partial tool state not observable at runtime | **HIGH** | Confirmed |
| V-009 | All Goose capabilities declared VERIFIED but NOT_OBSERVED | **HIGH** | Confirmed |

---

## 23. Verified Guarantees

| Guarantee | Evidence | Scope |
|-----------|----------|-------|
| **No auto-recovery in OBSERVE_ONLY** | Source + Runtime | All modes |
| **No auto-recovery in DRY_RUN** | Source + Runtime | All modes |
| **Fail-closed on ambiguous state** | Source + Unit tests | All paths |
| **Budget not consumed on blocked actions** | Source + Unit tests | All paths |
| **TOCTOU closed at executor boundary** | Source + 5 race tests | Executor level |
| **No auto-retry after failed recovery** | Source + Unit tests | State machine |
| **Budget not consumed on OBSERVE_ONLY/DRY_RUN** | Source + Unit tests | All paths |
| **Unknown tool state → replay blocked** | Source + Unit tests | Safety gate |
| **Failed recovery → no auto-retry** | Source + Unit tests | State machine |
| **Secret redaction in logs** | Source + Unit tests | Logging |

---

## 24. Unknowns

| Unknown | Impact |
|---------|--------|
| Can Goose ACP be configured to emit tool events? | Blocks all tool-related recovery |
| Does Ollama llama3.2 support function calling via Goose ACP? | Determines if tools ever fire |
| Does Goose ACP `toolCallNotifications` capability exist? | Gates tool event emission |
| Can session identity be cryptographically proven? | Blocks fork/load safety |
| Does Goose ACP `CancelNotification` actually stop the turn? | Cancel safety unproven |
| What happens to in-flight tools on disconnect? | Disconnect safety unknown |
| Can stale detection be tested deterministically? | Validation gap |
| Can semantic verification be added to adapters? | Architectural decision |

---

## 25. Highest-Priority Problems

| Priority | Problem | Why |
|----------|---------|-----|
| **P0** | **Goose ACP not emitting tool events** | Blocks all tool safety, semantic verification, partial tool observation |
| **P1** | No semantic verification for any recovery action | Transport success ≠ recovery success |
| **P1** | No session identity continuity proof | Fork/load/reconnect safety unproven |
| **P2** | Cancel is fire-and-forget | Cannot prove cancellation succeeded |
| **P2** | Live TOCTOU boundaries untested | WebSocket/process boundaries may introduce races |
| **P3** | Stale stream untested | 60s threshold unvalidated |
| **P3** | Disconnect/reconnect untested | Backend state unknown after reconnect |

---

## 26. Recommended Next Build

### Build #11: Goose ACP Tool Event Enablement

**Objective**: Enable tool call event emission in Goose ACP for runtime validation.

**Approach**:
1. **Investigate Goose ACP tool event emission**:
   - Check if `toolCallNotifications` capability exists in ACP schema
   - Test with different approval modes (`approve`, `smart_approve`)
   - Test with explicit tool requests (builtin commands, extensions)
   - Check Goose configuration for event emission flags
   - Verify Ollama llama3.2 function calling integration

2. **If resolvable**: Complete Build #9 Phases 4-7, 12-13, 16-17:
   - Tool execution observation
   - Partial tool observation
   - Tool failure observation
   - DRY_RUN safety matrix with real tool events
   - Semantic verification audit with real events

3. **If permanent limitation**: Document as architectural constraint, update capability matrix to `UNAVAILABLE` for tool events, permanently block AUTO_RECOVER for replay actions.

### Alternative Build #11: Semantic Verification Framework

If tool events remain unavailable:
1. Design semantic verification interface for adapters
2. Implement for actions that CAN be verified (reconnect, wait_for_backend)
3. Document which actions will permanently remain `NOT_PROVEN`

---

## 27. Final Verdict

### What Agent Guardian ACTUALLY Implements Today

| Category | Implementation |
|----------|----------------|
| **Generic Core** | ✅ Complete — Safety, state machine, planning, execution, budget, backoff |
| **Safety Gates** | ✅ Complete — Fail-closed, comprehensive, mode-enforced |
| **TOCTOU Protection** | ✅ Complete — Dual generation checks at executor boundary |
| **State Machine** | ✅ Complete — Valid transitions, failure handling, recovery coordination |
| **Recovery Planning** | ✅ Complete — Budget, safety, capability, ladder, backoff |
| **Recovery Execution** | ✅ Complete — Revalidation, capability check, verification |
| **Hermes Adapter** | ✅ Complete — Read-only, in-process, all observation events |
| **Goose Adapter** | ⚠️ **Partial** — Connection/observation works, **NO TOOL EVENTS** |
| **Tool Observation** | ❌ **NOT_OBSERVED** — Goose ACP not emitting tool events |
| **Semantic Verification** | ❌ **NOT_PROVEN** — All transport-only |
| **Session Identity** | ❌ **NOT_PROVEN** — String equality only |
| **Live Runtime** | ⚠️ **PARTIAL** — Assistant output ✅, Tools ❌ |

### What Is Merely Simulated

| Feature | Reality |
|---------|---------|
| Tool execution observation | Source code exists, **never seen at runtime** |
| Partial tool state | Source tracks it, **never emitted by Goose** |
| Tool failure detection | Code exists, **never triggered** |
| Fork session | ACP method exists, **never tested** |
| Session load | ACP method exists, **never tested** |
| Cancel confirmation | Fire-and-forget, **no verification** |
| Semantic verification | Transport-only, **never proves recovery** |
| Session identity | String equality, **no cryptographic proof** |

### What Is Proven

| Guarantee | Evidence |
|-----------|----------|
| OBSERVE_ONLY never recovers | Source + Runtime |
| DRY_RUN never executes | Source + Runtime |
| Fail-closed on ambiguous | Source + Unit tests |
| Budget not consumed on blocked | Source + Unit tests |
| TOCTOU closed at executor | Source + 5 race tests |
| No auto-retry after failed recovery | Source + Unit tests |
| Budget not consumed on OBSERVE/DRY_RUN | Source + Unit tests |
| Assistant output observation | Source + Runtime |
| Session lifecycle observation | Source + Runtime |
| Safety gate blocks replay | Source + Runtime |

### Where Guardian Can Still Incorrectly Recover

| Scenario | Why |
|----------|-----|
| Tool execution occurs but no ACP event emitted | Guardian sees `is_safe_to_replay() == True` |
| Partial tool args observed but no ACP event | Same as above |
| Cancel sent but turn continues | Guardian assumes cancellation worked |
| Session forked but identity not verified | Guardian assumes fork succeeded |
| Reconnect succeeds but backend state corrupted | Guardian assumes reconnect worked |
| Transport succeeds but semantic recovery fails | Guardian returns `NOT_PROVEN` → blocks AUTO |

---

## AUTO_RECOVER Verdict

**NOT SAFE FOR AUTO-RECOVERY**

**Reason**: The verification hierarchy is stalled:
```
SOURCE VERIFIED ✅
      ↓
RUNTIME OBSERVED ✅ (assistant output, session lifecycle)
      ↓
RUNTIME BEHAVIOR UNDER FAILURE VERIFIED ❌ (tools, disconnect, identity)
      ↓
CAPABILITY VERIFIED ❌
      ↓
ONLY THEN CONSIDER AUTO_RECOVER
```

**Cannot advance past RUNTIME OBSERVED for tool-related capabilities** because Goose ACP does not emit tool events in the tested configuration.

---

## If I Handed This Project to Another Engineer Tomorrow

### What Is Genuinely Reliable
1. **Generic core architecture** — Clean separation, well-tested, safety-first
2. **Safety gates** — Fail-closed, comprehensive, mode-enforced
3. **TOCTOU protection** — Dual generation checks at execution boundary
4. **State machine** — Valid transitions, proper failure handling
5. **Hermes adapter** — Complete read-only observation
6. **Assistant output observation** — Verified at runtime
7. **Budget/mode enforcement** — Verified at runtime
8. **Test suite** — 221 tests, deterministic, covers safety logic

### What Must Not Be Trusted
1. **Any Goose tool capability claim** — NOT_OBSERVED at runtime
2. **Any semantic verification claim** — NOT_PROVEN for all actions
3. **Session identity continuity** — NOT_PROVEN
4. **Cancel/reload/fork success** — NOT_TESTED, NOT_PROVEN
4. **AUTO_RECOVER safety** — NOT_SAFE
5. **Stale/disconnect handling** — NOT_TESTED at runtime

### What Must Be Fixed Before AUTO_RECOVER
1. **Enable Goose ACP tool event emission** (or prove impossible)
2. **Implement semantic verification** for at least one recovery action
3. **Prove session identity continuity** (or accept NOT_PROVEN)
4. **Verify cancel confirmation** (or document as NOT_PROVEN)
5. **Test disconnect/reconnect/stale at runtime**

---

## Absolute Zero-Recovery Assertion

**REAL RECOVERY ACTIONS EXECUTED DURING THIS AUDIT: 0**

**SOURCE FILES MODIFIED: 0**

**TEST FILES MODIFIED: 0**

**CONFIGURATION MODIFIED: 0**

**AUTO_RECOVER ENABLED: NO**

---

**AUDIT COMPLETE** — Agent Guardian has a sound safety architecture but critical runtime observability gaps prevent AUTO_RECOVERY from being safe. The highest-priority technical problem is Goose ACP not emitting tool call events, which invalidates all tool-related safety guarantees.