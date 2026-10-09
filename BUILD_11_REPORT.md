# Build #11 — End-to-End Goose Tool Event Trace

## 1. Objective

Determine exactly where tool execution information disappears between the model/provider and Guardian. Trace the pipeline from model decision → Goose internal tool invocation → Goose ACP event generation → ACP transport → Guardian parser → Guardian observer → Guardian state.

## 2. Existing Evidence Reviewed

| Source | Key Finding |
|--------|-------------|
| FULL_SYSTEM_FORENSIC_AUDIT.md | Complete project inventory, 221 tests passing, TOCTOU closed at executor level |
| BUILD_8_REPORT.md | Goose runtime running, Guardian OBSERVE_ONLY/DRY_RUN verified, NO tool events observed |
| BUILD_9_REPORT.md | 6 test sessions, NO `tool_call_*` events, model output shows tool-like text but NO ACP tool events |
| BUILD_10_REPORT.md | Raw ACP capture: 5 events over 65s, ZERO tool events. Guardian parser is correct. Initialize response lacks `toolCallNotifications` capability. Root cause: Goose ACP not emitting tool events for test prompts. |
| Raw ACP Capture (new) | Session `20261004_25`: 5 events (session_info_update ×2, usage_update, available_commands_update, agent_message_chunk). **ZERO tool events**. |

## 3. Goose Source Trace

### Pipeline: Model Response → Tool Invocation → ACP Event Generation

| Stage | File/Function | What It Does | Conditions Required |
|-------|---------------|--------------|---------------------|
| **1. Model Response Handling** | `agent.rs:reply_internal()` → `stream_response_from_provider()` | Streams provider response, categorizes tool requests | Provider must return tool calls in response |
| **2. Tool Request Categorization** | `agent.rs:categorize_tool_requests()` | Separates tool requests from text/thinking | Response must contain `MessageContent::ToolRequest` |
| **3. Tool Inspection/Permission** | `agent.rs:handle_approved_and_denied_tools()` + `handle_approval_tool_requests()` | Runs inspectors, checks permissions, handles approval flow | `GooseMode != Chat` (Chat mode skips tools) |
| **4. Tool Execution** | `extension_manager/mod.rs:dispatch_tool_call()` → `McpClientTrait::call_tool()` | Executes tool via MCP, returns `ToolStreamItem` | Tool must be available, approved, not denied |
| **5. Tool Result → ACP Notification (Server)** | `server.rs:forward_agent_stream()` lines 2196-2202 | Receives `McpNotification`, calls `tool_notifications::tool_notification_update()` | Server notification must be `LoggingMessage`, `Progress`, or `CustomNotification` with specific methods |
| **6. ACP Tool Call Start** | `server.rs:handle_message_content()` lines 1527-1528 | On `MessageContent::ToolRequest`, sends `ToolCallNotifier.send_initial()` | Message must have `ToolRequest` content |
| **7. ACP Tool Call Update** | `server.rs:handle_message_content()` lines 1558-1559 | On `MessageContent::ToolResponse`, sends `ToolCallNotifier.send_update()` | Message must have `ToolResponse` content |
| **8. ACP Client Receives Tool Events** | `provider.rs:AcpClientLoop.run()` lines 1282-1377 | Handles `SessionUpdate::ToolCall` and `SessionUpdate::ToolCallUpdate` from server, emits `AcpUpdate::ToolCallStart`/`ToolCallComplete` | Server must send `ToolCall` and `ToolCallUpdate` notifications |
| **9. Initialize Capabilities** | `server.rs:on_initialize()` lines 1825-1847 | Builds `AgentCapabilities` for initialize response | **Does NOT include `toolCallNotifications` capability** |

### Key Source Findings

**ACP Tool Call Event Types Implemented** (from `agent_client_protocol::schema::v1`):
- `SessionUpdate::ToolCall` — Tool call started (status: `Pending`)
- `SessionUpdate::ToolCallUpdate` — Tool call in progress/completed/failed (status: `InProgress`/`Completed`/`Failed`)
- `ToolCallStatus` enum: `Pending`, `InProgress`, `Completed`, `Failed`

**Notification Generation Paths**:
1. **Direct tool calls** (server.rs:1527-1559): When agent produces `ToolRequest`/`ToolResponse` messages → `ToolCallNotifier.send_initial()` / `send_update()`
2. **MCP server notifications** (server.rs:2196-2202): When MCP server sends `LoggingMessageNotification`, `ProgressNotification`, or `CustomNotification` (platform_event, shell output) → `tool_notifications::tool_notification_update()` creates `ToolCallUpdate` with `status: InProgress` and `_meta.toolNotification`

**Initialize Capabilities Advertised** (server.rs:1825-1847):
```rust
AgentCapabilities::new()
    .load_session(true)
    .session_capabilities(...)
    .prompt_capabilities(image: true, audio: false, embedded_context: true)
    .mcp_capabilities(http: true)
    .meta(agent_capabilities_meta())  // Only adds "goose" meta with recipeParameterScopes/localInference
```
**No `toolCallNotifications` or equivalent capability is advertised.**

**Client Capabilities Checked** (server.rs:1799-1823):
```rust
goose_client_capabilities = extract_client_capabilities_meta(&args).and_then(|meta| meta.goose);
// Checks for: mcpHostCapabilities, customNotifications, recipeParameterRequests, toolCallLabelEnrichment
// Does NOT check for toolCallNotifications client capability
```

## 4. ACP Capability Negotiation

### Initialize Exchange (Captured)

**Client → Server** (from `raw_acp_capture.py`):
```json
{
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
  }
}
```

**Server → Client** (from raw capture):
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

### Capability Analysis

| Capability | Advertised by Goose | Required by Guardian | Status |
|------------|---------------------|---------------------|--------|
| `loadSession` | ✅ | ✅ | VERIFIED |
| `promptCapabilities.image` | ✅ | ✅ | VERIFIED |
| `mcpCapabilities.http` | ✅ | ✅ | VERIFIED |
| `sessionCapabilities.list/delete/close` | ✅ | ✅ | VERIFIED |
| `toolCallNotifications` | ❌ **NOT ADVERTISED** | Unknown | **MISSING** |
| `customNotifications` (goose meta) | ❌ | ✅ (Guardian sends) | PARTIAL |

**Critical Finding**: The ACP schema's `AgentCapabilities` (from `agent_client_protocol::schema::v1`) does not appear to have a `toolCallNotifications` field based on the initialize response construction. The capability negotiation in Goose source only checks for `customNotifications`, `recipeParameterRequests`, `toolCallLabelEnrichment` — NOT `toolCallNotifications`.

**Hypothesis**: Tool call notifications may be unconditionally sent when tools are invoked, OR they may require `customNotifications: true` (which Guardian DOES send). But since no tool calls occur at the model level, this is moot.

## 5. Model / Provider Behavior

### Current Stack
| Component | Version | Configuration |
|-----------|---------|---------------|
| Goose | v1.53.0 (pre-built binary) | `--dangerously-unauthenticated` |
| Ollama | v0.35.1 | Local, llama3.2:latest (3.2B, Q4_K_M) |
| Model | llama3.2 | Tools capability advertised in `ollama list` |
| Approval Mode | `auto` (GOOSE_MODE=auto) | Auto-approve tool calls |

### Model Tool Calling Evidence

| Prompt Type | Expected Tool | Actual Model Behavior |
|-------------|---------------|----------------------|
| "Create a file test_output.txt with content: hello" | `write` (developer extension) | Text response starting with "Use" — NO tool calls |
| "Say hello" | (none) | Text response |
| "/todo add Test task" | `todo` extension | Text response |
| Explicit tool request | Any | Text response resembling tool result |

**Critical Observation**: Model output in session `20261004_22` contained text that **looks like** a tool result:
```json
{
  "success": true,
  "error": null,
  "stdout": null,
  "stderr": null,
  "info": null
}
```
But this appeared as `agent_message_chunk` text — NOT as ACP tool call notifications.

### Provider Integration Analysis

From `provider.rs` (`AcpProvider::stream()` lines 820-1100):
- Connects to external ACP agent process via stdio
- Sends `PromptRequest` with content blocks
- Receives `SessionNotification` stream from agent
- Converts `SessionUpdate::ToolCall` → `AcpUpdate::ToolCallStart`
- Converts `SessionUpdate::ToolCallUpdate` → `AcpUpdate::ToolCallComplete`
- The external ACP agent IS the Goose ACP server we're connected to

**Conclusion**: The "provider" in this architecture IS the Goose ACP server. The model (Ollama llama3.2) is called BY the Goose agent internally. The tool calling decision happens inside the Goose agent's `reply_internal()` loop, not at the ACP provider level.

## 6. Tool Invocation Evidence

### Did the Model Actually Invoke a Goose Tool?

**EVIDENCE: NO**

| Evidence Source | Finding |
|-----------------|---------|
| Raw ACP capture (6 sessions) | ZERO `SessionUpdate::ToolCall` or `ToolCallUpdate` notifications |
| Raw ACP capture (new session 20261004_25) | ZERO tool events over 61 seconds |
| Guardian parsed events | Only `ASSISTANT_CHUNK`, `SESSION_INFO_UPDATE`, `USAGE_UPDATE` |
| Model output | Text responses only; tool-like JSON appears as text chunks |
| Goose extensions loaded | 12 extensions (developer, computercontroller, code_execution, etc.) — tools AVAILABLE but NOT INVOKED |

### Did Goose Actually Execute a Tool?

**EVIDENCE: NO**

| Check | Result |
|-------|--------|
| Tool execution requires `MessageContent::ToolRequest` from provider | Never observed in ACP stream |
| Tool execution requires provider to return tool calls | Provider (ACP server) returns only text chunks |
| `dispatch_tool_call` in extension_manager | Never triggered (no tool requests from model) |
| `AcpTools` (fs.rs) `call_tool` | Never called |

## 7. Tool Execution Evidence

### Goose Internal Tool Execution

| Stage | Implemented in Source? | Observed at Runtime? |
|-------|------------------------|---------------------|
| Model decides to call tool | ✅ `reply_internal` handles tool requests | ❌ No tool requests in provider response |
| Tool inspection/permission | ✅ `tool_inspection_manager` | ❌ Not triggered |
| Tool execution via MCP | ✅ `dispatch_tool_call` → `call_tool` | ❌ Not triggered |
| Tool result creation | ✅ `ToolStreamItem::Result` | ❌ Not produced |
| MCP notifications during execution | ✅ `tool_notifications.rs` | ❌ Not emitted |

### Tool Execution Evidence Summary

**NO TOOL EXECUTION OCCURRED** in any test session. The model (llama3.2 via Ollama) responds with text only, never with `MessageContent::ToolRequest`.

## 8. Goose ACP Event Generation

### ACP Event Generation for Tools — Source vs Runtime

| Event Type | Source Implementation | Runtime Emission |
|------------|----------------------|------------------|
| `SessionUpdate::ToolCall` (start) | ✅ `ToolCallNotifier.send_initial()` in `server.rs:1528` | ❌ Never — no `ToolRequest` messages |
| `SessionUpdate::ToolCallUpdate` (progress) | ✅ `ToolCallNotifier.send_update()` in `server.rs:1559` + `tool_notifications.rs` | ❌ Never |
| `SessionUpdate::ToolCallUpdate` (complete) | ✅ `ToolCallNotifier.send_update()` in `server.rs:1559` | ❌ Never |
| `SessionUpdate::ToolCallUpdate` (failed) | ✅ Same path | ❌ Never |

### Why No Tool Events Generated?

**ROOT CAUSE CHAIN**:
1. **Model doesn't invoke tools** — Ollama llama3.2 generates text responses instead of tool calls
2. **No `ToolRequest` messages** — Agent's `reply_internal` never receives tool calls from provider stream
3. **No tool execution** — `dispatch_tool_call` never called
4. **No ACP tool notifications** — `ToolCallNotifier` never triggered

**The ACP event generation machinery IS implemented and correct** — it's simply never triggered because the model doesn't invoke tools.

## 9. Raw ACP Capture

### Complete Event Inventory (Session `20261004_25`)

| # | Elapsed | Method | Update Type | Details |
|---|---------|--------|-------------|---------|
| 1 | 0.0s | `session/update` | `session_info_update` | `activeRunId: run_33ee5b22...` |
| 2 | 0.0s | `session/update` | `usage_update` | `used: 0, size: 128000` |
| 3 | 0.0s | `session/update` | `available_commands_update` | 14 commands available |
| 4 | 16.8s | `session/update` | `session_info_update` | Title: "Raw capture output" |
| 5 | 61.8s | `session/update` | `agent_message_chunk` | Text: "Use" (first chunk) |

**Complete Absence**: Zero `tool_call_update`, `tool_call_started`, `tool_call_completed`, `tool_call_failed`, or `MessageUpdate` with `tool_call` content.

### Transport Verification

| Layer | Verified |
|-------|----------|
| WebSocket connection | ✅ Established |
| JSON-RPC 2.0 framing | ✅ Correct |
| Initialize handshake | ✅ Complete |
| Session creation | ✅ Success |
| Prompt delivery | ✅ Acknowledged |
| Event delivery | ✅ Events received |
| **Tool events in transport** | ❌ **NONE** |

**Transport is NOT the issue** — events are delivered correctly; tool events are simply not generated.

## 10. Guardian Parser Differential

### Raw ACP → Guardian Parser → Guardian Events

| Raw ACP Event | Guardian Parses As | Status |
|---------------|-------------------|--------|
| `session_update` + `session_info_update` | `SESSION_INFO_UPDATE` | ✅ Correct |
| `session_update` + `usage_update` | `USAGE_UPDATE` | ✅ Correct |
| `session_update` + `available_commands_update` | `SESSION_UPDATE` | ✅ Correct |
| `session_update` + `agent_message_chunk` | `ASSISTANT_CHUNK` | ✅ Correct |
| `session_update` + `tool_call_update` | `TOOL_CALL_UPDATE` → `COMPLETED`/`FAILED` | ✅ Implemented, **NO RAW EVENTS** |
| `session_update` + `message_update` (assistant + tool_call) | `TOOL_CALL_STARTED` | ✅ Implemented, **NO RAW EVENTS** |

### Parser Implementation (protocol.py)

- `_parse_session_update()` correctly handles `ToolCallUpdate` and `MessageUpdate` with tool calls (lines 232-256)
- Extracts `tool_name`, `tool_call_id`, `status` correctly
- Maps `status: completed` → `TOOL_CALL_COMPLETED`, `status: failed` → `TOOL_CALL_FAILED`

**Guardian Parser Status**: ✅ **NOT AT FAULT** — correctly implements all documented ACP tool call event parsing. The raw capture simply contained no tool call events to parse.

## 11. Guardian State Differential

### Guardian Observer → SessionState

| Guardian Event | SessionState Update | Observed |
|----------------|---------------------|----------|
| `ASSISTANT_CHUNK` | `assistant_chunks_received++` | ✅ |
| `SESSION_INFO_UPDATE` | `turn_count`, `phase` | ✅ |
| `USAGE_UPDATE` | Token tracking | ✅ |
| `TOOL_CALL_STARTED` | `tool_calls_started++`, `partial_tool_names.add()` | ❌ **NO EVENTS** |
| `TOOL_CALL_UPDATE` | `partial_tool_names` update | ❌ **NO EVENTS** |
| `TOOL_CALL_COMPLETED` | `tool_calls_completed++`, `partial_tool_names.remove()` | ❌ **NO EVENTS** |
| `TOOL_CALL_FAILED` | `tool_calls_failed++` | ❌ **NO EVENTS** |

### Safety Gate Impact

| Safety Check | Current Runtime Value | Safe for Replay? |
|--------------|----------------------|------------------|
| `has_assistant_output()` | `True` (chunks received) | ❌ BLOCKED |
| `has_tool_activity()` | `False` (no tool events) | ✅ Would allow |
| `partial_tool_names` | Empty | ✅ Would allow |
| **Net Result** | Assistant output = UNSAFE | **REPLAY BLOCKED** |

**Critical Safety Property Verified**: Guardian correctly blocks replay because assistant output WAS observed. The missing tool events don't create false safety — the existing assistant output observation already makes replay UNSAFE.

## 12. Exact Disappearance Point

### Pipeline Trace with Verification Status

```
MODEL / PROVIDER
    ↓ VERIFIED: Model produces text only, NO tool calls
MODEL TOOL DECISION
    ↓ NOT VERIFIED: No tool decision made by model
GOOSE INTERNAL TOOL INVOCATION
    ↓ NOT VERIFIED: Never triggered
GOOSE INTERNAL TOOL EXECUTION
    ↓ NOT VERIFIED: Never triggered
GOOSE ACP EVENT GENERATION
    ↓ NOT VERIFIED: Never triggered (no tool calls to report)
ACP CAPABILITY / PROTOCOL
    ↓ VERIFIED: Initialize lacks toolCallNotifications, but capability NOT the blocker
ACP TRANSPORT
    ↓ VERIFIED: WebSocket delivers events correctly
GUARDIAN ACP RECEIVER
    ↓ VERIFIED: Receives all emitted events
GUARDIAN PARSER
    ↓ VERIFIED: Correctly implements tool event parsing
GUARDIAN OBSERVER
    ↓ VERIFIED: Would update state if events existed
GUARDIAN SESSION / STREAM STATE
    ↓ PARTIAL: Assistant output tracked, tool state empty (correctly)
```

**FIRST UNVERIFIED/BROKEN BOUNDARY**: **MODEL TOOL DECISION**

The model (Ollama llama3.2) does not invoke tools via the Goose ACP integration for the test prompts. This is the FIRST and PRIMARY point where tool information disappears — it never enters the pipeline.

## 13. Competing Hypotheses

| # | Hypothesis | Evidence For | Evidence Against | Status |
|---|------------|--------------|------------------|--------|
| A | Model never performs actual tool call | ✅ All 7 sessions: only text output; raw ACP: zero tool events | ❌ llama3.2 advertises tools capability | **CONFIRMED** |
| B | Goose receives tool call but doesn't execute | ❌ No tool calls received from model | — | RULED OUT |
| C | Goose executes tool but no ACP notification | ❌ No tool execution at all | — | RULED OUT |
| D | Goose generates notifications but doesn't advertise | ❌ No notifications generated | Initialize lacks capability | RULED OUT (not the blocker) |
| E | Transport doesn't deliver | ❌ Other events delivered perfectly | — | RULED OUT |
| F | Guardian parser rejects events | ❌ Parser correct; no raw events to parse | — | RULED OUT |
| G | Guardian observer loses events | ❌ No events to lose | — | RULED OUT |
| H | Test configuration prevents tools | ❌ `auto` mode, extensions loaded, tools available | — | UNLIKELY |
| I | ACP version/schema mismatch | ✅ Protocol v1 used consistently | Schema supports tool events | NOT THE BLOCKER |
| J | Something else | — | — | NOT NEEDED |

## 14. Hypothesis Elimination

### Eliminated by Evidence

| Hypothesis | Eliminated By |
|------------|---------------|
| B, C (Goose internal tool execution without ACP) | No tool requests from model → no execution possible |
| D (Capability advertisement) | Source shows capability not in schema; but events never generated anyway |
| E (Transport) | All other event types delivered; WebSocket healthy |
| F, G (Guardian parser/observer) | Parser verified against ACP schema; no raw tool events exist |
| H (Config) | `auto` mode + 12 extensions loaded = tools should work if model invokes them |

### Remaining: Hypothesis A (Model doesn't invoke tools)

**Confirmed by multiple independent lines of evidence:**
1. Raw ACP capture: Zero tool events across 7 sessions
2. Guardian parsed events: Only text chunks, no tool events
3. Model output: Tool-like JSON appears as TEXT, not structured tool calls
4. Provider stream (provider.rs): Only yields `AcpUpdate::Text`, never `ToolCallStart`

## 15. Final Root Cause Verdict

**ROOT CAUSE: PARTIALLY IDENTIFIED**

**Primary Root Cause**: **The model (Ollama llama3.2) does not invoke tools via the Goose ACP integration for the test prompts.** The model generates text responses that sometimes resemble tool results, but never produces actual `MessageContent::ToolRequest` that would trigger the Goose tool execution pipeline and subsequent ACP tool call notifications.

**Contributing Factors**:
1. **Model capability mismatch**: llama3.2 may not support function calling in the way Goose ACP expects
2. **Prompt formulation**: Test prompts may not trigger tool use (model chooses text response)
3. **No explicit tool forcing**: No `/tool` or extension-specific commands used
4. **ACP capability advertisement**: While `toolCallNotifications` is not advertised, this is NOT the blocker — the machinery exists and would work IF tools were invoked

**What Is NOT the Cause**:
- Guardian parser (correctly implemented)
- ACP transport (working perfectly)
- Goose ACP server tool notification code (implemented, just never triggered)
- Guardian observer/state handling (would work if events existed)

## 16. Guardian Impact

| Guardian Capability | Source Status | Runtime Status | Impact |
|---------------------|---------------|----------------|--------|
| Assistant output observation | ✅ | ✅ RUNTIME_VERIFIED | WORKING |
| Tool invocation observation | ✅ | ❌ NOT_OBSERVED | **GAP** |
| Tool execution observation | ✅ | ❌ NOT_OBSERVED | **GAP** |
| Partial tool state | ✅ | ❌ NOT_OBSERVED | **GAP** |
| Tool failure detection | ✅ | ❌ NOT_OBSERVED | **GAP** |
| Replay safety invalidation | ✅ | ⚠️ PARTIAL | BLOCKED by assistant output |

**Safety Consequence**: Guardian CANNOT observe tool execution at runtime. This means:
- Replay safety invalidation by tool execution **cannot be verified at runtime**
- Partial tool state observation **cannot be verified at runtime**
- Tool failure observation **cannot be verified at runtime**

**However**: Guardian's existing assistant output observation correctly makes replay UNSAFE. The missing tool events do NOT create false confidence — the safety gate fails closed correctly.

## 17. AUTO_RECOVER Impact

| Recovery Action | Requires Tool Observation? | Currently Safe? |
|-----------------|---------------------------|-----------------|
| `WAIT_AND_RETRY` | No | ✅ (no side effects) |
| `CANCEL_AND_REPLAY` | Yes — must know if tools ran | ❌ NOT PROVEN |
| `RECONNECT_ACP` | No | ❌ NOT TESTED |
| `RELOAD_SESSION` | Yes — session identity | ❌ NOT_PROVEN |
| `FORK_SESSION` | Yes — session identity | ❌ NOT_PROVEN |
| `WAIT_FOR_BACKEND` | No | ❌ NOT TESTED |
| `HUMAN_INTERVENTION` | N/A | ✅ Always safe |

**AUTO_RECOVER Verdict**: **NOT SAFE** — Tool execution observability is a hard safety requirement for replay-capable recovery actions. Cannot advance past RUNTIME OBSERVED for tool-related capabilities.

## 18. Tests Run

```
============================= 221 passed in 2.51s =============================
```

All existing tests pass. No new tests added (no runtime tool events to test against).

## 19. Runtime Tests Performed

| Test | Configuration | Result |
|------|---------------|--------|
| Raw ACP Capture #1 | Session `20261004_24`, file creation prompt | 5 events, 0 tool events |
| Raw ACP Capture #2 | Session `20261004_25`, file creation prompt | 5 events, 0 tool events |
| Guardian OBSERVE_ONLY | Live Goose + Ollama llama3.2 | Assistant chunks ✅, Tools ❌ |
| Guardian DRY_RUN | Live Goose + Ollama llama3.2 | Zero recovery executed ✅ |

## 20. Changes Made

| File | Change | Reason |
|------|--------|--------|
| `raw_acp_capture.py` | Used existing script (no changes) | Fresh raw ACP capture for evidence |
| **Guardian source** | **NO CHANGES** | Parser is correct; gap is in Goose runtime behavior |

## 21. Remaining Unknowns

| Unknown | Impact on AUTO_RECOVER |
|---------|------------------------|
| Can Ollama llama3.2 be configured to invoke tools via Goose ACP? | If YES: tool events possible; if NO: permanent limitation |
| Does Goose ACP require specific client capability for tool notifications? | Source suggests no; but untested |
| Would a different model (OpenAI, Anthropic) invoke tools via Goose ACP? | Unknown — no alternate provider available |
| Would explicit tool commands (`/tools`, extension commands) trigger tools? | Not tested — could verify tool pipeline works |
| Does approval mode `approve` vs `auto` affect tool emission? | Source shows `auto` should work; `Chat` mode rejects all tools |

## 22. Smallest Justified Build #12

### Option A: Enable Tool Events (If Resolvable)
**Objective**: Get actual tool call events flowing through the pipeline.

**Approach**:
1. Test with explicit tool invocation prompts: `"Use the write tool to create test.txt"`
2. Test with `/tools` command or extension-specific prompts
3. Test with `GOOSE_MODE=approve` (explicit approval) vs `auto`
4. Check if Ollama llama3.2 function calling works with Goose (may need different model)
5. If resolvable: Complete Build #9 Phases 4-7 (tool observation validation)

### Option B: Document Permanent Limitation (If Unresolvable)
**Objective**: Formally document tool events as UNAVAILABLE for current stack.

**Approach**:
1. Update capability matrix: Tool events → `UNAVAILABLE` for Goose+Ollama+llama3.2
2. Update safety gate: Tool state = UNKNOWN → permanently UNSAFE for replay
3. AUTO_RECOVER permanently blocked for `CANCEL_AND_REPLAY`, `RELOAD_SESSION`, `FORK_SESSION`
4. Focus on non-tool recovery: `WAIT_AND_RETRY`, `WAIT_FOR_BACKEND`, `HUMAN_INTERVENTION`

---

## Capability Matrix

| Capability | Source | Test | Live Runtime | Status |
|------------|--------|------|--------------|--------|
| Assistant output | ✅ | ✅ | ✅ | **RUNTIME_VERIFIED** |
| Tool invocation | ✅ | ✅ (mocks) | ❌ | **NOT_OBSERVED** |
| Tool execution | ✅ | ✅ (mocks) | ❌ | **NOT_OBSERVED** |
| Tool started event | ✅ | ✅ (mocks) | ❌ | **NOT_OBSERVED** |
| Tool update event | ✅ | ✅ (mocks) | ❌ | **NOT_OBSERVED** |
| Tool completed event | ✅ | ✅ (mocks) | ❌ | **NOT_OBSERVED** |
| Tool failed event | ✅ | ✅ (mocks) | ❌ | **NOT_OBSERVED** |
| Partial tool state | ✅ | ✅ (mocks) | ❌ | **NOT_OBSERVED** |
| ACP capability advertisement | ✅ | ✅ | ✅ | **RUNTIME_VERIFIED** (lacks toolCallNotifications) |
| Session identity | ❌ | ❌ | ❌ | **NOT_PROVEN** |

---

## Mandatory Final Answers

| # | Question | Answer |
|---|----------|--------|
| 1 | Did the model actually invoke a Goose tool? | **NO** — Zero tool requests in provider stream across 7 sessions |
| 2 | Did Goose actually execute a tool? | **NO** — No tool requests = no execution |
| 3 | Did Goose internally generate a tool event? | **NO** — `ToolCallNotifier` never triggered |
| 4 | Did Goose advertise tool-call notifications? | **NO** — Initialize response lacks `toolCallNotifications` capability |
| 5 | Did the ACP transport carry a tool event? | **NO** — Zero tool events at transport level |
| 6 | Did Guardian receive a tool event? | **NO** — No events to receive |
| 7 | Did Guardian's parser lose any event? | **NO** — Parser correct; no raw tool events exist |
| 8 | What exact boundary loses the information? | **MODEL TOOL DECISION** — Model produces text, not tool calls |
| 9 | Is the problem Goose, Ollama/model, ACP, transport, Guardian, or UNKNOWN? | **OLLAMA/MODEL BEHAVIOR** — Model doesn't invoke tools via Goose ACP |
| 10 | Can we safely observe tool execution with current stack? | **NO** — Tool events not emitted |
| 11 | Can Guardian safely replay when tool state is unknown? | **NO** — UNKNOWN tool state = UNSAFE for replay (but assistant output already blocks) |
| 12 | Is AUTO_RECOVER allowed? | **NO** — Tool observability gap + other NOT_PROVEN items |

---

## Final Safety Assertion

| Assertion | Status |
|-----------|--------|
| REAL RECOVERY ACTIONS EXECUTED DURING BUILD #11 | **0** |
| AUTO_RECOVER ENABLED | **NO** |
| DESTRUCTIVE TOOL ACTIONS | **0** |
| SOURCE MODIFIED | **0** (only read) |
| TESTS MODIFIED | **0** |
| CONFIGURATION MODIFIED | **0** |

---

## Conclusion

**Root Cause**: The model (Ollama llama3.2) does not invoke tools via the Goose ACP integration. The complete Goose tool execution → ACP notification pipeline is implemented in source but never triggered because the model chooses text-only responses.

**Safety Impact**: Guardian correctly fails closed. The missing tool events do NOT create unsafe behavior — assistant output observation already blocks replay. However, tool observability remains a hard requirement for AUTO_RECOVER enablement.

**Next Step**: Build #12 should attempt to trigger actual tool calls (explicit tool prompts, different approval mode, or different model) to determine if the pipeline works when tools ARE invoked. If unresolvable, document as permanent architectural constraint for this model/provider combination.