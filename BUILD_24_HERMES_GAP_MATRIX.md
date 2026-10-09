# BUILD 24 HERMES GAP MATRIX

## Hermes Source Inspected

| Source Location | Description |
|-----------------|-------------|
| `C:/Users/Mitansh Kulshreshtha/AppData/Local/hermes/hermes-agent/.hermes-runtime/` | Hermes Python runtime with ACP SDK |
| `C:/Projects/AgentGuardian/acp_adapter/` | Hermes ACP adapter (server.py, events.py, entry.py) |
| `C:/Projects/AgentGuardian/goose-cli-source/` | Goose CLI source (ACP provider, tool notifications) |

---

## Gap Matrix

| Capability | Hermes Source Evidence | Guardian Implementation | Reuse/Adaptation Opportunity | Verification Needed |
|------------|------------------------|------------------------|------------------------------|---------------------|
| **Retry Classification** | `acp_adapter/events.py` - Tool callbacks with retry logic, error categorization | `failure_classifier.py` - Classifies by error message, HTTP status, stop reason | **HIGH** - Hermes has concrete retry taxonomy for provider, network, stream errors | Unit tests for each failure type mapping |
| **Rate-Limit Handling** | HTTP 429 detection in Goose provider, Retry-After header support | `RATE_LIMIT` failure type, `BackoffStrategy` with `retry_after` override | **HIGH** - Adopt Hermes backoff on 429 with server-provided timing | Live provider test with rate limiting |
| **Exponential Backoff & Jitter** | Goose provider uses exponential backoff with jitter | `BackoffStrategy` with `BackoffConfig`, `JitterType` enum (NONE, FULL, EQUAL) | **HIGH** - Align defaults and max delay caps | Integration test with flaky provider |
| **Stream Interruption & Recovery** | WebSocket ping/pong in observer, reconnection logic in `acp_adapter/entry.py` | `_check_stale()` with `stale_threshold`, `DISCONNECTED` event handling | **MEDIUM** - Add ping-based liveness, WebSocket reconnection | Network interruption test |
| **Stale-Stream/Liveness Detection** | Observer heartbeat (30s ping), stale check (10s interval) | `_check_stale()` with configurable threshold | **MEDIUM** - Enhance with ping-based detection | Simulated network stall test |
| **Session Persistence & Continuation** | ACP `session/load`, `session/fork` support in Goose provider | Tested - minimal response, no history replay | **LOW** - Requires agent support for true continuation | Agent-specific session restore test |
| **Provider Fallback** | Goose supports multiple providers (Ollama, NVIDIA, etc.) | `SWITCH_PROVIDER` capability (unused), `failure_classifier` has `SWITCH_PROVIDER` action | **LOW** - Needs adapter implementation for target agent | Goose multi-provider test |
| **Agent Process Crash Handling** | Goose monitors process health, restart capability | `BACKEND_CRASH` failure type, `RESTART_AGENT` action | **HIGH** - Add process health checks via adapter | Simulated crash test |
| **Cancellation & Shutdown** | ACP `cancel` notification, `session/close` | `TURN_CANCELLED`, `CANCEL_AND_REPLAY` action | **HIGH** - Validate cancellation propagation | Cancellation during tool test |
| **Error Propagation & Recovery Orchestration** | Linear retry with backoff, circuit breaker patterns | `RecoveryExecutor`, `RecoveryPlanner`, `RecoveryLadder` with budget | **HIGH** - Validate ladder logic against Hermes patterns | End-to-end recovery test |

---

## Reuse Decisions

### Reuse Directly (Legally & Technically Appropriate)
1. **Retry classification taxonomy** - Generic error categorization (rate limit, network, server, auth)
2. **Exponential backoff with jitter** - Algorithm is generic, not agent-specific
3. **Stale stream detection concept** - Heartbeat + threshold pattern
4. **Session/load & session/fork semantics** - ACP protocol standard

### Adapt with Minimal Changes
1. **Backoff configuration** - Align `BackoffConfig` defaults with Hermes (initial=1s, max=60s, multiplier=2)
2. **Retry-After header handling** - Already implemented in Guardian's `BackoffStrategy`
3. **Failure classifier patterns** - Map Hermes error patterns to Guardian's `FAILURE_PATTERNS`

### Implement Independently (Coupling Too High)
1. **WebSocket transport management** - Hermes-specific, Guardian uses adapters
2. **Goose-specific provider orchestration** - Agent-specific logic
3. **ACP message framing** - Protocol-specific, handled by `agent-client-protocol` SDK

### Defer (Evidence/Prerequisites Missing)
1. **True session continuation** - Requires agent that supports history replay
2. **Provider fallback** - Needs multi-provider agent adapter
3. **Circuit breaker** - Needs more production data on failure patterns

---

## Licensing Considerations

| Component | Hermes License | Guardian License | Reuse Status |
|-----------|---------------|------------------|--------------|
| Core algorithms (backoff, retry) | MIT | MIT | ✅ Compatible |
| ACP protocol handling | MIT | MIT | ✅ Compatible |
| Goose-specific code | MIT (Goose) | MIT | ⚠️ Agent-specific |
| WebSocket transport | MIT | MIT | ✅ Generic |

**Conclusion:** All generic reliability algorithms are MIT-licensed and can be freely adapted. Agent-specific protocol code remains in respective adapters. No substantial code copied; only algorithmic patterns adapted.

---

## Verification Plan

| Adaptation | Test Approach |
|------------|---------------|
| Retry classification | Unit tests mapping Hermes error patterns to Guardian failure types |
| Backoff alignment | Integration test with simulated flaky provider |
| Stale detection | Network partition test with controlled timing |
| Session load | Forensic agent test (already implemented) |
| Cancellation | ACP cancel notification test |