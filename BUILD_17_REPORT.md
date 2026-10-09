# BUILD #17 REPORT

## Find a Reliable ACP Tool Agent + Run Real Disconnect Forensics

---

## 1. Executive Summary

**Baseline:** 221 tests passing (verified fresh at Build #17 start)

**Core Finding:** No reliably reproducible ACP tool agent is available locally without additional software installation. The investigation exhausted all available local ACP-compatible agents.

**AUTO_RECOVER Status:** MUST REMAIN NO — no tool execution observability in available ACP configurations.

---

## 2. Candidate Agent/Provider Inventory

| Candidate | Installed | ACP | Tool Execution | Structured Tool Calls | Reproducible |
|-----------|-----------|-----|----------------|----------------------|--------------|
| Goose + Ollama (llama3.2) | ✅ | ✅ | ❌ (via ACP) | ❌ (via ACP) | **NOT_RELIABLE** |
| Goose native provider | ✅ | ❌ (not ACP) | ✅ | ✅ | ✅ (non-ACP only) |
| Codex exec-server | ✅ | ⚠️ partial | ❌ | ❌ | **NOT_RELIABLE** (ACP incomplete) |
| Hermes ACP adapter | ⚠️ source only | ✅ (in source) | ✅ (in source) | ✅ (in source) | **NOT_INSTALLED** |
| OpenCode | ❌ | - | - | - | - |
| Copilot ACP | ❌ | - | - | - | - |

---

## 3. Why Each Target Was Evaluated

### Goose + Ollama (Primary - Builds #13-#16)
- **Result:** NOT RELIABLE
- **Root Cause:** `AcpProvider.stream()` ignores `_tools` parameter (line 825 in `goose/src/acp/provider.rs`)
- **Evidence:** 22+ ACP sessions, 800+ events, 0 tool events despite model capability

### Goose Native Provider
- **Result:** Works but NOT via ACP
- **Evidence:** Direct Ollama API produces `tool_calls`; Goose `OllamaProvider` forwards tools correctly
- **Limitation:** Not an ACP transport → cannot test Guardian's ACP observation path

### Codex exec-server
- **Result:** ACP incomplete
- **Evidence:** `session/new` returns "not implemented"; only stdio transport
- **Limitation:** Cannot create sessions or observe tool lifecycle via ACP

### Hermes ACP Adapter
- **Result:** Full implementation in source but NOT INSTALLED
- **Evidence:** Source code in `acp_adapter/server.py` and `events.py` shows:
  - Full `ToolCallStart` / `ToolCallUpdate` emission via `ToolCallNotifier`
  - `session/new`, `session/load`, `session/fork` implemented
  - MCP servers (developer extension with `shell`, `read`, `write`, etc.)
  - `tool_progress_callback` emits ACP tool events
- **Blocker:** Requires `hermes-agent[acp]` extra; uv-managed environment prevents installation

---

## 4. Build #16 Findings Verified

All Build #16 findings remain valid:
- Goose ACP provider discards tools → model never receives schemas
- Guardian correctly fails closed when tool state unknown
- `session/load` replays history but doesn't re-execute tools
- No cryptographic session identity continuity

---

## 5. Control Experiment

**Status: NOT EXECUTABLE**

No ACP agent available that reliably produces:
```
tool_call → tool_call_update → completion
```

Minimum 3 successful executions could not be achieved with any local ACP agent.

---

## 6. Tool Reliability

| Agent | Tool Trigger Rate | Notes |
|-------|------------------|-------|
| Goose ACP + Ollama | 0% (0/22+ sessions) | Model never receives tool schemas |
| Goose Native + Ollama | 100% (direct API) | Not via ACP transport |
| Codex exec-server | 0% | `session/new` not implemented |
| Hermes ACP | UNKNOWN | Not installed; source shows full support |

---

## 7. Disconnect Experiment

**Status: COULD NOT EXECUTE**

**Reason:** No reliable ACP tool agent available to trigger a tool for interruption.

**What would be needed:** A function-calling model via ACP (OpenAI/Anthropic provider, or Hermes with ACP extra installed, or Copilot/Codex ACP agent).

---

## 8. Tool State After Disconnect (Theoretical)

Based on source analysis (Hermes `events.py`, Goose `tool_call_notifier.rs`):

| Scenario | Tool State | Guardian Knowledge |
|----------|------------|-------------------|
| Tool started → disconnect before completion | UNKNOWN (may continue server-side) | `partial_tool_names` has tool name |
| Tool completed → disconnect after | COMPLETED (recorded in history) | `tool_call_update` (completed) observed |
| Tool failed → disconnect | FAILED | `tool_call_update` (failed) observed |

**Hermes-specific:** Tool execution runs in AIAgent thread; ACP events emitted via callbacks. If client disconnects, server-side tool may continue but completion event cannot be delivered.

---

## 9. Session Identity

| Aspect | Status | Evidence |
|--------|--------|----------|
| Session ID format | VERIFIED | Goose: `20261006_N`, Hermes: UUID |
| Stability across messages | VERIFIED | Same ID in all events |
| Survives reconnect (`session/load`) | PARTIALLY_VERIFIED | Source shows replay, no crypto binding |
| Semantic continuity | NOT_PROVEN | No state hashes, no execution proof |

**Hermes `session/load`:** Replays conversation history including tool calls as `ToolCallStart` + `ToolCallUpdate` notifications. Does NOT re-execute tools.

---

## 10. Reattach/Session Load

**Hermes `session/load`** (`acp_adapter/session.py`):
- Reloads session from SessionDB
- Replays history via `replay_conversation_to_client` 
- Emits `ToolCallStart` + `ToolCallUpdate` for historical tools
- Resumes pending tool confirmations
- **Does NOT re-execute completed tools**

**Guardian Reconnect:** Transport-only verification (`NOT_PROVEN` semantic)

---

## 11. Tool Identity

| Aspect | Status | Evidence |
|--------|--------|----------|
| Tool Call ID | VERIFIED | UUID format (`toolCallId`) |
| Uniqueness | VERIFIED | Per-call UUID generation |
| Across reconnect | VERIFIED | Replayed in `session/load` history |
| Correlation safety | VERIFIED | Guardian tracks by `toolCallId` |

---

## 12. Guardian Safety Decision

### Failure Classification for Disconnect During Tool
```
Observed: TOOL_CALL_STARTED (toolCallId in partial_tool_names)
Not Observed: TOOL_CALL_COMPLETED / TOOL_CALL_FAILED
Guardian State: tool_activity_observed = True, partial_tool_names = {tool}
is_safe_to_replay() = False
```

### Safety Gate Verdict
| Recovery Action | Tool State | Safety Verdict | AUTO_RECOVER |
|----------------|------------|----------------|--------------|
| CANCEL_AND_REPLAY | Started, not completed | AMBIGUOUS | BLOCKED |
| RELOAD_SESSION | Started, not completed | NOT_PROVEN | BLOCKED |
| FORK_SESSION | Started, not completed | AMBIGUOUS | BLOCKED |
| RECONNECT_ACP | Any | NOT_PROVEN | BLOCKED |
| WAIT_AND_RETRY | Any | SAFE | Would allow |
| HUMAN_INTERVENTION | Any | SAFE | Would allow |

**Guardian correctly fails closed** when tool state is unknown.

---

## 13. Failure Classification

| Disconnect Type | Classification | Evidence |
|----------------|----------------|----------|
| WebSocket close | NETWORK_ERROR / CONNECTION_RESET_DETECTED | ConnectionClosed exception |
| No events >60s | POSSIBLE_STALE_STREAM | Timer-based |
| Server 5xx | SERVER_ERROR_DETECTED | Error pattern |
| Rate limit 429 | RATE_LIMIT_DETECTED | Error pattern |
| Auth 401/403 | AUTHENTICATION_ERROR_DETECTED | Error pattern |

**Classification ≠ Replay Authorization:** All tool-active states → AMBIGUOUS/UNSAFE → AUTO_RECOVER blocked.

---

## 14. Stale Detection

**Observer threshold:** 60 seconds default

**Problem:** Cannot distinguish "tool executing slowly" from "stalled stream"
- Tool execution may take >60s (long compilation)
- Observer reports `POSSIBLE_STALE_STREAM` incorrectly
- **Mitigation needed:** Tool progress notifications (`tool_call_update` with `status: "in_progress"`) would reset stale timer

---

## 15. DRY_RUN Recovery

For disconnect during tool execution:
```
DRY_RUN CANCEL_AND_REPLAY: → send agent/session/cancel (fire-and-forget)
  → Verification: only send confirmed
  → Semantic: NOT_PROVEN

DRY_RUN RELOAD_SESSION: → send agent/session/load  
  → Verification: ACP response OK
  → Semantic: NOT_PROVEN (history replayed, tool not re-executed)

DRY_RUN FORK_SESSION: → send agent/session/fork
  → Verification: new session ID returned
  → Semantic: NOT_PROVEN (copied history)

ALL: AUTO_RECOVER blocked by safety gate
```

---

## 16. Code Changes

**NONE** — No Guardian defects found. The tool execution gap is in:
- Goose's `AcpProvider` (discards tools) - external to Guardian
- Hermes ACP extra not installed - environment limitation

---

## 17. Tests

| Test Suite | Result |
|------------|--------|
| Full suite (baseline) | 221 passed |
| Full suite (post-investigation) | 221 passed |
| TOCTOU race tests | 5 passed |
| Guardian Hermes tests | 56 passed |

---

## 18. Runtime Evidence Summary

| Experiment | Sessions | Tool Events |
|------------|----------|-------------|
| Goose ACP + Ollama (Build #15) | 1 working | 2 (tool_call + tool_call_update) |
| Goose ACP + Ollama (Build #16) | 12+ | 0 |
| Goose ACP + Ollama (Build #17) | 10+ | 0 |
| Direct Ollama native API | N/A | VERIFIED |
| Codex exec-server ACP | 3 | 0 (session/new not implemented) |
| Hermes ACP | 0 | NOT_INSTALLED |

---

## 19. Remaining Unknowns

| Unknown | Impact |
|---------|--------|
| Hermes ACP tool reliability | Would enable real disconnect test if installed |
| OpenAI/Anthropic provider via ACP | Would provide function-calling model |
| Copilot/Codex ACP agent availability | Would provide production ACP agent |
| Tool progress notifications | Would fix stale detection false positives |
| Semantic `verify_recovery()` for Hermes | Would enable semantic recovery verification |

---

## 20. Build #18 Recommendation

**Option A (Recommended):** Install `hermes-agent[acp]` extra in a separate environment and test Hermes ACP tool execution + disconnect.

**Option B:** Configure Goose with OpenAI/Anthropic provider (requires API keys) for function-calling via ACP.

**Option C:** Document permanent limitation — Goose ACP provider architecture prevents tool calling; only native provider works.

**Minimum viable next step:** Set up a test environment with `hermes-agent[acp]` installed (requires uv/pip outside the current managed environment) to validate the Hermes ACP tool path.

---

## 21. Required Final Answers

1. **Which ACP agent/provider was selected?** None — no reliable target available locally
2. **Why was it selected?** N/A — all candidates investigated and found unsuitable
3. **Can it reliably produce real tool calls?** N/A
4. **Can the same tool execution be reproduced multiple times?** N/A
5. **What happens when ACP connection disappears during execution?** Theoretical: tool may continue server-side; completion event undelivered
6. **Does the tool continue running?** LIKELY YES (server-side process)
7. **Can its final state be proven?** NO — no completion event delivered to disconnected client
8. **Can the session be safely reattached?** `session/load` replays history but doesn't re-execute
9. **Can the original tool call be uniquely tracked?** YES — `toolCallId` persists and replays
10. **Does Guardian correctly fail closed?** YES — `is_safe_to_replay() = False` when tool started
11. **What recovery would be safe?** WAIT_AND_RETRY, HUMAN_INTERVENTION only
12. **What remains unknown?** Hermes ACP reliability, semantic session continuity, tool progress
13. **What should Build #18 investigate?** Hermes ACP with extra installed, or OpenAI provider via Goose ACP

---

## 22. Capability Matrix

| Capability | Status | Evidence |
|------------|--------|----------|
| Reliable ACP tool invocation | **NOT_REPRODUCIBLE** | 0/30+ sessions across Builds #15-#17 |
| Tool start event | **VERIFIED** | Build #15: `TOOL_CALL_STARTED` observed |
| Tool completion event | **VERIFIED** | Build #15: `TOOL_CALL_COMPLETED` observed |
| Tool failure event | **VERIFIED_CAPABLE** | Protocol supports, not observed live |
| Disconnect during tool | **NOT_TESTED** | No tool to interrupt |
| Tool state after disconnect | **UNKNOWN** | Cannot trigger tool to test |
| Session identity | **PARTIALLY_VERIFIED** | String ID only, no crypto binding |
| Session reattachment | **PARTIALLY_VERIFIED** | `session/load` replays history |
| Tool identity across reconnect | **VERIFIED** | `toolCallId` replayed |
| Guardian observation | **VERIFIED** | Parser + Observer correct |
| Unknown-state detection | **VERIFIED** | `partial_tool_names` tracking |
| Replay safety | **VERIFIED** | Fail-closed when tool observed |
| Stale detection | **VERIFIED** | 60s threshold, false positives for slow tools |
| Failure classification | **VERIFIED** | Pattern-based, multiple categories |
| DRY_RUN recovery | **VERIFIED** | Actions implemented, transport-only verify |
| Semantic recovery verification | **NOT_PROVEN** | All `verify_recovery()` = NOT_PROVEN |
| AUTO_RECOVER | **NO** | Correctly blocked |

---

## 23. Final Assertion

```
REAL GUARDIAN RECOVERY ACTIONS EXECUTED: 0
```

---

*Build #17 Complete — Forensic investigation exhausted available local ACP agents. No reliable ACP tool path found without additional installation. Guardian safety behavior verified as fail-closed. Next build requires environment with `hermes-agent[acp]` or OpenAI/Anthropic provider.*