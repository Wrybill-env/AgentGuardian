# Strixora — Hermes Research & Integration Design

**Date:** 2026-10-03  
**Hermes Version:** Current (installed at `C:\Users\Mitansh Kulshreshtha\AppData\Local\hermes\hermes-agent\`)  
**Strixora Location:** `C:\Projects\AgentGuardian`  
**Platform:** Windows 11

---

## 1. Executive Summary

This document captures a complete architectural investigation of Hermes Agent to inform the design of **Strixora** — an external watchdog that detects transient failures (rate limits, network errors, stream stalls, backend crashes) and recovers safely **without duplicating side effects** (tool calls, file writes, API calls).

**Key Architectural Findings:**

| Aspect | Finding |
|--------|---------|
| **Architecture** | Python async agent core (`AIAgent`) with provider-agnostic design, runs on CLI/TUI/Desktop/Web/ACP |
| **Process Model** | Single-process, multi-threaded (ThreadPoolExecutor for blocking I/O), no subprocess spawning for model calls |
| **Communication** | Direct HTTP/HTTPS to providers (OpenAI-compatible, Anthropic, Gemini, etc.), no ACP between core and provider |
| **Protocol** | OpenAI Chat Completions, Anthropic Messages, Gemini, Responses API, Codex Responses — NOT ACP for provider communication |
| **Session Storage** | SQLite (`state.db`) with FTS5, sessions table + messages table, WAL mode, persisted per-turn |
| **Retry Logic** | Multi-layer: provider-level (exponential backoff, jitter, Retry-After), stream-level (reconnect), turn-level (auto-recovery ladder), recipe-level (goal verification) |
| **Streaming** | SSE-style chunked responses via httpx/httpx-sse, surfaced via delta callbacks (`stream_delta_callback`, `reasoning_callback`) |
| **Crash Recovery** | Session persistence in SQLite survives process restart; `adopt_unanswered_turn` re-stages unanswered user messages; turn boundary export via `export_current_turn_boundary` |
| **ACP Server** | Separate `acp_adapter` exposes Hermes via ACP for IDEs (VS Code/Zed/JetBrains) — NOT used internally |

**Critical Safety Constraint:** Hermes **does** have session-aware recovery mechanisms (session persistence, turn boundary tracking, auto-recovery ladder), but **once a tool call is dispatched to the tool executor, it executes immediately** — resending the prompt **will re-execute tools**. The safety gate is `_current_streamed_assistant_text` + `partial_tool_names` tracking.

**Guardian Integration Point:** The safest observation point is the **streaming monitor** (`StreamingWaitMonitor` + `ChatCompletionStreamMonitor`) and **turn lifecycle** (`conversation_loop.py` + `turn_retry_state.py`). A read-only observer can hook into delta callbacks and turn events without interfering.

---

## 2. Hermes Architecture Deep Dive

### 2.1 Process Topology

```
┌─────────────────────────────────────────────────────────────────┐
│ Hermes Agent (Python)                                           │
│  ├─ AIAgent (agent/agent_init.py) — core agent class           │
│  ├─ conversation_loop.py — turn orchestration                   │
│  │   ├─ turn_api_call.py — API call + retry loop               │
│  │   ├─ turn_api_error.py — error classification + recovery    │
│  │   ├─ turn_retry_state.py — per-attempt recovery guards      │
│  │   ├─ turn_recovery.py — one-shot recovery handlers          │
│  │   ├─ turn_recovery_autorecover.py — auto-recovery ladder    │
│  │   ├─ turn_tool_round.py — tool dispatch + execution         │
│  │   ├─ turn_finalizer.py — post-turn persistence              │
│  │   └─ ... (10+ phase modules)                                 │
│  ├─ chat_completion_helpers.py — streaming/non-streaming drivers│
│  │   ├─ _StreamingCall — manages stream lifecycle              │
│  │   ├─ _accept_chat_chunk — per-chunk processing              │
│  │   └─ _finish_chat_stream — stream completion + stubs        │
│  ├─ chat_completion_stream_monitor.py — StreamingWaitMonitor   │
│  │   ├─ _monitor_loop — stale detection (30s heartbeat)        │
│  │   ├─ _heartbeat — gateway inactivity heartbeat              │
│  │   ├─ _poll_local_load_notice — local model loading          │
│  │   └─ _kill_stale_stream / _abort_for_interrupt              │
│  ├─ error_classifier.py — FailoverReason taxonomy (27 types)   │
│  ├─ retry_utils.py — jittered backoff, Retry-After parsing     │
│  ├─ session_persistence.py — SQLite flush with _DB_PERSISTED_MARKER│
│  ├─ turn_context.py — per-turn setup (build_turn_context)      │
│  ├─ providers/ — 20+ provider adapters (Anthropic, OpenAI, etc.)│
│  ├─ tool_executor.py — tool dispatch + execution (side effects)│
│  ├─ hermes_state*.py — SQLite session DB (sessions + messages) │
│  └─ acp_adapter/ — ACP server for IDE integration (external)   │
└─────────────────────────────────────────────────────────────────┘
```

**Sources:**
- `agent/agent_init.py` — AIAgent class, initialization
- `agent/conversation_loop.py` — `_run_conversation_turn`, phase orchestration
- `agent/chat_completion_helpers.py` — `_StreamingCall`, `_attempt_stream_response`
- `agent/chat_completion_stream_monitor.py` — `StreamingWaitMonitor`
- `agent/error_classifier.py` — `FailoverReason` enum, classification pipeline
- `agent/retry_utils.py` — `jittered_backoff`, `parse_retry_after_seconds`
- `agent/session_persistence.py` — `_DB_PERSISTED_MARKER`, flush logic
- `agent/turn_context.py` — `build_turn_context`, `compose_user_api_content`
- `hermes_state_sessions.py` — SessionDB CRUD, lineage, compression
- `acp_adapter/server.py` — `HermesACPAgent`, ACP protocol implementation

### 2.2 Turn Lifecycle & State Machine

```
User Message Received
        │
        ▼
build_turn_context() — restores system prompt, checks compression, builds api_messages
        │
        ▼
while (api_call_count < max_iterations):
        │
        ├─ prepare_iteration / assemble_api_request / run_preflight_gate
        │
        ├─ announce_api_call
        │
        ├─ _run_api_retry_loop (inner retry loop):
        │       ├─ nous_rate_limit_guard
        │       ├─ build_api_request
        │       ├─ perform_api_call (chat_completions / anthropic / gemini / codex)
        │       │       ├─ _attempt_stream_response (streaming)
        │       │       │       ├─ _iter_provider_stream_chunks
        │       │       │       ├─ _accept_chat_chunk (per delta)
        │       │       │       └─ _finish_chat_stream (completion/stub)
        │       │       └─ interruptible_api_call (non-streaming)
        │       ├─ check_api_response
        │       └─ handle_api_error / handle_api_interrupt
        │
        ├─ apply_retry_restarts (redirect / rebuilt / compressed / length_continuation)
        │
        ├─ normalize_model_response
        │
        ├─ run_tool_round (if tool_calls) OR finish_text_response
        │
        └─ handle_outer_loop_error
        │
        ▼
finalize_turn — persist, export turn boundary, handle compression_exhausted
```

**Key State Objects:**
- `_LoopState` (conversation_loop.py:1384) — all turn-scoped locals
- `TurnRetryState` (turn_retry_state.py) — per-attempt recovery guards
- `SessionState` (acp_adapter/session.py) — ACP session runtime state
- `StreamingWaitMonitor._mon` — stale detection state

### 2.3 Session Identity & Persistence

| Identifier | Source | Stability |
|------------|--------|-----------|
| `session_id` | UUID v7 (time-ordered), `agent.session_id` | Stable per chat, survives restart |
| `turn_id` | `f"{session_id}:turn:{turn_count}"` | Per-turn |
| `api_request_id` | `f"{turn_id}:api:{api_call_count}"` | Per API attempt |
| `parent_session_id` | Compression/branch lineage | Lineage tracking |
| `_usage_anchor` | Provider `usage` from last successful response | Prompt token anchor |

**Session Storage (SQLite):**
- `sessions` table: `id`, `parent_session_id`, `source`, `cwd`, `git_repo_root`, `message_count`, `archived`, `end_reason`, `model_config` (JSON), `system_prompt`, `tool_names` (pinned)
- `messages` table: `id`, `session_id`, `role`, `content` (text or JSON parts), `timestamp`, `tool_call_id`, `tool_calls` (JSON), `reasoning_content`, `display_kind`, `_meta` (compaction markers)
- WAL mode enabled, FTS5 for search
- `_DB_PERSISTED_MARKER` (object id) on dicts = already flushed to DB
- `adopt_unanswered_turn` re-stages tail user message on dispatcher re-run
- `export_current_turn_boundary` stamps `current_turn_user_idx` on result

---

## 3. Streaming Path — The Critical Path for Guardian

### 3.1 Model Request → Streaming Response

```
conversation_loop.py:_run_api_retry_loop
        │
        ▼
perform_api_call (agent/turn_api_call.py)
        │
        ▼
_StreamingCall._attempt_stream_response (agent/chat_completion_helpers.py)
        │
        ├─ _set_managed_stream(relay_llm.stream(...))
        │       ├─ _open_stream → provider-specific (OpenAI, Anthropic, Gemini, Codex)
        │       ├─ on_stream_created → captures httpx response, claims stream writer
        │       ├─ on_chunk → accumulator.observe (provider-specific)
        │       ├─ accept_chunk → _accept_chat_chunk (validates writer token)
        │       └─ defer_logical_completion=True
        │
        ▼
for chunk in _iter_provider_stream_chunks(stream):
        │
        ├─ _count_chunk (diagnostics)
        ├─ interrupt check
        ├─ _stream_attempt_is_active (writer token check)
        ├─ _discard_stale_stream_chunk (if superseded)
        ├─ delta processing:
        │       ├─ reasoning_content / reasoning → _emit_reasoning
        │       ├─ content (text) → _emit_text / pending buffer for SSE shim
        │       ├─ tool_calls delta → tool_calls.feed → _emit_tool_started
        │       └─ refusal delta
        │
        ▼
tool_calls.materialize()
_close_managed_stream()
        │
        ▼
_finish_chat_stream → assembles response OR returns partial stub:
        ├─ EmptyStreamError (no chunks, no finish_reason)
        ├─ _build_partial_stream_stub (clean EOF mid-tool, mid-text)
        ├─ provider_stream_error_from_text (error in stream body)
        └─ Normal completion → SimpleNamespace response
```

### 3.2 Chunk Processing Details (`_accept_chat_chunk`)

```python
def _accept_chat_chunk(self, stream_attempt_id: int, chunk) -> bool:
    # 1. Writer token check — superseded streams are fenced out
    if not self._stream_attempt_is_active(stream_attempt_id):
        self._discard_stale_stream_chunk(stream_attempt_id, chunk)
        return False
    
    # 2. Model/response_id/upstream_provider capture
    # 3. Choiceless chunks (usage, finish_reason only)
    # 4. Choice[0].delta processing:
    #    - finish_reason normalization
    #    - usage accumulation
    #    - reasoning_content / reasoning / model_extra.reasoning*
    #    - reasoning_details (structured)
    #    - refusal
    #    - content (text) → pending_text_parts or _emit_text
    #    - tool_calls delta → tool_calls.feed(name) → _emit_tool_started
    # 5. _flush_pending_stream_text on non-SSE, non-shim text
```

### 3.3 Stream Monitoring (`StreamingWaitMonitor._monitor_loop`)

```python
def _monitor_loop(self):
    _HEARTBEAT_INTERVAL = 30.0
    self._mon = SimpleNamespace(
        last_heartbeat=time.time(),
        last_load_poll=0.0,
        load_notice_shown=False,
        wait_notice=WaitNoticeState(),
    )
    _is_local_base = is_local_endpoint(base_url)
    
    while not self._call_done.is_set():
        self._call_done.wait(timeout=0.3)
        _hb_now = time.time()
        
        # Local model loading notice (poll ~1s after 2s no chunks)
        if _is_local_base and self._poll_local_load_notice(_hb_now):
            continue
        
        # Reasoning callback clears wait notice
        if self._mon.wait_notice_started_ts and self.last_chunk_time["t"] > self._mon.wait_notice_started_ts:
            self.agent._emit_wait_notice("")
            self._mon.wait_notice_started_ts = None
            self._mon.wait_notice.reset()
        
        # Gateway heartbeat (every 30s)
        if _hb_now - self._mon.last_heartbeat >= _HEARTBEAT_INTERVAL:
            self._mon.last_heartbeat = _hb_now
            self._heartbeat(int(_hb_now - self.last_chunk_time["t"]))
        
        # STALE DETECTION
        _stale_elapsed = time.time() - self.last_chunk_time["t"]
        if _stale_elapsed > self._stream_stale_timeout:
            self._mon.wait_notice_started_ts = None
            self._mon.wait_notice.reset()
            self._kill_stale_stream(_stale_elapsed)
        
        # Interrupt check
        if self.agent._interrupt_requested:
            self._abort_for_interrupt(_stale_elapsed)
            return
```

**Stale Timeout Configuration:**
- `get_provider_stale_timeout()` from `hermes_cli/timeouts.py`
- Default: 60s for remote, 300s for local (configurable)
- `_stream_stale_timeout` set per `_StreamingCall` instance

### 3.4 Stale Stream Kill (`_kill_stale_stream`)

```python
def _kill_stale_stream(self, stale_elapsed: float):
    logger.warning("Stream stale for %.0fs (timeout=%s) — killing stream attempt",
                   stale_elapsed, self._stream_stale_timeout)
    self.agent._emit_stream_drop(
        error=TimeoutError(f"Stream stalled for {stale_elapsed:.0f}s"),
        attempt=self.api_call_count + 1,
        max_attempts=self.max_retries + 1,
        mid_tool_call=self.provider_tool_in_flight["yes"],
        diag=self.clients.diag
    )
    # Force-close the httpx response to unblock the stream iterator
    if self._attempt_stream_response and hasattr(self._attempt_stream_response, "close"):
        try:
            self._attempt_stream_response.close()
        except Exception:
            pass
    self._cancel_current_stream_attempt("stale_stream_kill")
    # Stale monitor will retry via handle_stream_error path
```

### 3.5 Stream Error Handling (`_handle_stream_error`)

```python
def _handle_stream_error(self, e: Exception, attempt: int, max_retries: int) -> bool:
    # Classify: timeout, connection error, stream parse error, empty stream
    _is_transient = _is_timeout or _is_conn_err or _is_sse_conn_err or _is_stream_parse_err
    _is_empty_stream = isinstance(e, EmptyStreamError)
    
    if self.deltas_were_sent["yes"]:
        # PARTIAL DELIVERY — normally NO RETRY (would duplicate text)
        # EXCEPTION: tool call in flight + transient error → retry (abort discards tool)
        if not (partial_tool_in_flight and _is_transient and attempt < max_retries):
            return False  # Stop, propagate to main retry loop
        # Marker: "Connection dropped mid tool-call; reconnecting..."
        self._retry_after_drop(..., mid_tool_call=True)
        return True
    
    # NO DELIVERY YET — safe to retry transient errors
    if _is_transient or _is_empty_stream:
        if attempt < max_retries:
            self._retry_after_drop(..., mid_tool_call=False)
            return True
        # Exhausted: log diagnostics, maybe unmask with non-streaming probe
    else:
        self._maybe_disable_streaming(e)  # 5xx → non-streaming probe
    return False
```

### 3.6 Auto-Recovery Ladder (`turn_recovery_autorecover.py`)

```python
_LADDER_REASONS = {FailoverReason.overloaded, FailoverReason.server_error, FailoverReason.timeout}

def ladder_eligible(agent, classified) -> bool:
    if auto_recovery_cycles(agent) <= 0 or classified.reason not in _LADDER_REASONS:
        return False
    streamed = agent._current_streamed_assistant_text or ""
    return not agent._has_content_after_think_block(streamed)  # NO TEXT DELIVERED

def auto_recover_after_exhaustion(agent, api_error, classified, _retry, ...):
    if not ladder_eligible(agent, classified): return None
    total = auto_recovery_cycles(agent)  # default 5
    used = _retry.auto_recovery_cycles_used
    if used >= total: return None
    
    cycle = used + 1
    wait_s = ladder_wait_seconds(cycle, api_error)  # 15/30/60/60/60s + jitter
    notice = ladder_notice(agent, wait_s, cycle, total)
    agent._emit_diagnostic_status(notice)
    agent._emit_diagnostic_wait(notice)
    
    interrupted = interruptible_backoff_sleep(agent, wait_s, _retry, ...)
    if interrupted: return {"action": "return", "result": interrupted}
    if _retry.restart_with_redirected_messages: return {"action": "break"}
    return {"action": "continue"}  # Zero retry_count, re-enter retry loop
```

---

## 4. Tool Execution Path — Safety Critical

### 4.1 Tool Dispatch Flow

```
conversation_loop.py:run_tool_round
        │
        ▼
tool_executor.py:dispatch_tool_calls
        │
        ├─ tool_dispatch_helpers.py:make_tool_result_message
        │
        ├─ inline_tool_executors.py (built-in tools: read, write, shell, etc.)
        │
        ├─ MCP tools → mcp_tool_agent.py → MCP client
        │
        ├─ Browser tools → browser_provider.py
        │
        ├─ Shell commands → terminal_env_provider.py
        │
        └─ Sub-agent delegation → subagent_lifecycle.py
```

### 4.2 Tool Execution Tracking

| Tracking Point | Location | What It Captures |
|----------------|----------|------------------|
| `partial_tool_names` | `_StreamingCall.result` | Tool names from streaming `tool_calls` delta |
| `tool_calls_acc` | `_StreamingCall.tool_calls` | Accumulator for tool call args |
| `tool_progress_callback` | `agent.tool_progress_callback` | ACP/CLI tool start/progress/complete |
| `step_callback` | `agent.step_callback` | Per-step notifications |
| `_current_streamed_assistant_text` | `agent._streamed_assistant_text_parts` | All visible text this turn |

### 4.3 Safety Gates (When Replay Is Blocked)

```python
# In turn_retry_state.py + chat_completion_helpers.py:
# 1. Partial tool call in flight (args incomplete, no finish_reason)
has_truncated_tool_args = True → _build_partial_stream_stub (clean_eof=True)

# 2. Text delivered (deltas_were_sent["yes"] = True)
#    → _handle_stream_error returns False (no retry)
#    → Exception: tool call in flight + transient → retry with reconnect marker

# 3. Auto-recovery ladder eligibility:
def ladder_eligible(agent, classified):
    streamed = agent._current_streamed_assistant_text or ""
    return not agent._has_content_after_think_block(streamed)
    # _has_content_after_think_block strips <think> blocks, returns True if visible text remains
```

### 4.4 Side Effect Categories & Detectability

| Category | Examples | Detected By | Replay Safe? |
|----------|----------|-------------|--------------|
| File writes | `write`, `edit`, `patch` | `tool_progress_callback` (started) | ❌ |
| Shell commands | `shell`, `bash`, `command` | `tool_progress_callback` (started) | ❌ |
| MCP tools | Any MCP tool | `tool_progress_callback` (started) | ❌ |
| API calls | HTTP via tools | `tool_progress_callback` (started) | ❌ |
| Sub-agent spawn | `summon`, delegation | `subagent_lifecycle.py` | ❌ |
| Assistant text | Streaming chunks | `_current_streamed_assistant_text` | ❌ |

**Rule:** NEVER replay if `partial_tool_names` non-empty OR `_current_streamed_assistant_text` non-empty.

---

## 5. Session / Conversation State — Recovery Foundation

### 5.1 Turn Boundary & Resume

```python
# conversation_loop.py:export_current_turn_boundary
def export_current_turn_boundary(agent, result, user_message):
    # Stamps current_turn_user_idx on result
    # Called after EVERY history rewrite including micro-compaction
    # Gateway reads this to know where the turn started
```

### 5.2 Session Resume After Crash

```python
# session_persistence.py:adopt_unanswered_turn
def adopt_unanswered_turn(history, query, agent):
    # Finds tail user message matching query
    # Verifies nothing after it is plain assistant (only tool scaffolding)
    # Pops tail, marks _DB_PERSISTED_MARKER, sets _pending_cli_user_message
    # Dispatcher re-run reuses this as turn's user message — NO DUPLICATE USER ROW
```

### 5.3 Crash Scenarios & Recovery

| Scenario | Session State | Recovery Possible? | Guardian Role |
|----------|---------------|-------------------|---------------|
| Process crash mid-stream | SQLite has pre-turn state, in-flight turn lost | ✅ `adopt_unanswered_turn` re-stages user msg | Detect crash via process monitor, wait for restart |
| Stream stall (no chunks) | Partial turn in memory, SQLite at turn start | ✅ If no tools/text → cancel + replay | Detect via stale monitor, trigger cancel |
| Tool executing during crash | Tool may have side-effected | ❌ **UNSAFE** to replay | Detect tool_started, block replay |
| Backend (provider) 5xx | Retries at stream level, then turn level | ✅ If pre-tools | Observe retry exhaustion, wait |
| Rate limit (429) | Provider retry + turn retry + auto-recovery | ✅ If pre-tools | Observe, enforce backoff |

---

## 6. Provider Failure Behavior — What Hermes Already Handles

### 6.1 Error Classification (`error_classifier.py`)

| FailoverReason | HTTP Status | Retryable | Fallback | Notes |
|----------------|-------------|-----------|----------|-------|
| `rate_limit` | 429 | ✅ | ✅ | Respects `Retry-After`, Z.AI overload special handling |
| `overloaded` | 503/529 | ✅ | ✅ | "temporarily overloaded" — same key, backoff |
| `server_error` | 500/502 | ✅ | ✅ | Generic 5xx |
| `timeout` | Connect/Read | ✅ | ✅ | Rebuilds client |
| `auth` | 401/403 | ❌ (1 refresh) | ✅ | Rotates credential |
| `billing` | 402/400 | ❌ | ✅ | Verified vs unverified (Anthropic content filter) |
| `context_overflow` | 413/400 | N/A | N/A | Compression, not failover |
| `content_policy_blocked` | 400 | ❌ | ❌ | Deterministic per-request |
| `model_not_found` | 404 | ❌ | ✅ | Fallback model |
| `upstream_blocked` | 403 (WAF) | ❌ | ✅ | Key healthy, fallback |
| `format_error` | 400 | ❌ | ❌ | Malformed request |

### 6.2 Retry Behavior by Layer

| Layer | Config | Max Attempts | Backoff | Scope |
|-------|--------|--------------|---------|-------|
| Provider HTTP | `RetryConfig` | 3 | 1s×2^N, jitter 0.8-1.2 | Per HTTP request |
| Stream reconnect | `_retry_after_drop` | `max_retries` (3) | 1s×2^N, jitter 0 | Per stream attempt |
| Turn retry loop | `agent._api_max_retries` | 3 | Exponential + Retry-After | Per API call |
| Auto-recovery ladder | `agent.auto_recovery_cycles` | 5 | 15/30/60/60/60s | Post-exhaustion |
| Recipe verification | Recipe config | Configurable | N/A | Post-turn |

**Guardian should NOT duplicate** provider HTTP retry or stream reconnect — only add value at **turn level** (detecting stall before Hermes does, or when Hermes gives up).

---

## 7. Stream Stall Detection — Guardian's Primary Target

### 7.1 Hermes's Existing Detection

| Detector | Threshold | Action |
|----------|-----------|--------|
| `StreamingWaitMonitor._monitor_loop` | `_stream_stale_timeout` (60s remote, 300s local) | `_kill_stale_stream` → force-closes httpx response |
| `StreamingWaitMonitor._heartbeat` | 30s no chunks | Emits wait notice, touches gateway activity |
| `StreamingWaitMonitor._poll_local_load_notice` | 2s no chunks (local only) | Shows model loading progress |
| Provider HTTP deadline | 600s total (configurable) | `ProviderError.NetworkError("Request timed out")` |

### 7.2 What Hermes Misses (Guardian Opportunity)

| Gap | Guardian Can Add |
|-----|------------------|
| **First-chunk timeout** — no detection of "waiting for first chunk" separately from "stalled mid-stream" | Separate `first_chunk_timeout` (e.g., 30s) |
| **Tool execution stall** — tool progress callbacks exist but no timeout | Per-tool timeout (configurable) |
| **Provider-side partial stall** — chunks arrive but with long gaps (>10s) | Chunk gap threshold (e.g., 10s between chunks) |
| **Silent connection death** — TCP connection dies without RST/FIN | TCP keepalive monitoring |
| **Cross-session correlation** — multiple sessions hitting same provider | Provider health aggregation |

### 7.3 Stale Stream Semantics (Critical)

```
IDLE (no active turn)                    ≠ STALE
COMPLETED (stop_reason=end_turn)         ≠ STALE  
DISCONNECTED (WebSocket/HTTP closed)     ≠ STALE
ACTIVE STREAM + NO PROGRESS > THRESHOLD  = POSSIBLE STALE
TOOL_EXECUTING + NO PROGRESS > THRESHOLD = TOOL STALL (different handling)
```

**Guardian must understand Hermes's turn phase:**
- `STREAMING` (assistant chunks) → stale = provider/network issue
- `TOOL_ACTIVE` (tool running) → stall = tool timeout, different recovery
- `OBSERVING` (idle) → never stale

---

## 8. Available Hermes Interfaces for Guardian Integration

### 8.1 Internal (Python) — Recommended for Deep Integration

| Interface | Access | What It Provides |
|-----------|--------|------------------|
| `agent.stream_delta_callback` | Callable(set by CLI/TUI) | Every text delta |
| `agent.reasoning_callback` | Callable | Reasoning/thinking deltas |
| `agent.tool_progress_callback` | Callable | Tool start/progress/complete |
| `agent.step_callback` | Callable | Step notifications |
| `StreamingWaitMonitor` | Internal class | Stale detection, heartbeat |
| `conversation_loop` events | Via `_run_phase` returns | Turn start/end, tool, error |
| `TurnRetryState` | Per-attempt | Recovery guards, restart signals |
| SessionDB (`hermes_state_sessions.py`) | `agent._session_db` | Session list, messages, lineage |

### 8.2 External (ACP) — For IDE/Editor Integration

| Method | Purpose |
|--------|---------|
| `agent/session/new` | Create session |
| `agent/session/load` | Resume session |
| `agent/session/prompt` | Send user message |
| `agent/session/cancel` | Cancel in-flight |
| `agent/session/fork` | Fork at timestamp |
| `sessionUpdate` notifications | All turn events (text, tools, errors) |

**ACP Server:** Runs separately (`hermes proxy` or `acp_adapter/server.py`), not used by core agent.

### 8.3 Process-Level — For External Supervision

| Signal | Detection |
|--------|-----------|
| Process PID | `agent._session_db` connection, or CLI `ps` |
| Port | No fixed port (direct HTTPS to providers) |
| Health endpoint | None (but `hermes doctor` exists) |
| Logs | `~/.hermes/logs/` + `agent.log_prefix` structured logs |

---

## 9. Integration Strategy Comparison

| Strategy | Observability | Reliability | Stream Stall Detection | Tool Execution Detection | Recovery Safety | Maintenance | Coupling |
|----------|---------------|-------------|------------------------|--------------------------|-----------------|-------------|----------|
| **A) Internal Observer (callbacks)** | ⭐⭐⭐⭐⭐ Full delta stream | ⭐⭐⭐⭐ In-process | ⭐⭐⭐⭐⭐ Direct monitor access | ⭐⭐⭐⭐⭐ Tool callbacks | ⭐⭐⭐⭐ State machine integration | ⭐⭐⭐ Requires Hermes source | Medium |
| **B) ACP Client (external)** | ⭐⭐⭐⭐ Session updates only | ⭐⭐⭐⭐ Protocol-stable | ⭐⭐ Indirect (stop_reason) | ⭐⭐ ToolCallUpdate events | ⭐⭐⭐ Fork/cancel/load | ⭐⭐⭐⭐ Stable protocol | Low |
| **C) Process Wrapper** | ⭐⭐ Stdout/stderr only | ⭐⭐ Process-level | ⭐⭐ Log parsing | ⭐ Log parsing | ⭐⭐ Restart session | ⭐⭐⭐⭐ Zero code coupling | None |
| **D) Local Proxy** | ⭐⭐⭐ HTTP interception | ⭐⭐⭐ Proxy stability | ⭐⭐⭐⭐ Stream chunks visible | ⭐⭐ Request/response only | ⭐⭐ Replay via proxy | ⭐⭐ Proxy maintenance | Low |
| **E) SQLite Monitor** | ⭐⭐ Session state only | ⭐⭐⭐⭐ DB stable | ❌ No streaming visibility | ⭐ Tool calls in messages | ⭐⭐ Session load | ⭐⭐⭐ Read-only DB | None |

### 9.1 Recommended: Hybrid Internal + ACP

**Primary: Internal Observer (Python)**
- Hook `stream_delta_callback`, `reasoning_callback`, `tool_progress_callback`
- Subclass/extend `StreamingWaitMonitor` for custom stale thresholds
- Integrate with `TurnRetryState` for recovery decisions
- Access `SessionDB` for session identity and turn boundary

**Secondary: ACP Observer (for multi-IDE scenarios)**
- Connect to ACP WebSocket if user runs `hermes proxy` 
- Observe `sessionUpdate` notifications
- Use `agent/session/cancel` + `agent/session/fork` for safe recovery

**Why NOT process wrapper / proxy:**
- Hermes streams directly to providers (no local proxy by default)
- Process wrapper loses streaming visibility
- Proxy adds latency and failure modes

---

## 10. Proposed Hermes Adapter Architecture

### 10.1 Adapter Interface (Agent-Agnostic)

```python
class AgentAdapter(ABC):
    @abstractmethod
    async def connect(self) -> bool: ...
    
    @abstractmethod
    async def disconnect(self): ...
    
    @abstractmethod
    def on_stream_delta(self, callback: Callable[[str], None]): ...
    
    @abstractmethod
    def on_reasoning_delta(self, callback: Callable[[str], None]): ...
    
    @abstractmethod
    def on_tool_event(self, callback: Callable[[ToolEvent], None]): ...
    
    @abstractmethod
    def on_turn_event(self, callback: Callable[[TurnEvent], None]): ...
    
    @abstractmethod
    def on_failure(self, callback: Callable[[FailureEvent], None]): ...
    
    @abstractmethod
    async def cancel_turn(self, session_id: str) -> bool: ...
    
    @abstractmethod
    async def fork_session(self, session_id: str, before_timestamp: float) -> str: ...
    
    @abstractmethod
    async def load_session(self, session_id: str) -> bool: ...
    
    @property
    @abstractmethod
    def session_id(self) -> Optional[str]: ...
    
    @property
    @abstractmethod
    def turn_context(self) -> Optional[TurnContext]: ...
```

### 10.2 Hermes Adapter Implementation

```python
class HermesAdapter(AgentAdapter):
    def __init__(self, config: HermesAdapterConfig):
        self.agent = self._create_or_attach_agent(config)
        self._setup_callbacks()
    
    def _setup_callbacks(self):
        # Stream monitoring
        self.agent.stream_delta_callback = self._on_stream_delta
        self.agent.reasoning_callback = self._on_reasoning_delta
        self.agent.tool_progress_callback = self._on_tool_event
        
        # Turn lifecycle (via conversation_loop phase hooks)
        # Requires patching _run_phase or using a mixin
        
        # Stale detection (extend StreamingWaitMonitor)
        self._stale_monitor = CustomStreamingWaitMonitor(
            stale_threshold=config.stale_threshold,
            first_chunk_timeout=config.first_chunk_timeout,
            chunk_gap_threshold=config.chunk_gap_threshold,
            on_stale=self._on_stale_detected,
        )
    
    async def cancel_turn(self, session_id: str) -> bool:
        # Use agent's interrupt mechanism
        self.agent._interrupt_requested = True
        # Wait for _handle_stream_error to process
        await asyncio.sleep(0.5)
        return True
    
    async def fork_session(self, session_id: str, before_timestamp: float) -> str:
        # Via ACP: agent/session/fork with _meta.conversationBefore
        # Requires ACP connection or direct SessionDB manipulation
        pass
```

### 10.3 Safety Gate Integration

```python
class HermesSafetyGate(SafetyGate):
    def check_replay_safety(self, turn_context, failure_classification):
        # Hermes-specific checks
        if turn_context.partial_tool_names:
            return SafetyCheckResult(UNSAFE, "Tool calls dispatched", 
                                   blocking_factors=["partial_tool_names"])
        
        if turn_context.streamed_text_chars > 0:
            return SafetyCheckResult(UNSAFE, "Assistant text streamed",
                                   blocking_factors=["assistant_text_delivered"])
        
        # Check turn phase from Hermes state
        if turn_context.phase in (TurnPhase.TOOL_EXECUTING, TurnPhase.TURN_ENDING):
            return SafetyCheckResult(UNSAFE, f"Turn phase: {turn_context.phase}")
        
        return super().check_replay_safety(turn_context, failure_classification)
```

---

## 11. Windows Considerations

| Concern | Hermes Behavior | Guardian Mitigation |
|---------|-----------------|---------------------|
| Process spawning | `subprocess.Popen` for MCP servers, no model subprocess | Use `psutil` for process tree monitoring |
| Signals | No SIGTERM/SIGINT handling in agent (CLI handles) | Use `taskkill /PID` or `agent._interrupt_requested = True` |
| Config paths | `~/.hermes/` (expands to `C:\Users\<user>\.hermes`) | Use `os.path.expanduser`, `$HERMES_HOME` env |
| Session DB | SQLite WAL, file locking works on Windows | Read-only connections (`PRAGMA query_only=ON`) |
| Path separators | Mixed `/` and `\` in codebase | Use `pathlib.Path` everywhere |
| Python environment | Bundled in `venv/`, `hermes` entry point | Use same Python for Guardian |

---

## 12. Reusable Guardian Components (From Goose Work)

| Component | Status | Adaptation Needed |
|-----------|--------|-------------------|
| `core/types.py` | ✅ Complete | Add Hermes-specific `TurnPhase` values |
| `core/failure_classifier.py` | ✅ Complete | Map Hermes `FailoverReason` → Guardian `FailureType` |
| `core/safety_gate.py` | ✅ Complete | Add `HermesSafetyGate` subclass |
| `core/backoff.py` | ✅ Complete | Use Hermes `jittered_backoff` params |
| `core/state_machine.py` | ✅ Complete | Align states with Hermes turn phases |
| `core/config.py` | ✅ Complete | Add Hermes adapter config section |
| `goose/observer.py` | ✅ Complete | Replace ACP parser with Hermes callback hooks |
| `goose/protocol.py` | Goose-specific | **ISOLATE** — move to `goose/` only |
| `goose/discovery.py` | Goose-specific | **ISOLATE** — Goose port scanning |
| `goose/adapter.py` | Goose-specific | **REWRITE** for Hermes callbacks |

### 12.1 Failure Type Mapping

| Hermes `FailoverReason` | Guardian `FailureType` |
|-------------------------|------------------------|
| `rate_limit` | `RATE_LIMIT` |
| `overloaded` | `TRANSIENT_PROVIDER` |
| `server_error` | `SERVER_ERROR` |
| `timeout` | `TIMEOUT` |
| `auth` | `AUTHENTICATION_FAILURE` |
| `billing` | `AUTHENTICATION_FAILURE` (credential) |
| `context_overflow` | `STREAM_TRUNCATED` (compression handles) |
| `content_policy_blocked` | `PERMISSION_FAILURE` |
| `model_not_found` | `INVALID_MODEL` |
| `upstream_blocked` | `NETWORK_TRANSIENT` |
| `format_error` | `INVALID_CONFIGURATION` |
| `unknown` | `UNKNOWN_FAILURE` |

---

## 13. Goose-Specific Components To Isolate

Move to `guardian/goose/` only (do not import in core):

```
guardian/goose/
├── __init__.py
├── discovery.py      # Port scanning for /status
├── observer.py       # ACP WebSocket observer
├── protocol.py       # ACP frame parsing
└── adapter.py        # GooseAdapter (ACP → Guardian core)
```

**Core must have ZERO Goose imports.** Verify with:
```bash
grep -r "from guardian.goose" guardian/core/  # Should return nothing
```

---

## 14. Unknowns Requiring Live Testing

| Unknown | Test Required |
|---------|---------------|
| Exact `stream_delta_callback` timing vs tool callbacks | Run Hermes with tool call, log callback order |
| Does `adopt_unanswered_turn` work for crashed mid-tool turns? | Kill process during tool, restart, observe |
| Can `fork_session` be called via internal API (not ACP)? | Check `SessionManager` for fork method |
| Stream stale timeout default for each provider | Check `get_provider_stale_timeout` |
| Does `auto_recovery_cycles` work with streaming? | Simulate 5xx during stream, observe ladder |
| Tool progress callback for MCP tools | Execute MCP tool, verify callback fires |
| SessionDB concurrent read during write | Read `state.db` while Hermes writes (WAL mode) |
| Turn boundary `current_turn_user_idx` accuracy | Multi-turn session, verify index matches |

---

## 15. Proposed Testing Strategy

### Level 1 — Unit Tests (Existing + New)
- Failure classification mapping (Hermes → Guardian)
- Safety gate decisions with Hermes turn context
- Backoff strategy with Hermes parameters
- State machine transitions with Hermes events

### Level 2 — Fault Injection (Mock Hermes)
```python
class MockHermesAgent:
    def __init__(self):
        self.stream_delta_callback = None
        self.tool_progress_callback = None
        self._interrupt_requested = False
    
    async def simulate_stream(self, chunks, delay=0.1, stall_at=None):
        for i, chunk in enumerate(chunks):
            if stall_at and i == stall_at:
                await asyncio.sleep(100)  # Simulate stall
            self.stream_delta_callback(chunk)
            await asyncio.sleep(delay)
```

Inject: 429, 5xx, timeout, connection reset, stall, tool call mid-stream, auth failure.

### Level 3 — Real Hermes Validation
1. Start Hermes CLI/TUI
2. Run Guardian observer (callback hooks)
3. Send prompts triggering: normal, tool, rate limit (mock), stall (slow provider)
4. Verify: detection accuracy, safety gate decisions, no duplicate side effects

---

## 16. Recommended Next Implementation Step

> **Build the Hermes Read-Only Observer with Callback Hooks**

**Concrete Steps:**

1. **Create `guardian/hermes/__init__.py`** — exports
2. **Create `guardian/hermes/observer.py`** — `HermesObserver` class that:
   - Attaches to running Hermes agent (or spawns one for testing)
   - Hooks `stream_delta_callback`, `reasoning_callback`, `tool_progress_callback`
   - Tracks `TurnContext` (session_id, phase, assistant_chunks, tool_calls)
   - Integrates `StreamingWaitMonitor` with custom thresholds
   - Emits `GuardianEvent` (START, CHUNK, TOOL_START, TOOL_COMPLETE, STALE, ERROR, TURN_END)
3. **Create `guardian/hermes/adapter.py`** — `HermesAdapter` implementing `AgentAdapter`:
   - `connect()` — find/attach to Hermes agent
   - `cancel_turn()` — set `_interrupt_requested`
   - `fork_session()` — via ACP or SessionDB
   - `load_session()` — via SessionDB
4. **Create `guardian/hermes/failure_mapper.py`** — map `FailoverReason` → `FailureType`
5. **Create `guardian/hermes/safety_gate.py`** — `HermesSafetyGate` subclass
6. **Add tests** — `tests/test_hermes_observer.py`, `tests/test_hermes_safety.py`

**Success Criteria:**
- Observer runs alongside Hermes without affecting it
- Correctly identifies: streaming, tool execution, turn completion, stale stream
- `is_safe_to_replay()` accurate in all observed cases
- No secrets logged
- All 138 existing tests still pass

---

## 17. Files to Create/Modify

### New Files
```
docs/HERMES_RESEARCH.md                    ← This document
guardian/hermes/__init__.py
guardian/hermes/observer.py
guardian/hermes/adapter.py
guardian/hermes/failure_mapper.py
guardian/hermes/safety_gate.py
tests/test_hermes_observer.py
tests/test_hermes_safety.py
```

### Modified Files
```
guardian/core/__init__.py                  # Export Hermes adapter types
guardian/cli.py                            # Add `guardian observe hermes` command
README.md                                  # Update with Hermes support
```

### Isolated (Goose-Only)
```
guardian/goose/                            # Keep existing, no core imports
```

---

*End of Research Document — Ready for Implementation Phase*