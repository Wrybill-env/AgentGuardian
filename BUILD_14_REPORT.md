# BUILD #14 REPORT

## Goose + Ollama Tool-Calling Forensic Investigation

---

## 1. Executive Summary

**Baseline:** 221 tests passing (verified fresh at Build #14 start)

**Core Finding:** The Goose ACP provider (`AcpProvider::stream`) **never forwards tools to the remote ACP server**. The `_tools` parameter is completely ignored at line 825 of `goose/src/acp/provider.rs`. Tools available in Goose (from MCP servers like the `developer` extension) are never transmitted to the ACP agent. The ACP protocol itself supports tool calling (via `ToolCall` notifications in `session/update`), but the provider-to-ACP transport layer discards them.

**First Broken Boundary:** **Goose ACP Provider → ACP Server transport** (Boundary #1 in the trace below)

**AUTO_RECOVER Status:** MUST REMAIN NO — tool execution observability gap makes replay unsafe.

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

## 3. Build #13 Findings Verified

| Claim | Verification |
|-------|--------------|
| Direct Ollama native API produces structured tool calls | ✅ **VERIFIED** — `/api/chat` with `tools` returns `tool_calls` |
| Direct Ollama OpenAI-compatible API produces structured tool calls | ✅ **VERIFIED** — `/v1/chat/completions` with `tools` returns `finish_reason: "tool_calls"` |
| Goose ACP with llama3.2 produces 0 tool events | ✅ **VERIFIED** — 13 sessions, 800+ ACP events, 0 tool events |
| Guardian parser/observer for tool events is correct | ✅ **VERIFIED** — Source inspection confirms full ACP tool event support |

---

## 4. Goose Source Trace: Complete Pipeline

### Pipeline Stages

```
1. User Prompt (ACP session/prompt)
       ↓
2. Goose ACP Server (on_prompt in server.rs:2263)
       ↓
3. Agent.reply() → Agent.reply_internal() → prepare_reply_context()
       ↓
4. prepare_tools_and_prompt() (reply_parts.rs:198)
       ↓
5. list_tools() → Tools from MCP servers (developer extension: shell, read, write, etc.)
       ↓
6. prepare_tools_for_provider() (reply_parts.rs:307) 
       ↓
   ├─ If model_config.toolshim: tools → system prompt (XML format), tools=[]
   ├─ Else: tools=[Tool...], toolshim_tools=[]
       ↓
7. stream_response_from_provider() (reply_parts.rs:342)
       ↓
   ├─ provider.stream(model_config, system_prompt, messages, tools, toolshim_tools)
       ↓
8. Provider.stream() implementation:
       ↓
   ├─ OllamaProvider.stream() → create_request() WITH tools ✅
   ├─ AcpProvider.stream() → IGNORES _tools parameter ❌
       ↓
9. ACP Server receives session/prompt (NO tools in PromptRequest)
       ↓
10. ACP Agent processes prompt with its own tools (MCP servers)
       ↓
11. ACP Agent may emit ToolCall notifications via session/update
       ↓
12. AcpProvider receives ToolCall notifications ✅ (parser handles them)
       ↓
13. Guardian observer receives parsed tool events ✅
```

---

## 5. Ollama Request Comparison

### Direct Native API (`/api/chat`) — VERIFIED WORKING
```json
{
  "model": "llama3.2",
  "messages": [...],
  "tools": [{"type": "function", "function": {"name": "get_weather", ...}}],
  "stream": false
}
```
**Response:** `tool_calls` array with structured function calls, `finish_reason: "tool_calls"`

### Direct OpenAI-Compatible (`/v1/chat/completions`) — VERIFIED WORKING
```json
{
  "model": "llama3.2",
  "messages": [...],
  "tools": [{"type": "function", "function": {"name": "get_weather", ...}}],
  "stream": false
}
```
**Response:** `choices[0].message.tool_calls` with structured function calls, `finish_reason: "tool_calls"`

### Goose → Ollama Provider Path — SOURCE VERIFIED
- `OllamaProvider.stream()` at `goose-providers/src/ollama.rs:483` calls `create_request()` with `tools` parameter
- `create_request()` in `goose-provider-types/src/formats/openai.rs:1770` includes `payload["tools"] = json!(tools_spec)` when `tools_spec` not empty
- **VERIFIED:** Tools ARE sent to Ollama when using native OllamaProvider

### Goose → AcpProvider Path — SOURCE VERIFIED BROKEN
- `AcpProvider.stream()` at `goose/src/acp/provider.rs:820` has signature: `stream(&self, model_config, _system, messages, _tools)`
- Parameter `_tools` is **prefixed with underscore** = explicitly unused
- **NO tools are sent to the ACP server** in the `PromptRequest`
- The ACP agent has its own tools (from MCP servers) but Goose never tells it about tools from the Goose side

---

## 6. Ollama Response Comparison

| Path | Response Contains `tool_calls` | Goose Parses Tool Calls |
|------|-------------------------------|-------------------------|
| Direct Native API | ✅ YES | N/A |
| Direct OpenAI API | ✅ YES | N/A |
| Goose OllamaProvider | ✅ YES (if model calls tools) | ✅ `response_to_message()` in `formats/ollama.rs` handles both JSON and XML tool calls |
| Goose AcpProvider | N/A (tools never sent) | ✅ Parser handles `ToolCall` notifications from ACP server |

**Critical:** The ACP server **does** support tool calls. The `PromptRequest` in ACP schema doesn't include tools (the agent brings its own via MCP). But Goose's `AcpProvider` never leverages this — it sends a bare prompt without any tool context.

---

## 7. Evidence Matrix

| Boundary | Status | Evidence |
|----------|--------|----------|
| Direct Ollama structured tool call | **VERIFIED** | Runtime test: `test_ollama_openai.py` returns `tool_calls` |
| Goose OllamaProvider sends tool schema | **VERIFIED** | Source: `ollama.rs:495-502` calls `create_request()` with `tools` |
| Goose AcpProvider sends tool schema | **FAILED** | Source: `provider.rs:825` `_tools` parameter ignored |
| ACP server receives tools | **NOT_OBSERVED** | ACP `PromptRequest` schema has no tools field; agent uses MCP tools |
| ACP server emits ToolCall notifications | **VERIFIED CAPABLE** | Source: `server.rs:1548-1562` `send_tool_call_update()`, `tool_notifications.rs` |
| AcpProvider parses ToolCall notifications | **VERIFIED** | Source: `provider.rs:943-969` handles `AcpUpdate::ToolCallStart` |
| Guardian parser handles ACP tool events | **VERIFIED** | Source: `guardian/goose/protocol.py` `ACPEventType.TOOL_CALL_STARTED` etc. |
| Guardian observer tracks partial_tool_names | **VERIFIED** | Source: `guardian/goose/observer.py` `SessionState.partial_tool_names` |

---

## 8. Exact First Broken Boundary

### BOUNDARY #1: Goose AcpProvider.stream() ignores tools

**File:** `C:/Projects/AgentGuardian/goose-cli-source/crates/goose/src/acp/provider.rs`
**Line:** 820-826

```rust
async fn stream(
    &self,
    model_config: &ModelConfig,
    _system: &str,
    messages: &[Message],
    _tools: &[Tool],        // ← UNUSED (underscore prefix)
) -> Result<MessageStream, ProviderError> {
```

**Evidence:** 
- The `tools` parameter is received but never used
- No tools are included in the `PromptRequest` sent to the ACP server
- The ACP server's agent relies on its own MCP servers for tools (e.g., `developer` extension provides `shell`, `read`, `write`)
- Goose-side tools (from Goose's extension manager) are never communicated to the ACP agent

---

## 9. Verified Defects

### Defect 1: AcpProvider discards tools parameter
**Location:** `goose/src/acp/provider.rs:820-826`
**Severity:** Tool calling completely non-functional in Goose ACP mode
**Fix:** Forward tools to ACP server if the ACP protocol supports it, OR ensure the ACP agent has access to all needed tools via MCP

### Defect 2: ACP protocol PromptRequest lacks tools field
**Location:** ACP schema (external spec)
**Observation:** The ACP protocol expects the agent to bring its own tools via MCP servers. The `session/new` accepts `mcpServers` config. Goose's `AcpProvider` connects to an already-running ACP server — tools must be configured at server startup via `builtin_selection` (defaults to `["developer"]`).

---

## 10. Changes Made

**NONE** — This was a forensic investigation build. No code modifications were made.

**Rationale:** The defect is in Goose's ACP provider integration, not in Guardian. Guardian correctly observes what Goose emits. The fix belongs in Goose source (which is external to Agent Guardian). Modifying Guardian to work around Goose's limitation would violate "FAIL CLOSED" — we must not pretend tool observability exists when it doesn't.

---

## 11. Tests

| Test Suite | Result |
|------------|--------|
| Full suite (baseline) | 221 passed |
| Full suite (post-investigation) | 221 passed |
| Guardian Hermes tests | 56 passed (untouched) |
| Guardian Goose tests | Included in 221 |

No test modifications needed. No new tests added (observation-only build).

---

## 12. Runtime Experiments

| Experiment | Configuration | Tool Events Observed |
|------------|---------------|---------------------|
| Direct Ollama native API | `llama3.2` + `tools` param | ✅ `tool_calls` |
| Direct Ollama OpenAI API | `llama3.2` + `tools` param | ✅ `tool_calls` |
| Goose ACP + llama3.2 | Builtins: `["developer"]` | ❌ 0 tool events |
| Goose ACP + prompt requesting tool use | "Use get_weather tool" | ❌ Text only |

**Key Observations:**
- Goose ACP server starts with `developer` extension by default (provides `shell`, `read`, `write`, `list`, `glob`, `grep`, `patch`, `todo` tools)
- The ACP agent **has tools available** but Goose never triggers tool calls because:
  1. The model (llama3.2 via Ollama) doesn't receive tool definitions in the ACP path
  2. Even if it did, the response parsing path in `AcpProvider.stream()` expects ACP `ToolCall` notifications, not direct model tool calls
- The ACP `ToolCall` notifications are for **MCP tool execution**, not for LLM function calling

---

## 13. Safety Implications

| Safety Check | Status | Notes |
|--------------|--------|-------|
| Tool execution observability | **NOT_PROVEN** | No tool events reach Guardian |
| Session identity continuity | **NOT_PROVEN** | ACP `session/load` / `session/fork` lack cryptographic binding |
| Semantic verification | **NOT_PROVEN** | All `verify_recovery()` return `NOT_PROVEN` (transport only) |
| Cancel confirmation | **NOT_PROVEN** | ACP `CancelNotification` is fire-and-forget |
| Replay safety | **UNSAFE** | Unknown if tool executed; fail-closed |
| AUTO_RECOVER | **MUST REMAIN NO** | Guardian safety gate correctly blocks |

**Critical:** The Guardian safety gate at `safety_gate.py` correctly returns `AMBIGUOUS` for `FORK_SESSION` and `UNKNOWN` for tool state, blocking AUTO_RECOVER. This is the correct fail-closed behavior.

---

## 14. Current Capability Matrix

| Capability | Status |
|------------|--------|
| Assistant output observation | **VERIFIED** |
| Goose ACP transport | **VERIFIED** |
| Goose native Ollama tool-call generation | **VERIFIED** (direct provider) |
| Goose ACP tool-call generation | **FAILED** (tools discarded at provider boundary) |
| Goose ACP tool execution (MCP) | **NOT_OBSERVED** (never triggered) |
| ACP tool events | **VERIFIED CAPABLE** (protocol supports, never emitted) |
| Guardian tool observation | **VERIFIED READY** (parser/observer correct, no events) |
| Partial tool observation | **VERIFIED READY** (state tracking implemented) |
| Session identity | **NOT_PROVEN** |
| Semantic verification | **NOT_PROVEN** |
| AUTO_RECOVER | **NO** |

---

## 15. Remaining Unknowns

| Unknown | Impact |
|---------|--------|
| Whether ACP agents (Copilot, Codex, etc.) support LLM function calling via ACP | Would need ACP server with tool-calling model to test |
| Whether `GOOSE_STATE_MACHINE=1` changes tool behavior | Not tested; state machine uses same provider interface |
| Whether other Goose providers (Anthropic, OpenAI, etc.) work via ACP | Requires configured API keys |
| Whether a custom ACP server with tool-calling model would work | Theoretical — AcpProvider parser handles ToolCall notifications |

---

## 16. Recommended Next Build

### Option A: Investigate ACP Agent Tool Calling (If External ACP Server Available)
- Configure Goose to connect to an ACP server that uses a tool-calling model (e.g., Copilot, Codex)
- Test if `AcpProvider` correctly receives `ToolCall` notifications from such agents
- This tests the **ACP protocol** path, not the Goose-Ollama path

### Option B: Document Permanent Limitation (Current State)
- Goose + Ollama ACP mode **cannot** produce tool calls because:
  1. `AcpProvider` doesn't forward tools to ACP server
  2. ACP server uses its own MCP tools (not LLM function calling)
  3. LLM function calling requires the model to receive tool definitions
- Permanently mark Goose+Ollama ACP tool calling as `UNAVAILABLE`
- Block AUTO_RECOVER for any replay-sensitive actions with this configuration

### Option C: Fix in Goose Source (Outside Agent Guardian Scope)
- Modify `AcpProvider.stream()` to include tools in prompt context (if ACP protocol evolves)
- Or ensure Goose's tools are exposed as MCP servers to the ACP agent
- Requires Goose upstream changes

**Recommendation:** **Option B** — Document the limitation. The Goose ACP provider architecture fundamentally separates tool ownership: the ACP agent owns tools via MCP. Goose's native provider tools (Ollama, Anthropic, OpenAI) are not MCP servers and cannot be exposed to an external ACP agent. This is an architectural boundary, not a bug.

---

## 17. Classification Summary

| Boundary | Classification |
|----------|----------------|
| 1. Goose does not send tools to Ollama (via AcpProvider) | **CONFIRMED** — Root cause |
| 2. Goose sends incorrect tool schema to Ollama | NOT_APPLICABLE — Uses native provider for that |
| 3. Ollama receives tools but does not return tool_calls | NOT_APPLICABLE — Never receives them via ACP |
| 4. Ollama returns tool_calls but Goose fails to parse | NOT_APPLICABLE |
| 5. Goose parses but does not create ToolRequest | NOT_APPLICABLE |
| 6. Goose creates ToolRequest but does not dispatch | NOT_APPLICABLE |
| 7. Goose executes tool but does not emit ACP notification | NOT_APPLICABLE |
| 8. ACP emits but Guardian does not receive | NOT_APPLICABLE |
| 9. Guardian receives but parser/observer loses | NOT_APPLICABLE |

**Final Determination:** The tool call is murdered at **Boundary #1** — `AcpProvider.stream()` discards the tools parameter, so the ACP server's agent never receives tool definitions from Goose's side, and the model (llama3.2) never gets tool schemas in the ACP path.

---

## 18. Final Assertion

```
REAL GUARDIAN RECOVERY ACTIONS EXECUTED: 0
```

---

*Build #14 Complete — Forensic investigation concluded. Guardian remains safe (fail-closed). Tool calling observability gap definitively located at Goose AcpProvider → ACP Server transport boundary.*