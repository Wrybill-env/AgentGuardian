"""
Cross-process recovery lease for Strixora.

Ensures that at most one Guardian process executes a recovery action for a
given session at a time. Backed by an OS-level file lock, so:

  * Ownership  : one lock file per session, under ``lease_dir``.
  * Acquisition: non-blocking single attempt (fail closed if already held).
  * Expiry     : implicit — the OS releases the lock when the holder process
                 exits, so a crashed holder cannot leave a permanent lease.
  * Release    : explicit by the holder (see :meth:`release`).

This is intentionally minimal: it protects the interval around an external
recovery action. It does NOT make the external action atomic; it only prevents
two Guardian processes from *initiating* recovery for the same session
concurrently.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .file_lock import FileLock


_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


class RecoveryLease:
    """Per-session cross-process recovery lease."""

    def __init__(self, lease_dir: str):
        self.lease_dir = Path(lease_dir).expanduser()

    def _lock_path(self, session_id: str) -> Path:
        # Sanitize the session id so it is a safe filename across platforms.
        token = _UNSAFE_CHARS.sub("_", session_id or "default")[:128] or "default"
        return self.lease_dir / f"{token}.recovery.lock"

    def try_acquire(self, session_id: str) -> Optional[FileLock]:
        """
        Non-blocking acquisition.

        Returns the held :class:`FileLock` if the lease was acquired, or
        ``None`` if another holder currently owns the lease for this session.
        """
        try:
            self.lease_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Cannot create the lease directory -> fail closed (deny recovery).
            return None

        try:
            lock = FileLock(str(self._lock_path(session_id)), timeout=0.0)
            if lock.acquire():
                return lock
        except OSError:
            # Lock file could not be opened/locked (permissions, path length, ...)
            # -> fail closed: deny recovery rather than proceed unprotected.
            return None
        return None

    @staticmethod
    def release(lock: Optional[FileLock]) -> None:
        """Release a lease previously returned by :meth:`try_acquire`."""
        if lock is not None:
            lock.release()
