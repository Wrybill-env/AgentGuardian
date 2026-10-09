"""
Exponential backoff strategy with jitter for Guardian.

Implements configurable backoff with support for:
- Initial delay
- Maximum delay
- Jitter (full or decorrelated)
- Maximum attempts
- Optional indefinite retry mode
- Retry-After header support
"""

import random
import time
from typing import Optional, Protocol, runtime_checkable
from dataclasses import dataclass, field
from enum import Enum


class JitterType(Enum):
    """Type of jitter to apply."""
    NONE = "none"
    FULL = "full"                    # random(0, delay)
    EQUAL = "equal"                  # delay/2 + random(0, delay/2)
    DECORRELATED = "decorrelated"    # random(base, previous_delay * 3)


@runtime_checkable
class JitterProvider(Protocol):
    """Protocol for injectable jitter/randomness provider."""
    def random(self) -> float: ...
    def uniform(self, a: float, b: float) -> float: ...


class SystemJitterProvider:
    """Default system jitter provider using system random."""
    def random(self) -> float:
        return random.random()
    
    def uniform(self, a: float, b: float) -> float:
        return random.uniform(a, b)


@dataclass
class BackoffConfig:
    """Configuration for backoff behavior."""
    initial_delay: float = 1.0           # Initial delay in seconds
    max_delay: float = 60.0              # Maximum delay in seconds
    multiplier: float = 2.0              # Exponential multiplier
    jitter_type: JitterType = JitterType.FULL
    max_attempts: int = 3                # Maximum retry attempts (0 = unlimited with indefinite_retry)
    indefinite_retry: bool = False       # Retry indefinitely (overrides max_attempts)
    respect_retry_after: bool = True     # Honor Retry-After headers
    max_retry_after: float = 300.0       # Cap on Retry-After (seconds)
    jitter_provider: Optional[JitterProvider] = None  # Injectable for testing
    
    def __post_init__(self):
        if self.initial_delay <= 0:
            raise ValueError("initial_delay must be positive")
        if self.max_delay < self.initial_delay:
            raise ValueError("max_delay must be >= initial_delay")
        if self.multiplier <= 1.0:
            raise ValueError("multiplier must be > 1.0")
        if self.max_attempts < 0:
            raise ValueError("max_attempts must be >= 0")
        
        if self.jitter_provider is None:
            self.jitter_provider = SystemJitterProvider()


@dataclass
class BackoffState:
    """Current state of backoff sequence."""
    attempt: int = 0                     # Current attempt number (0 = first try)
    last_delay: float = 0.0              # Last delay applied
    total_waited: float = 0.0            # Total time spent waiting
    start_time: float = field(default_factory=time.time)
    last_retry_after: Optional[float] = None
    
    def reset(self):
        """Reset backoff state for new sequence."""
        self.attempt = 0
        self.last_delay = 0.0
        self.total_waited = 0.0
        self.start_time = time.time()
        self.last_retry_after = None


class BackoffStrategy:
    """
    Exponential backoff with configurable jitter.
    
    Supports:
    - Standard exponential backoff
    - Full jitter (AWS-style)
    - Equal jitter
    - Decorrelated jitter (AWS-style for high contention)
    - Retry-After header integration
    - Configurable caps and limits
    """
    
    def __init__(self, config: Optional[BackoffConfig] = None):
        self.config = config or BackoffConfig()
        self.state = BackoffState()
    
    def next_delay(self, retry_after: Optional[float] = None) -> float:
        """
        Calculate next delay.
        
        Args:
            retry_after: Optional Retry-After value from server (seconds)
            
        Returns:
            Delay in seconds before next attempt
        """
        # If server provides Retry-After, use it (capped)
        if retry_after is not None and self.config.respect_retry_after:
            capped = min(retry_after, self.config.max_retry_after)
            self.state.last_retry_after = capped
            return capped
        
        # Calculate exponential backoff
        if self.state.attempt == 0:
            base_delay = self.config.initial_delay
        else:
            base_delay = self.config.initial_delay * (self.config.multiplier ** self.state.attempt)
        
        # Cap at max_delay
        base_delay = min(base_delay, self.config.max_delay)
        
        # Apply jitter
        delay = self._apply_jitter(base_delay)
        
        # Update state
        self.state.attempt += 1
        self.state.last_delay = delay
        self.state.total_waited += delay
        
        return delay
    
    def _apply_jitter(self, base_delay: float) -> float:
        """Apply configured jitter type to base delay."""
        provider = self.config.jitter_provider or SystemJitterProvider()
        
        if self.config.jitter_type == JitterType.NONE:
            return base_delay
        
        elif self.config.jitter_type == JitterType.FULL:
            # Full jitter: random(0, delay)
            return provider.uniform(0, base_delay)
        
        elif self.config.jitter_type == JitterType.EQUAL:
            # Equal jitter: delay/2 + random(0, delay/2)
            return base_delay / 2 + provider.uniform(0, base_delay / 2)
        
        elif self.config.jitter_type == JitterType.DECORRELATED:
            # Decorrelated jitter: random(base, previous_delay * 3)
            if self.state.last_delay > 0:
                upper = min(self.state.last_delay * 3, self.config.max_delay)
                return provider.uniform(self.config.initial_delay, upper)
            else:
                return provider.uniform(0, base_delay)
        
        return base_delay
    
    def should_retry(self, attempt: Optional[int] = None) -> bool:
        """
        Check if another retry should be attempted.
        
        Args:
            attempt: Attempt number to check (uses current state if None)
            
        Returns:
            True if should retry, False if max attempts reached
        """
        attempt = attempt if attempt is not None else self.state.attempt
        
        if self.config.indefinite_retry:
            return True
        
        return attempt < self.config.max_attempts
    
    def get_attempt(self) -> int:
        """Get current attempt number."""
        return self.state.attempt
    
    def get_total_waited(self) -> float:
        """Get total time spent waiting."""
        return self.state.total_waited
    
    def get_elapsed_time(self) -> float:
        """Get total elapsed time since backoff started."""
        return time.time() - self.state.start_time
    
    def reset(self):
        """Reset backoff state for new failure sequence."""
        self.state.reset()
    
    def get_status(self) -> dict:
        """Get current backoff status for logging/monitoring."""
        return {
            "attempt": self.state.attempt,
            "last_delay": self.state.last_delay,
            "total_waited": self.state.total_waited,
            "elapsed_time": self.get_elapsed_time(),
            "max_attempts": self.config.max_attempts if not self.config.indefinite_retry else "indefinite",
            "next_delay_estimate": self._estimate_next_delay(),
        }
    
    def _estimate_next_delay(self) -> float:
        """Estimate next delay without advancing state."""
        if self.state.attempt == 0:
            base = self.config.initial_delay
        else:
            base = self.config.initial_delay * (self.config.multiplier ** self.state.attempt)
        base = min(base, self.config.max_delay)
        
        if self.config.jitter_type == JitterType.FULL:
            return base / 2  # Expected value
        elif self.config.jitter_type == JitterType.EQUAL:
            return base * 0.75  # Expected value
        elif self.config.jitter_type == JitterType.DECORRELATED:
            if self.state.last_delay > 0:
                upper = min(self.state.last_delay * 3, self.config.max_delay)
                return (self.config.initial_delay + upper) / 2
            return base / 2
        return base


# Predefined backoff configurations
CONSERVATIVE_BACKOFF = BackoffConfig(
    initial_delay=2.0,
    max_delay=120.0,
    multiplier=2.0,
    jitter_type=JitterType.FULL,
    max_attempts=5,
    respect_retry_after=True,
    max_retry_after=300.0,
)

AGGRESSIVE_BACKOFF = BackoffConfig(
    initial_delay=0.5,
    max_delay=30.0,
    multiplier=1.5,
    jitter_type=JitterType.EQUAL,
    max_attempts=3,
    respect_retry_after=True,
    max_retry_after=60.0,
)

INDEFINITE_BACKOFF = BackoffConfig(
    initial_delay=5.0,
    max_delay=300.0,
    multiplier=2.0,
    jitter_type=JitterType.DECORRELATED,
    max_attempts=0,  # Unlimited
    indefinite_retry=True,
    respect_retry_after=True,
    max_retry_after=600.0,
)

DEFAULT_BACKOFF = BackoffConfig()