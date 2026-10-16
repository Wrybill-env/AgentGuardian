# Changelog

All notable changes to Agent Guardian will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-16

**First public release.**

Supported: observation and state tracking, failure classification, diagnostics,
and dry-run recovery planning (`OBSERVE_ONLY` default, `DRY_RUN`).

**Automatic recovery (`AUTO_RECOVER`) is experimental and restricted.** It
requires an explicit opt-in and a configured cross-process recovery lease, and
with no adapter able to prove corroborated freshness, all replay/reconnect/
reload/fork actions remain blocked. See the README section
"Limitations & Not Yet Supported".

### Security
- **Single safety policy.** `HermesSafetyGate` now delegates to the core safety
  gate and returns the more restrictive verdict. It previously had no freshness
  check and could authorise prompt replay that the core gate denies.
- **Reconnect requires proven freshness.** `RECONNECT_ACP` was added to the
  `replay_actions` set — a non-empty session id is no longer sufficient.
- **`WAIT_FOR_BACKEND` is not unconditionally safe.** It is blocked when a tool
  may have executed, because the shipped handler reconnects and abandons the
  turn. Backend reachability is no longer treated as proof of session recovery.
- **`AUTO_RECOVER` requires cross-process exclusion.** The state machine refuses
  to start automatic recovery when no `RecoveryLease` is configured.
- **Persisted state no longer grants replay freshness.** A snapshot cannot prove
  the agent is still at that state or that no tool ran since.
- **Duplicate recovery is refused.** An in-flight guard prevents repeated
  failure events from starting a second recovery.

### Added
- Generic safety core: state machine, failure classifier, safety gate, recovery
  executor, recovery budget, recovery ladder, ping monitor
- Failure taxonomy with retry-eligibility separated from replay safety
- Freshness evidence model (`guardian/core/freshness.py`): persisted state is
  never sufficient; only independently corroborated evidence may authorise replay
- Cross-process recovery lease (`guardian/core/recovery_lease.py`)
- Hermes adapter (in-process callback observer, simulated recovery)
- Goose adapter (ACP WebSocket)
- OpenCode adapter (ACP stdio subprocess, protocol v1)
- FreeBuff/Codebuff adapter (HTTP/WebSocket; interface only — runtime unverified)
- CLI with `observe`, `watch`, `doctor` and `config` commands
- Ping-based liveness monitoring
- Automated retry with exponential backoff
- Forensic integration test suite (14 tests, subprocess ACP test agent)
- Packaging with wheel/sdist support, CLI entry point, Apache-2.0 `LICENSE`
- Secret-redacting logger
- 384 automated tests

### Changed
- OpenCode adapter uses correct ACP event types (`agent_message_chunk`,
  `tool_call`, `cancel`, `loadSession`, `forkSession`)
- FreeBuff adapter capabilities corrected to match the Codebuff SDK
  (no session load/fork/reattach)
- Documentation updated with explicit evidence levels, limitations, and the
  accurate scope of each safety invariant

### Fixed
- OpenCode ACP subprocess on Windows (`.cmd` handling with `shell=True`)
- Recovery action implementations for OpenCode (proper ACP cancel, loadSession, forkSession)
- Capability declarations now accurately reflect verified vs simulated behavior

### Safety
- `OBSERVE_ONLY` default safety mode
- `AUTO_RECOVER` requires explicit opt-in **and** a cross-process recovery lease
- `UNKNOWN` execution state blocks automatic recovery
- Tool execution without proven completion blocks replay
- TOCTOU protection via state generation comparison at planning and execution
- Replay/reconnect/reload/fork require proven freshness (blocked today)

### Not validated
- No real-agent end-to-end recovery has been performed. All adapter evidence is
  source inspection plus mock and subprocess tests.

## [0.0.1] - 2026-10-02

### Added
- Initial Goose ACP observer prototype
- Basic protocol parsing and state tracking
- Read-only WebSocket observation
- Secret redaction in logs
- Basic failure detection (stale streams, disconnects, rate limits)