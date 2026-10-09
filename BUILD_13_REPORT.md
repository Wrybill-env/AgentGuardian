# Build #13 — Known-Good Goose Tool-Calling Validation

## 1. Executive Summary

**NO KNOWN-GOOD LOCAL TOOL-CAPABLE MODEL AVAILABLE**

The only locally installed model is **Ollama llama3.2**. While this model **DOES support structured function calling via Ollama's native API** (verified), it **DOES NOT produce tool calls through the Goose ACP integration** (13 test sessions, 0 tool events).

The Goose ACP tool notification pipeline is **SOURCE_VERIFIED** (fully implemented in source) but **NOT_OBSERVED at runtime** with the tested configuration.

**AUTO_RECOVER: NOT SAFE** — Tool observability remains a hard prerequisite.

---

## 2. Baseline

| Metric | Value |
|--------|-------|
| Test suite | 221 passed, 0 failed |
| Duration | ~6.2s |
| Project | C:\Projects\AgentGuardian (NOT a Git repository) |
| Previous reports | BUILD_8–12 reviewed |

---

## 3. Environment

| Component | Details |
|-----------|---------|
| Goose binary | `C:/Projects/AgentGuardian/goose_bin/goose.exe` (v1.53.0, pre-built from GitHub releases) |
| Goose mode | `serve --host 127.0.0.1 --port 3284 --dangerously-unauthenticated` |
| Ollama | `C:/Users/Mitansh Kulshreshtha/AppData/Local/Programs/Ollama/ollama.exe` (v0.35.1) |
| Available models | **llama3.2:latest only** (3.2B, Q4_K_M, `capabilities: ["completion", "tools"]`) |
| ACP endpoint | `ws://127.0.0.1:3284/acp` |
| Test workspace | `C:/tmp/goose-test` (disposable) |
| Guardian mode | OBSERVE_ONLY / DRY_RUN only |

---

## 4. Available Models

**Only one model is locally installed:**

| Model | Size | Capabilities (per `ollama list`) | Function Calling via Native API |
|-------|------|----------------------------------|--------------------------------|
| llama3.2:latest | 2.0 GB | `completion`, `tools` | ✅ **VERIFIED WORKS** |

No other models are available locally. No paid/cloud providers configured. No arbitrary software installed.

---

## 5. Candidate Model Selection

**Candidate: llama3.2** — The only available model.

### Provider-Level Tool Calling Test (Ollama Native API)

```bash
POST http://127.0.0.1:11434/api/chat
{
  "model": "llama3.2",
  "messages": [{"role": "user", "content": "What is the weather in London? Use the get_weather tool."}],
  "tools": [{"type": "function", "function": {"name": "get_weather", ...}}],
  "stream": false
}
```

**Result: STRUCTURED TOOL CALL PRODUCED**

```json
{
  "message": {
    "role": "assistant",
    "content": "",
    "tool_calls": [{
      "id": "call_w8ad3lfk",
      "function": {
        "name": "get_weather",
        "arguments": {"location": "London"}
      }
    }]
  },
  "done": true
}
```

**Provider-Level Verdict: VERIFIED — Ollama llama3.2 produces structured tool requests via native API.**

---

## 6. Goose Runtime Test

### Goose ACP Sessions Tested (13 Total)

| Session | Prompt Type | Expected Tool | Result |
|---------|-------------|---------------|--------|
| `20261004_18`–`23` | File creation, /todo, hello | `write`, `todo` | Text only |
| `20261004_24`–`25` | Raw capture (60s each) | `write` | Text only |
| `20261005_2`–`3` | Raw capture (60s each) | `write` | Text only |
| `20261005_4` | Explicit `/todo` | `todo` | Text only + timeout |
| `20261005_5` | Explicit "use get_weather tool" | `get_weather` | Text only + timeout |
| `20261005_6` | Explicit "call get_weather function" | `get_weather` | Text only + timeout |
| `20261005_7` | Shell command "echo hello" | `shell` | Text only + timeout |
| `20261005_8` | Write file prompt | `write` | Text only + timeout |
| `20261005_9` | `/todo` command | `todo` | Text only + timeout |
| `20261005_10` | Raw capture (control) | `write` | Text only |

**All 13 sessions: ZERO structured `ToolRequest` messages, ZERO ACP tool events.**

### Model Output Characteristics

The model produces **text-only responses** that sometimes:
- Describe tools in natural language ("The function `write` is used to create a new file...")
- Output JSON resembling tool results (`{"success": true, "stdout": null, ...}`)
- But **NEVER** produce structured `MessageContent::ToolRequest` that would trigger Goose tool execution.

---

## 7. Raw ACP Evidence

### Aggregate Statistics (All 13 Sessions)

| Metric | Value |
|--------|-------|
| Total sessions | 13 |
| Total ACP events captured | 800+ |
| Tool events at transport level | **0** |
| `agent_message_chunk` events | 700+ |
| `session_info_update` events | 20+ |
| `usage_update` events | 10+ |
| `available_commands_update` events | 10+ |

### Transport Verification

| Layer | Status |
|-------|--------|
| WebSocket connection | ✅ Stable |
| JSON-RPC 2.0 framing | ✅ Correct |
| Initialize handshake | ✅ Complete |
| Session creation | ✅ Success |
| Prompt delivery | ✅ Acknowledged |
| Event delivery | ✅ All 800+ events received |
| **Tool events in transport** | ❌ **NONE** |

---

## 7. Goose Tool Execution Evidence

**VERDICT: NOT_OBSERVED**

| Stage | Source Implemented? | Runtime Triggered? |
|-------|---------------------|-------------------|
| Model decides to call tool | ✅ `reply_internal` | ❌ No tool requests from provider |
| Tool inspection/permission | ✅ `tool_inspection_manager` | ❌ Not triggered |
| Tool execution via MCP | ✅ `dispatch_tool_call` → `call_tool` | ❌ Not triggered |
| Tool result creation | ✅ `ToolStreamItem::Result` | ❌ Not produced |
| MCP notifications during execution | ✅ `tool_notifications.rs` | ❌ Not emitted |

**The Goose tool execution pipeline is never reached because the model never produces `ToolRequest`.**

---

## 8. Guardian Observation Evidence

**VERDICT: NO EVENTS TO OBSERVE**

| Guardian Component | Status |
|-------------------|--------|
| WebSocket receiver | ✅ Receives all 800+ events |
| ACP parser (`protocol.py`) | ✅ Source-verified correct |
| Observer (`observer.py`) | ✅ Would process events if they existed |
| SessionState tool tracking | ❌ No events to process |

**Guardian parser correctly implements all ACP tool event types:**
- `SessionUpdate::ToolCall` → `TOOL_CALL_STARTED`
- `SessionUpdate::ToolCallUpdate` (InProgress) → `TOOL_CALL_UPDATE`
- `SessionUpdate::ToolCallUpdate` (Completed) → `TOOL_CALL_COMPLETED`
- `SessionUpdate::ToolCallUpdate` (Failed) → `TOOL_CALL_FAILED`
- `MessageUpdate` with `tool_call` content → `TOOL_CALL_STARTED`

**No Guardian bugs found in tool observation path.**

---

## 9. Tool State Results

**No tool events → no tool state updates.**

| Guardian Event | SessionState Update | Observed |
|----------------|---------------------|----------|
| `ASSISTANT_CHUNK` | `assistant_chunks_received++` | ✅ (700+) |
| `TOOL_CALL_STARTED` | `tool_calls_started++`, `partial_tool_names.add()` | ❌ |
| `TOOL_CALL_UPDATE` | `partial_tool_names` update | ❌ |
| `TOOL_CALL_COMPLETED` | `tool_calls_completed++`, `partial_tool_names.remove()` | ❌ |
| `TOOL_CALL_FAILED` | `tool_calls_failed++` | ❌ |

**Safety Gate Impact:**
- `has_assistant_output()` = `True` → **REPLAY BLOCKED** (correct)
- `has_tool_activity()` = `False` → Would allow (but irrelevant)
- **Guardian correctly fails closed**

---

## 10. Replay Safety Results

| Condition | Guardian Verdict | Verified? |
|-----------|-----------------|-----------|
| No output, no tools | SAFE | N/A (output exists) |
| Assistant output exists | BLOCK | ✅ |
| Tool started | BLOCK | ❌ NOT_OBSERVED |
| Tool completed | BLOCK | ❌ NOT_OBSERVED |
| Partial tool | BLOCK | ❌ NOT_OBSERVED |

**Actual observed tool execution invalidating replay safety: NOT TESTED (no tools executed).**

---

## 11. DRY_RUN Results

**VERDICT: VERIFIED**

| Test | Result |
|------|--------|
| Guardian in DRY_RUN mode connected | ✅ |
| `RecoveryExecutor._check_mode()` returns `BLOCKED` | ✅ |
| `would_execute: true` in result details | ✅ |
| `adapter.execute_recovery_action()` never called | ✅ |
| Zero recovery actions executed | ✅ |

---

## 12. Session Identity

**NOT_PROVEN** — Only string equality on `sessionId`. No cryptographic identity, no continuity proof across reconnect/load/fork.

---

## 13. Semantic Verification

**NOT_PROVEN** — All recovery actions only verify transport success, not semantic success.

---

## 14. Capability Matrix

| Capability | Provider | Goose | ACP | Guardian | Runtime Status |
|------------|----------|-------|-----|----------|----------------|
| Structured tool request | ✅ Native API | ❌ | N/A | N/A | **PROVIDER: VERIFIED / GOOSE: NOT_OBSERVED** |
| Tool start observation | ✅ | ✅ Source | ✅ Schema | ✅ Parser | **NOT_OBSERVED** |
| Tool update observation | ✅ | ✅ Source | ✅ Schema | ✅ Parser | **NOT_OBSERVED** |
| Tool completion observation | ✅ | ✅ Source | ✅ Schema | ✅ Parser | **NOT_OBSERVED** |
| Tool failure observation | ✅ | ✅ Source | ✅ Schema | ✅ Parser | **NOT_OBSERVED** |
| Partial tool state | ✅ | ✅ Source | ✅ Schema | ✅ Parser | **NOT_OBSERVED** |
| Tool safety invalidation | ✅ | ✅ Source | ✅ Schema | ✅ Parser | **NOT_TESTED** |
| Raw ACP preservation | N/A | N/A | ✅ Transport | ✅ Receiver | **RUNTIME_VERIFIED** |
| Session identity | N/A | ❌ | ❌ | ❌ | **NOT_PROVEN** |
| Semantic verification | N/A | ❌ | ❌ | ❌ | **NOT_PROVEN** |

---

## 15. Bugs Found

| ID | Component | Bug | Severity |
|----|-----------|-----|----------|
| B-001 | Goose ACP + Ollama | llama3.2 doesn't invoke tools via Goose ACP despite native API support | CRITICAL |
| B-002 | Goose ACP | No `toolCallNotifications` capability advertised | HIGH |
| B-003 | Guardian | None in tool observation path | — |

---

## 16. Bugs NOT Found

| Claim | Reality |
|-------|---------|
| Guardian parser drops tool events | ❌ False — parser correct; no raw events |
| Guardian observer loses events | ❌ False — no events to lose |
| ACP transport loses events | ❌ False — 800+ events delivered correctly |
| Goose source lacks tool notification code | ❌ False — fully implemented |
| Capability advertisement blocks tool events | ❌ False — events never generated regardless |
| llama3.2 incapable of tool calling | ❌ False — works via native API |

---

## 17. Unknowns

| Unknown | Impact |
|---------|--------|
| Why does llama3.2 work via native API but not Goose ACP? | Integration issue: system prompt, tool format, or provider config |
| Would a different model (GPT-4o, Claude) work via Goose ACP? | Unknown — no alternate model available |
| Does Goose ACP require specific tool format (OpenAI vs XML)? | Source shows XML fallback for Qwen; llama3.2 should use JSON |
| Is the developer extension properly loaded in ACP mode? | Source suggests yes (client capabilities sent) |

---

## 18. Adversarial Audit

| Question | Answer |
|----------|--------|
| A. Did model actually issue structured tool request? | **NO** — 13 sessions, 0 `ToolRequest` |
| B. Could provider transform to plain text? | ❌ Native API proves structured output works |
| C. Did Goose actually dispatch tool? | ❌ No `ToolRequest` = no dispatch |
| D. Did Goose actually execute it? | ❌ Never reached |
| E. Did ACP event originate from Goose? | N/A — no events |
| F. Did raw ACP contain the event? | **NO** — 800+ events, 0 tool events |
| G. Did Guardian parse the exact event? | N/A — no events |
| H. Could Guardian fabricate tool state? | ❌ No events to fabricate from |
| I. Could apparent tool result be model text? | **YES** — Observed JSON-as-text |
| J. Was test tool harmless? | ✅ All prompts read-only/non-destructive |
| K. Did any Guardian recovery execute? | **NO** — 0 recovery actions |

---

## 19. Test Results

```
============================= 221 passed in 6.19s =============================
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

## 20. AUTO_RECOVER Verdict

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

## 21. Exact Conclusion

```
MODEL / PROVIDER (Ollama llama3.2)
    ↓ NATIVE API: PRODUCES TOOL CALLS ✅
    ↓ GOOSE ACP: PRODUCES TEXT ONLY ❌
MODEL TOOL DECISION (IN GOOSE ACP CONTEXT)
    ↓ FIRST BROKEN BOUNDARY: Model never produces ToolRequest
GOOSE INTERNAL TOOL INVOCATION
    ↓ NEVER TRIGGERED
GOOSE INTERNAL TOOL EXECUTION
    ↓ NEVER TRIGGERED
GOOSE ACP EVENT GENERATION
    ↓ NEVER TRIGGERED
ACP TRANSPORT
    ↓ VERIFIED WORKING
GUARDIAN RECEIVER / PARSER / OBSERVER
    ↓ VERIFIED WORKING — NO EVENTS TO PROCESS
```

**FIRST UNVERIFIED/FAILURE BOUNDARY: MODEL TOOL DECISION (within Goose ACP context)**

The discrepancy between native API (tools work) and Goose ACP (tools don't work) indicates a **Goose ACP + Ollama integration issue** — likely system prompt, tool schema format, or provider configuration differences in the ACP server path.

---

## 22. Build #14 Recommendation

**No known-good local model available.** Only llama3.2 is installed.

### Option A (If Resolvable): Debug Goose ACP + Ollama Integration
1. Compare native API request format vs Goose ACP provider request format
2. Check if Goose ACP passes tools to provider correctly
3. Test with `GOOSE_STATE_MACHINE=1` (new agent loop)
4. If resolvable: complete tool observation validation

### Option B (If Permanent): Document Constraint
1. Update capability matrix: Tool events → `UNAVAILABLE` for Goose+Ollama+llama3.2
2. AUTO_RECOVER permanently blocked for replay actions
3. Focus on non-tool recovery paths

---

## Mandatory Final Answers

| # | Question | Answer |
|---|----------|--------|
| 1 | Candidate model? | **llama3.2** (only available) |
| 2 | Provider? | **Ollama** (local) |
| 3 | Provider-level tool request? | **VERIFIED** — Native API produces structured `tool_calls` |
| 4 | Goose tool execution? | **NOT_OBSERVED** — 0/13 sessions |
| 5 | ACP tool event? | **NOT_OBSERVED** — 0 events at transport |
| 6 | Guardian tool observation? | **NOT_OBSERVED** — No events to observe |
| 7 | Replay safety invalidation after tool? | **NOT_TESTED** — No tools executed |
| 8 | DRY_RUN? | **VERIFIED** — Zero recovery actions |
| 9 | Guardian bug? | **NO** |
| 10 | Goose bug? | **PARTIAL** — Tool infrastructure exists but not activated |
| 11 | Model/provider limitation? | **YES** — llama3.2 doesn't tool-call via Goose ACP |
| 12 | Session identity? | **NOT_PROVEN** |
| 13 | Semantic verification? | **NOT_PROVEN** |
| 14 | TOCTOU? | **SOURCE_VERIFIED** (executor-level dual generation checks) |
| 15 | AUTO_RECOVER? | **NOT SAFE** |
| 16 | Real Guardian recovery actions executed? | **0** |
| 17 | Files modified? | **None** (experiment scripts in `/tmp` only) |

---

## Final Assertions

| Assertion | Status |
|-----------|--------|
| REAL GUARDIAN RECOVERY ACTIONS EXECUTED | **0** |
| AUTO_RECOVER ENABLED | **NO** |
| DESTRUCTIVE TOOL ACTIONS | **0** |
| SOURCE FILES MODIFIED | **0** |
| TEST FILES MODIFIED | **0** |
| CONFIGURATION MODIFIED | **0** |

---

```
BUILD #13 STATUS: COMPLETE

BASELINE: 221 passed

FINAL TEST COUNT: 221

CANDIDATE MODEL: llama3.2 (only available)

PROVIDER: Ollama (local)

PROVIDER-LEVEL TOOL REQUEST: VERIFIED

GOOSE TOOL EXECUTION: NOT_OBSERVED

ACP TOOL EVENT: NOT_OBSERVED

GUARDIAN TOOL OBSERVATION: NOT_OBSERVED

REPLAY SAFETY INVALIDATION AFTER TOOL: NOT_TESTED

DRY_RUN: VERIFIED

GUARDIAN BUG: NO

GOOSE BUG: PARTIAL — Tool infrastructure exists but not activated by this model in ACP mode

MODEL/PROVIDER LIMITATION: YES — llama3.2 works via native API but not Goose ACP

SESSION IDENTITY: NOT_PROVEN

SEMANTIC VERIFICATION: NOT_PROVEN

TOCTOU: SOURCE_VERIFIED

AUTO_RECOVER: NOT SAFE

REAL GUARDIAN RECOVERY ACTIONS EXECUTED: 0

FILES MODIFIED: None

BUILD_13_REPORT: C:\Projects\AgentGuardian\BUILD_13_REPORT.md

BUILD #14: No known-good local model available. Either debug Goose ACP + Ollama integration (compare request formats, check tool passing) or document permanent UNAVAILABLE for tool events with this provider/model combo. No further local models to test.
```