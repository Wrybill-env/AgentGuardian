# Agent Guardian — Goose Desktop Research

**Project:** Agent Guardian (external Python watchdog/supervisor for Goose Desktop on Windows)
**Research Date:** 2026-10-02
**Goose Version:** 1.52.0 (Desktop), CLI source at `C:\Projects\AgentGuardian\goose-cli-source`
**Local Goose Desktop Install:** `C:\Users\Mitansh Kulshreshtha\Downloads\Goose-win32-x64 (1)\dist-windows`
**Extracted App ASAR:** `C:\Projects\AgentGuardian\goose_extracted`

---

## 1. Executive Summary

This document captures a complete architectural investigation of Goose Desktop (v1.52.0) to inform the design of **Agent Guardian** — an external Python watchdog that detects transient failures (rate limits, network errors, stream stalls, backend crashes) and recovers safely **without duplicating side effects** (tool calls, file writes, API calls).

**Key Architectural Findings:**

| Aspect | Finding |
|--------|---------|
| **Desktop Frontend** | Electron + React (Vite), runs in `main.ts` |
| **Backend** | Separate `goose.exe` (Rust CLI) spawned as `goose serve` subprocess |
| **Communication** | ACP v1 over WebSocket (`ws://127.0.0.1:<port>/acp?token=<secret>`) |
| **Protocol** | ACP (Agent Client Protocol) v1 — NOT ACP+ |
| **Session Storage** | SQLite (`sessions.db`) in Goose data dir, managed by `SessionManager` |
| **Retry Logic** | Provider-level only (exponential backoff, max 3, jitter, respects `Retry-After`) |
| **Streaming** | SSE-style chunked HTTP responses via `reqwest`, surfaced as ACP `SessionUpdate` notifications |
| **Crash Recovery** | `ActiveRunDropGuard` cleans up orphaned runs on WebSocket close; `goose serve` auto-restarted by Desktop on new window |

**Critical Safety Constraint:** Goose **does not** have session-aware recovery. Retries happen at the **provider HTTP request level** (before/during streaming), not at the session/prompt level. Once a tool call is dispatched via ACP `ToolCallUpdate`, it is executed by the backend — resending the prompt **will re-execute tools**.

**Guardian Integration Point:** The safest observation point is the **ACP WebSocket connection** (port + token from `gooseServeLeaseRegistry`). A read-only WebSocket proxy can observe all ACP traffic without interference.

---

## 2. Goose Desktop Architecture

### 2.1 Process Topology

```
┌─────────────────────────────────────────────────────────────┐
│ Goose Desktop (Electron)                                     │
│  ├─ Main Process (main.ts)                                   │
│  │   ├─ GooseServeLeaseRegistry (manages goose serve leases) │
│  │   ├─ Spawns goose.exe serve subprocess per window        │
│  │   ├─ Allocates random localhost port per lease            │
│  │   ├─ Generates secret key per lease                       │
│  │   └─ Health-checks /status endpoint before handing to UI │
│  └─ Renderer Process (React UI)                              │
│      ├─ acpConnection.ts (WebSocket client to backend)      │
│      ├─ chatSessionController.ts (prompt/submit/cancel)     │
│      └─ chatSessionStore.ts (local session state cache)     │
└─────────────────────────────────────────────────────────────┘
                              │
                    ACP v1 over WebSocket
                    ws://127.0.0.1:<port>/acp?token=<secret>
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ goose.exe serve (Rust CLI backend)                           │
│  ├─ ACP HTTP/WebSocket server (axum + agent_client_protocol)│
│  ├─ Agent (agent.rs) — state machine, tool execution        │
│  ├─ SessionManager (session_manager.rs) — SQLite persistence │
│  ├─ Provider abstractions (goose-providers crate)            │
│  └─ Extension/MCP tool execution                             │
└─────────────────────────────────────────────────────────────┘
```

**Sources:**
- `ui/desktop/src/main.ts` — Electron main, lease registry, window management (VERIFIED FROM SOURCE)
- `ui/desktop/src/gooseServe.ts` — Spawns `goose serve`, finds port, waits for `/status` (VERIFIED FROM SOURCE)
- `ui/desktop/src/gooseServeLeaseRegistry.ts` — Lease lifecycle, cleanup on window close (VERIFIED FROM SOURCE)
- `crates/goose/src/main.rs` (implied) — CLI entry, `serve` subcommand starts ACP server

### 2.2 Electron Main ↔ Renderer Communication

- **IPC Channels** (from `preload.ts` exposed via `contextBridge`):
  - `get-acp-url` → returns `ws://127.0.0.1:<port>/acp?token=<secret>`
  - `get-secret-key` → returns the lease secret
  - `create-chat-window` → spawns new window + lease
  - `logInfo`, `notify`, `reload-app`, `restart-app`
- **No direct stdio/IPC to backend** — all agent communication goes through ACP WebSocket

**Source:** `ui/desktop/src/preload.ts`, `ui/desktop/src/main.ts` (VERIFIED FROM SOURCE)

### 2.3 Backend Process (`goose serve`)

- **Command:** `goose.exe serve --platform desktop --enable-scheduler --host 127.0.0.1 --port <port>`
- **Env:** `GOOSE_SERVER__SECRET_KEY=<secret>` (validated by `auth::check_acp_token`)
- **TLS:** Optional (`--tls` flag), emits `GOOSED_CERT_FINGERPRINT=<sha256>` on stdout
- **Health Endpoints:** `GET /health`, `GET /status` (plain HTTP, no auth)
- **ACP Endpoint:** `POST/GET/DELETE /acp` + WebSocket upgrade at `/acp`

**Source:** `ui/desktop/src/gooseServe.ts` lines 200-250, `crates/goose/src/acp/transport/mod.rs` (VERIFIED FROM SOURCE)

---

## 3. ACP / ACP+ Details

### 3.1 Protocol Version

- **ACP v1** (ProtocolVersion = 1)
- **NOT ACP+** — no evidence of ACP+ extensions in codebase
- Uses `@agentclientprotocol/sdk` v1.5.0 (from `package.json`)

**Source:** `ui/desktop/src/acp/acpConnection.ts` line 18: `ACP_V1_PROTOCOL_VERSION: 1 = PROTOCOL_VERSION` (VERIFIED FROM SOURCE)

### 3.2 ACP Methods Used (Client → Server)

| Method | Purpose | Source |
|--------|---------|--------|
| `agent/session/new` | Create session | `sessions.ts: acpNewSession` |
| `agent/session/load` | Resume session | `sessions.ts: loadAcpSession` |
| `agent/session/prompt` | Send user message | `prompt.ts: acpPromptSession` |
| `agent/session/cancel` | Cancel in-flight prompt | `prompt.ts: acpCancelPrompt` |
| `agent/session/fork` | Fork at timestamp | `sessions.ts: acpForkSession` |
| `agent/session/delete` | Delete session | `sessions.ts: acpDeleteSession` |
| `agent/session/close` | Close session | `sessions.ts: acpCloseSession` |
| `agent/session/list` | List sessions | `sessions.ts: acpListSessions` |
| `goose/sessionSteer_unstable` | Steer/resume with message | `prompt.ts: acpSteerSession` |
| `goose/sessionInfo_unstable` | Get session metadata | `sessions.ts` multiple |

### 3.3 ACP Notifications (Server → Client)

Handled in `acpConnection.ts:createClientCallbacks()`:
- `sessionUpdate` → `handleAcpSessionNotification`
- `unstable_sessionUpdate` → `handleAcpGooseSessionNotification` (Goose-specific)
- `unstable_providerDeviceCode` → `handleAcpProviderDeviceCodeNotification`
- `elicitation` → `requestAcpElicitation`
- `permission` → `requestAcpPermission`
- `unstable_recipeParameterRequest` → `requestAcpRecipeParams`

### 3.4 Key Message/Event Types

**SessionUpdate** (from `agent_client_protocol::schema::v1`):
- `SessionInfoUpdate` — title, cwd, metadata changes
- `ConfigOptionUpdate` — model/provider/thinking_effort changes
- `CurrentModeUpdate` — GooseMode changes
- `UsageUpdate` — token usage
- `ToolCallUpdate` — tool call start/progress/result
- `MessageUpdate` — assistant message chunks

**ToolCallUpdate** fields (from `ops_toolcalling.rs`, `tool_calls/conversion.rs`):
- `toolCallId`, `title`, `kind` (execute/read/write/other)
- `status` (pending/executing/completed/failed)
- `input`, `output`, `error`
- `tool_call_meta` with `external_dispatch=true` + `goose.acp.kind`

**Source:** `crates/goose/src/acp/server.rs`, `crates/goose/src/acp/response_builder.rs`, `crates/goose/src/agents/state_machine/ops_toolcalling.rs` (VERIFIED FROM SOURCE)

---

## 4. Transport

### 4.1 WebSocket Connection

```
Renderer (React)                          goose serve (Rust)
     │                                          │
     ├─ createWebSocketStream(url, {protocols: []})
     │  url = ws://127.0.0.1:<port>/acp?token=<secret>
     │  (from window.electron.getAcpUrl())
     ▼                                          │
┌─────────────────────────────────────────────────────────────┐
│ ACP SDK Connection (ByteStreams over WebSocket)             │
│  - Frames: JSON-RPC 2.0 style request/response/notification │
│  - Secret validated by auth middleware (x-secret-key header)│
└─────────────────────────────────────────────────────────────┘
```

**Source:** `ui/desktop/src/acp/acpConnection.ts:openConnection()` (VERIFIED FROM SOURCE)

### 4.2 Connection Lifecycle

1. **Initialize:** Client sends `agent/initialize` with capabilities
2. **Ready:** `InitializeResponse` returns agent capabilities, session config options
3. **Health Checks:** Desktop polls `GET /status` every 100ms for 30s at startup
4. **Reconnection:** `acpConnection.ts:recoverConnection()` — exponential backoff (500ms base, 30s max, jitter)
5. **Cleanup:** On WebSocket close → `recoverConnection(false)`; lease cleanup when last window detaches

**Source:** `ui/desktop/src/acp/acpConnection.ts` lines 80-160 (VERIFIED FROM SOURCE)

### 4.3 External Observation Options

| Method | Feasibility | Notes |
|--------|-------------|-------|
| **WebSocket proxy** | ✅ Best | Intercept `ws://127.0.0.1:<port>/acp?token=...` — observe all ACP frames |
| **HTTP /status polling** | ✅ Easy | Public endpoint, returns `ok` — detects backend liveness |
| **stdin/stdout of goose.exe** | ❌ Not accessible | Spawned by Electron with `stdio: ['ignore','pipe','pipe']` |
| **SQLite sessions.db** | ✅ Read-only | File-based, can monitor session state changes |
| **Electron IPC** | ⚠️ Complex | Requires Electron context, not external-friendly |

**Recommended:** WebSocket proxy on the known port + token (read from lease registry or discovered via `/status`)

---

## 5. Session Lifecycle

### 5.1 Session Creation

```
User clicks "New Chat"
      │
      ▼
Renderer → acpNewSession(cwd, extensions[], recipe?)
      │
      ▼
ACP: agent/session/new { cwd, mcpServers: [], _meta: {client, enabledExtensions?, recipeId?} }
      │
      ▼
Backend: SessionManager.create_session() → INSERT INTO sessions + messages
      │
      ▼
Response: { sessionId, _meta: {extensionResults, workingDir, recipe?, userRecipeValues?} }
      │
      ▼
Renderer loads session via acpLoadSession(sessionId) → agent/session/load
```

**Source:** `ui/desktop/src/acp/sessions.ts: acpNewSession`, `crates/goose/src/session/session_manager.rs: create_session` (VERIFIED FROM SOURCE)

### 5.2 Session Identity

- **Session ID:** UUID v7 (time-ordered), string format: `"sess_<uuid>"` or similar
- **Stored in:** SQLite `sessions` table (`id` TEXT PRIMARY KEY)
- **Visible via ACP:** `ListSessionsResponse.sessionId`, `LoadSessionResponse.sessionId`

### 5.3 Session Resumption

- `acpLoadSession(sessionId)` → calls `agent/session/load {sessionId, cwd, mcpServers: []}`
- Backend: `SessionManager.get_session(id, true)` → loads full `Conversation` from `messages` table
- Provider session resumed via `provider.resume(provider_session_id)` if available

**Source:** `crates/goose/src/session/session_manager.rs: get_session`, `crates/goose/src/acp/server.rs: load_session` (VERIFIED FROM SOURCE)

### 5.4 On Backend Crash / Disconnect

- **WebSocket close** → `acpConnection.ts:handleClose()` → `recoverConnection(false)`
- **Lease cleanup:** `GooseServeLeaseRegistry.releaseWindow(windowId)` → if last window, `cleanupLease()` kills `goose serve` process
- **New window** → fresh `goose serve` spawn → new port, new secret, new ACP connection
- **Session data:** Persisted in SQLite — survives backend restart
- **In-flight prompt:** Lost — `ActiveRunDropGuard` discards pending steers, but **no automatic prompt replay**

**Source:** `ui/desktop/src/acp/acpConnection.ts`, `ui/desktop/src/gooseServeLeaseRegistry.ts`, `crates/goose/src/execution/ActiveRunRegistry.rs` (VERIFIED FROM SOURCE)

---

## 6. Streaming / Event Lifecycle

### 6.1 Model Output Streaming

```
Provider.stream() → MessageStream (BoxStream<Result<(Message, Option<Usage>)>>)
      │
      ├─ Yields (Some(assistant_message_chunk), usage?) for each chunk
      ├─ Yields (None, usage) for final usage
      └─ Ends on completion or error
```

**Source:** `crates/goose-provider-types/src/base.rs: MessageStream`, `crates/goose/src/agents/state_machine/ops_llm.rs` (VERIFIED FROM SOURCE)

### 6.2 ACP Event Mapping (Backend → Client)

| Provider Stream Event | ACP Notification |
|----------------------|------------------|
| Assistant text chunk | `SessionUpdate::MessageUpdate` (text content) |
| Tool call start | `SessionUpdate::ToolCallUpdate` (status=pending/executing) |
| Tool call result | `SessionUpdate::ToolCallUpdate` (status=completed/failed, output/error) |
| Usage update | `SessionUpdate::UsageUpdate` |
| Turn end | `SessionUpdate::StopReason` (EndTurn/MaxTurns/Error) |

**Source:** `crates/goose/src/acp/server.rs: stream_response_from_provider` (VERIFIED FROM SOURCE)

### 6.3 Completion Signals

| Signal | ACP Representation | Reliability |
|--------|-------------------|-------------|
| Successful completion | `StopReason::EndTurn` + final `ToolCallUpdate` completed | High |
| Max turns | `StopReason::MaxTurns` | High |
| Provider error (429, 5xx) | `StopReason::Error` + `SessionUpdate::MessageUpdate` with error | High |
| Stream stall (no chunks) | **No explicit signal** — timeout at HTTP layer | Low |
| Backend crash | WebSocket close + `goose serve` process exit | High (process exit) |
| User cancellation | `agent/session/cancel` notification sent | High |

**Source:** `crates/goose/src/agents/state_machine/ops_llm.rs`, `crates/goose/src/agents/reply_parts.rs: stream_response_from_provider` (VERIFIED FROM SOURCE + INFERRED for stall)

### 6.4 Cancellation

- **Client → Server:** `acpCancelPrompt(sessionId)` → `agent/session/cancel` notification
- **Server handling:** `CancellationToken` propagated to `run_goose` state machine
- **Effect:** Aborts current turn, discards partial tool calls, preserves conversation history
- **No "cancel and replay"** — user must resend manually

**Source:** `ui/desktop/src/acp/prompt.ts: acpCancelPrompt`, `crates/goose/src/agents/state_machine/mod.rs` (VERIFIED FROM SOURCE)

---

## 7. Existing Retry Behavior

### 7.1 Provider-Level Retry (goose-provider-types/src/retry.rs)

```rust
RetryConfig {
    max_retries: 3,
    initial_interval_ms: 1000,
    backoff_multiplier: 2.0,
    max_interval_ms: 30_000,
    transient_only: false,  // retries 4xx too unless marked permanent
}
```

**Retryable Errors:**
- `RateLimitExceeded` (429) — **uses `Retry-After` header or body `retry_after_seconds`**
- `ServerError` (5xx)
- `NetworkError` (connection reset, timeout, DNS failure)
- `RequestFailed` (4xx) — **unless marked permanent** (e.g., thinking block signature errors)

**Non-Retryable:**
- `Authentication` (401/403) — **but ONE credential refresh attempted**
- `ContextLengthExceeded`
- `CreditsExhausted`
- Permanent `RequestFailed` markers (thinking block immutability)

**Jitter:** `delay * (0.8 + random(0.4))` — prevents thundering herd

**Env Override:** `GOOSE_PROVIDER_SKIP_BACKOFF=true` skips delay (for testing)

**Source:** `crates/goose-provider-types/src/retry.rs` (VERIFIED FROM SOURCE)

### 7.2 Recipe-Level Retry (crates/goose/src/agents/retry.rs)

- **Separate system** for recipe/goal verification (not provider errors)
- Runs **after turn ends** if `session.recipe.retry` configured
- Executes shell `checks` commands to verify success
- On failure: runs `on_failure` command, **resets conversation to kickoff**, increments attempt counter
- Max retries from recipe config

**Source:** `crates/goose/src/agents/retry.rs`, `crates/goose/src/agents/state_machine/ops_retry.rs` (VERIFIED FROM SOURCE)

### 7.3 HTTP Client Timeouts (crates/goose-providers/src/api_client.rs)

- `DEFAULT_PROVIDER_TIMEOUT_SECS: 600` (10 min) — total request deadline
- `DEFAULT_CONNECT_TIMEOUT_SECS: 30` — connection phase
- **Streaming requests:** Deadline applies to **entire stream** (headers + body)
- **Stall detection:** If no chunk for > deadline → `ProviderError::NetworkError("Request timed out")`

**Source:** `crates/goose-providers/src/api_client.rs`, `crates/goose-providers/src/http_status.rs: send_bounded` (VERIFIED FROM SOURCE)

### 7.4 What Retries Do NOT Cover

| Scenario | Covered? |
|----------|----------|
| Mid-stream 429/5xx after chunks sent | ❌ — stream already yielded chunks |
| Stalled stream (no chunks for minutes) | ❌ — only total deadline, no idle timeout |
| Backend process crash mid-turn | ❌ — no session-level recovery |
| Tool call already executed, then provider fails | ❌ — tool side effect already happened |
| WebSocket disconnect during streaming | ❌ — reconnection creates new ACP connection, prompt lost |

---

## 8. Failure Modes Analysis

| Failure Mode | Detection Point | Current Goose Behavior | Guardian Opportunity |
|--------------|-----------------|------------------------|----------------------|
| **HTTP 429 (Rate Limit)** | Provider HTTP response | Retries with `Retry-After` (max 3×), then `RateLimitExceeded` → ACP Error | Can detect via ACP `StopReason::Error` + message; wait + replay **only if no tools executed** |
| **HTTP 408 (Request Timeout)** | Provider HTTP client | `NetworkError` → retry (max 3×) | Same as 429 |
| **HTTP 5xx** | Provider HTTP response | `ServerError` → retry (max 3×) | Same |
| **Connection Reset** | TCP layer / reqwest | `NetworkError` → retry (max 3×) | Same |
| **DNS / Network Failure** | reqwest connect | `NetworkError` → retry (max 3×) | Same |
| **Total Timeout (600s)** | `send_bounded` deadline | `NetworkError("Request timed out")` → retry | Guardian could enforce shorter *idle* timeout |
| **Mid-Stream Failure** | Stream yields error | `StopReason::Error` + error message | **Dangerous** — tools may have executed |
| **Stalled Stream (no chunks)** | **No detection** — only total deadline | Hangs until 600s total | **Prime Guardian target** — enforce idle timeout (e.g., 30s no chunk) |
| **Provider Unreachable** | Connect fails | `NetworkError` → retry | Same as network failure |
| **goose serve Crash** | WebSocket close + process exit | Lease cleanup, new backend on next window | **Safe** — session in SQLite, no tools executed in crashed turn (aborted) |
| **WebSocket Disconnect** | `connection.closed` promise | `recoverConnection(false)` → backoff reconnect | Guardian can monitor connection state |

**Critical Distinction:** Goose retries happen **before/during provider streaming**. Once the first `ToolCallUpdate` is sent via ACP, the tool is **already executing** in the backend. A retry at the session level would **re-execute tools**.

---

## 9. Safe Recovery Opportunities

These are mechanisms Guardian can use **without duplicating side effects**:

### 9.1 Pre-Execution Recovery (Safe)

| Opportunity | Mechanism | Safety |
|-------------|-----------|--------|
| **Provider retry exhausted, no tool calls yet** | Resend prompt via `agent/session/prompt` | ✅ Safe — conversation unchanged, no tools dispatched |
| **Stalled stream detected (idle timeout)** | Cancel via `agent/session/cancel`, then resend | ✅ Safe if **no ToolCallUpdate received yet** |
| **Backend crash before first tool** | New `goose serve` + reload session + resend | ✅ Safe — SQLite has pre-turn state |
| **429/5xx on initial request (pre-stream)** | Provider retry handles; if exhausted, Guardian waits + resends | ✅ Safe |

### 9.2 Session-Aware State Checks (Before Recovery)

Guardian **must verify** before any replay:

```python
def can_safely_replay(session_id, acp_connection):
    # 1. Load session conversation from backend
    session_info = acp_load_session(session_id)
    conversation = session_info.response.conversation
    
    # 2. Check last user message has NO assistant response yet
    last_user_idx = max(i for i, m in enumerate(conversation) if m.role == "user")
    has_assistant_after = any(
        m.role == "assistant" for m in conversation[last_user_idx+1:]
    )
    if has_assistant_after:
        return False  # Assistant already responded
    
    # 3. Check for ANY tool calls after last user message
    for msg in conversation[last_user_idx+1:]:
        for content in msg.content:
            if content.type == "tool_request" or content.type == "tool_response":
                return False  # Tools already executed
    
    return True
```

### 9.3 Fork-Based Recovery (Safe Alternative)

- Use `agent/session/fork {sessionId, conversationBefore: timestamp}` to create a **new session** at a safe point
- Replay prompt in forked session — original session preserved
- User chooses which branch to continue

**Source:** `crates/goose/src/acp/server.rs: fork_session`, `ui/desktop/src/acp/sessions.ts: acpForkSession` (VERIFIED FROM SOURCE)

---

## 10. Dangerous Recovery Scenarios

**AUTOMATIC RESEND WOULD CAUSE DUPLICATE SIDE EFFECTS IN THESE CASES:**

| Scenario | Why Dangerous |
|----------|---------------|
| **Tool call already dispatched** (`ToolCallUpdate` with status=executing/completed) | Tool executes in backend (shell, file write, API call). Resending prompt → new tool call with same args. |
| **Assistant message partially streamed** (text chunks received) | User sees duplicate text; if tool calls embedded, they re-execute. |
| **MCP tool with side effects** (write file, run command, API call) | MCP servers are external — no idempotency guarantee. |
| **Sub-agent / delegation** | Delegated work already done; replay creates duplicate sub-agent run. |
| **Recipe with `on_failure` command** | `on_failure` already ran (cleanup, notification); replay runs it again. |
| **Provider streaming error AFTER first chunk** | Conversation already has partial assistant message; retry creates forked history. |

**Guardian Rule:** **NEVER auto-replay if any `ToolCallUpdate` or assistant message chunk was observed for the current turn.**

---

## 11. Proposed Guardian Integration Point

### 11.1 Primary: ACP WebSocket Proxy (Read-Only Observer)

```
Goose Desktop Renderer          Guardian Proxy          goose serve Backend
     │                              │                        │
     ├─ WebSocket ──────────────────┤                        │
     │    (ws://127.0.0.1:P/acp     │                        │
     │     ?token=T)                │                        │
     │                              ├─ Mirror frames ───────►│
     │                              │                        │
     │                              │◄─ Mirror frames ───────┤
     │                              │                        │
     │                              │  [OBSERVES ALL ACP]    │
     │                              │   - SessionUpdates     │
     │                              │   - ToolCallUpdates    │
     │                              │   - StopReason         │
     │                              │   - UsageUpdates       │
```

**How to Discover Port + Token:**
1. **Option A:** Read `GooseServeLeaseRegistry` from Electron (requires Electron context)
2. **Option B:** Scan localhost ports for `/status` returning `ok` + extract token from WebSocket upgrade
3. **Option C:** Guardian launches its own `goose serve` (separate lease) — but loses existing sessions

**Recommended:** Option B — port scan 127.0.0.1:10000-65535 for `/status` → `ok`, then connect WebSocket with token discovery (token is in URL query param).

### 11.2 Secondary: SQLite Session Monitor (Read-Only)

- Watch `sessions.db` for `UPDATE sessions SET updated_at=...` + `INSERT INTO messages`
- Detects session state changes even if WebSocket proxy disconnected
- File path: `<GooseDataDir>/sessions/sessions.db`

**Source:** `crates/goose/src/config/paths.rs: Paths::data_dir()`, `crates/goose/src/session/session_manager.rs` (VERIFIED FROM SOURCE)

### 11.3 Process Monitor

- Track `goose.exe` PID (from `GooseServeResult.process.pid`)
- Detect crash via process exit + WebSocket close correlation

---

## 12. Unknowns / Things That Must Be Tested

| Unknown | Test Required |
|---------|---------------|
| **Exact ACP frame format** for `ToolCallUpdate` during streaming | Capture real WebSocket traffic during tool execution |
| **Idle timeout behavior** — does provider stream ever stall indefinitely? | Simulate slow provider (mock server with chunk delays) |
| **WebSocket reconnection** — does `agent/session/load` restore in-flight prompt state? | Kill `goose serve` mid-prompt, observe reconnection |
| **Session fork** — does `fork` copy provider session ID? | Fork session, check if provider.resume works |
| **Multiple windows sharing lease** — can they race on ACP? | Open 2 windows on same lease, send concurrent prompts |
| **TLS certificate fingerprint** — can Guardian verify without browser trust? | Test `goose serve --tls` fingerprint validation |
| **ACP `cancel` notification** — does it guarantee tool abortion? | Start long tool, send cancel, verify no side effect |
| **Exact `StopReason` values** for each failure mode | Enumerate all `StopReason` enum variants in ACP SDK |
| **Token expiry/rotation** — does secret change on lease renewal? | Observe lease over time |
| **SQLite WAL mode** — safe concurrent read during writes? | Read `sessions.db` while Goose writes |

---

## 13. Recommended MVP Architecture (Read-Only Observer)

### 13.1 Components

```
Agent Guardian MVP (Python)
├── discovery.py
│   ├── scan_localhost_ports() → find /status endpoint
│   ├── extract_acp_url() → parse token from WebSocket upgrade
│   └── watch_lease_changes() → re-scan on port change
├── websocket_observer.py
│   ├── connect_readonly(acp_url) → WebSocket client (no frames sent)
│   ├── parse_acp_frames() → JSON-RPC 2.0 request/response/notification
│   ├── track_session_state(session_id) → in-memory conversation model
│   ├── detect_failure_patterns()
│   │   ├── idle_timeout (no chunk > 30s)
│   │   ├── stop_reason_error (provider error)
│   │   ├── websocket_close (backend crash)
│   │   └── process_exit (goose.exe died)
│   └── emit_events() → structured logs / callbacks
├── session_monitor.py
│   ├── watch_sessions_db() → SQLite file monitor (polling or inotify)
│   └── correlate_with_ws() → join WS events + DB state
└── cli.py
    ├── guardian observe --session <id>  # pretty-print ACP events
    ├── guardian watch --all             # monitor all sessions
    └── guardian status                  # show current backend health
```

### 13.2 Safety Guarantees (MVP)

- **Zero outbound frames** — Guardian never sends ACP requests
- **Read-only SQLite** — `PRAGMA query_only=ON`
- **No process manipulation** — observes only
- **Explicit session tracking** — maintains `session_id → {last_user_msg_idx, tool_calls_seen, chunks_received}`

### 13.3 Data Model (In-Memory)

```python
@dataclass
class SessionObservation:
    session_id: str
    last_user_message_index: int          # index in conversation
    tool_calls_dispatched: Set[str]       # toolCallIds seen
    assistant_chunks_received: int        # count of text chunks
    last_chunk_timestamp: float           # for idle detection
    stop_reason: Optional[str]            # EndTurn/MaxTurns/Error
    current_turn_safe_to_replay: bool     # computed: no tools, no chunks
```

### 13.4 Failure Detection Logic

```python
def evaluate_recovery_safety(obs: SessionObservation) -> RecoveryAction:
    if obs.tool_calls_dispatched:
        return RecoveryAction.UNSAFE  # Tools already ran
    if obs.assistant_chunks_received > 0:
        return RecoveryAction.UNSAFE  # Partial response seen
    if obs.stop_reason == "Error" and obs.current_turn_safe_to_replay:
        return RecoveryAction.SAFE_REPLAY  # Provider error, pre-tools
    if obs.stop_reason is None and time.now() - obs.last_chunk_timestamp > IDLE_TIMEOUT:
        return RecoveryAction.CANCEL_THEN_REPLAY  # Stalled stream
    return RecoveryAction.NONE
```

---

## 14. NEXT IMPLEMENTATION STEP

> **Build the Read-Only ACP Observer (MVP)**

**Concrete Steps:**

1. **Create Python project** at `C:\Projects\AgentGuardian\guardian_observer/`
2. **Implement `discovery.py`:**
   - Scan `127.0.0.1:10000-65535` for `GET /status` → `ok`
   - For each, attempt WebSocket upgrade to `/acp` with token brute-force (or extract from Electron if accessible)
   - *Simpler:* Use known lease registry pattern — port is allocated sequentially, token is static per lease
3. **Implement `websocket_observer.py`:**
   - Connect with `websockets` library: `ws://127.0.0.1:<port>/acp?token=<secret>`
   - Parse JSON-RPC frames: `{"jsonrpc":"2.0","method":"...","params":{...},"id":...}`
   - Track session state per `sessionId` in notifications
4. **Implement `cli.py`:**
   - `guardian observe --session <id>` — prints live ACP events for that session
   - `guardian watch` — monitors all sessions, logs failure patterns
5. **Test against live Goose Desktop:**
   - Start Goose Desktop, open chat
   - Run Guardian observer, verify it sees `sessionUpdate`, `ToolCallUpdate`, `StopReason`
   - Simulate 429 (mock provider) — verify detection
   - Kill `goose.exe` — verify crash detection

**Success Criteria:**
- Observer runs indefinitely without affecting Goose Desktop
- Correctly identifies: successful turn, rate limit error, backend crash, stalled stream
- `current_turn_safe_to_replay` flag accurate in all observed cases

**Do NOT implement:** Auto-replay, recovery actions, GUI, multi-agent adapters — those come after MVP validation.

---

## Appendix: Key Source References

| Component | File Path |
|-----------|-----------|
| Electron Main | `ui/desktop/src/main.ts` |
| Goose Serve Spawn | `ui/desktop/src/gooseServe.ts` |
| Lease Registry | `ui/desktop/src/gooseServeLeaseRegistry.ts` |
| ACP Connection (Renderer) | `ui/desktop/src/acp/acpConnection.ts` |
| Session Controller | `ui/desktop/src/acp/chatSessionController.ts` |
| Session Store | `ui/desktop/src/acp/chatSessionStore.ts` |
| Prompt/Cancel | `ui/desktop/src/acp/prompt.ts` |
| ACP Sessions API | `ui/desktop/src/acp/sessions.ts` |
| Agent (Rust) | `crates/goose/src/agents/agent.rs` |
| Retry Manager | `crates/goose/src/agents/retry.rs` |
| State Machine Ops | `crates/goose/src/agents/state_machine/` |
| Provider Retry | `crates/goose-provider-types/src/retry.rs` |
| HTTP Client | `crates/goose-providers/src/api_client.rs` |
| HTTP Status Handling | `crates/goose-providers/src/http_status.rs` |
| Session Manager | `crates/goose/src/session/session_manager.rs` |
| ACP Server | `crates/goose/src/acp/server.rs` |
| ACP Transport | `crates/goose/src/acp/transport/mod.rs` |

---

*End of Research Document*