# BUILD #15 REPORT

## Goose ACP/MCP Tool Execution Forensic Investigation

---

## 1. Executive Summary

**Baseline:** 221 tests passing (verified fresh at Build #15 start)

**Core Discovery:** **Goose ACP DOES execute real tools via the ACP/MCP path**, and **Guardian CAN observe these tool executions end-to-end** after parser fixes.

### Pipeline Verified:

```
ACP Client → Goose ACP Server → ACP Session → MCP/Developer Extension → Tool Execution → ACP session/update (tool_call, tool_call_update) → ACP Transport → Guardian Parser → Guardian Observer
```

### Key Findings:

1. **ACP Tools Source**: Tools come from **MCP servers / Goose built-in extensions** (primarily the `developer` extension providing `shell`, `read`, `write`, `edit`, `list`, `glob`, `grep`, `patch`, `todo` + `code_execution` extension providing `execute_typescript`)
2. **Tool Discovery**: Available via `_goose/unstable/tools/list` ACP custom method
3. **Tool Execution**: Triggered when model (llama3.2) chooses a tool → ACP `tool_call` notification → tool executes via `AcpTools` (bridges to MCP/ACP) → `tool_call_update` notification with result
4. **Guardian Observation**: **VERIFIED** — Parser correctly classifies `tool_call` → `TOOL_CALL_STARTED` and `tool_call_update` (status=completed) → `TOOL_CALL_COMPLETED`
5. **Session Identity**: **PARTIALLY_VERIFIED** — ACP session IDs are stable strings (`20261006_N` format) but no cryptographic binding
6. **Tool Identity**: **VERIFIED** — Each tool call has unique `toolCallId` (e.g., `call_n53tw6cm`)
7. **Replay Safety**: Guardian correctly detects tool activity via `tool_activity_observed` flag and `partial_tool_names` tracking

---

## 2. Build #14 Findings Verified

| Claim | Verification |
|-------|--------------|
| Direct Ollama native API produces structured tool calls | ✅ VERIFIED |
| Direct Ollama OpenAI-compatible API produces structured tool calls | ✅ VERIFIED |
| Goose ACP provider discards `_tools` parameter | ✅ VERIFIED (architectural boundary) |
| ACP protocol itself supports tool calling | ✅ VERIFIED — via MCP tools, not provider-native |

**Critical Correction to Build #14**: Build #14 concluded "Goose ACP cannot produce tool calls." This was **INCOMPLETE**. Goose ACP *does* execute tools, but through **MCP/ACP tool execution** (the `developer` extension, `code_execution` extension), not through provider-native function calling. The model (llama3.2) chooses tools from the available MCP tool set, and Goose executes them via the ACP/MCP bridge.

---

## 3. Goose ACP Architecture (Source-Backed)

### Tool Origin Chain

```
Goose ACP Server (server.rs)
    ↓
Session Creation (session/new) → loads builtin_selection (defaults to ["developer"])
    ↓
apply_acp_extension_overrides() (server.rs:1081-1141)
    ↓
Creates AcpTools wrapper around DeveloperClient (fs.rs:66)
    ↓
AcpTools implements McpClientTrait (fs.rs:395)
    ↓
list_tools() delegates to inner (DeveloperClient) + adds ACP-specific tools (read, write, edit, shell) (fs.rs:396-410)
    ↓
call_tool() dispatches to acp_read, acp_write, acp_edit, acp_shell OR inner.call_tool (fs.rs:412-442)
    ↓
Tool execution uses ACP client connection (cx) to send ReadTextFileRequest, WriteTextFileRequest, CreateTerminalRequest, etc.
    ↓
ToolCallNotifier sends ToolCall/ToolCallUpdate notifications via ACP transport (tool_call_notifier.rs)
```

### Built-in Tools Available (from `_goose/unstable/tools/list`)

| Tool | Extension | Description |
|------|-----------|-------------|
| `shell` | developer | Execute shell commands |
| `read` | developer | Read text files |
| `write` | developer | Write text files |
| `edit` | developer | Edit files |
| `list` | developer | List directory |
| `glob` | developer | Glob pattern matching |
| `grep` | developer | Search files |
| `patch` | developer | Apply patches |
| `todo` | developer | Todo list |
| `execute_typescript` | code_execution | Execute TypeScript in sandbox |
| `analyze` | analyze | Code structure analysis |
| `apps__create_app` | apps | Create Goose apps |
| `delegate` | delegate | Subagent delegation |
| `summarize` | summarize | File summarization |
| ...and 20+ more | | |

---

## 4. Tool Discovery (Runtime Verified)

**Method**: `_goose/unstable/tools/list` ACP custom request
**Response**: Full tool list with schemas, permissions, input/output schemas
**Evidence**: Runtime capture shows 35+ tools available in session `20261006_13`

```json
{
  "method": "_goose/unstable/tools/list",
  "params": {"sessionId": "20261006_13"},
  "result": {"tools": [{"name": "shell", "description": "...", "parameters": [...], "permission": null, "inputSchema": {...}}, ...]}
}
```

---

## 5. Tool Execution Lifecycle (Runtime Verified)

### Complete Trace (Session `20261006_14`)

| Step | ACP Event | Guardian Parsed | Details |
|------|-----------|-----------------|---------|
| 1 | `session/update` → `session_info_update` (activeRunId) | SESSION_INFO_UPDATE | Run starts |
| 2 | `session/update` → `usage_update` | USAGE_UPDATE | Token usage |
| 3 | `session/update` → `available_commands_update` | CONFIG_OPTION_UPDATE | Commands list |
| 4 | `session/update` → `session_info_update` (title) | SESSION_INFO_UPDATE | Session titled |
| 5 | **`session/update` → `tool_call`** | **TOOL_CALL_STARTED** | `toolCallId: call_n53tw6cm`, `title: "execute typescript"`, `rawInput: {code: "...", tool: "developer/shell"}` |
| 6 | `session/update` → `tool_call_update` (status=completed) | **TOOL_CALL_COMPLETED** | `toolCallId: call_n53tw6cm`, content with execution result |
| 7 | `session/update` → `agent_message_chunk` (streaming) | ASSISTANT_CHUNK | Assistant response |
| 8 | `session/update` → `usage_update` | USAGE_UPDATE | Final usage |
| 9 | `session/update` → `session_info_update` (activeRunId=null) | SESSION_INFO_UPDATE | Run ends |
| 10 | Response to `session/prompt` (stopReason=end_turn) | TURN_COMPLETE | Turn complete |

**Tool Call Details:**
- **Tool**: `execute_typescript` (from `code_execution` extension)
- **Internal Tool**: `developer/shell` (TypeScript code runs `dir` via shell)
- **Execution**: Sandbox TypeScript execution → shell command
- **Result**: Completed (with stderr showing TypeScript execution)

---

## 6. ACP Events (Source + Runtime Verified)

| Event Type | ACP `sessionUpdate` Value | Guardian Classification | Tool Info Extracted |
|------------|---------------------------|------------------------|---------------------|
| Tool Call Start | `tool_call` | `TOOL_CALL_STARTED` | `toolCallId`, `title` (as tool_name), `rawInput` |
| Tool Call Update | `tool_call_update` | `TOOL_CALL_UPDATE` / `TOOL_CALL_COMPLETED` / `TOOL_CALL_FAILED` | `toolCallId`, `status`, `content` |
| Assistant Chunk | `agent_message_chunk` | `ASSISTANT_CHUNK` | Text content |
| Turn Complete | Response `stopReason=end_turn` | `TURN_COMPLETE` | — |
| Usage | `usage_update` | `USAGE_UPDATE` | Tokens |
| Session Info | `session_info_update` | `SESSION_INFO_UPDATE` | Run ID, title |

**Critical Parser Fix Applied**: 
- Added `session/update` to `NOTIFICATION_METHODS`
- Added handling for lowercase `tool_call` and `tool_call_update` update types
- Fixed tool info extraction for ACP protocol format (top-level fields, not nested `toolCall`)
- Fixed `_parse_response` to initialize `event_type` before conditional branches

---

## 7. Guardian Observation (Runtime Verified)

### Parser Output (Session `20261006_14`)

```
GUARDIAN PARSED TOOL: tool_call_started tool=execute typescript id=call_n53tw6cm
GUARDIAN PARSED TOOL: tool_call_completed tool=None id=call_n53tw6cm
```

### Observer State Tracking (observer.py)

| State Field | Updated On |
|-------------|------------|
| `tool_activity_observed` | Any tool event |
| `last_tool_event_timestamp` | Any tool event |
| `tool_calls[tool_call_id]` | Tool start/update/complete/failed |
| `partial_tool_names` (Set) | Tool start/update (add), complete/failed (remove) |
| `is_safe_to_replay()` | Returns `False` if `tool_activity_observed` |

**Guardian correctly understands: "A tool may have executed"**

---

## 8. Session Identity

| Aspect | Status | Evidence |
|--------|--------|----------|
| Session ID format | **VERIFIED** | `20261006_N` (date + counter) |
| Stability across messages | **VERIFIED** | Same ID in all `session/update` notifications |
| Survives reconnect | **NOT_TESTED** | ACP `session/load` exists but not tested |
| Cryptographic binding | **NOT_PROVEN** | No signatures/tokens binding session to identity |
| Guardian continuity proof | **PARTIALLY_VERIFIED** | Tracks by string ID only |

**Conclusion**: Session identity is **string-based only**. Guardian tracks sessions by `sessionId` string. No semantic proof of continuity across reconnects.

---

## 9. Tool Identity

| Aspect | Status | Evidence |
|--------|--------|----------|
| Tool Call ID | **VERIFIED** | `call_<random>` format (e.g., `call_n53tw6cm`, `call_hbqcv10s`) |
| Uniqueness | **VERIFIED** | Each tool call has unique ID |
| Association with session | **VERIFIED** | All events carry `sessionId` |
| Association with turn | **PARTIALLY_VERIFIED** | Run ID in `_meta.goose.activeRunId` |
| Completion correlation | **VERIFIED** | `tool_call_update` references same `toolCallId` |

---

## 10. Disconnect Behavior

**Status: NOT_TESTED**

**Reason**: Cannot safely interrupt ACP WebSocket during tool execution without risking:
- Incomplete tool execution state
- Orphaned processes (terminal/shell)
- Session corruption

**What would need testing** (future build):
1. Kill WebSocket during `tool_call` (before `tool_call_update`)
2. Observe what events remain
3. Test Guardian's `partial_tool_names` tracking
4. Test `session/load` recovery

---

## 11. Safety Analysis

### Scenario A: Failure BEFORE Tool Execution
- **Observable**: Only `assistant_chunk` events, no tool events
- **Guardian State**: `tool_activity_observed = False`, `partial_tool_names = {}`
- **Replay Safety**: `is_safe_to_replay() = True`
- **Classification**: `UNKNOWN` (no tool activity) → could be `TRANSIENT`/`PERMANENT`

### Scenario B: Failure AFTER Tool Start (DURING Execution)
- **Observable**: `tool_call_started` observed, `tool_call_completed/failed` NOT observed
- **Guardian State**: `tool_activity_observed = True`, `partial_tool_names = {"execute typescript"}`
- **Replay Safety**: `is_safe_to_replay() = False` (correctly blocks replay)
- **Classification**: `AMBIGUOUS` / `UNKNOWN` → **FAIL CLOSED**

### Scenario C: Failure AFTER Tool Completion
- **Observable**: `tool_call_started` + `tool_call_completed` both observed
- **Guardian State**: `tool_activity_observed = True`, `partial_tool_names = {}` (cleared on completion)
- **Replay Safety**: `is_safe_to_replay() = False` (tool executed, side effects possible)
- **Classification**: Depends on tool type (shell=side effect, read=idempotent)

**Guardian Safety Gate Status**: **CORRECTLY FAIL-CLOSED**

| Recovery Action | Tool Activity | Safety Gate | AUTO_RECOVER |
|-----------------|---------------|-------------|--------------|
| CANCEL_AND_REPLAY | None | SAFE | Would allow |
| CANCEL_AND_REPLAY | Started | AMBIGUOUS | BLOCKED |
| CANCEL_AND_REPLAY | Completed | UNSAFE | BLOCKED |
| RECONNECT_ACP | Any | NOT_PROVEN | BLOCKED |
| FORK_SESSION | Any | AMBIGUOUS | BLOCKED |

---

## 12. Code Changes

### File: `C:/Projects/AgentGuardian/guardian/goose/protocol.py`

**Changes:**
1. Added `"session/update"` to `NOTIFICATION_METHODS` dictionary (line 108)
2. Modified `_parse_notification` to check for `"session/update"` method BEFORE generic method lookup (line 185)
3. Modified `_parse_session_update` to:
   - Accept `sessionUpdate` field for update type (line 202)
   - Handle lowercase `tool_call` and `tool_call_update` event types (lines 243-252)
   - Extract tool info from top-level fields for ACP protocol format (lines 260-276)
   - Use `title` field as tool name fallback (line 275)
4. Fixed `_parse_response` to initialize `event_type = ACPEventType.UNKNOWN` and set `SESSION_UPDATE` for successful responses (lines 324, 340-341)

### File: `C:/Projects/AgentGuardian/guardian/goose/observer.py`

**Change:**
- Fixed `disconnect()` to handle `websockets.ClientConnection` which lacks `.closed` attribute (lines 510-514)

**Rationale**: These are **defect fixes** in Guardian's ACP protocol parsing. The parser was designed for a different ACP message format (camelCase `ToolCallUpdate` with nested `toolCall`) but the actual Goose ACP protocol uses snake_case `tool_call`/`tool_call_update` with top-level fields.

---

## 13. Tests

| Test Suite | Result |
|------------|--------|
| Baseline (pre-changes) | 221 passed |
| Post parser fixes | 221 passed |
| Guardian Hermes tests | 56 passed |
| Guardian Goose tests | Included in 221 |

**No test modifications needed.** All existing tests pass.

---

## 14. Capability Matrix

| Capability | Status | Evidence |
|------------|--------|----------|
| ACP transport | **VERIFIED** | WebSocket + JSON-RPC 2.0 working |
| ACP session | **VERIFIED** | `session/new`, `session/prompt`, `session/update` |
| Tool discovery | **VERIFIED** | `_goose/unstable/tools/list` returns 35+ tools |
| Tool invocation | **VERIFIED** | Model chooses tool, ACP executes via `AcpTools` |
| Tool execution | **VERIFIED** | `shell` via `execute_typescript` → `dir` command executed |
| Tool start event | **VERIFIED** | `sessionUpdate: "tool_call"` → `TOOL_CALL_STARTED` |
| Tool completion event | **VERIFIED** | `sessionUpdate: "tool_call_update"` (status=completed) → `TOOL_CALL_COMPLETED` |
| Tool failure event | **VERIFIED_CAPABLE** | Protocol supports `status: "failed"` (not observed live) |
| Guardian tool observation | **VERIFIED** | Parser + Observer correctly capture tool lifecycle |
| Session identity | **PARTIALLY_VERIFIED** | String ID stable, no crypto binding |
| Tool identity | **VERIFIED** | Unique `toolCallId` per call |
| Replay safety after tool | **VERIFIED** | `is_safe_to_replay() = False` when tool observed |
| Disconnect handling | **NOT_TESTED** | Cannot safely test |
| Semantic recovery verification | **NOT_PROVEN** | `verify_recovery()` returns `NOT_PROVEN` |
| AUTO_RECOVER | **NO** | Correctly blocked by safety gate |

---

## 15. Required Final Answers

1. **Can Goose ACP actually expose tools?** YES — via built-in extensions (developer, code_execution, etc.) registered as MCP servers
2. **Where do those tools come from?** MCP servers / Goose built-in extensions. The `developer` extension provides filesystem/shell tools. The `code_execution` extension provides TypeScript execution.
3. **Can a real Goose ACP tool execute?** YES — Verified: `execute_typescript` tool ran `dir C:/tmp/goose-test` via shell
4. **Does ACP emit a tool event?** YES — `tool_call` (start) and `tool_call_update` (completion/failure)
5. **Can Guardian observe it?** YES — After parser fixes, Guardian classifies both events correctly
6. **Can Guardian distinguish tool-started from tool-not-started?** YES — `partial_tool_names` tracks in-progress tools; `tool_activity_observed` flag
7. **Can Guardian prove session continuity?** PARTIALLY — String ID tracking only, no cryptographic proof
8. **Can Guardian prove replay safety after a tool may have executed?** YES — Fail-closed: `is_safe_to_replay()` returns `False` when any tool activity observed
9. **What remains unknown?**
   - Session continuity across reconnect (`session/load`)
   - Disconnect behavior during tool execution
   - Semantic verification of recovery actions
   - Whether other ACP agents (Copilot, Codex) emit same events
10. **What should Build #16 investigate?**
    - ACP `session/load` and `session/fork` for session identity continuity
    - Controlled disconnect during tool execution
    - Semantic `verify_recovery()` implementation for Goose
    - Multi-turn tool sequences

---

## 16. Final Assertion

```
REAL GUARDIAN RECOVERY ACTIONS EXECUTED: 0
```

---

*Build #15 Complete — Real ACP/MCP tool execution path traced, verified, and observed by Guardian. Parser defects fixed. Safety gate remains fail-closed.*