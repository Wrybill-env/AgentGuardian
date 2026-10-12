# Agent Guardian Capability Intelligence Report

**Generated**: 2026-10-12  
**Commit**: `e13e103`  
**Registry Version**: 1.0

---

## Executive Summary

| Agent | Supported | Unsupported | Verified | Mock-Verified | Simulated | Unverified | Unsafe |
|-------|-----------|-------------|----------|---------------|-----------|------------|--------|
| **Hermes** | 13 | 15 | 11 | 0 | 2 | 0 | 0 |
| **Goose** | 18 | 10 | 18 | 0 | 0 | 0 | 0 |
| **OpenCode** | 22 | 6 | 20 | 2 | 0 | 0 | 0 |
| **FreeBuff** | 13 | 15 | 0 | 0 | 0 | 13 | 0 |

---

## Hermes (In-Process Observer)

**Evidence Level**: VERIFIED (in-process callback hooks + unit tests)

### Supported Capabilities (13)
| Capability | Verification | Evidence |
|------------|--------------|----------|
| OBSERVE_STREAM | ✅ VERIFIED | In-process stream delta callbacks |
| OBSERVE_REASONING | ✅ VERIFIED | Reasoning callback hooks |
| OBSERVE_TOOL_PROGRESS | ✅ VERIFIED | Tool progress callbacks |
| OBSERVE_TURN_LIFECYCLE | ✅ VERIFIED | Turn start/end callbacks |
| RECONNECT_STREAM | 🔶 SIMULATED | Callback reattach only |
| RETRY_REQUEST | 🔶 SIMULATED | Callback reattach + replay |
| GET_PARTIAL_TOOL_NAMES | ✅ VERIFIED | Observer tracks partial tool names |
| GET_SESSION_STATE | ✅ VERIFIED | Observer session tracking |
| GET_TURN_CONTEXT | ✅ VERIFIED | TurnContext construction |
| VERIFY_SESSION_IDENTITY | ✅ VERIFIED | Session ID tracking |
| DETECT_DISCONNECT | ✅ VERIFIED | Callback detachment |
| DETECT_STALE | ✅ VERIFIED | Time-based stale detection |
| WAIT_FOR_BACKEND | ✅ VERIFIED | Internal wait |

### Gaps (17)
| Capability | Status | Resolution | Priority |
|------------|--------|------------|----------|
| CANCEL_STREAM | UNSUPPORTED | BLOCKED - No cancel in Hermes architecture | 2 |
| REATTACH_SESSION | UNSUPPORTED | ADAPTER_SPECIFIC - No session persistence | 2 |
| FORK_SESSION | UNSUPPORTED | ADAPTER_SPECIFIC - No session management API | 2 |
| LOAD_SESSION | UNSUPPORTED | ADAPTER_SPECIFIC - No session load API | 2 |
| SAVE_SESSION | UNSUPPORTED | ADAPTER_SPECIFIC - No session save API | 2 |
| SWITCH_PROVIDER | UNSUPPORTED | OPTIONAL_MODULE - Internal to Hermes | 2 |
| GET_PROVIDER_LIST | UNSUPPORTED | OPTIONAL_MODULE - Internal to Hermes | 2 |
| RESTART_AGENT | UNSUPPORTED | BLOCKED - Cannot restart in-process | 2 |
| GET_AGENT_STATUS | UNSUPPORTED | BLOCKED - No external status API | 2 |
| IS_AGENT_ALIVE | UNSUPPORTED | BLOCKED - In-process only | 2 |
| CANCEL_TURN | UNSUPPORTED | BLOCKED - No cancellation API | 2 |
| REPLAY_TURN | UNSUPPORTED | BLOCKED - No replay API | 2 |
| GET_TOOL_STATE | UNSUPPORTED | ADAPTER_SPECIFIC - No tool state API | 2 |
| VERIFY_SESSION_EXISTS | UNSUPPORTED | BLOCKED - No session list API | 2 |
| DETECT_CRASH | UNSUPPORTED | BLOCKED - In-process only | 2 |

---

## Goose (ACP WebSocket)

**Evidence Level**: VERIFIED (ACP protocol + forensic integration tests)

### Supported Capabilities (18)
| Capability | Verification | Evidence |
|------------|--------------|----------|
| OBSERVE_STREAM | ✅ VERIFIED | ACP `agent_message_chunk` events |
| OBSERVE_TOOL_PROGRESS | ✅ VERIFIED | ACP `tool_call` / `tool_call_update` |
| OBSERVE_TURN_LIFECYCLE | ✅ VERIFIED | ACP `sessionNotification` |
| RECONNECT_STREAM | ✅ VERIFIED | WebSocket reconnection |
| FORK_SESSION | ✅ VERIFIED | ACP `agent/session/fork` |
| LOAD_SESSION | ✅ VERIFIED | ACP `agent/session/load` |
| CANCEL_TURN | ✅ VERIFIED | ACP `cancel` notification |
| RETRY_REQUEST | ✅ VERIFIED | Reattach + replay |
| REPLAY_TURN | ✅ VERIFIED | Load session with history |
| GET_TOOL_STATE | ✅ VERIFIED | ACP tool call tracking |
| GET_SESSION_STATE | ✅ VERIFIED | Observer session tracking |
| GET_TURN_CONTEXT | ✅ VERIFIED | TurnContext from observer |
| VERIFY_SESSION_IDENTITY | ✅ VERIFIED | Session ID matching |
| GET_AGENT_STATUS | ✅ VERIFIED | HTTP `/status` endpoint |
| IS_AGENT_ALIVE | ✅ VERIFIED | WebSocket connection status |
| DETECT_DISCONNECT | ✅ VERIFIED | WebSocket close detection |
| DETECT_STALE | ✅ VERIFIED | Observer stale detection |
| WAIT_FOR_BACKEND | ✅ VERIFIED | Poll `/status` endpoint |

### Gaps (10)
| Capability | Status | Resolution | Priority |
|------------|--------|------------|----------|
| OBSERVE_REASONING | UNSUPPORTED | BLOCKED - Not in ACP protocol | 2 |
| CANCEL_STREAM | UNSUPPORTED | BLOCKED - No explicit ACP cancel | 2 |
| REATTACH_SESSION | UNSUPPORTED | ADAPTER_SPECIFIC - No direct ACP support | 2 |
| SAVE_SESSION | UNSUPPORTED | ADAPTER_SPECIFIC - No ACP session save | 2 |
| SWITCH_PROVIDER | UNSUPPORTED | OPTIONAL_MODULE - Not in ACP | 2 |
| GET_PROVIDER_LIST | UNSUPPORTED | OPTIONAL_MODULE - Not in ACP | 2 |
| RESTART_AGENT | UNSUPPORTED | BLOCKED - Cannot restart Goose Desktop | 2 |
| GET_PARTIAL_TOOL_NAMES | UNSUPPORTED | BLOCKED - Not in ACP | 2 |
| VERIFY_SESSION_EXISTS | UNSUPPORTED | BLOCKED - No session list query | 2 |
| DETECT_CRASH | UNSUPPORTED | BLOCKED - Only detects disconnect | 2 |

---

## OpenCode (ACP stdio Subprocess)

**Evidence Level**: VERIFIED (ACP protocol + real handshake verified)

### Supported Capabilities (22)
| Capability | Verification | Evidence |
|------------|--------------|----------|
| OBSERVE_STREAM | ✅ VERIFIED | ACP `agent_message_chunk` |
| OBSERVE_REASONING | ✅ VERIFIED | ACP `agent_thought_chunk` |
| OBSERVE_TOOL_PROGRESS | ✅ VERIFIED | ACP `tool_call` / `tool_call_update` |
| OBSERVE_TURN_LIFECYCLE | ✅ VERIFIED | ACP `sessionNotification` |
| RECONNECT_STREAM | ✅ VERIFIED | Subprocess restart |
| FORK_SESSION | 🔶 MOCK_VERIFIED | ACP `forkSession` (unstable) |
| LOAD_SESSION | 🔶 MOCK_VERIFIED | ACP `loadSession` (unstable) |
| RESTART_AGENT | ✅ VERIFIED | Subprocess restart |
| CANCEL_TURN | ✅ VERIFIED | ACP `cancel` notification |
| RETRY_REQUEST | ✅ VERIFIED | New session + replay |
| REPLAY_TURN | ✅ VERIFIED | Load session with history |
| GET_PARTIAL_TOOL_NAMES | ✅ VERIFIED | Observer tracking |
| GET_SESSION_STATE | ✅ VERIFIED | Observer session tracking |
| GET_TURN_CONTEXT | ✅ VERIFIED | Turn tracking |
| VERIFY_SESSION_IDENTITY | ✅ VERIFIED | Session ID tracking |
| VERIFY_SESSION_EXISTS | ✅ VERIFIED | ACP `listSessions` |
| GET_AGENT_STATUS | ✅ VERIFIED | Process status |
| IS_AGENT_ALIVE | ✅ VERIFIED | Process/connection status |
| DETECT_DISCONNECT | ✅ VERIFIED | Process/connection monitoring |
| DETECT_CRASH | ✅ VERIFIED | Process termination detection |
| DETECT_STALE | ✅ VERIFIED | Observer stale detection |
| WAIT_FOR_BACKEND | ✅ VERIFIED | Process readiness polling |

### Gaps (6)
| Capability | Status | Resolution | Priority |
|------------|--------|------------|----------|
| CANCEL_STREAM | UNSUPPORTED | BLOCKED - No explicit ACP cancel | 2 |
| REATTACH_SESSION | UNSUPPORTED | ADAPTER_SPECIFIC - No direct ACP support | 2 |
| SAVE_SESSION | UNSUPPORTED | ADAPTER_SPECIFIC - No ACP session save | 2 |
| SWITCH_PROVIDER | UNSUPPORTED | OPTIONAL_MODULE - Not in ACP | 2 |
| GET_PROVIDER_LIST | UNSUPPORTED | OPTIONAL_MODULE - Not in ACP | 2 |
| GET_TOOL_STATE | UNSUPPORTED | BLOCKED - Not in ACP | 2 |

---

## FreeBuff / Codebuff (HTTP/WebSocket)

**Evidence Level**: DECLARED (interface + mock, blocked by missing credentials)

### Supported Capabilities (13)
| Capability | Verification | Evidence |
|------------|--------------|----------|
| OBSERVE_STREAM | ❓ UNVERIFIED | SDK `handleEvent` callback |
| OBSERVE_TOOL_PROGRESS | ❓ UNVERIFIED | SDK tool call events |
| OBSERVE_TURN_LIFECYCLE | ❓ UNVERIFIED | Run start/end events |
| RECONNECT_STREAM | ❓ UNVERIFIED | WebSocket reconnection |
| RETRY_REQUEST | ❓ UNVERIFIED | New run with `previousRun` |
| GET_SESSION_STATE | ❓ UNVERIFIED | Observer run tracking |
| GET_TURN_CONTEXT | ❓ UNVERIFIED | Observer turn tracking |
| VERIFY_SESSION_IDENTITY | ❓ UNVERIFIED | Session/run ID tracking |
| IS_AGENT_ALIVE | ❓ UNVERIFIED | WebSocket connection |
| DETECT_DISCONNECT | ❓ UNVERIFIED | WebSocket close detection |
| DETECT_CRASH | ❓ UNVERIFIED | Connection loss detection |
| DETECT_STALE | ❓ UNVERIFIED | Observer stale detection |
| WAIT_FOR_BACKEND | ❓ UNVERIFIED | Health endpoint polling |

### Gaps (28)
| Capability | Status | Resolution | Priority |
|------------|--------|------------|----------|
| OBSERVE_REASONING | UNSUPPORTED | BLOCKED - Not in Codebuff API | 2 |
| CANCEL_STREAM | UNSUPPORTED | BLOCKED - No explicit cancel | 2 |
| REATTACH_SESSION | UNSUPPORTED | BLOCKED - No session reattach | 2 |
| FORK_SESSION | UNSUPPORTED | BLOCKED - No session fork | 2 |
| LOAD_SESSION | UNSUPPORTED | BLOCKED - Use `previousRun` instead | 2 |
| SAVE_SESSION | UNSUPPORTED | BLOCKED - No session save | 2 |
| SWITCH_PROVIDER | UNSUPPORTED | BLOCKED - Not in API | 2 |
| GET_PROVIDER_LIST | UNSUPPORTED | BLOCKED - Not in API | 2 |
| RESTART_AGENT | UNSUPPORTED | BLOCKED - SaaS, cannot restart | 2 |
| GET_AGENT_STATUS | UNSUPPORTED | BLOCKED - No status endpoint | 2 |
| CANCEL_TURN | UNSUPPORTED | BLOCKED - No cancel API | 2 |
| REPLAY_TURN | UNSUPPORTED | BLOCKED - No replay | 2 |
| GET_PARTIAL_TOOL_NAMES | UNSUPPORTED | BLOCKED - Not exposed | 2 |
| GET_TOOL_STATE | UNSUPPORTED | BLOCKED - Not in API | 2 |
| VERIFY_SESSION_EXISTS | UNSUPPORTED | BLOCKED - No session list | 2 |

---

## Gap Resolution Summary

| Resolution Strategy | Capabilities | Implementation Approach |
|---------------------|--------------|-------------------------|
| **core_generic** | DETECT_STALE, DETECT_DISCONNECT, WAIT_FOR_BACKEND | Implement in Guardian core |
| **adapter_specific** | RECONNECT_STREAM, FORK_SESSION, LOAD_SESSION, REATTACH_SESSION, CANCEL_TURN, RETRY_REQUEST, REPLAY_TURN, VERIFY_SESSION_IDENTITY, VERIFY_SESSION_EXISTS, GET_SESSION_STATE, GET_TURN_CONTEXT | Implement in each adapter |
| **optional_module** | SWITCH_PROVIDER, GET_PROVIDER_LIST, RESTART_AGENT, GET_AGENT_STATUS, IS_AGENT_ALIVE, GET_PARTIAL_TOOL_NAMES, GET_TOOL_STATE | Separate optional modules |
| **blocked** | OBSERVE_REASONING (Goose/FreeBuff), CANCEL_STREAM, RESTART_AGENT (Hermes/FreeBuff), etc. | Document as protocol/architecture limitations |

---

## Recommendations

1. **Immediate**: Implement core_generic capabilities in Guardian core (DETECT_STALE, DETECT_DISCONNECT, WAIT_FOR_BACKEND)

2. **Short-term**: Add adapter-specific implementations for critical session recovery (FORK_SESSION, LOAD_SESSION, REATTACH_SESSION) where protocol supports it

3. **Documentation**: Clearly mark BLOCKED capabilities in adapter documentation with architectural reasons

4. **Verification**: Add integration tests to move MOCK_VERIFIED → VERIFIED for OpenCode unstable APIs

5. **FreeBuff**: Requires Codebuff SDK access for real runtime verification; currently DECLARED only

---

*Report generated by Agent Guardian Capability Intelligence System*