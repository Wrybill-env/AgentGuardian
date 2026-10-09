"""
Recovery budget tracking for Agent Guardian.

Tracks recovery attempts, cycles, and budgets to prevent infinite pathological loops
while allowing persistent recovery through genuinely recoverable failures.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import time


class BudgetExhaustedReason(Enum):
    """Reasons why recovery budget was exhausted."""
    MAX_ATTEMPTS = "max_attempts"
    MAX_CYCLES = "max_cycles"
    MAX_TOTAL_TIME = "max_total_time"
    MAX_BACKOFF_DELAY = "max_backoff_delay"
    PERMANENT_FAILURE = "permanent_failure"
    SAFETY_BLOCKED = "safety_blocked"
    CANCELLED = "cancelled"


@dataclass
class RecoveryBudgetConfig:
    """Configuration for recovery budget."""
    # Hard limits
    max_total_attempts: int = 10              # Absolute max recovery attempts
    max_recovery_cycles: int = 5              # Max auto-recovery cycles (Hermes-style ladder)
    max_total_recovery_time: float = 300.0    # Max total time in recovery (seconds)
    max_single_backoff: float = 60.0          # Cap on single backoff delay
    max_retry_after: float = 120.0            # Cap on Retry-After header (match Hermes)
    
    # Behavior
    indefinite_retry: bool = False            # If True, ignore max_attempts (use with caution)
    respect_retry_after: bool = True          # Honor Retry-After headers
    safety_mode_strict: bool = True           # Block unsafe actions even if budget remains
    
    # Per-failure-type overrides (optional)
    failure_type_limits: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.max_total_attempts < 0:
            raise ValueError("max_total_attempts must be >= 0")
        if self.max_recovery_cycles < 0:
            raise ValueError("max_recovery_cycles must be >= 0")
        if self.max_total_recovery_time <= 0:
            raise ValueError("max_total_recovery_time must be > 0")
        if self.max_single_backoff <= 0:
            raise ValueError("max_single_backoff must be > 0")


@dataclass
class RecoveryAttemptRecord:
    """Record of a single recovery attempt."""
    attempt_number: int
    cycle_number: int
    failure_type: str
    action: str
    timestamp: float
    delay_seconds: float
    success: bool
    safety_verdict: str
    blocking_factors: List[str] = field(default_factory=list)
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryBudget:
    """
    Tracks recovery budget for a failure sequence.
    
    A recovery sequence starts when a failure is first detected and ends when:
    - Recovery succeeds
    - Budget is exhausted
    - Safety blocks recovery
    - User cancels
    - Permanent failure detected
    """
    config: RecoveryBudgetConfig
    failure_type: str
    
    # Attempt tracking
    total_attempts: int = 0
    recovery_cycles: int = 0
    attempt_history: List[RecoveryAttemptRecord] = field(default_factory=list)
    
    # Timing
    sequence_start_time: float = field(default_factory=time.time)
    last_attempt_time: Optional[float] = None
    total_waited_seconds: float = 0.0
    
    # State
    exhausted: bool = False
    exhausted_reason: Optional[BudgetExhaustedReason] = None
    safety_blocked: bool = False
    
    # Current backoff state (for coordination with BackoffStrategy)
    current_backoff_delay: float = 0.0
    
    def record_attempt(
        self,
        cycle_number: int,
        action: str,
        delay_seconds: float,
        success: bool,
        safety_verdict: str,
        blocking_factors: List[str] = None,
        error: str = None,
        details: Dict[str, Any] = None,
    ):
        """Record a recovery attempt."""
        self.total_attempts += 1
        if cycle_number > self.recovery_cycles:
            self.recovery_cycles = cycle_number
        
        self.last_attempt_time = time.time()
        self.total_waited_seconds += delay_seconds
        self.current_backoff_delay = delay_seconds
        
        attempt = RecoveryAttemptRecord(
            attempt_number=self.total_attempts,
            cycle_number=cycle_number,
            failure_type=self.failure_type,
            action=action,
            timestamp=self.last_attempt_time,
            delay_seconds=delay_seconds,
            success=success,
            safety_verdict=safety_verdict,
            blocking_factors=blocking_factors or [],
            error=error,
            details=details or {},
        )
        self.attempt_history.append(attempt)
        
        # Check if budget exhausted
        self._check_exhaustion()
    
    def _check_exhaustion(self):
        """Check if recovery budget is exhausted."""
        if self.exhausted:
            return
        
        now = time.time()
        elapsed = now - self.sequence_start_time
        
        # Check max total attempts
        if not self.config.indefinite_retry and self.total_attempts >= self.config.max_total_attempts:
            self.exhausted = True
            self.exhausted_reason = BudgetExhaustedReason.MAX_ATTEMPTS
            return
        
        # Check max recovery cycles
        if self.recovery_cycles >= self.config.max_recovery_cycles:
            self.exhausted = True
            self.exhausted_reason = BudgetExhaustedReason.MAX_CYCLES
            return
        
        # Check max total time
        if elapsed >= self.config.max_total_recovery_time:
            self.exhausted = True
            self.exhausted_reason = BudgetExhaustedReason.MAX_TOTAL_TIME
            return
        
        # Check max backoff delay (if last delay exceeded cap)
        if self.current_backoff_delay > self.config.max_single_backoff:
            self.exhausted = True
            self.exhausted_reason = BudgetExhaustedReason.MAX_BACKOFF_DELAY
            return
        
        # Check safety blocked
        if self.safety_blocked:
            self.exhausted = True
            self.exhausted_reason = BudgetExhaustedReason.SAFETY_BLOCKED
            return
    
    def can_continue(self) -> bool:
        """Check if recovery can continue."""
        return not self.exhausted
    
    def get_remaining_attempts(self) -> Optional[int]:
        """Get remaining attempts (None if indefinite)."""
        if self.config.indefinite_retry:
            return None
        remaining = self.config.max_total_attempts - self.total_attempts
        return max(0, remaining)
    
    def get_remaining_cycles(self) -> int:
        """Get remaining recovery cycles."""
        remaining = self.config.max_recovery_cycles - self.recovery_cycles
        return max(0, remaining)
    
    def get_remaining_time(self) -> float:
        """Get remaining time budget in seconds."""
        elapsed = time.time() - self.sequence_start_time
        remaining = self.config.max_total_recovery_time - elapsed
        return max(0.0, remaining)
    
    def get_elapsed_time(self) -> float:
        """Get total elapsed time since sequence start."""
        return time.time() - self.sequence_start_time
    
    def mark_safety_blocked(self):
        """Mark budget as exhausted due to safety gate."""
        self.safety_blocked = True
        self.exhausted = True
        self.exhausted_reason = BudgetExhaustedReason.SAFETY_BLOCKED
    
    def mark_permanent_failure(self):
        """Mark budget as exhausted due to permanent failure."""
        self.exhausted = True
        self.exhausted_reason = BudgetExhaustedReason.PERMANENT_FAILURE
    
    def mark_cancelled(self):
        """Mark budget as exhausted due to cancellation."""
        self.exhausted = True
        self.exhausted_reason = BudgetExhaustedReason.CANCELLED
    
    def get_status(self) -> Dict[str, Any]:
        """Get current budget status for logging/monitoring."""
        return {
            "failure_type": self.failure_type,
            "exhausted": self.exhausted,
            "exhausted_reason": self.exhausted_reason.value if self.exhausted_reason else None,
            "total_attempts": self.total_attempts,
            "recovery_cycles": self.recovery_cycles,
            "remaining_attempts": self.get_remaining_attempts(),
            "remaining_cycles": self.get_remaining_cycles(),
            "remaining_time_seconds": self.get_remaining_time(),
            "elapsed_time_seconds": self.get_elapsed_time(),
            "total_waited_seconds": self.total_waited_seconds,
            "current_backoff_delay": self.current_backoff_delay,
            "safety_blocked": self.safety_blocked,
            "config": {
                "max_total_attempts": self.config.max_total_attempts,
                "max_recovery_cycles": self.config.max_recovery_cycles,
                "max_total_recovery_time": self.config.max_total_recovery_time,
                "indefinite_retry": self.config.indefinite_retry,
            },
        }


class RecoveryBudgetManager:
    """
    Manages recovery budgets across multiple failure sequences.
    
    Tracks budgets per failure type and coordinates with backoff strategy.
    """
    
    def __init__(self, default_config: Optional[RecoveryBudgetConfig] = None):
        self.default_config = default_config or RecoveryBudgetConfig()
        self.budgets: Dict[str, RecoveryBudget] = {}  # key = failure_type
        self.current_budget: Optional[RecoveryBudget] = None
    
    def start_sequence(self, failure_type: str, config: Optional[RecoveryBudgetConfig] = None) -> RecoveryBudget:
        """Start a new recovery sequence for a failure type."""
        cfg = config or self.default_config
        budget = RecoveryBudget(config=cfg, failure_type=failure_type)
        self.budgets[failure_type] = budget
        self.current_budget = budget
        return budget
    
    def get_budget(self, failure_type: str) -> Optional[RecoveryBudget]:
        """Get budget for a failure type."""
        return self.budgets.get(failure_type)
    
    def record_attempt(
        self,
        failure_type: str,
        cycle_number: int,
        action: str,
        delay_seconds: float,
        success: bool,
        safety_verdict: str,
        blocking_factors: List[str] = None,
        error: str = None,
        details: Dict[str, Any] = None,
    ):
        """Record an attempt for the current budget."""
        budget = self.budgets.get(failure_type)
        if budget:
            budget.record_attempt(
                cycle_number=cycle_number,
                action=action,
                delay_seconds=delay_seconds,
                success=success,
                safety_verdict=safety_verdict,
                blocking_factors=blocking_factors,
                error=error,
                details=details,
            )
    
    def mark_safety_blocked(self, failure_type: str):
        """Mark budget as safety-blocked."""
        budget = self.budgets.get(failure_type)
        if budget:
            budget.mark_safety_blocked()
    
    def mark_permanent_failure(self, failure_type: str):
        """Mark budget as permanently failed."""
        budget = self.budgets.get(failure_type)
        if budget:
            budget.mark_permanent_failure()
    
    def mark_cancelled(self, failure_type: str):
        """Mark budget as cancelled."""
        budget = self.budgets.get(failure_type)
        if budget:
            budget.mark_cancelled()
    
    def can_continue(self, failure_type: str) -> bool:
        """Check if recovery can continue for a failure type."""
        budget = self.budgets.get(failure_type)
        if not budget:
            return True  # No budget yet = can start
        return budget.can_continue()
    
    def get_status(self, failure_type: str = None) -> Dict[str, Any]:
        """Get status for specific failure type or all."""
        if failure_type:
            budget = self.budgets.get(failure_type)
            return budget.get_status() if budget else {"failure_type": failure_type, "active": False}
        
        return {
            ft: budget.get_status() 
            for ft, budget in self.budgets.items()
        }
    
    def reset(self, failure_type: str = None):
        """Reset budget(s)."""
        if failure_type:
            self.budgets.pop(failure_type, None)
            if self.current_budget and self.current_budget.failure_type == failure_type:
                self.current_budget = None
        else:
            self.budgets.clear()
            self.current_budget = None