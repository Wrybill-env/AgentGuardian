<p align="center">
  <img src="assets/logo.svg" alt="Agent Guardian" width="180" height="180"/>
</p>

<h1 align="center">Agent Guardian</h1>

<p align="center">
  <strong>Safety-first reliability for AI coding agents</strong>
</p>

<p align="center">
  <a href="https://github.com/Wrybill-env/AgentGuardian/actions/workflows/tests.yml"><img src="https://github.com/Wrybill-env/AgentGuardian/actions/workflows/tests.yml/badge.svg?branch=main" alt="Tests"/></a>
  <a href="https://github.com/Wrybill-env/AgentGuardian/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License: Apache-2.0"/></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+"/>
  <img src="https://img.shields.io/badge/status-v0.1.0%20pre--release-orange" alt="Status: v0.1.0 pre-release"/>
</p>

> **Status: v0.1.0 pre-release.** Observation, failure classification and
> dry-run planning are the supported v1 capabilities. **Automatic recovery is
> experimental and restricted** — see [Limitations](#limitations--not-yet-supported).

---

## The Problem

AI coding agents (Goose, OpenCode, Hermes, Codebuff) execute tools that modify files, run commands, and call APIs. When a stream fails — network glitch, rate limit, crash — **the agent may have already executed the tool**. Blindly retrying the prompt duplicates the side effect: a file gets written twice, a command runs twice, an API is called twice.

**Agent Guardian prevents this.** It observes the agent's event stream, tracks exactly what side effects have been observed, and only permits recovery when it can *prove* no ambiguous execution occurred.

---

## Core Safety Lifecycle

```
OBSERVE -> CLASSIFY -> VERIFY STATE -> CHECK SAFETY -> RECOVER ONLY WHEN PROVABLY SAFE
```

**The critical principle:**  
**`UNKNOWN` execution state = `UNSAFE` for automatic recovery.**

- Observing an error does **not** prove a tool didn't execute
- A valid checksum does **not** prove the state is fresh or that replay is safe
- A successful reconnect does **not** prove the prior turn completed safely
- A valid `loadSession` response does **not** prove the prior tool outcomes are known

> If a tool may have executed and its outcome cannot be established, **replay remains blocked**.

---

## How It Works

Agent Guardian provides a **generic, agent-agnostic safety core** plus **agent-specific adapters** that translate each agent's protocol into Guardian's unified event model.

### Architecture

```
+-----------------------------------------------------------------------------+
|                        GUARDIAN CORE (agent-agnostic)                       |
|  +--------------+ +-----------------+ +--------------+ +----------------+  |
|  | State Machine| | Failure Classifier| |  Safety Gate | |Recovery Executor|  |
|  |  (lifecycle) | |  (retry hints)   |  | (replay gate) | |  (budget+ladder)|  |
|  +--------------+ +-----------------+ +--------------+ +----------------+  |
|           ^                  ^                  ^                   ^       |
|           +------------------+------------------+-------------------+       |
|                                    |                                        |
+------------------------------------+----------------------------------------+
                                     |
         +---------------------------+---------------------------+
         v                           v                           v
+-----------------+         +-----------------+         +-----------------+
|  Hermes Adapter |         |  Goose Adapter  |         | OpenCode Adapter|
|  (in-process)   |         |  (ACP WebSocket)|         | (ACP stdio)     |
+-----------------+         +-----------------+         +-----------------+
         ^                           ^                           ^
         |                           |                           |
   In-process                   ACP over                    ACP over
   callbacks                    WebSocket                     stdio
```

**Each adapter implements the `AgentAdapter` protocol** — a single interface the core uses for observation, capability queries, and recovery actions.

---

## Safety Modes

| Mode | Behavior | Recovery Execution |
|------|----------|-------------------|
| `OBSERVE_ONLY` (default) | Observe, classify, report — **never executes recovery** | Blocked |
| `DRY_RUN` | Determine what *would* happen, report intended actions — **no side effects** | Simulated only |
| `AUTO_RECOVER` | **Explicit opt-in only** — executes recovery only when safety gate returns `SAFE` | When `SAFE` |

> `AUTO_RECOVER` is never enabled by default. It must be explicitly passed via `--safety-mode auto_recover` or config, **and it additionally requires a cross-process recovery lease** (supplied automatically by `guardian watch`; refused otherwise).

---

## Installation

```bash
# From source (no PyPI release yet)
git clone https://github.com/Wrybill-env/AgentGuardian.git
cd AgentGuardian
pip install -e ".[dev]"

# Verify installation
python -m guardian --help
```

**Requirements:**
- Python 3.11+
- At least one supported agent installed and running:
  - **Goose Desktop** (ACP WebSocket)
  - **OpenCode** (ACP stdio subprocess)
  - **Hermes** (in-process callbacks)
  - **Codebuff/FreeBuff** (HTTP/WebSocket — requires API token)

---

## Quick Start

### Observe Goose (read-only, auto-discovers backend)
```bash
python -m guardian observe goose
# With specific port
python -m guardian observe goose --port 49684
# Verbose logging
python -m guardian observe goose -v
```

### Observe OpenCode (ACP stdio subprocess)
```bash
python -m guardian observe opencode
# Custom opencode path
python -m guardian observe opencode --opencode-path /usr/local/bin/opencode
```

### Observe Hermes (in-process, requires running Hermes agent)
```bash
python -m guardian observe hermes
```

### Observe Codebuff/FreeBuff (requires API token)
```bash
python -m guardian observe freebuff --api-token YOUR_TOKEN
```

### Run Diagnostics (Goose)
```bash
# Test backend discovery, WebSocket connection, and ACP handshake
python -m guardian doctor
# Test specific port
python -m guardian doctor --port 49684
```

### Manage Configuration
```bash
# Show current config
python -m guardian config show
# Save config to file
python -m guardian config save --path ~/.guardian/config.yaml
```

---

## Agent Support Matrix

> **Evidence levels:** `VERIFIED` = implementation inspected + behavior supported by tests/runtime; `MOCK-VERIFIED` = controlled mock/fixture tests; `SIMULATED` = callback reattach or process restart only; `UNVERIFIED` = insufficient evidence; `BLOCKED` = missing prerequisites.

| Capability | Hermes | Goose | OpenCode | Codebuff (FreeBuff) |
|------------|--------|-------|----------|---------------------|
| **Observation** | | | | |
| Stream chunks | VERIFIED | VERIFIED | VERIFIED | SIMULATED |
| Reasoning | VERIFIED | NO | VERIFIED | NO |
| Tool progress | VERIFIED | VERIFIED | VERIFIED | SIMULATED |
| Turn lifecycle | VERIFIED | VERIFIED | VERIFIED | SIMULATED |
| **Stream Recovery** | | | | |
| Reconnect stream | SIMULATED | VERIFIED | VERIFIED | SIMULATED |
| Cancel stream | NO | NO | NO | NO |
| **Session Recovery** | | | | |
| Load session | NO | VERIFIED | VERIFIED | NO (uses previousRun) |
| Fork session | NO | VERIFIED | VERIFIED* | NO |
| **Provider Recovery** | | | | |
| Switch provider | NO | NO | NO | NO |
| **Agent Process** | | | | |
| Restart agent | NO | NO | VERIFIED | NO (SaaS) |
| Get agent status | NO | VERIFIED | VERIFIED | NO |
| Is agent alive | NO | VERIFIED | VERIFIED | SIMULATED |
| **Turn Control** | | | | |
| Cancel turn | NO | VERIFIED | VERIFIED | NO |
| Retry request | SIMULATED | VERIFIED | VERIFIED | SIMULATED |
| **Introspection** | | | | |
| Partial tool names | VERIFIED | NO | VERIFIED | NO |
| Session state | VERIFIED | VERIFIED | VERIFIED | SIMULATED |
| Turn context | VERIFIED | VERIFIED | VERIFIED | SIMULATED |
| **Safety** | | | | |
| Verify session identity | NO | NO † | VERIFIED | SIMULATED |
| **Monitoring** | | | | |
| Detect disconnect | VERIFIED | VERIFIED | VERIFIED | SIMULATED |
| Detect crash | NO | NO | VERIFIED | SIMULATED |
| Detect stale | VERIFIED | VERIFIED | VERIFIED | SIMULATED |

*OpenCode forkSession uses unstable ACP API

† Goose sends `agent/session/load` but only checks the response for an error — it
does **not** verify the returned session identity.

> **Legend:** VERIFIED = Implemented + tested; SIMULATED = Partially implemented/simulated; NO = Not available in protocol

---

## Evidence Levels by Adapter

| Adapter | Protocol | Evidence |
|---------|----------|----------|
| **Hermes** | In-process callbacks | **VERIFIED** (interface + mock tests) |
| **Goose** | ACP WebSocket | **VERIFIED** (interface + mock + 14 forensic integration tests) |
| **OpenCode** | ACP stdio subprocess | **VERIFIED** (interface + mock + real ACP handshake verified) |
| **Codebuff** | HTTP/WebSocket | **BLOCKED** (interface + mock, runtime blocked by missing credentials) |

> **No capability is claimed as `VERIFIED` without either runtime evidence or comprehensive mock tests that exercise the actual code paths.**

---

## Safety Invariants — Enforced by Design

| Invariant | Implementation |
|-----------|----------------|
| `OBSERVE_ONLY` default | `SafetyMode.OBSERVE_ONLY` in all adapters |
| `UNKNOWN` blocks recovery | `SafetyVerdict.AMBIGUOUS` → recovery not authorised |
| Tool started + no completion = replay blocked | `TurnContext.is_safe_to_replay()` checks `tool_calls_started > 0` |
| TOCTOU protection | `state_generation` comparison at planning + execution boundary |
| `AUTO_RECOVER` never auto-enabled | Explicit `--safety-mode auto_recover` required |
| `AUTO_RECOVER` requires cross-process exclusion | `GuardianStateMachine` refuses to start recovery when no `RecoveryLease` is configured (fail closed) |
| Replay/reconnect/reload/fork require proven freshness | all in the `replay_actions` set (`WAIT_AND_RETRY`, `CANCEL_AND_REPLAY`, `RECONNECT_ACP`, `RECONNECT_STREAM`, `RELOAD_SESSION`, `FORK_SESSION`) and denied unless `verified_fresh=True`. **Nothing can currently establish corroborated freshness, so these remain blocked.** |
| Backend reachability ≠ session recovery | `WAIT_FOR_BACKEND` is blocked when a tool may have executed, and its verification only proves the *backend* is answering — not that the session/turn recovered |
| Session identity verification scope | OpenCode: `loadSession` round-trip validates the returned `sessionId`. **Goose: not verified** (the load response is only checked for an error) |
| One safety policy | `HermesSafetyGate` delegates to the core gate and returns the more restrictive verdict |
| Failed verification = no recovery | All failure paths return `False`, log error |

---

## Limitations & Not Yet Supported

Agent Guardian v0.1.0 is a **pre-release**. What it does today, precisely:

### Supported (evidence-backed)
- Observation and state tracking (`OBSERVE_ONLY`, the default).
- Failure classification and diagnostics.
- Dry-run recovery planning (`DRY_RUN`) — reports intent, executes nothing.
- Cross-process exclusion for any recovery that does execute.
- 384 automated tests, including a true two-process lease-exclusion test.

### Experimental / restricted
- **`AUTO_RECOVER` is experimental.** It requires an explicit opt-in *and* a
  configured cross-process recovery lease; the state machine refuses to start
  automatic recovery without one.
- No adapter can establish corroborated freshness today, so **every
  replay/reconnect/reload/fork action stays blocked**. The only automatic action
  that can be selected is `WAIT_FOR_BACKEND`, and only when no tool may have
  executed; `HUMAN_INTERVENTION` is always available.

### Not validated
- **No real-agent end-to-end recovery has ever been performed.** All adapter
  evidence is source inspection plus mock/forensic-subprocess tests. No claim of
  production reliability for any adapter is made.
- Goose tool events (`TOOL_CALL_STARTED` / `TOOL_CALL_COMPLETED`) were never
  observed at runtime with the models available — the parser is source-verified
  only.
- FreeBuff/Codebuff is **unverified** (requires credentials).
- Goose `RELOAD_SESSION` / `FORK_SESSION` read the socket directly instead of
  through a correlated reader. They are unreachable while those actions remain
  blocked, but this must be fixed before they could be enabled.

### Known issues
- Four untracked root audit scripts encode **obsolete safety expectations** and
  are expected to fail. They are user-owned audit scripts and have deliberately
  been left unmodified:
  - `test_persistence_audit.py`, `test_crash_windows.py` and
    `test_interprocess_locking_windows.py` assert that a *persisted snapshot*
    grants replay freshness (`verified_fresh=True`).
  - `test_adversarial_full.py` asserts `WAIT_FOR_BACKEND` is "always safe" even
    with 5 tools started.

  Both expectations contradict the v1 safety contract. Recommended correction:
  assert the fail-closed verdicts (`AMBIGUOUS` / not `SAFE`) instead.
- `tests/integration/forensic/forensic_agent.py` has a pre-existing
  `IndentationError`; it is unused (the suite uses `forensic_agent_v2.py`).

---

## Testing & Evidence

```bash
# Full supported suite (384 tests) - includes the 14 forensic integration tests
python -m pytest tests/ -q

# Forensic integration tests only (14 tests, spawns a subprocess test agent)
python -m pytest tests/integration/forensic/test_integration.py -q

# Adversarial safety tests (TOCTOU, fault injection, persistence, crash windows)
python test_adversarial.py
python test_adversarial_full.py
python test_restart_safety_regression.py
python test_persistence_audit.py
python test_crash_windows.py

# Reconnect protocol tests
python test_reconnect_race.py
python test_reconnect_stream_identity.py

# Lifecycle adversarial matrix (20 scenarios)
python test_opencode_adversarial_matrix.py

# All adversarial/regression tests
python test_adversarial.py && python test_adversarial_full.py && python test_restart_safety_regression.py && python test_persistence_audit.py && python test_crash_windows.py && python test_interprocess_locking_windows.py
```

> **Total: ~396 unique test scenarios** covering core logic, adversarial fault injection, persistence crash windows, cross-process locking, reconnect protocol correctness, and safety gate invariants.

### Build & Package
```bash
# Build wheel + sdist
python -m build

# Install from local wheel
pip install dist/agent_guardian-0.1.0-py3-none-any.whl --break-system-packages

# Verify CLI
python -m guardian --help
```

---

## Project Structure

```
C:\Projects\AgentGuardian\
├── assets/                    # Brand assets (logo.svg, social-preview.svg)
├── guardian/
│   ├── __init__.py            # Package exports
│   ├── __main__.py            # Entry point
│   ├── cli.py                 # Command-line interface
│   ├── logging.py             # Secret-redacting logger
│   ├── core/                  # Agent-agnostic safety core
│   │   ├── state_machine.py   # GuardianStateMachine
│   │   ├── safety_gate.py     # Replay safety gate
│   │   ├── failure_classifier.py
│   │   ├── recovery_executor.py
│   │   ├── recovery_ladder.py
│   │   ├── recovery_budget.py
│   │   ├── recovery_planner.py
│   │   ├── ping_monitor.py    # Liveness detection
│   │   ├── adapter_capabilities.py  # AgentAdapter protocol
│   │   └── ...                # Types, config, backoff, budgets
│   ├── hermes/                # Hermes integration
│   ├── goose/                 # Goose Desktop (ACP WebSocket)
│   ├── opencode/              # OpenCode (ACP stdio)
│   └── freebuff/              # Codebuff/FreeBuff (HTTP/WS)
├── tests/
│   ├── test_core.py
│   ├── test_fault_injection.py      # 38 fault scenarios
│   ├── test_hermes.py
│   ├── test_ping_monitor.py
│   ├── test_protocol.py
│   ├── test_recovery_executor.py
│   ├── test_state.py
│   ├── test_utils.py
│   ├── test_adversarial*.py         # Adversarial suites
│   ├── test_reconnect_*.py          # Reconnect protocol tests
│   ├── test_opencode_adversarial_matrix.py
│   └── integration/forensic/
│       └── test_integration.py      # 14 real ACP tests
├── docs/                          # Deep-dive documentation
├── assets/                        # Logo, social preview
├── pyproject.toml
├── CHANGELOG.md
└── README.md
```

---

## Documentation

- **[Recovery Design](docs/RECOVERY_DESIGN.md)** — Safety gate, recovery ladder, budget, TOCTOU protection
- **[Goose Observer](docs/GOOSE_OBSERVER.md)** — ACP WebSocket observer deep dive
- **[Hermes Observer](docs/HERMES_OBSERVER.md)** — In-process callback observer
- **[Hermes Research](docs/HERMES_RESEARCH.md)** — Protocol research notes
- **[Goose Event Validation](docs/GOOSE_EVENT_VALIDATION.md)** — ACP event parsing tests

---

## Roadmap

| Status | Area | Description |
|--------|------|-------------|
| Done | Core safety | State machine, safety gate, failure classifier, recovery executor, budgets, TOCTOU |
| Done | Hermes adapter | In-process observer, simulated recovery |
| Done (partial) | Goose adapter | ACP WebSocket + 14 forensic integration tests. Tool events **not observed at runtime**. |
| Done | OpenCode adapter | ACP stdio, real handshake verified, reconnect identity fixed |
| Interface only | FreeBuff adapter | HTTP/WS interface; runtime **UNVERIFIED** (needs credentials) |
| Done | CLI | observe, watch, doctor, config commands |
| Done | Packaging | wheel/sdist, entry point, Apache-2.0 (`LICENSE` included) |
| Active | Adapter contracts | Shared parameterized test suite across all adapters |
| Active | Real-runtime validation | Induced-disconnect recovery for OpenCode/Goose |
| Planned | Persistence hardening | Crash-window coverage, durability guarantees |
| Done | CI integration | GitHub Actions (`.github/workflows/tests.yml`): tests + build + CLI smoke check |
| Planned | Fault injection framework | Extensible fault injection for all adapters |
| Future | Codebuff runtime | Requires API credentials |
| Future | Hermes real-runtime | Requires Hermes agent installation |

> **No capability is marked "Done" without either runtime evidence or comprehensive mock tests exercising the actual code paths.**

---

## Contributing

We welcome contributions that improve safety, correctness, or evidence quality.

### Development Setup
```bash
git clone https://github.com/Wrybill-env/AgentGuardian.git
cd AgentGuardian
pip install -e ".[dev]"
```

### Running Tests
```bash
# Full supported suite
python -m pytest tests/ -q

# Specific areas
python -m pytest tests/test_core.py -v
python -m pytest tests/test_protocol.py -v
python test_adversarial.py  # Safety regression tests
```

### Agent Adapter Boundaries
- **Core never calls agent-specific code directly** — only via `AgentAdapter` protocol
- **Adapters never modify core safety logic** — they provide capabilities and execute actions
- **New adapters** implement `AgentAdapter` and declare capabilities honestly

### Safety Invariants (must never be weakened)
1. `OBSERVE_ONLY` never executes recovery
2. `UNKNOWN`/`AMBIGUOUS` state blocks automatic replay
3. Tool started + no completion proof = replay blocked
4. `verified_fresh=False` blocks all replay actions
5. Reconnect success != session identity verified (must call `loadSession`)

### Reporting Issues
- **Security issues:** Open a private security advisory on GitHub
- **Bugs:** Include reproduction steps, expected vs actual behavior, and relevant logs
- **Integration claims:** Must include test evidence or runtime verification steps

---

## License

Apache-2.0 — see [LICENSE](LICENSE) for details.

---

<p align="center">
  <img src="assets/logo.svg" alt="Agent Guardian" width="120" height="120"/>
  <br/>
  <strong>Observe -> Classify -> Verify State -> Check Safety -> Recover only when provably safe</strong>
</p>
