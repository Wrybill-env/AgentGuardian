"""
True two-process recovery-lease tests.

These spawn *independent OS processes* (not threads, not same-process locks) and
prove the cross-process exclusion actually holds:

  1. Process A holds the lease.
  2. Process B cannot acquire it.
  3. Process B cannot enter the protected execution section.
  4. The protected adapter operation is NOT called by B.
  5. After A releases, B can acquire and execute.
  6. If the owning process crashes, the OS releases the lock.
  7. An unusable lease directory blocks recovery (see test_recovery_hardening).

All waits are bounded; children are killed reliably in finally blocks.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from guardian.core.recovery_lease import RecoveryLease

REPO_ROOT = Path(__file__).resolve().parent.parent

# Child worker. argv: repo_root lease_dir session ready_file control_file out_file mode
_WORKER = r'''
import asyncio, json, os, sys, time
sys.path.insert(0, sys.argv[1])
from guardian.core.adapter_capabilities import create_capabilities
from guardian.core.recovery_budget import RecoveryBudgetManager
from guardian.core.recovery_executor import RecoveryExecutor
from guardian.core.recovery_lease import RecoveryLease
from guardian.core.recovery_planner import RecoveryPlan
from guardian.core.safety_gate import SafetyGate
from guardian.core.types import (AdapterCapability, FailureClassification, FailureType,
    RecoveryAction, SafetyMode, SafetyVerdict, TurnContext, TurnPhase)

lease_dir, session, ready_file, control_file, out_file, mode = sys.argv[2:8]
lease = RecoveryLease(lease_dir)


class A:
    def __init__(self):
        self.capabilities = create_capabilities("w", "w", {c: True for c in AdapterCapability})
        self.calls = []

    async def execute_recovery_action(self, action, context):
        self.calls.append(action.value)
        return True

    async def verify_recovery(self, action, context):
        return (True, {})


def _plan():
    return RecoveryPlan(
        plan_id="p",
        failure_classification=FailureClassification(
            failure_type=FailureType.RATE_LIMIT, retryable=True, replay_safe=True,
            requires_human=False, recommended_action=RecoveryAction.WAIT_AND_RETRY, confidence=0.9),
        session_id=session, turn_id="t",
        safety_verdict=SafetyVerdict.SAFE, recommended_action=RecoveryAction.WAIT_AND_RETRY,
        cycle_number=1, max_cycles=5, can_continue=True,
        state_snapshot={"assistant_chunks": 0, "tools_started": 0, "partial_tool_names": [],
                        "turn_phase": TurnPhase.USER_MESSAGE_SENT.value, "stop_reason": None,
                        "session_id": session, "state_generation": 0})


if mode == "crash":
    lock = lease.try_acquire(session)
    open(ready_file, "w").write("1" if lock else "0")
    if lock is None:
        sys.exit(3)
    time.sleep(120)          # parent kills us; OS must release the lock
    sys.exit(0)

if mode == "hold":
    lock = lease.try_acquire(session)
    open(ready_file, "w").write("1" if lock else "0")
    if lock is None:
        sys.exit(3)
    deadline = time.time() + 30
    while time.time() < deadline and not os.path.exists(control_file):
        time.sleep(0.05)
    lease.release(lock)
    sys.exit(0)

async def _main():
    adapter = A()
    ex = RecoveryExecutor(
        safety_gate=SafetyGate(SafetyMode.AUTO_RECOVER),
        budget_manager=RecoveryBudgetManager(),
        default_mode=SafetyMode.AUTO_RECOVER,
        recovery_lease=lease,
    )
    turn = TurnContext(session_id=session, phase=TurnPhase.USER_MESSAGE_SENT)
    r = await ex.execute_plan(_plan(), adapter, turn, {"partial_tool_names": []})
    json.dump({"result": r.result.value, "success": r.success,
               "adapter_called": bool(adapter.calls)}, open(out_file, "w"))

asyncio.run(_main())
'''


def _worker_path(tmp_path: Path) -> Path:
    p = tmp_path / "lease_worker.py"
    p.write_text(_WORKER, encoding="utf-8")
    return p


def _spawn(tmp_path, lease_dir, session, mode, timeout=15.0):
    worker = _worker_path(tmp_path)
    ready = tmp_path / f"ready_{mode}.txt"
    control = tmp_path / f"control_{mode}.txt"
    out = tmp_path / f"out_{mode}.json"
    proc = subprocess.Popen(
        [sys.executable, str(worker), str(REPO_ROOT), lease_dir, session,
         str(ready), str(control), str(out), mode],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    deadline = time.time() + timeout
    while time.time() < deadline and not ready.exists():
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    return proc, ready, control, out


def _kill(proc):
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def _run_exec_worker(tmp_path, lease_dir, session, timeout=30.0):
    """Run the child executor once (mode=exec); return (returncode, parsed_json)."""
    worker = _worker_path(tmp_path)
    out = tmp_path / f"exec_{time.time_ns()}.json"
    proc = subprocess.run(
        [sys.executable, str(worker), str(REPO_ROOT), lease_dir, session,
         str(tmp_path / "r"), str(tmp_path / "c"), str(out), "exec"],
        capture_output=True, timeout=timeout,
    )
    data = json.loads(out.read_text()) if out.exists() else None
    return proc.returncode, data


def test_second_process_cannot_enter_protected_section(tmp_path):
    lease_dir = str(tmp_path / "leases")
    session = "sess_mp_exec"
    parent = RecoveryLease(lease_dir)

    held = parent.try_acquire(session)
    assert held is not None, "parent must acquire the lease"
    try:
        rc, data = _run_exec_worker(tmp_path, lease_dir, session)
        assert rc == 0 and data is not None
        assert data["result"] == "blocked", data
        assert data["success"] is False
        # Requirement 4: the protected adapter operation was NOT called.
        assert data["adapter_called"] is False, data
    finally:
        parent.release(held)

    # Requirement 5: after release, the (new) process can acquire and execute.
    rc2, data2 = _run_exec_worker(tmp_path, lease_dir, session)
    assert rc2 == 0 and data2 is not None
    assert data2["result"] == "verification_passed", data2
    assert data2["adapter_called"] is True


def test_lease_released_when_owner_crashes(tmp_path):
    lease_dir = str(tmp_path / "leases")
    session = "sess_mp_crash"
    proc, ready, _control, _out = _spawn(tmp_path, lease_dir, session, "crash")
    try:
        assert ready.exists() and ready.read_text() == "1", "owner failed to acquire"

        parent = RecoveryLease(lease_dir)
        assert parent.try_acquire(session) is None, "held lease must block another process"
    finally:
        _kill(proc)  # simulate a crash: no graceful release

    # The OS must release the lock once the owning process dies. Allow a short,
    # bounded settle window before declaring failure.
    parent = RecoveryLease(lease_dir)
    acquired = None
    deadline = time.time() + 10
    while time.time() < deadline and acquired is None:
        acquired = parent.try_acquire(session)
        if acquired is None:
            time.sleep(0.1)
    assert acquired is not None, "crashed owner must not leave the lease held forever"
    parent.release(acquired)


def test_graceful_release_frees_lease_for_other_process(tmp_path):
    lease_dir = str(tmp_path / "leases")
    session = "sess_mp_hold"
    proc, ready, control, _out = _spawn(tmp_path, lease_dir, session, "hold")
    try:
        assert ready.exists() and ready.read_text() == "1"

        parent = RecoveryLease(lease_dir)
        assert parent.try_acquire(session) is None
        control.write_text("go")           # owner releases gracefully and exits
        proc.wait(timeout=15)
        assert proc.returncode == 0

        acquired = parent.try_acquire(session)
        assert acquired is not None
        parent.release(acquired)
    finally:
        _kill(proc)


def test_different_sessions_are_not_serialized(tmp_path):
    """Two distinct sessions must not block each other."""
    lease_dir = str(tmp_path / "leases")
    a = RecoveryLease(lease_dir)
    b = RecoveryLease(lease_dir)
    la = a.try_acquire("sess_alpha")
    try:
        assert la is not None
        lb = b.try_acquire("sess_beta")
        assert lb is not None, "different sessions must not be serialized"
        b.release(lb)
    finally:
        a.release(la)
