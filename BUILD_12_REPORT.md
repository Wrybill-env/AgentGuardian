# Build #12 — Goose Tool-Calling Reality Check

## 1. Executive Summary

**GOOSE TOOL EXECUTION: NOT_OBSERVED**

The Goose ACP tool-call infrastructure is **SOURCE_VERIFIED** (implemented in source code) but **NOT_OBSERVED** at runtime with the tested configuration (Goose 1.53.0 + Ollama + llama3.2). The model never produces structured `MessageContent::ToolRequest` — it generates text-only responses that sometimes resemble tool output (e.g., JSON with `success: true`) but never triggers the Goose tool execution pipeline.

**AUTO_RECOVER: NOT SAFE** — Tool observability remains a hard prerequisite.

---

## 2. Baseline

| Metric | Value |
|--------|-------|
| Test suite | 221 passed, 0 failed |
| Duration | ~7.8s |
| Project | C:\Projects\AgentGuardian (NOT a Git repository) |
| Previous reports | BUILD_8–11 reviewed |

---

## 3. Environment

| Component | Details |
|-----------|---------|
| Goose binary | `C:/Projects/AgentGuardian/goose_bin/goose.exe` (v1.53.0, pre-built from GitHub releases) |
| Goose mode | `serve --host 127.0.0.1 --port 3284 --dangerously-unauthenticated` |
| Ollama | `C:/Users/Mitansh Kulshreshtha/AppData/Local/Programs/Ollama/ollama.exe` (v0.35.1) |
| Model | llama3.2:latest (3.2B, Q4_K_M, `capabilities: ["completion", "tools"]`) |
| ACP endpoint | `ws://127.0.0.1:3284/acp` |
| Test workspace | `C:/tmp/goose-test` (disposable) |
| Guardian mode | OBSERVE_ONLY / DRY_RUN only |

---

## 4. Goose Version

**Goose 1.53.0** — Pre-built binary from official GitHub releases. Source available at `goose-cli-source/`.

---

## 5. Provider

**Ollama** (local) — Connected to Goose via environment variables `GOOSE_PROVIDER=ollama`, `GOOSE_MODEL=llama3.2`. The provider runs as a separate process; Goose ACP server communicates with it internally.

---

## 6. Model

**llama3.2** (3.2B, Q4_K_M) — Advertises `tools` capability in `/api/tags`. However, in the Goose ACP integration, it never produces structured tool calls — only text responses.

---

## 7. Configuration

| Setting | Value | Source |
|---------|-------|--------|
| `GOOSE_MODE` | `auto` (auto-approve) | Environment |
| `--dangerously-unauthenticated` | Yes | CLI flag |
| Extensions loaded | 12 (developer, computercontroller, code_execution, todo, etc.) | Observed in `available_commands_update` |
| `customNotifications` | `true` (client capability) | Initialize request |
| `toolCallLabelEnrichment` | `true` (client capability) | Initialize request |
| ACP protocol version | 1 | Initialize exchange |

---

## 8. Source Trace

### Complete Pipeline: Provider Response → Guardian State

| # | Boundary | Source Location | What Happens | Runtime Verified? |
|---|----------|-----------------|--------------|-------------------|
| 1 | Provider returns response | `provider.rs:AcpClientLoop.run()` lines 1269–1380 | Receives `SessionNotification` from ACP server | ✅ |
| 2 | Tool call detection | `provider.rs` lines 1282–1333 | Matches `SessionUpdate::ToolCall` → emits `AcpUpdate::ToolCallStart` | ❌ NO TOOL CALLS |
| 3 | Tool update detection | `provider.rs` lines 1335–1377 | Matches `SessionUpdate::ToolCallUpdate` → emits `AcpUpdate::ToolCallComplete` | ❌ NO TOOL UPDATES |
| 4 | Model response handling | `agent.rs:reply_internal()` | Streams provider response, categorizes tool requests | ❌ NO TOOL REQUESTS |
| 5 | Tool request categorization | `agent.rs:categorize_tool_requests()` | Separates `ToolRequest` from text/thinking | ❌ NO TOOL REQUESTS |
| 6 | Tool inspection/permission | `agent.rs:handle_approved_and_denied_tools()` | Runs inspectors, checks permissions | ❌ NOT TRIGGERED |
| 7 | Tool execution | `extension_manager/mod.rs:dispatch_tool_call()` → `McpClientTrait::call_tool()` | Executes tool via MCP | ❌ NOT TRIGGERED |
| 8 | Tool result → ACP (server) | `server.rs:forward_agent_stream()` lines 2196–2202 | `McpNotification` → `tool_notifications::tool_notification_update()` | ❌ NO MCP NOTIFICATIONS |
| 9 | ACP tool call start (server) | `server.rs:handle_message_content()` lines 1527–1528 | `ToolRequest` → `ToolCallNotifier.send_initial()` | ❌ NO TOOL REQUESTS |
| 10 | ACP tool call update (server) | `server.rs:handle_message_content()` lines 1558–1559 | `ToolResponse` → `ToolCallNotifier.send_update()` | ❌ NO TOOL RESPONSES |
| 11 | ACP transport | WebSocket | Delivers all notifications | ✅ VERIFIED |
| 12 | Guardian receiver | `observer.py` | Receives WebSocket frames | ✅ VERIFIED |
| 13 | Guardian parser | `protocol.py:_parse_session_update()` | Parses `ToolCallUpdate`, `MessageUpdate` with tool calls | ✅ SOURCE VERIFIED |
| 14 | Guardian observer state | `observer.py:GooseObserver._handle_event()` | Updates `SessionState.partial_tool_names`, counters | ❌ NO EVENTS TO PROCESS |

### ACP Tool Event Types Implemented (Source)

| Event | ACP Schema | Guardian Parses As |
|-------|------------|-------------------|
| `SessionUpdate::ToolCall` (status: `Pending`) | `agent_client_protocol::schema::v1` | `TOOL_CALL_STARTED` |
| `SessionUpdate::ToolCallUpdate` (status: `InProgress`) | `agent_client_protocol::schema::v1` | `TOOL_CALL_UPDATE` |
| `SessionUpdate::ToolCallUpdate` (status: `Completed`) | `agent_client_protocol::schema::v1` | `TOOL_CALL_COMPLETED` |
| `SessionUpdate::ToolCallUpdate` (status: `Failed`) | `agent_client_protocol::schema::v1` | `TOOL_CALL_FAILED` |
| `MessageUpdate` (role: `assistant`, content has `tool_call`) | `agent_client_protocol::schema::v1` | `TOOL_CALL_STARTED` |

### Initialize Capabilities (Server → Client)

```json
{
  "protocolVersion": 1,
  "agentCapabilities": {
    "loadSession": true,
    "promptCapabilities": {"image": true, "audio": false, "embeddedContext": true},
    "mcpCapabilities": {"http": true, "sse": false},
    "sessionCapabilities": {"list": {}, "delete": {}, "close": {}},
    "auth": {},
    "_meta": {"goose": {"recipeParameterScopes": {}, "localInference": {}}}
  }
}
```

**No `toolCallNotifications` capability advertised.** Client capabilities checked: `customNotifications`, `recipeParameterRequests`, `toolCallLabelEnrichment` — NOT `toolCallNotifications`.

---

## 9. Runtime Trace

### Sessions Tested

| Session | Prompt | Expected Tool | Result |
|---------|--------|---------------|--------|
| `20261004_18` | "Create a file test_output.txt..." | `write` | Text only |
| `20261004_19` | "Say hello" | (none) | Text only |
| `20261004_20` | "/todo add Test task" | `todo` | Text only |
| `20261004_21`–`23` | "hi" | (none) | Text only |
| `20261004_24` | File creation (raw capture) | `write` | Text only |
| `20261004_25` | File creation (raw capture) | `write` | Text only |
| `20261005_2` | File creation (raw capture, 60s) | `write` | Text only |
| `20261005_3` | File creation (raw capture, 60s) | `write` | Text only |
| `20261005_4` | "/todo add Test task" (explicit) | `todo` | Text only |

**All 9 sessions: ZERO tool events at ACP transport level.**

### Raw ACP Capture Summary (Session `20261005_2` — 60s, 209 events)

| Event Type | Count |
|------------|-------|
| `session_info_update` | 2 |
| `usage_update` | 1 |
| `available_commands_update` | 1 |
| `agent_message_chunk` | 205 |
| **Tool events** | **0** |

Model output was **text chunks** (e.g., "The function `write` is used to create a new file..."), including text that **resembles tool results** (JSON with `success: true`, `stdout`, `stderr`), but **never** structured ACP tool call notifications.

### Raw ACP Capture Summary (Session `20261005_3` — 60s, 239 events)

| Event Type | Count |
|------------|-------|
| `session_info_update` | 2 |
| `usage_update` | 1 |
| `available_commands_update` | 1 |
| `agent_message_chunk` | 235 |
| **Tool events** | **0** |

### Explicit `/todo` Test (Session `20261005_4` — 20s)

Events: `session_info_update`, `usage_update`, `available_commands_update`, `session_info_update`, then **timeout** — no further events, no tool events.

---

## 10. Raw ACP Evidence

### Complete Event Inventory (All Sessions)

| Session | Duration | Total Events | Tool Events | Tool Event Types |
|---------|----------|--------------|-------------|------------------|
| `20261004_18` | ~65s | 5 | 0 | — |
| `20261004_24` | ~65s | 5 | 0 | — |
| `20261004_25` | ~61s | 5 | 0 | — |
| `20261005_2` | ~60s | 209 | 0 | — |
| `20261005_3` | ~60s | 239 | 0 | — |
| `20261005_4` | ~20s | ~10 | 0 | — |

### Transport Verification

| Layer | Status |
|-------|--------|
| WebSocket connection | ✅ Established & stable |
| JSON-RPC 2.0 framing | ✅ Correct |
| Initialize handshake | ✅ Complete |
| Session creation | ✅ Success |
| Prompt delivery | ✅ Acknowledged |
| Event delivery | ✅ All 400+ events received |
| **Tool events in transport** | ❌ **NONE** |

---

## 11. Provider/Model Tool Decision

**VERDICT: NOT_OBSERVED**

The Goose agent (which calls the Ollama model internally) never produces `MessageContent::ToolRequest` in its response stream. The provider stream (`AcpProvider::stream()`) yields only `AcpUpdate::Text` and `AcpUpdate::Thought` — never `AcpUpdate::ToolCallStart` or `ToolCallComplete`.

### Evidence

1. **Provider stream** (`provider.rs` lines 912–1100): Only `Text` and `Thought` updates observed
2. **Agent reply loop** (`agent.rs` lines 2718–3200): `categorize_tool_requests()` receives empty `tool_requests` list
3. **No tool inspection** (`tool_inspection_manager`): Never triggered
4. **No tool execution** (`dispatch_tool_call`): Never called
5. **Model output**: Text chunks containing tool-like descriptions, sometimes fake JSON results, but never structured tool calls

---

## 12. Goose Tool Invocation

**VERDICT: NOT_OBSERVED**

| Stage | Source Implemented? | Runtime Triggered? |
|-------|---------------------|-------------------|
| Model decides to call tool | ✅ `reply_internal` | ❌ No tool requests from provider |
| Tool inspection/permission | ✅ `tool_inspection_manager` | ❌ Not triggered |
| Tool execution via MCP | ✅ `dispatch_tool_call` → `call_tool` | ❌ Not triggered |
| Tool result creation | ✅ `ToolStreamItem::Result` | ❌ Not produced |
| MCP notifications during execution | ✅ `tool_notifications.rs` | ❌ Not emitted |

---

## 13. Goose ACP Event Generation

**VERDICT: NOT_OBSERVED (Machinery exists but never triggered)**

| Event Type | Source Implementation | Runtime Emission |
|------------|----------------------|------------------|
| `SessionUpdate::ToolCall` (start) | ✅ `ToolCallNotifier.send_initial()` (server.rs:1528) | ❌ Never — no `ToolRequest` messages |
| `SessionUpdate::ToolCallUpdate` (progress) | ✅ `ToolCallNotifier.send_update()` + `tool_notifications.rs` | ❌ Never |
| `SessionUpdate::ToolCallUpdate` (complete) | ✅ `ToolCallNotifier.send_update()` (server.rs:1559) | ❌ Never |
| `SessionUpdate::ToolCallUpdate` (failed) | ✅ Same path | ❌ Never |

**The ACP event generation machinery IS implemented and correct** — it's simply never triggered because the model doesn't invoke tools.

---

## 14. Guardian Reception

**VERDICT: NO EVENTS TO RECEIVE**

Guardian's WebSocket connection receives all emitted events correctly (400+ events across captures). Zero tool events were transmitted, so Guardian received zero tool events.

---

## 15. Guardian Parsing

**VERDICT: SOURCE_VERIFIED — NOT AT FAULT**

| Raw ACP Event | Guardian Parses As | Implementation |
|---------------|-------------------|----------------|
| `session_update` + `session_info_update` | `SESSION_INFO_UPDATE` | ✅ Correct |
| `session_update` + `usage_update` | `USAGE_UPDATE` | ✅ Correct |
| `session_update` + `available_commands_update` | `SESSION_UPDATE` | ✅ Correct |
| `session_update` + `agent_message_chunk` | `ASSISTANT_CHUNK` | ✅ Correct |
| `session_update` + `tool_call_update` | `TOOL_CALL_UPDATE` → `COMPLETED`/`FAILED` | ✅ Implemented, **NO RAW EVENTS** |
| `session_update` + `message_update` (assistant + tool_call) | `TOOL_CALL_STARTED` | ✅ Implemented, **NO RAW EVENTS** |

**Parser code** (`protocol.py:_parse_session_update()` lines 232–256): Correctly extracts `tool_name`, `tool_call_id`, `status` from `ToolCallUpdate` and `MessageUpdate`.

---

## 16. Guardian State Updates

**VERDICT: NO EVENTS TO PROCESS**

| Guardian Event | SessionState Update | Observed |
|----------------|---------------------|----------|
| `ASSISTANT_CHUNK` | `assistant_chunks_received++` | ✅ (200+ per session) |
| `SESSION_INFO_UPDATE` | `turn_count`, `phase`, `active_run_id` | ✅ |
| `USAGE_UPDATE` | Token tracking | ✅ |
| `TOOL_CALL_STARTED` | `tool_calls_started++`, `partial_tool_names.add()` | ❌ NO EVENTS |
| `TOOL_CALL_UPDATE` | `partial_tool_names` update | ❌ NO EVENTS |
| `TOOL_CALL_COMPLETED` | `tool_calls_completed++`, `partial_tool_names.remove()` | ❌ NO EVENTS |
| `TOOL_CALL_FAILED` | `tool_calls_failed++` | ❌ NO EVENTS |

### Safety Gate Impact

| Safety Check | Runtime Value | Safe for Replay? |
|--------------|---------------|------------------|
| `has_assistant_output()` | `True` | ❌ BLOCKED |
| `has_tool_activity()` | `False` | ✅ Would allow |
| `partial_tool_names` | Empty | ✅ Would allow |
| **Net Result** | **Assistant output = UNSAFE** | **REPLAY BLOCKED** |

**Critical**: Guardian correctly fails closed. Missing tool events don't create false safety.

---

## 17. Replay Safety Result

| Condition | Guardian Verdict | Verified? |
|-----------|-----------------|-----------|
| No output, no tools | SAFE (if all else proves) | ✅ N/A — output exists |
| Assistant output exists | BLOCK | ✅ Verified |
| Tool started | BLOCK | ❌ NOT OBSERVED |
| Tool completed | BLOCK | ❌ NOT OBSERVED |
| Partial tool | BLOCK | ❌ NOT OBSERVED |
| Unknown tool state | BLOCK | ❌ NOT OBSERVED |

---

## 18. DRY_RUN Result

**VERDICT: VERIFIED**

| Test | Result |
|------|--------|
| Guardian in DRY_RUN mode connected | ✅ |
| `RecoveryExecutor._check_mode()` returns `BLOCKED` | ✅ |
| `would_execute: true` in result details | ✅ |
| `adapter.execute_recovery_action()` **never called** | ✅ |
| Zero recovery actions executed | ✅ |

---

## 19. Disconnect Findings

**NOT_TESTED** — Controlled disconnect would risk uncontrolled backend state. No safe test performed.

---

## 20. Stale Findings

**NOT_TESTED** — No deterministic clock injection; 60s threshold too long for validation.

---

## 21. Capability Matrix

| Capability | Source | Test | Live Runtime | Guardian | Status |
|------------|--------|------|--------------|----------|--------|
| Assistant output observation | ✅ | ✅ | ✅ | ✅ | **RUNTIME_VERIFIED** |
| Tool start observation | ✅ | ✅ (mocks) | ❌ | ✅ | **NOT_OBSERVED** |
| Tool update observation | ✅ | ✅ (mocks) | ❌ | ✅ | **NOT_OBSERVED** |
| Tool completion observation | ✅ | ✅ (mocks) | ❌ | ✅ | **NOT_OBSERVED** |
| Tool failure observation | ✅ | ✅ (mocks) | ❌ | ✅ | **NOT_OBSERVED** |
| Partial tool observation | ✅ | ✅ (mocks) | ❌ | ✅ | **NOT_OBSERVED** |
| Session identity | ❌ | ❌ | ❌ | ❌ | **NOT_PROVEN** |
| Session continuity | ❌ | ❌ | ❌ | ❌ | **NOT_PROVEN** |
| ACP reconnect | ✅ | ❌ | ❌ | ✅ | **NOT_TESTED** |
| Session load | ✅ | ❌ | ❌ | ✅ | **NOT_TESTED** |
| Session fork | ✅ | ❌ | ❌ | ✅ | **NOT_TESTED** |
| Cancel confirmation | ✅ | ❌ | ❌ | ✅ | **NOT_TESTED** |
| Semantic verification | ❌ | ❌ | ❌ | ❌ | **NOT_PROVEN** |
| DRY_RUN | ✅ | ✅ | ✅ | ✅ | **RUNTIME_VERIFIED** |
| Safety revalidation | ✅ | ✅ | ✅ | ✅ | **SOURCE_VERIFIED** |
| TOCTOU protection | ✅ | ✅ | ❓ | ✅ | **SOURCE_VERIFIED** |
| Stale detection | ✅ | ✅ | ❌ | ✅ | **NOT_TESTED** |
| Failure classification | ✅ | ✅ | ✅ | ✅ | **SOURCE_VERIFIED** |

---

## 22. Bugs Found

| ID | Component | Bug | Severity |
|----|-----------|-----|----------|
| B-001 | Guardian | None found in tool observation path | — |
| B-002 | Goose | Tool events not emitted for llama3.2 + test prompts | CRITICAL |
| B-003 | Goose | No `toolCallNotifications` capability advertised | HIGH |

---

## 23. Bugs NOT Found

| Claim | Reality |
|-------|---------|
| Guardian parser drops tool events | ❌ False — parser correct; no raw events |
| Guardian observer loses events | ❌ False — no events to lose |
| ACP transport loses events | ❌ False — 400+ events delivered correctly |
| Goose source lacks tool notification code | ❌ False — fully implemented |
| Capability advertisement blocks tool events | ❌ False — events never generated regardless |

---

## 24. Unknowns

| Unknown | Impact |
|---------|--------|
| Can Ollama llama3.2 be configured to invoke tools via Goose ACP? | If YES: tool events possible; if NO: permanent limitation |
| Does Goose ACP require specific model capabilities for tool calls? | Source suggests structured tool calling required |
| Would a different model (OpenAI, Anthropic) invoke tools via Goose ACP? | Unknown — no alternate provider available |
| Would different approval mode (`approve` vs `auto`) change behavior? | Source shows `auto` should work; `Chat` mode rejects all tools |
| Is the `tools` capability in `ollama list` actually usable via Goose? | Advertised but not observed in practice |

---

## 25. Adversarial Analysis

| Question | Answer |
|----------|--------|
| A. Could model merely write fake tool-looking text? | **YES** — Observed: JSON with `success: true` as text chunks |
| B. Could Goose generate tool request filtered before execution? | ❌ No — provider stream shows no `ToolCallStart` |
| C. Could tool execution occur outside ACP path? | ❌ No — all execution goes through ACP notifications |
| D. Could ACP capability negotiation suppress notifications? | ❌ Not the blocker — events never generated |
| E. Could Guardian listen to wrong event type? | ❌ Parser handles all documented ACP tool event types |
| F. Could provider adapter suppress tool calls? | ❌ Provider passes through all `SessionNotification` |
| G. Could installed model lack structured tool-calling? | **LIKELY** — llama3.2 may not support function calling via Goose ACP |
| H. Could test prompt fail to trigger tool? | Tested 9 prompts including explicit `/todo` — none triggered tools |
| I. Could event be under different ACP schema? | ❌ Protocol v1 used consistently; schema supports tool events |
| J. Could observed tool result be synthetic model output? | **YES** — "execute_typescript {success: true...}" appeared as text |
| K. Could raw ACP capture be incomplete? | ❌ Captured 400+ events; all non-tool types present |

---

## 26. Test Results

```
============================= 221 passed in 7.83s =============================
```

| Test File | Passed |
|-----------|--------|
| test_core.py | 42 |
| test_fault_injection.py | 35 |
| test_hermes.py | 56 |
| test_protocol.py | 21 |
| test_recovery_executor.py | 27 |
| test_state.py | 18 |
| test_utils.py | 12 |

No new tests added (no runtime tool events to test against).

---

## 27. AUTO_RECOVER Verdict

**NOT SAFE**

| Prerequisite | Status |
|--------------|--------|
| Correct observation | ❌ Tool events NOT_OBSERVED |
| Correct state tracking | ⚠️ Partial (no tool state) |
| Safe replay boundary | ✅ Assistant output blocks replay |
| Semantic recovery verification | ❌ NOT_PROVEN |
| Identity/continuity | ❌ NOT_PROVEN |
| Verified recovery action | ❌ NOT_PROVEN |
| Bounded recovery | ⚠️ Budget works but no live validation |

**Cannot advance past RUNTIME OBSERVED for tool-related capabilities.**

---

## 28. Exact Root Cause / Boundary

```
MODEL / PROVIDER (Ollama llama3.2)
    ↓ VERIFIED: Produces text only
MODEL TOOL DECISION
    ↓ FIRST BROKEN BOUNDARY: Model never produces ToolRequest
GOOSE INTERNAL TOOL INVOCATION
    ↓ NEVER TRIGGERED
GOOSE INTERNAL TOOL EXECUTION
    ↓ NEVER TRIGGERED
GOOSE ACP EVENT GENERATION
    ↓ NEVER TRIGGERED
ACP CAPABILITY / PROTOCOL
    ↓ NOT THE BLOCKER (capability missing but events never generated)
ACP TRANSPORT
    ↓ VERIFIED WORKING
GUARDIAN RECEIVER / PARSER / OBSERVER
    ↓ VERIFIED WORKING — NO EVENTS TO PROCESS
```

**FIRST UNVERIFIED/FAILURE BOUNDARY: MODEL TOOL DECISION**

---

## 29. Remaining Risks

| Risk | Likelihood | Impact |
|------|------------|--------|
| Tool events permanently unavailable for this model/provider | HIGH | AUTO_RECOVER permanently blocked for replay actions |
| Different model might work but untested | UNKNOWN | Could enable tool observability |
| Goose ACP tool notification requires specific model capability | LIKELY | Architectural limitation |

---

## 30. Build #13 Recommendation

### Option A (If Resolvable): Test Alternative Model
**Objective**: Determine if any available model works with Goose ACP tool calling.
**Approach**:
1. Test if another locally-available model (if any) produces tool calls
2. If yes: Complete tool observation validation (Build #9 Phases 4-7)
3. If no: Document as limitation

### Option B (If Permanent): Document Constraint
**Objective**: Formally document tool events as UNAVAILABLE for Goose+Ollama+llama3.2.
**Approach**:
1. Update capability matrix: Tool events → `UNAVAILABLE`
2. Update safety gate: Tool state = UNKNOWN → permanently UNSAFE for replay
3. AUTO_RECOVER permanently blocked for `CANCEL_AND_REPLAY`, `RELOAD_SESSION`, `FORK_SESSION`
4. Focus on non-tool recovery: `WAIT_AND_RETRY`, `WAIT_FOR_BACKEND`, `HUMAN_INTERVENTION`

---

## Mandatory Final Answers

| # | Question | Answer |
|---|----------|--------|
| 1 | Can the tested Goose stack generate a real structured tool request? | **NO** — 0/9 sessions |
| 2 | If yes, exactly where does it originate? | N/A |
| 3 | Does Goose execute the tool? | **NO** — No tool requests |
| 4 | Does Goose emit an ACP tool event? | **NO** — Zero tool events at transport |
| 5 | Does raw ACP contain it? | **NO** |
| 6 | Does Guardian receive it? | **NO** — No events to receive |
| 7 | Does Guardian parse it? | **SOURCE_VERIFIED** — Parser correct |
| 8 | Does Guardian update tool state? | **NO** — No events to process |
| 9 | Does replay safety become unsafe after tool execution? | **NOT TESTED** — No tools executed |
| 10 | Cause: model/provider, config, Goose, ACP, Guardian? | **MODEL/PROVIDER BEHAVIOR** (llama3.2) |
| 11 | If no tool call, what exact boundary prevents it? | **MODEL TOOL DECISION** — No `ToolRequest` produced |
| 12 | Limitation specific to this combo? | **LIKELY** — Goose source implements full pipeline |
| 13 | Smallest safe Build #13? | Test alternative model or document constraint |

---

## Final Assertions

| Assertion | Status |
|-----------|--------|
| REAL GUARDIAN RECOVERY ACTIONS EXECUTED | **0** |
| AUTO_RECOVER ENABLED | **NO** |
| DESTRUCTIVE TOOL ACTIONS | **0** |
| SOURCE FILES MODIFIED | **0** |
| TEST FILES MODIFIED | **0** |
| CONFIGURATION MODIFIED | **0** (temporary env vars only) |

---

## Build #12 Status

```
BUILD #12 STATUS: COMPLETE

BASELINE: 221 passed

FINAL TEST COUNT: 221

SOURCE FINDINGS: Goose ACP tool notification pipeline fully implemented in source (ToolCallNotifier, tool_notifications.rs, provider.rs, server.rs). Guardian parser correctly implements all ACP tool event types.

RUNTIME FINDINGS: 9 test sessions, 400+ ACP events captured, ZERO tool events. Model (Ollama llama3.2) produces text-only responses. Goose tool execution pipeline never triggered.

FIRST UNVERIFIED / FAILURE BOUNDARY: MODEL TOOL DECISION

GOOSE TOOL EXECUTION: NOT_OBSERVED

ACP TOOL EVENTS: NOT_OBSERVED

GUARDIAN TOOL OBSERVATION: NOT_OBSERVED (no events to observe)

GUARDIAN BUG: NO

GOOSE BUG: PARTIAL — Tool event infrastructure exists but not activated by this model

MODEL/PROVIDER LIMITATION: YES — llama3.2 does not invoke tools via Goose ACP for tested prompts

CONFIGURATION LIMITATION: UNKNOWN — auto mode + extensions loaded should work

SEMANTIC VERIFICATION: NOT_PROVEN

SESSION IDENTITY: NOT_PROVEN

TOCTOU: SOURCE_VERIFIED (executor-level dual generation checks)

DRY_RUN: VERIFIED

AUTO_RECOVER: NOT SAFE

REAL GUARDIAN RECOVERY ACTIONS EXECUTED: 0

FILES MODIFIED: None (C:/tmp/test_todo.py created for experiment only)

BUILD_12_REPORT: C:/Projects/AgentGuardian/BUILD_12_REPORT.md

BUILD #13: Test alternative locally-available model for tool calling; if none works, document permanent UNAVAILABLE for tool events with this provider/model combo
```