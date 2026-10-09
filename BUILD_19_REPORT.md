# 🔨 BUILD MODE — AGENT GUARDIAN BUILD #19

## REAL ACP TOOL EXECUTION + DISCONNECT RECOVERY FORENSICS — FINAL REPORT

**Project Root:** `C:\Projects\AgentGuardian`  
**Date:** 2026-10-08  
**Build Status:** COMPLETE — Goose + NVIDIA ACP tool calling NOT reliably reproducible via WebSocket ACP

---

## Executive Summary

Build #19 attempted to establish a reliably reproducible Goose ACP tool execution environment using the NVIDIA cloud provider (which has a valid API key configured). The investigation found:

1. **Goose native (non-ACP) with NVIDIA provider works** — tool calling via `goose run` executes successfully
2. **Goose ACP server starts and accepts WebSocket connections** — initialize, session/new succeed
3. **BUT ACP tool calling via `session/prompt` or `tools/call` does not complete** — requests hang indefinitely
4. **No live ACP tool events (`ToolCallStart`, `ToolCallComplete`) observed** — despite model having tools available

**Result:** The disconnect forensics experiment could not be run. Goose ACP with NVIDIA provider is **NOT reliably reproducible** for tool execution testing.

---

## Baseline

| Metric | Value |
|--------|-------|
| Test Suite | **221 / 221 passing** (100%) |
| Guardian Modified Files | None (Build #19 made zero code changes) |
| AUTO_RECOVER | **OFF** (0 real recovery actions executed) |

---

## Available Agents

| Agent | ACP Support | Status |
|-------|-------------|--------|
| Goose v1.53.0 | ✅ HTTP/WebSocket + stdio | **Tested** — native works, ACP tool calling hangs |
| Hermes | ✅ stdio only | **Tested in #18** — uv env blocks external clients |
| Codex | ❌ incomplete | **Not tested** — no `session/new` |
| OpenCode | Unknown | Not available locally |

---

## Available Providers

### NVIDIA (Configured & Working)
- **API Key:** Present in Hermes `.env` (`NVIDIA_API_KEY=REDACTED`)
- **Model:** `nvidia/nemotron-3-ultra-550b-a55b`
- **Base URL:** `https://integrate.api.nvidia.com/v1`
- **Native Goose:** ✅ Tool calling works via `goose run`
- **Goose ACP:** ❌ Tool calling hangs

### Other Providers (Not Configured)
- OpenRouter — configured in Goose settings but no API key
- Anthropic — not configured
- OpenAI — not configured
- Google — not configured
- Groq — not configured

---

## Selected Runtime: Goose ACP + NVIDIA Provider

### Why Selected
1. NVIDIA API key already available in environment
2. Goose native tool calling with NVIDIA works reliably
3. Goose ACP server exposes HTTP/WebSocket endpoint
4. Build #18 recommendation prioritized this path

### Configuration Used
```bash
NVIDIA_API_KEY="REDACTED" \
GOOSE_PROVIDER=nvidia \
GOOSE_MODEL="nvidia/nemotron-3-ultra-550b-a55b" \
goose serve --host 127.0.0.1 --port 3284 --dangerously-unauthenticated
```

---

## Goose ACP Source Findings

### Provider Path vs ACP Path (Confirmed from Build #14)
| Path | Tool Handling |
|------|---------------|
| **Native Provider** (`OllamaProvider`, `OpenAIProvider`, etc.) | Passes `_tools` to model; model emits `MessageContent::ToolRequest` |
| **ACP Provider** (`AcpProvider`) | **Ignores `_tools` parameter** (line 825 in `provider.rs`) |

### ACP Provider `stream()` Implementation
```rust
async fn stream(
    &self,
    model_config: &ModelConfig,
    _system: &str,
    messages: &[Message],
    _tools: &[Tool],  // ← DISCARDED — never forwarded to ACP agent
) -> Result<MessageStream, ProviderError>
```

**Result:** Provider-native tool schemas are **never sent** to the ACP agent. The ACP agent (spawned child process) owns tools via MCP servers (extensions), not the native provider.

### ACP Agent Tools
From `goose/src/acp/server/tools.rs`:
- `on_get_tools` → lists tools from `developer` extension (shell, read_file, write_file, etc.)
- `on_call_tool` → executes tools via `extension_manager.dispatch_app_tool_call`
- Tools only available when `goose_mode == Auto` and extension enabled

### Session Management (Source Verified)
| Method | Implementation | Continuity |
|--------|---------------|------------|
| `session/load` | `handle_load_session` → replays history via `replay_conversation_to_client` | **History replay only** — emits tool events from DB |
| `session/fork` | Not implemented in ACP server | N/A |
| `session/resume` | `resume_session` → calls `handle_load_session` | **History replay only** |
| `tools/call` | Not implemented (returns -32601) | N/A |

### Tool Event Emission (Source Verified)
From `tool_call_notifier.rs` + `load_session.rs`:
- `ToolCall` emitted via `send_initial()` for tool requests
- `ToolCallUpdate` emitted via `send_update()` for tool responses
- History replay re-emits **both** with original `toolCallId`
- **No live execution state** — replay is notification only

---

## Hermes Source Findings (Reference)

### Mechanisms Inspected
| Mechanism | File | Relevance to Guardian |
|-----------|------|----------------------|
| Tool start callback | `acp_adapter/events.py:make_tool_progress_cb()` | Already implemented in Guardian observer |
| Tool completion callback | `acp_adapter/events.py:close_tool_call()` | Already implemented |
| Tool abandonment | `acp_adapter/events.py:flush_open_tool_calls()` | Already implemented |
| Session history replay | `acp_adapter/server.py:_replay_session_history()` | Confirms `HISTORY ≠ LIVE EXECUTION` |
| Session persistence | `acp_adapter/session.py:SessionManager` | SQLite backend; `load/fork/resume` implemented |
| MCP server registration | `acp_adapter/server.py:_register_session_mcp_servers()` | Per-session MCP tools |

### Hermes Mechanisms NOT Reused
- No code copied — Guardian already has equivalent/stronger mechanisms
- Guardian's dual-generation TOCTOU protection exceeds Hermes
- Guardian's `partial_tool_names` tracking is more granular
- Hermes `session/load` history replay confirms Guardian's `is_safe_to_replay()` logic is correct

---

## Tool Reliability Results

| Metric | Goose Native | Goose ACP | Hermes ACP |
|--------|-------------|-----------|------------|
| Adapter starts | ✅ | ✅ | ✅ (partial) |
| ACP client connection | N/A | ✅ | ❌ (blocked) |
| Tool discovery | ✅ | ✅ (via extensions) | ✅ (source) |
| Real tool invocation | ✅ | ❌ (hangs) | ❌ (blocked) |
| Repeatable tool invocation | ✅ | ❌ | ❌ |
| Tool start observation | N/A | ❌ | ❌ |
| Tool completion observation | N/A | ❌ | ❌ |
| Disconnect during tool | N/A | ❌ | ❌ |

### Goose Native Evidence (3 Successful Executions)
```bash
# Execution 1
goose run --text "Use the shell tool to run echo hello world"
→ shell: echo hello world → "hello world" ✅

# Execution 2
goose run --text "Use the shell tool to run 'dir C:/tmp/goose-test'"
→ shell: dir "C:\tmp\goose-test" → directory listing ✅

# Execution 3
goose run --text "Use the shell tool to run echo test"
→ shell: echo test → "test" ✅
```

### Goose ACP Evidence (0 Successful Tool Executions)
| Test | Result |
|------|--------|
| `session/prompt` with tool request | Hangs indefinitely (no response) |
| `tools/call` direct invocation | Returns -32601 Method Not Found |
| `tools/list` | Returns -32601 Method Not Found |
| Raw ACP events captured | 0 tool events |

---

## Control Experiment

**NOT ACHIEVED** — No live ACP tool execution observed.

Expected flow (not achieved):
```
session/prompt
    ↓
model chooses tool (developer__shell)
    ↓
ACP emits ToolCallStart
    ↓
tool executes
    ↓
ACP emits ToolCallComplete
    ↓
Guardian observes full lifecycle
```

---

## Disconnect Experiment

**NOT EXECUTABLE** — Prerequisite control experiment not achieved.

---

## Tool State After Disconnect

**UNKNOWN** — No disconnect experiment possible.

---

## Session Identity

### Goose ACP Source Analysis
| Method | Implementation | Identity Continuity |
|--------|---------------|---------------------|
| `session/load` | Replays history via `session/update` notifications | **History only** — no execution continuity |
| `session/fork` | Not implemented | N/A |
| `session/resume` | Delegates to `load_session` | **History only** |
| `tools/call` | Not implemented | N/A |

### Critical Finding
Goose ACP `session/load` **replays history as notifications** (including tool events) before responding. This means:
- Guardian would observe `TOOL_CALL_STARTED`/`TOOL_CALL_COMPLETED` from history
- **These are replay, not live execution state**
- Guardian's `is_safe_to_replay()` correctly returns `False` when `partial_tool_names` non-empty

---

## Session Reattachment

| Capability | Status | Evidence |
|------------|--------|----------|
| ACP `session/load` | **IMPLEMENTED** | Replays history |
| ACP `session/fork` | **NOT_IMPLEMENTED** | Source: not in `server.rs` |
| ACP `session/resume` | **IMPLEMENTED** | Delegates to `load_session` |
| Cryptographic binding | **NOT_IMPLEMENTED** | No session binding |
| Execution continuity | **NOT_PROVEN** | Load = history replay only |

---

## Tool Identity

| Aspect | Status | Evidence |
|--------|--------|----------|
| `toolCallId` generation | **IMPLEMENTED** | `tool_call_notifier.rs` uses stable IDs |
| History replay preserves IDs | **IMPLEMENTED** | `load_session.rs` re-emits with same IDs |
| Stability across reconnect | **NOT_PROVEN** | No live reconnect |
| Correlation across disconnect | **NOT_PROVEN** | Requires live experiment |

---

## Guardian Safety Decision

### Tested Scenarios (Source + Tests)
| Condition | Guardian Decision | Correct? |
|-----------|------------------|----------|
| `TOOL_STARTED` + `CONNECTION_LOST` + `NO_COMPLETION_PROOF` | `UNKNOWN` → `UNSAFE` → `BLOCKED` | ✅ Verified |
| `TOOL_STARTED` → `TOOL_COMPLETED` → `DISCONNECT` | `VERIFICATION_PASSED` → `SAFE` | ✅ (with proof) |
| `session/load` replay shows completed tool | `NOT_PROVEN` (history ≠ live) | ✅ Fail-closed |

### Safety Invariant Maintained
```
POSSIBLE TOOL EXECUTION
        ↓
STATE NOT PROVEN
        ↓
UNKNOWN
        ↓
UNSAFE FOR AUTOMATIC REPLAY
```
**AUTO_RECOVER = OFF** maintained throughout Build #19.

---

## Failure Classification

| Scenario | Guardian Classification | Safety Verdict |
|----------|------------------------|----------------|
| ACP disconnect during active tool | `UNKNOWN` (no completion proof) | `UNSAFE_FOR_AUTO_RECOVER` |
| Tool started, no completion observed | `partial_tool_names` non-empty | `is_safe_to_replay()` → `False` |
| Session load after disconnect | History replay only | `NOT_PROVEN` for live state |

---

## Stale Detection

| State | Detection Method | Status |
|-------|------------------|--------|
| User waiting | No tool events, stream idle | `StreamingWaitMonitor` |
| Active tool | `partial_tool_names` non-empty | Observer tracks `tool.started` |
| Active but stalled | No `tool.completed` for extended period | `stale_timeout_seconds` config |
| Transport disconnected | WebSocket closed | Observer `on_disconnect` |
| Agent crashed | Process gone | `DETECT_CRASH` → STUB (Goose) |

---

## Recovery Analysis

| Recovery Action | Evidence Required | Safe Without Live Proof? |
|-----------------|-------------------|--------------------------|
| `WAIT_AND_RETRY` | Tool completion proof | **NO** |
| `RECONNECT_ACP` | Session identity + tool state | **NO** |
| `RELOAD_SESSION` | History replay + completion | **NO** |
| `FORK_SESSION` | N/A (not implemented) | **NO** |
| `CANCEL_AND_REPLAY` | Cancellation confirmed | **NO** (fire-and-forget) |
| `HUMAN_INTERVENTION` | N/A | **YES** — only safe path |

---

## Code Changes

**None.** Build #19 made zero modifications to Agent Guardian source code.

---

## Tests

| Test Suite | Result |
|------------|--------|
| Full Guardian test suite | **221 / 221 passing** |
| Baseline verification | ✅ Clean at start and end |
| Regression tests added | None (no defects found) |

---

## Runtime Evidence

| Observation | Evidence |
|-------------|----------|
| Goose native tool calling | 3 successful executions with NVIDIA provider |
| Goose ACP server startup | HTTP/WebSocket on port 3284 |
| ACP initialize | Returns capabilities including `load_session=true` |
| ACP session/new | Returns session with modes, config options, model list |
| ACP session/prompt | Hangs indefinitely (no response) |
| ACP tools/call | Returns -32601 Method Not Found |
| ACP tools/list | Returns -32601 Method Not Found |
| Raw ACP events captured | 0 tool events across all sessions |

---

## Remaining Unknowns

1. **Why does Goose ACP `session/prompt` hang?** — No response, no error, no timeout
2. **Does Goose ACP support `tools/call`?** — Source says not implemented
3. **Can Goose ACP emit tool events at all?** — Source has `ToolCallNotifier` but never triggered
4. **Does NVIDIA provider work differently in ACP vs native?** — ACP path discards tools
5. **Would OpenAI/Anthropic provider behave differently?** — Unlikely; ACP architecture discards provider tools

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Guardian safety untested against real ACP disconnect | High | Safety regression possible | Need live ACP tool execution |
| `session/load` history replay mistaken for completion | Medium | False `SAFE` verdict | `is_safe_to_replay()` checks live `partial_tool_names` |
| Tool identity correlation unproven | High | Cannot correlate execution | Need live experiment |

---

## Build #20 Recommendation

### Primary: Build a Minimal Forensic ACP Test Agent
Since no existing ACP agent provides reliable tool execution, build a minimal test agent that:
1. Implements `initialize`, `session/new`, `session/prompt`
2. Emits `ToolCallStart` → executes controllable tool (e.g., `sleep 10`) → emits `ToolCallComplete`
3. Allows deliberate transport disconnect during tool execution
4. Runs as standalone process (no uv/environment issues)

### Alternative: Test Goose with OpenAI/Anthropic Provider
If API keys become available, test whether cloud providers behave differently in ACP mode. However, the ACP provider architecture discards provider tools regardless of provider.

### Alternative: Zed Editor Integration for Hermes
Configure Zed to spawn `hermes acp` as intended — matches Hermes ACP architecture.

---

## Capability Matrix

| Capability | Status | Evidence |
|------------|--------|----------|
| Reliable cloud tool calling | **NOT_REPRODUCIBLE** | Goose native works; ACP hangs |
| Goose ACP tool execution | **NOT_REPRODUCIBLE** | `session/prompt` hangs |
| Hermes ACP tool execution | **BLOCKED** | uv env isolation |
| Real tool start | **NOT_OBSERVED** | No live ACP tool events |
| Real tool completion | **NOT_OBSERVED** | No live ACP tool events |
| Reproducible tool lifecycle | **NOT_REPRODUCIBLE** | 0/∞ ACP tool executions |
| Disconnect during tool | **NOT_OBSERVED** | Prerequisite not met |
| Tool state after disconnect | **UNKNOWN** | No experiment |
| Session identity | **PARTIALLY_VERIFIED** | Source: load=replay, no fork |
| Session persistence | **VERIFIED** | Source: SQLite + history replay |
| Session reattachment | **NOT_PROVEN** | Load = replay only |
| Tool identity across reconnect | **NOT_PROVEN** | IDs stable in replay only |
| Guardian observation | **VERIFIED** | Tests pass; parser/observer ready |
| Unknown-state detection | **VERIFIED** | `partial_tool_names` tracking |
| Replay safety | **VERIFIED** | `is_safe_to_replay()` fail-closed |
| Failure classification | **VERIFIED** | Source: UNKNOWN → UNSAFE |
| Stale detection | **VERIFIED** | `StreamingWaitMonitor` + tool tracking |
| DRY_RUN | **VERIFIED** | Tests pass; budget/ladder/gate work |
| Semantic verification | **NOT_PROVEN** | All `verify_recovery()` = `NOT_PROVEN` |
| Hermes mechanism reused | **NONE** | Guardian already equivalent/stronger |
| AUTO_RECOVER | **NO** | Explicitly OFF; 0 real actions |

---

## Final Answers

| # | Question | Answer |
|---|----------|--------|
| 1 | Reliably tool-calling ACP runtime obtained? | **NO** — Goose ACP hangs; Hermes blocked |
| 2 | Which runtime? | N/A |
| 3 | Real harmless tool repeatedly? | **NO** — 0 ACP tool executions |
| 4 | Disconnect during execution? | **NOT TESTED** — no tool execution |
| 5 | What happens to tool? | **UNKNOWN** |
| 6 | Can we prove final state? | **NO** |
| 7 | Can we reconnect? | **NOT TESTED** |
| 8 | Prove session continuity? | **NOT_PROVEN** — load=replay only |
| 9 | Correlate original execution? | **NOT_PROVEN** |
| 10 | Guardian fail closed? | **YES** — verified |
| 11 | Guardian real defect? | **NO** — no defects found |
| 12 | Hermes useful mechanism? | **YES** — confirms `HISTORY ≠ LIVE` |
| 13 | Hermes implementation adapted? | **NO** — Guardian already stronger |
| 14 | What remains unknown? | Tool behavior during ACP disconnect; session continuity |
| 15 | Build #20 should do? | Build minimal forensic ACP test agent |

---

## Final Safety Invariant

```
POSSIBLE TOOL EXECUTION
        ↓
STATE NOT PROVEN
        ↓
UNKNOWN
        ↓
UNSAFE FOR AUTOMATIC REPLAY
```

**AUTO_RECOVER = OFF** maintained throughout Build #19.

---

## Cleanup Verification

| Item | Status |
|------|--------|
| Goose ACP server | Terminated (PID 13980) |
| Background test processes | Terminated |
| Modified files | None |
| Temporary files | `C:/tmp/test_*.py` (harmless) |
| BUILD_19_REPORT.md | **CREATED** |

---

## Shutdown

All work complete. Final test suite passing. Report saved. No active experimental processes. Safe to shutdown.