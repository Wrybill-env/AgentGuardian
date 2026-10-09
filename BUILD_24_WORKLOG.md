# BUILD 24 WORKLOG

## 2026-10-09 - Baseline Established

**Baseline:** 221/221 tests passing

**Files to inspect:**
- guardian/core/types.py
- guardian/core/state_machine.py
- guardian/goose/protocol.py
- Recovery and safety modules
- Adapter interfaces
- ACP forensic agent and harness

---

## Workstream A: Event Identity and Turn-Boundary Correctness

### Initial Finding
Build #23 added deduplication sets to TurnContext:
- `_seen_tool_call_ids`
- `_completed_tool_call_ids`
- `_failed_tool_call_ids`

Modified `_update_turn_context` to check these sets before incrementing counters.

**Need to verify:**
1. Fresh turn gets fresh deduplication state ✓ (verified in BUILD_23)
2. Event handlers always use correct context
3. No old context references retained

### Files Inspected
- guardian/core/types.py - TurnContext with deduplication sets
- guardian/core/state_machine.py - _update_turn_context with deduplication logic

### Changes Made
- Added deduplication tests to test_core.py (TestTurnContextDeduplication class) - 9 new tests

### Tests Results
- All 9 deduplication tests PASSING
- Full test suite: 230/230 passing (221 original + 9 new)

---

## Workstream B: 12 Disconnect Scenarios as Regression Tests

### Current State
BUILD_23 reported 12 scenarios tested manually. Need to convert to permanent automated tests.

### Next Action
Create integration tests in tests/integration/forensic/ using the real forensic agent and ACP transport.

---

## Workstream C: Safety-State Audit

### Need to Review
- State machine transitions
- Safety gate logic
- Recovery decision boundary
- is_safe_to_replay() implementation

### Initial Findings
- is_safe_to_replay() checks: `not has_assistant_output() and not has_tool_activity()`
- has_tool_activity() returns `tool_calls_started > 0`
- This means ANY tool start (even completed) blocks replay - CORRECT behavior
- Safety gate properly checks turn context for replay safety

---

## Workstream D: Hermes Reliability Reuse Audit

### Need to Inspect
- Hermes ACP adapter source
- Available Hermes documentation

---

## Workstream E: Generic Reliability Improvement

### Candidates
1. Failure classification enhancement
2. Bounded retry/backoff
3. Stream liveness detection
4. Recovery orchestration

---

## Workstream F: Failure Injection

### Need to Add
- Deterministic fault injection at narrow boundaries
- Before/after side effect cases

---

## Workstream G: Independent Review

### After Implementation
- Adversarial challenge of deduplication
- Safety boundary verification
- Test coverage validation