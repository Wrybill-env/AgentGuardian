# 🔨 BUILD MODE — AGENT GUARDIAN BUILD #25

## OPERATION: AUTONOMOUS RELIABILITY ENGINEERING SPRINT — FINAL REPORT

**Project Root:** `C:\Projects\AgentGuardian`  
**Date:** 2026-10-10  
**Build Status:** COMPLETE — Retry eligibility separated from replay safety in failure classification

---

## Executive Summary

Build #25 successfully **separated retry eligibility from replay safety** in Guardian's failure classification system. This resolves a critical design flaw where a single `replay_safe` field was being used for both "can we technically try again?" and "is it safe to re-execute the prompt?"

**Key Achievement:** The failure classifier now correctly distinguishes:
- **Technical retry eligibility** (`retryable`) — network ok, auth ok, etc.
- **Replay safety** (`replay_safe`) — no side effects observed
- **Recovery action** (`recommended_action`) — determined by BOTH factors

This resolves the safety invariant violation where a transient network error with tools already started would incorrectly block RECONNECT_ACP (which is safe since it doesn't replay the prompt).

---

## Baseline

| Metric | Value |
|--------|-------|
| Test Suite (Unit) | **231 / 231 passing** (230 original + 1 new) |
| Test Suite (Integration) | **14 / 14 passing** |
| Guardian Modified Files | **3** (`failure_classifier.py`, `test_core.py`, `BUILD_25_WORKLOG.md`) |
| AUTO_RECOVER | **OFF** (0 real recovery actions executed) |

---

## Root Cause

**Original Problem:** In `failure_classifier.py:543-546`, the classifier's `replay_safe` field was overloaded:
1. **Retry eligibility** — "Can we technically try again?" (network ok, auth ok)
2. **Replay safety** — "Is it safe to re-execute the prompt?" (no side effects)

**Consequence:** A connection reset with tools already started would have `replay_safe=False` and incorrectly recommend `NO_SAFE_RECOVERY` instead of `RECONNECT_ACP` (which is safe since it doesn't replay the prompt).

---

## Changes Made

### 1. `guardian/core/failure_classifier.py`

**Core Logic Change:** Separated technical retry eligibility from replay safety:

```python
# Determine technical retry eligibility (separate from replay safety)
technically_retryable = best_match.retryable

# Replay safety considers turn context (side effects observed)
replay_safe = best_match.replay_safe
if turn_context and not turn_context.is_safe_to_replay():
    replay_safe = False

# Determine action based on BOTH technical feasibility AND safety
recommended_action = best_match.recommended_action
if not technically_retryable:
    # Not technically retryable (auth failure, config error)
    # Use pattern's recommended action (e.g., HUMAN_INTERVENTION)
    pass
elif not replay_safe and recommended_action in (
    RecoveryAction.WAIT_AND_RETRY,
    RecoveryAction.CANCEL_AND_REPLAY,
    RecoveryAction.RELOAD_SESSION,
    RecoveryAction.FORK_SESSION,
):
    # Technically retryable but not safe to replay
    recommended_action = RecoveryAction.NO_SAFE_RECOVERY
```

**Result:**
- `retryable` = technical feasibility (pattern-based)
- `replay_safe` = safety (turn-context adjusted)
- `recommended_action` = determined by BOTH

### 2. `tests/test_core.py`

**New Tests Added:**
- `test_retryable_but_not_replay_safe_separation`: Connection reset with tools started → `retryable=True`, `replay_safe=False` → `RECONNECT_ACP`
- `test_rate_limit_retryable_and_replay_safe_when_no_tools`: Rate limit with no tools → `retryable=True`, `replay_safe=True` → `WAIT_AND_RETRY`

**Updated Tests:**
- `test_replay_safe_blocked_by_turn_context` → renamed to `test_retryable_but_not_replay_safe_separation` with corrected expectations

---

## Verification Results

### Safety Invariant Maintained
> **TOOL STARTED + COMPLETION NOT PROVEN = AUTOMATIC REPLAY BLOCKED**

| Scenario | retryable | replay_safe | Action | Correct? |
|----------|-----------|-------------|--------|----------|
| Connection reset, tools started | True | False | RECONNECT_ACP | ✅ |
| Rate limit, no tools | True | True | WAIT_AND_RETRY | ✅ |
| Rate limit, tools started | True | False | NO_SAFE_RECOVERY | ✅ |
| Auth failure | False | False | HUMAN_INTERVENTION | ✅ |
| Tool timeout | False | False | HUMAN_INTERVENTION | ✅ |

### Key Distinction Clarified
| Action | Technically Retryable | Replay Safe | Use Case |
|--------|----------------------|-------------|----------|
| **RECONNECT_ACP** | ✅ | N/A (no prompt replay) | Network issues, tools may have started |
| **WAIT_AND_RETRY** | ✅ | ✅ | Rate limits, transient errors, no side effects |
| **NO_SAFE_RECOVERY** | ❌ OR ✅ + ❌ | ❌ | Auth failures, tool timeouts, or prompt replay unsafe |
| **HUMAN_INTERVENTION** | ❌ | ❌ | Auth failures, config errors, cancellations |

---

## Test Results

| Test Suite | Tests | Status |
|------------|-------|--------|
| Unit (core) | 231 | ✅ PASS |
| Integration (forensic) | 14 | ✅ PASS |
| **Total** | **245** | ✅ **PASS** |

---

## Files Changed

| File | Change |
|------|--------|
| `guardian/core/failure_classifier.py` | Separated retry eligibility from replay safety (19 lines changed) |
| `tests/test_core.py` | Added separation tests, fixed expectations (35 lines changed) |
| `BUILD_25_WORKLOG.md` | Created worklog |
| `BUILD_25_REPORT.md` | This report |

---

## Cleanup Verification

| Item | Status |
|------|--------|
| Background processes | ✅ All terminated |
| Test processes | ✅ None running |
| Modified source files | 2 (`failure_classifier.py`, `test_core.py`) |
| New test file | 1 (`BUILD_25_WORKLOG.md`) |
| Secrets exposed | ❌ NO |
| AUTO_RECOVER | OFF |

---

## Remaining Risks & Blockers

1. **Retry execution not yet automated** — `BackoffStrategy` and `RecoveryExecutor` need integration with `technically_retryable` field
2. **Provider fallback** — Requires multi-provider agent adapter (not built)
3. **Goose ACP integration** — Still blocked by provider tool discard (Build #14)
4. **Hermes ACP stdio** — Blocked by uv environment isolation

---

## Next Recommended Build (Build #26)

1. **Primary**: Integrate `technically_retryable` with `BackoffStrategy` and `RecoveryExecutor` for automated retry execution
2. **Secondary**: Add ping-based liveness detection (from Hermes pattern) to observer
3. **Alternative**: Build Goose ACP adapter with working provider tool support

---

## Final Safety Invariant

```
TOOL MAY HAVE EXECUTED
      ↓
COMPLETION NOT PROVEN
      ↓
STATE UNKNOWN
      ↓
AUTOMATIC REPLAY UNSAFE
```

**AUTO_RECOVER = OFF** maintained throughout Build #25.

---

## Shutdown

All work complete. Full test suite passing (231 unit + 14 integration). Report saved. No active experimental processes. Safe to shutdown.