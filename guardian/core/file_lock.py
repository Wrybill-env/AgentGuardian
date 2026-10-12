"""
Cross-process file locking for Windows and POSIX.
"""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False
    msvcrt = None  # type: ignore

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False
    fcntl = None  # type: ignore


class FileLock:
    """
    Cross-process file lock using platform-specific mechanisms.
    
    On POSIX: uses fcntl.flock (advisory lock)
    On Windows: uses msvcrt.locking (mandatory lock)
    
    Usage:
        lock = FileLock("/path/to/state.json.lock")
        with lock:
            # critical section
            pass
    """
    
    def __init__(self, lock_path: str, timeout: float = 10.0):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self._fd: Optional[int] = None
        self._acquired = False
    
    def acquire(self) -> bool:
            """Acquire the lock. Returns True if successful."""
            if self._acquired:
                return True
           
            # Ensure lock file exists
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        
            # Open lock file
            flags = os.O_CREAT | os.O_RDWR
            if HAS_MSVCRT:
                self._fd = os.open(str(self.lock_path), flags)
            else:
                self._fd = os.open(str(self.lock_path), flags)
        
            try:
                if HAS_MSVCRT:
                    # Windows: use msvcrt.locking (mandatory)
                    import time
                    start = time.time()
                    while time.time() - start < self.timeout:
                        try:
                            msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                            self._acquired = True
                            return True
                        except OSError:
                            time.sleep(0.1)
                    # Failed to acquire - close the file descriptor
                    if self._fd is not None:
                        os.close(self._fd)
                        self._fd = None
                    return False
                else:
                    # POSIX: use fcntl.flock (advisory)
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
                    self._acquired = True
                    return True
            except (OSError, IOError):
                if self._fd is not None:
                    os.close(self._fd)
                    self._fd = None
                return False
    
    def release(self) -> None:
        """Release the lock."""
        if not self._acquired or self._fd is None:
            return
            
        try:
            if HAS_MSVCRT:
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]
        except (OSError, IOError):
            pass
        finally:
            os.close(self._fd)
            self._fd = None
            self._acquired = False
    
    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Could not acquire lock on {self.lock_path} within timeout")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False
    
    def __del__(self):
        self.release()


@contextmanager
def file_lock(lock_path: str, timeout: float = 10.0):
    """Context manager for file locking."""
    lock = FileLock(lock_path, timeout)
    if not lock.acquire():
        raise RuntimeError(f"Could not acquire lock on {lock_path} within timeout")
    try:
        yield
    finally:
        lock.release()