# BUILD #16 REPORT

## Goose ACP Tool Execution + Disconnect Forensics

---

## 1. Executive Summary

**Baseline:** 221 tests passing (verified fresh at Build #16 start)

**Core Finding:** The Build #15 successful tool execution (`execute_typescript` → `shell`) was **not reliably reproducible**. Despite extensive testing with the same configuration (Goose ACP + Ollama + llama3.2), the model **does not consistently trigger tool calls through the ACP path**. This confirms Build #14's architectural finding: Goose ACP provider discards tool definitions, so the model never receives tool schemas in the ACP context.

**Disconnect Experiment:** Could not be executed because no tool call could be triggered to interrupt.

**Guardian Status:** Parser/observer correctly handle ACP tool events (verified in Build #15), but no events to observe in current configuration.

**AUTO_RECOVER:** MUST REMAIN NO — tool execution observability gap persists.

---

## 2. Baseline Verification

| Check | Result |
|-------|--------|
| Test suite | 221 passed, 0 failed, 0 errors |
| Goose binary | v1.53.0 at `goose_bin/goose.exe` |
| Ollama | Running at `127.0.0.1:11434` (OpenAI-compatible endpoint verified) |
| Guardian | OBSERVE_ONLY / DRY_RUN only |
| Model | `llama3.2` (only locally available) |

---

## 3. Build #15 Findings Re-verified

| Claim | Verification |
|-------|--------------|
| Goose ACP CAN execute tools via MCP/ACP path | **PARTIALLY VERIFIED** — One successful execution in Build #15 (session `20261006_10`), not reproducible |
| `execute_typescript` tool works | **VERIFIED** — Executes TypeScript in sandbox, calls `shell` internally |
| ACP tool events (`tool_call`, `tool_call_update`) | **VERIFIED** — Protocol supports, Guardian parser handles |
| Guardian observes tool lifecycle | **VERIFIED** — Parser classifies `TOOL_CALL_STARTED`/`TOOL_CALL_COMPLETED` |
| Build #14 architectural boundary (AcpProvider ignores `_tools`) | **CONFIRMED** — Root cause of unreliable tool calling |

**Critical Correction:** Build #15's single successful tool execution appears to have been an anomaly. The underlying architecture (AcpProvider discarding `_tools` parameter) means the model never receives tool definitions, so it cannot reliably invoke tools.

---

## 4. Goose Disconnect Source Trace

### ACP Connection Lifecycle (server.rs)

```
WebSocket Connection
    ↓
ACP Initialize (protocol handshake)
    ↓
session/new → creates session, loads builtin extensions (default: "developer")
    ↓
session/prompt → on_prompt() → Agent.reply() → provider.stream()
    ↓
AcpProvider.stream() → sends PromptRequest (NO tools forwarded)
    ↓
ACP Server Agent processes prompt with ITS OWN tools (MCP servers)
    ↓
If tool invoked: ToolCallNotifier sends tool_call / tool_call_update via session/update
    ↓
AcpProvider receives ToolCall notifications, yields MessageStream
```

### Disconnect Handling

**Goose ACP Server (server.rs:2622-2645 `on_close_session`):**
- Adds session to `closed_session_ids` set
- Cancels active run via `active_runs.cancel_agent_run(session_id)`
- Stops live voice interaction
- Removes session from in-memory sessions map
- Removes agent from agent_manager

**Critical:** `cancel_agent_run` is called but **tool execution may continue** in background:
- `AcpTools.acp_shell()` creates terminal via `CreateTerminalRequest`
- Runs `WaitForTerminalExitRequest` — this is a blocking ACP request to the **client**
- If client disconnects, the terminal may keep running on server side
- No automatic tool cancellation on client disconnect

### Session Reattachment (load_session.rs:382-486)

`handle_load_session`:
1. Loads session from session_manager
2. Prepares session for activation (cwd, mcp_servers)
3. **Replays conversation to client** via `replay_conversation_to_client` (lines 407-413)
4. Replays tool calls as `tool_call` + `tool_call_update` notifications
5. Resumes pending tool confirmations if state machine enabled
6. **Does NOT re-execute completed tools** — only replays history

**Key Finding:** `session/load` **replays tool events** but does not re-execute tools. The tool's original execution state is not recoverable — only its recorded output.

### Session Fork (server.rs:2615-2619)

`on_fork_session` creates new session with copied history. No cryptographic identity binding.

---

## 5. Guardian Disconnect Source Trace

### Observer Disconnect (observer.py)

```python
async def _receive_loop(self):
    async for message in self._ws:
        await self._handle_message(message)
    # ConnectionClosed exception → marks all sessions disconnected
```

**On disconnect:** All sessions marked `connection_state = "disconnected"`

### Failure Detector (observer.py:158-201)

Checks for:
- `POSSIBLE_STALE_STREAM`: No events for `stale_threshold` (default 60s)
- `BACKEND_DISCONNECTED`: WebSocket closed
- Various error patterns (rate limit, server error, timeout, connection reset, auth error)
- `UNEXPECTED_TERMINATION`: Error/cancelled before any turn

### State Machine (state_machine.py)

On `FAILURE_DETECTED`:
1. Classifies failure via `FailureClassifier`
2. Checks safety via `SafetyGate.check_recovery_action_safety()`
3. If AUTO_RECOVER enabled and SAFE → executes recovery action
4. **If tool activity observed** → `is_safe_to_replay() = False` → blocks replay

### Recovery Executor (recovery_executor.py)

Dual generation checks at execution boundary (lines 516-616):
- Pre-execution: compares `state_generation` + `partial_tool_names`
- Post-execution: same checks
- If mismatch → `TOCTOU_DETECTED`, action blocked

### Adapter Recovery Actions (adapter.py)

| Action | Implementation | Verification |
|--------|---------------|--------------|
| `CANCEL_AND_REPLAY` | Sends `agent/session/cancel` (fire-and-forget) | Only verifies send succeeded |
| `RELOAD_SESSION` | Sends `agent/session/load` | Verifies ACP response has no error |
| `FORK_SESSION` | Sends `agent/session/fork` | Verifies new session ID returned |
| `RECONNECT_ACP` | Disconnects + reconnects | Verifies WebSocket connected |

**Critical Gap:** All verifications are **transport-level only** (`NOT_PROVEN` semantic verification).

---

## 6. Control Experiment (Normal Tool Execution)

**Expected (from Build #15):**
```
tool_call → tool_call_update (completed) → Guardian observes both
```

**Actual (Build #16 - 10+ attempts):**
```
session_info_update → usage_update → available_commands_update → TIMEOUT
```

**Result:** No tool events observed in any session. The model (llama3.2) outputs text only, never invokes tools through ACP.

**Root Cause Confirmed:** `AcpProvider.stream()` ignores `_tools` parameter → model never receives tool definitions → cannot invoke tools.

---

## 7. Disconnect-During-Tool Experiment

**Status: COULD NOT EXECUTE**

**Reason:** No tool call could be triggered to interrupt. All attempts resulted in:
- 3-4 initial session events (info, usage, commands)
- Then complete silence (no assistant chunks, no tool calls)
- Session eventually times out

**What would be needed:**
1. A model that reliably triggers tool calls through ACP (e.g., GPT-4 via OpenAI provider)
2. Or a different ACP agent that supports function calling (Copilot, Codex)
3. Or fixing Goose's AcpProvider to forward tools

---

## 8. Tool State After Disconnect (Theoretical Analysis)

Based on source code analysis:

| Scenario | Tool State | Guardian Knowledge | Replay Safe? |
|----------|------------|-------------------|--------------|
| Tool started → disconnect before completion | UNKNOWN (may continue on server) | `partial_tool_names` has tool name | **NO** (AMBIGUOUS) |
| Tool completed → disconnect after | COMPLETED (recorded in history) | `tool_call_update` (completed) observed | **NO** (side effects possible) |
| Tool failed → disconnect | FAILED | `tool_call_update` (failed) observed | **NO** |
| Tool cancelled by user → disconnect | CANCELLED | `tool_call_update` (cancelled) observed | **NO** |

**Goose Behavior:** 
- Tool execution runs in `AcpTools` methods (e.g., `acp_shell`)
- Terminal created via ACP `CreateTerminalRequest` to **client**
- `WaitForTerminalExitRequest` blocks on client connection
- **If client disconnects:** Terminal may keep running (server-side process), but completion notification cannot be delivered

---

## 9. Session Identity

| Aspect | Status | Evidence |
|--------|--------|----------|
| Session ID format | VERIFIED | `20261006_N` (date + counter) |
| Stability across messages | VERIFIED | Same ID in all events |
| Survives reconnect (`session/load`) | PARTIALLY_VERIFIED | Source shows replay, no crypto binding |
| Turn continuity | PARTIALLY_VERIFIED | Run ID in `_meta.goose.activeRunId` |
| Semantic continuity | NOT_PROVEN | No state hashes, no tool execution proof |

**`session/load` Implementation:** Replays conversation history including tool calls as notifications. Does not prove the tool actually re-executed or that state is identical.

---

## 10. Reattachment/Reconnection

### `session/load` (ACP Protocol)
- **Reloads session history** including tool calls
- **Replays** `tool_call` + `tool_call_update` notifications to client
- **Resumes** pending tool confirmations (if state machine)
- **Does NOT** re-execute completed tools

### `session/fork` (ACP Protocol)
- Creates new session with copied history
- New session ID
- No identity binding to parent

### Guardian Reconnect
- `RECONNECT_ACP`: New WebSocket connection, no session continuity
- `RELOAD_SESSION`: Uses `session/load`, transport-only verification
- Both verifications return `NOT_PROVEN` for semantic success

---

## 11. Guardian Safety Verdict

### Failure Classification for Disconnect During Tool

```
Observed: TOOL_CALL_STARTED (tool_call_id recorded in partial_tool_names)
Not Observed: TOOL_CALL_COMPLETED / TOOL_CALL_FAILED
Guardian State: tool_activity_observed = True, partial_tool_names = {"tool_name"}
is_safe_to_replay() = False
```

### Safety Gate (safety_gate.py)

| Recovery Action | Tool State | Safety Verdict | AUTO_RECOVER |
|----------------|------------|----------------|--------------|
| CANCEL_AND_REPLAY | Tool started, not completed | AMBIGUOUS | BLOCKED |
| RELOAD_SESSION | Tool started, not completed | NOT_PROVEN | BLOCKED |
| FORK_SESSION | Tool started, not completed | AMBIGUOUS | BLOCKED |
| RECONNECT_ACP | Any | NOT_PROVEN | BLOCKED |
| WAIT_AND_RETRY | Any | SAFE (no action) | Would allow |
| HUMAN_INTERVENTION | Any | SAFE | Would allow |

**Guardian correctly fails closed** when tool state is unknown.

---

## 12. Stale Detection

**Observer stale threshold:** 60 seconds (default)

**Stale vs Disconnect:**
- Stale: No events for 60s, connection still open
- Disconnect: WebSocket closed, `connection_state = "disconnected"`

**Problem:** Cannot distinguish "tool executing slowly" from "stalled stream"
- Tool execution may take >60s (e.g., long compilation)
- Observer would report `POSSIBLE_STALE_STREAM` incorrectly
- No mechanism to know tool is actively running

**Mitigation Needed:** Tool progress notifications (via `tool_call_update` with `status: "in_progress"`) would reset stale timer.

---

## 13. Failure Classification

| Disconnect Type | Classification | Evidence |
|----------------|----------------|----------|
| WebSocket close | NETWORK_ERROR / CONNECTION_RESET_DETECTED | ConnectionClosed exception |
| No events >60s | POSSIBLE_STALE_STREAM | Timer-based |
| Server 5xx | SERVER_ERROR_DETECTED | Error message pattern |
| Rate limit 429 | RATE_LIMIT_DETECTED | Error message pattern |
| Auth 401/403 | AUTHENTICATION_ERROR_DETECTED | Error message pattern |

**Classification ≠ Replay Authorization:** All tool-active states → AMBIGUOUS/UNSAFE → AUTO_RECOVER blocked.

---

## 14. DRY_RUN Recovery Plan

For disconnect during tool execution:

```
DRY_RUN CANCEL_AND_REPLAY:
  → Would send agent/session/cancel (fire-and-forget)
  → Verification: only confirms send succeeded
  → Semantic verification: NOT_PROVEN (cancel may not have been processed)

DRY_RUN RELOAD_SESSION:
  → Would send agent/session/load
  → Verification: ACP response has no error
  → Semantic verification: NOT_PROVEN (history replayed, tool not re-executed)

DRY_RUN FORK_SESSION:
  → Would send agent/session/fork
  → Verification: new session ID returned
  → Semantic verification: NOT_PROVEN (new session, copied history)

ALL: AUTO_RECOVER blocked by safety gate
```

---

## 15. Code Changes

**NONE** — No Guardian defects found. The tool execution gap is in Goose's AcpProvider (external to Guardian).

---

## 16. Tests

| Test Suite | Result |
|------------|--------|
| Full suite (baseline) | 221 passed |
| Full suite (post-investigation) | 221 passed |
| TOCTOU race tests | 5 passed (in test_recovery_executor) |
| Guardian Hermes tests | 56 passed |

---

## 17. Runtime Evidence Summary

| Experiment | Sessions | Tool Events |
|------------|----------|-------------|
| Build #15 (working) | 1 | 2 (tool_call + tool_call_update) |
| Build #16 control | 12+ | 0 |
| Disconnect during tool | 0 (could not trigger) | N/A |
| Direct Ollama tool calling | N/A | VERIFIED (native API) |

---

## 18. Remaining Unknowns

| Unknown | Impact |
|---------|--------|
| Whether other ACP agents (Copilot, Codex) trigger tools reliably | Would enable real disconnect testing |
| Whether GOOSE_STATE_MACHINE=1 changes tool behavior | Not tested reliably |
| Tool behavior after client disconnect (server-side continuation) | Source suggests continues, not verified |
| Whether `session/load` replays in-progress tool calls | Source shows only completed tools in history |
| Cryptographic session identity | NOT_PROVEN |

---

## 19. Required Final Answers

1. **What happens to a Goose tool when ACP disconnects during execution?**
   - Tool execution likely continues on server (terminal process runs), but completion notification cannot be delivered to disconnected client.

2. **Does the tool continue running?**
   - **LIKELY YES** — `AcpTools.acp_shell` creates terminal on server, waits via `WaitForTerminalExitRequest` to client. If client gone, terminal process may complete orphaned.

3. **Can Goose prove its final state?**
   - **NO** — No mechanism to report completion to disconnected client. `session/load` only replays recorded history.

4. **Can Guardian prove its final state?**
   - **NO** — Only knows tool started (`partial_tool_names`). No completion event = UNKNOWN state.

5. **Can the same session be safely reattached?**
   - `session/load` replays history but **does not re-execute tools**. Safe for read-only, unsafe if replay would duplicate side effects.

6. **Can a tool call be uniquely tracked across reconnect?**
   - **YES** — `toolCallId` persists in session history and is replayed on `session/load`.

7. **Does Guardian correctly fail closed when tool state is unknown?**
   - **YES** — `is_safe_to_replay() = False` when `tool_activity_observed = True`.

8. **Does Guardian distinguish completed-before-disconnect from interrupted-before-completion?**
   - **YES** — Completed: `partial_tool_names` cleared. Interrupted: tool name remains in `partial_tool_names`.

9. **What recovery actions are safe in DRY_RUN?**
   - `WAIT_AND_RETRY`, `HUMAN_INTERVENTION` (no external effects)
   - All others blocked by safety gate

10. **What remains impossible to prove?**
    - Whether tool actually completed after disconnect
    - Semantic session continuity across reconnect
    - Whether replay would duplicate side effects

11. **What should Build #17 investigate?**
    - Test with OpenAI/Anthropic provider (function-calling models)
    - Implement controlled disconnect test with real tool
    - Add tool progress notifications to stale detection
    - Semantic `verify_recovery()` for Goose

---

## 20. Capability Matrix

| Capability | Status | Evidence |
|------------|--------|----------|
| Normal ACP tool execution | NOT_REPRODUCIBLE | Build #15: 1/13 sessions; Build #16: 0/12+ |
| Tool start observation | VERIFIED | Build #15: `TOOL_CALL_STARTED` |
| Tool completion observation | VERIFIED | Build #15: `TOOL_CALL_COMPLETED` |
| Tool failure observation | VERIFIED_CAPABLE | Protocol supports, not observed live |
| Disconnect observation | VERIFIED | Observer marks `disconnected` |
| Tool state after disconnect | UNKNOWN | Cannot trigger tool to test |
| Session identity | PARTIALLY_VERIFIED | String ID only, no crypto |
| Session reattachment | PARTIALLY_VERIFIED | `session/load` replays history |
| Tool identity across reconnect | VERIFIED | `toolCallId` replayed |
| Stale detection | VERIFIED | 60s threshold, but false positives for slow tools |
| Failure classification | VERIFIED | Pattern-based, multiple categories |
| Replay safety after known completion | VERIFIED UNSAFE | `is_safe_to_replay() = False` |
| Replay safety after unknown state | VERIFIED UNSAFE | `is_safe_to_replay() = False` |
| DRY_RUN recovery planning | VERIFIED | Actions implemented, transport-only verify |
| Semantic recovery verification | NOT_PROVEN | All `verify_recovery()` = NOT_PROVEN |
| AUTO_RECOVER | **NO** | Correctly blocked |

---

## 21. Final Assertion

```
REAL GUARDIAN RECOVERY ACTIONS EXECUTED: 0
```

---

*Build #16 Complete — Disconnect forensics traced through source. Tool execution not reliably reproducible in current Goose ACP + Ollama configuration. Guardian correctly fails closed. Real disconnect test requires function-calling model via ACP.*