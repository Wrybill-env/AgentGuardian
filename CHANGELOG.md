# Changelog

All notable changes to Agent Guardian will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- OpenCode ACP adapter with proper protocol v1 implementation
- FreeBuff/Codebuff adapter with accurate capability declarations
- Packaging with `pyproject.toml` and CLI entry point
- Forensic integration test suite (14 tests)
- Ping-based liveness monitoring
- Automated retry with exponential backoff

### Changed
- OpenCode adapter now uses correct ACP event types (`agent_message_chunk`, `tool_call`, `cancel`, `loadSession`, `forkSession`)
- FreeBuff adapter capabilities corrected to match Codebuff SDK (no session load/fork/reattach)
- Safety invariants documentation updated with evidence levels

### Fixed
- OpenCode ACP subprocess on Windows (`.cmd` handling with `shell=True`)
- Recovery action implementations for OpenCode (proper ACP cancel, loadSession, forkSession)
- Capability declarations now accurately reflect verified vs simulated behavior

## [0.1.0] - 2026-10-11

### Added
- Generic safety core: state machine, failure classifier, safety gate, recovery executor, recovery budget, recovery ladder, ping monitor
- Hermes adapter (in-process callback observer)
- Goose adapter (ACP WebSocket with forensic integration tests)
- OpenCode adapter (ACP stdio subprocess)
- FreeBuff adapter (HTTP/WebSocket)
- CLI with observe, watch, doctor, config commands
- 256 unit tests covering core components, fault injection, and adapters
- 14 forensic integration tests against real Goose ACP
- Secret-redacting logger
- Packaging with wheel/sdist support

### Safety
- `OBSERVE_ONLY` default safety mode
- `AUTO_RECOVER` requires explicit opt-in
- `UNKNOWN` execution state blocks automatic recovery
- Tool execution without proven completion blocks replay
- TOCTOU protection via state generation comparison

## [0.0.1] - 2026-10-02

### Added
- Initial Goose ACP observer prototype
- Basic protocol parsing and state tracking
- Read-only WebSocket observation
- Secret redaction in logs
- Basic failure detection (stale streams, disconnects, rate limits)