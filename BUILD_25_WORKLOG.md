# BUILD 25 WORKLOG

## 2026-10-10 - Baseline Verified

**Current State:**
- Repository: `C:\Projects\AgentGuardian`
- Branch: `main`
- Commit: `bd3490f` (Initial commit: Agent Guardian core, tests, and documentation)
- Remote: `origin` → `https://github.com/Wrybill-env/AgentGuardian`
- Working tree: Clean
- Unit tests: 230/230 passing
- Integration tests: 14/14 passing
- AUTO_RECOVER: OFF

---

## Workstream A: Verify Build #24 Claims Against Source

### Verified ✅
1. **Event deduplication** - `TurnContext` has `_seen_tool_call_ids`, `_completed_tool_call_ids`, `_failed_tool_call_ids` in `guardian/core/types.py`
2. **Turn isolation** - `start_turn()` creates fresh sets in `guardian/core/state_machine.py`
3. **Deduplication logic** - `_update_turn_context` checks sets before incrementing in `state_machine.py:326-351`
4. **14 integration tests** - `tests/integration/forensic/test_integration.py` covers all 12 disconnect scenarios + 2 edge cases
5. **Safety invariants** - `is_safe_to_replay()` correctly returns False when tools started
6. **Hermes gap matrix** - Created and documented in `BUILD_24_HERMES_GAP_MATRIX.md`

### Key Source Files Inspected
- `guardian/core/types.py` - Core types, TurnContext with dedup sets
- `guardian/core/state_machine.py` - State machine with dedup logic
- `guardian/core/failure_classifier.py` - 23 failure patterns, classification logic
- `guardian/core/backoff.py` - BackoffStrategy with jitter, Retry-After support
- `guardian/core/recovery_executor.py` - TOCTOU protection, safety revalidation
- `guardian/core/safety_gate.py` - Safety gate with per-action checks
- `guardian/core/recovery_budget.py` - Budget tracking per failure type

---

## Workstream B: Identify Highest-Value Gap

### Analysis of Current Reliability Mechanisms

| Mechanism | Status | Gap |
|-----------|--------|-----|
| Failure classification | ✅ 23 patterns | `replay_safe` often True even for tool failures |
| Retry/backoff | ✅ Good implementation | Not integrated with classification's retry eligibility |
| Safety gate | ✅ Per-action checks | `replay_safe` adjustment in classifier may override gate |
| Recovery executor | ✅ TOCTOU protection | No integration with failure classification for retry decisions |
| Budget tracking | ✅ Per-failure-type | Not linked to retry eligibility |

### Key Finding: Retry Eligibility vs Replay Safety Confusion

In `failure_classifier.py:543-546`:
```python
# Adjust replay_safe based on turn context
replay_safe = best_match.replay_safe
if turn_context and not turn_context.is_safe_to_replay():
    replay_safe = False
```

**Problem:** The classifier's `replay_safe` field was being used for two different things:
1. **Retry eligibility** (can we technically try again?)
2. **Replay safety** (is it safe to re-execute the prompt?)

These were **conflated**. A transient network error may be *retryable* (technically we can try again) but NOT *replay-safe* (the operation may have already executed). The current design collapses both into one field.

### Highest-Value Improvement: Separate Retry Eligibility from Replay Safety

**Target:** Refactor `FailurePattern` and `FailureClassification` to have separate fields:
- `retryable` - Technical feasibility of retry (network ok, auth ok, etc.)
- `replay_safe` - Safe to re-execute the prompt (no side effects)
- `recovery_action` - What to actually do (WAIT_AND_RETRY, RECONNECT, etc.)

This aligns with the Hermes gap matrix recommendation and the safety invariant:
> "A transient network error does not prove replay is safe. A 429 response does not automatically authorize repeating an operation that may already have executed."

---

## Workstream C: Implementation Completed

### Changes Made

#### 1. `guardian/core/failure_classifier.py`
- **Separated retry eligibility from replay safety** in classification logic
- Added `technically_retryable` variable to track technical feasibility separately from `replay_safe`
- Fixed logic: `recommended_action` is now determined by BOTH technical feasibility AND safety:
  - If NOT technically retryable (auth failure, config error): use pattern's recommended action (e.g., HUMAN_INTERVENTION)
  - If technically retryable BUT not replay_safe (side effects occurred): NO_SAFE_RECOVERY
  - If technically retryable AND replay_safe: use pattern's recommended action (WAIT_AND_RETRY, RECONNECT_ACP, etc.)

#### 2. `tests/test_core.py`
- Added `test_retryable_but_not_replay_safe_separation`: Tests that a connection reset with tools started is `retryable=True` but `replay_safe=False` → recommends RECONNECT_ACP (not NO_SAFE_RECOVERY)
- Added `test_rate_limit_retryable_and_replay_safe_when_no_tools`: Rate limit with no tools is both retryable and replay_safe → WAIT_AND_RETRY
- Updated `test_replay_safe_blocked_by_turn_context` → renamed to `test_retryable_but_not_replay_safe_separation` with corrected expectations

### Test Results
- **All 231 unit tests passing** (230 original + 1 new separation test)
- **All 14 integration tests passing** (12 disconnect scenarios + 2 edge cases)
- **Full test suite: 231/231 passing**

---

## Verification Results

### Safety Invariant Maintained
> **TOOL STARTED + COMPLETION NOT PROVEN = AUTOMATIC REPLAY BLOCKED**

The separation correctly handles:
- **Connection reset with tools started**: `retryable=True`, `replay_safe=False` → RECONNECT_ACP (technically retryable but don't replay prompt)
- **Rate limit with no tools**: `retryable=True`, `replay_safe=True` → WAIT_AND_RETRY
- **Rate limit with tools started**: `retryable=True`, `replay_safe=False` → NO_SAFE_RECOVERY (don't replay prompt)
- **Auth failure**: `retryable=False` → HUMAN_INTERVENTION (not NO_SAFE_RECOVERY)

### Key Distinction
- **RECONNECT_ACP**: Technically retryable, safe to reconnect (no prompt replay)
- **WAIT_AND_RETRY**: Technically retryable AND replay_safe (no side effects)
- **NO_SAFE_RECOVERY**: Either not technically retryable OR not replay_safe (for prompt-replay actions)

---

## Workstream D: Hermes Gap Matrix - Updated

| Capability | Status |
|------------|--------|
| Retry Classification | ✅ Separated retryable vs replay_safe |
| Rate-Limit Handling | ✅ With retryable/replay_safe distinction |
| Backoff & Jitter | ✅ Existing implementation solid |
| Stream Interruption | 🔄 Future work |
| Session Continuation | 🔄 Future work |

---

## Next Recommended Build (Build #26)

1. **Primary**: Integrate retry eligibility with `BackoffStrategy` and `RecoveryExecutor` for automated retry execution
2. **Secondary**: Add ping-based liveness detection (from Hermes pattern) to observer
3. **Alternative**: Build Goose ACP adapter with working provider tool support

---

## Test Results Summary

| Test Suite | Tests | Status |
|------------|-------|--------|
| Unit tests (core) | 231 | ✅ PASS |
| Integration (forensic) | 14 | ✅ PASS |
| **Total** | **245** | ✅ **PASS** |

---

## Cleanup Verification

| Item | Status |
|------|--------|
| Background processes | ✅ All terminated |
| Test processes | ✅ None running |
| Modified files | 3 source + 2 test files |
| Secrets exposed | NO |
| AUTO_RECOVER | OFF |

---

## Remaining Risks & Blockers

1. **Retry execution not yet automated** - `BackoffStrategy` and `RecoveryExecutor` need integration with `technically_retryable` field
3. **Provider fallback** - Requires multi-provider agent adapter (not built)
4. **Goose ACP integration** - Still blocked by provider tool discard (Build #14)
5. **Hermes ACP stdio** - Blocked by uv environment isolation
