"""
Configurable recovery ladder for Agent Guardian.

Implements a generic recovery ladder mechanism that can reproduce Hermes-style
behavior (15/30/60/60/60s with Retry-After support) while being fully
configurable for other agents.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import time


class LadderDelaySource(Enum):
    """Source of the delay value."""
    SCHEDULE = "schedule"           # From configured schedule
    RETRY_AFTER = "retry_after"     # From provider Retry-After header
    JITTERED_SCHEDULE = "jittered_schedule"  # Schedule with jitter applied


@dataclass
class LadderConfig:
    """Configuration for a recovery ladder."""
    # Base delays for each cycle (1-indexed)
    # Example: [15.0, 30.0, 60.0, 60.0, 60.0] = 5 cycles
    cycle_delays: List[float] = field(default_factory=lambda: [15.0, 30.0, 60.0, 60.0, 60.0])
    
    # Jitter configuration
    jitter_ratio: float = 0.2           # Jitter as fraction of delay (0.0 - 1.0)
    jitter_type: str = "uniform"        # "uniform", "decorrelated", "full"
    
    # Retry-After handling
    respect_retry_after: bool = True
    max_retry_after: float = 120.0      # Cap on Retry-After (seconds)
    
    # Cycle limits
    max_cycles: int = 5                 # Max cycles (0 = unlimited, uses cycle_delays length)
    
    # Eligibility (can be overridden per-failure)
    eligible_failure_types: List[str] = field(default_factory=lambda: [
        "rate_limit", "transient_provider", "overloaded", "server_error",
        "timeout", "connection_reset", "network_transient", "dns_failure",
        "stream_stale", "stream_truncated", "first_chunk_timeout", "chunk_gap",
        "backend_disconnect", "backend_crash", "backend_restart",
    ])
    
    # Additional eligibility conditions (evaluated at runtime)
    require_no_visible_output: bool = True   # Match Hermes: no text delivered
    require_no_tool_activity: bool = True    # Match Hermes: no tools started
    
    def __post_init__(self):
        if self.max_cycles <= 0:
            self.max_cycles = len(self.cycle_delays)
        if self.max_cycles > len(self.cycle_delays):
            # Extend with last value
            last = self.cycle_delays[-1] if self.cycle_delays else 60.0
            self.cycle_delays.extend([last] * (self.max_cycles - len(self.cycle_delays)))
        
        if not 0.0 <= self.jitter_ratio <= 1.0:
            raise ValueError("jitter_ratio must be between 0.0 and 1.0")


@dataclass
class LadderState:
    """Current state of recovery ladder."""
    current_cycle: int = 0              # 0 = not started, 1 = first cycle
    cycles_completed: int = 0
    total_waited: float = 0.0
    start_time: float = field(default_factory=time.time)
    last_delay: float = 0.0
    last_delay_source: LadderDelaySource = LadderDelaySource.SCHEDULE
    last_retry_after: Optional[float] = None
    
    def reset(self):
        self.current_cycle = 0
        self.cycles_completed = 0
        self.total_waited = 0.0
        self.start_time = time.time()
        self.last_delay = 0.0
        self.last_delay_source = LadderDelaySource.SCHEDULE
        self.last_retry_after = None
    
    def get_elapsed_time(self) -> float:
        return time.time() - self.start_time


class RecoveryLadder:
    """
    Manages recovery ladder delays and eligibility.
    
    Provides Hermes-style ladder by default but fully configurable.
    Each cycle delay comes from schedule or Retry-After header.
    """
    
    def __init__(self, config: Optional[LadderConfig] = None):
        self.config = config or LadderConfig()
        self.state = LadderState()
    
    def is_eligible(
        self,
        failure_type: str,
        visible_output_delivered: bool = False,
        tool_activity_observed: bool = False,
        custom_check: callable = None,
    ) -> bool:
        """
        Check if ladder can engage for this failure.
        
        Args:
            failure_type: The classified failure type
            visible_output_delivered: Whether visible text reached consumer
            tool_activity_observed: Whether any tool started
            custom_check: Optional custom eligibility function
            
        Returns:
            True if ladder may engage
        """
        # Check failure type eligibility
        if failure_type not in self.config.eligible_failure_types:
            return False
        
        # Check cycle limit
        if self.state.current_cycle >= self.config.max_cycles:
            return False
        
        # Check visible output requirement (Hermes: ladder_eligible checks _has_content_after_think_block)
        if self.config.require_no_visible_output and visible_output_delivered:
            return False
        
        # Check tool activity requirement
        if self.config.require_no_tool_activity and tool_activity_observed:
            return False
        
        # Custom check
        if custom_check and not custom_check():
            return False
        
        return True
    
    def get_next_delay(
        self,
        retry_after: Optional[float] = None,
        visible_output_delivered: bool = False,
        tool_activity_observed: bool = False,
    ) -> float:
        """
        Get delay for next recovery cycle.
        
        Args:
            retry_after: Optional Retry-After value from provider (seconds)
            visible_output_delivered: For eligibility check
            tool_activity_observed: For eligibility check
            
        Returns:
            Delay in seconds, or -1 if not eligible
        """
        # Check eligibility first
        if not self.is_eligible(
            failure_type="",  # Will be checked in is_eligible
            visible_output_delivered=visible_output_delivered,
            tool_activity_observed=tool_activity_observed,
        ):
            return -1.0
        
        # Use Retry-After if provided and enabled
        if retry_after is not None and self.config.respect_retry_after:
            capped = min(retry_after, self.config.max_retry_after)
            self.state.last_delay = capped
            self.state.last_delay_source = LadderDelaySource.RETRY_AFTER
            self.state.last_retry_after = capped
            self.state.current_cycle += 1
            self.state.cycles_completed = self.state.current_cycle
            self.state.total_waited += capped
            return capped
        
        # Get scheduled delay
        cycle_index = self.state.current_cycle  # 0-indexed
        if cycle_index < len(self.config.cycle_delays):
            base_delay = self.config.cycle_delays[cycle_index]
        else:
            # Beyond configured cycles - use last value
            base_delay = self.config.cycle_delays[-1]
        
        # Apply jitter
        delay = self._apply_jitter(base_delay)
        
        self.state.last_delay = delay
        self.state.last_delay_source = LadderDelaySource.JITTERED_SCHEDULE
        self.state.current_cycle += 1
        self.state.cycles_completed = self.state.current_cycle
        self.state.total_waited += delay
        
        return delay
    
    def _apply_jitter(self, base_delay: float) -> float:
        """Apply configured jitter to base delay."""
        import random
        
        if self.config.jitter_ratio == 0.0:
            return base_delay
        
        if self.config.jitter_type == "full":
            # Full jitter: random(0, delay)
            return random.uniform(0, base_delay)
        
        elif self.config.jitter_type == "decorrelated":
            # Decorrelated jitter: random(base, previous_delay * 3)
            if self.state.last_delay > 0:
                upper = min(self.state.last_delay * 3, base_delay * 10)
                return random.uniform(self.config.cycle_delays[0], upper)
            return random.uniform(0, base_delay)
        
        else:  # uniform/equal jitter
            # Equal jitter: delay/2 + random(0, delay/2)
            return base_delay / 2 + random.uniform(0, base_delay / 2)
    
    def get_cycle_info(self, cycle: int = None) -> Dict[str, Any]:
        """Get information about a specific cycle."""
        c = cycle if cycle is not None else self.state.current_cycle
        if c <= 0:
            return {}
        
        idx = c - 1
        if idx < len(self.config.cycle_delays):
            base = self.config.cycle_delays[idx]
        else:
            base = self.config.cycle_delays[-1]
        
        return {
            "cycle": c,
            "base_delay": base,
            "jitter_ratio": self.config.jitter_ratio,
            "jitter_type": self.config.jitter_type,
            "expected_delay": base * (1 - self.config.jitter_ratio / 2) if self.config.jitter_type == "uniform" else base / 2,
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Get current ladder status."""
        return {
            "current_cycle": self.state.current_cycle,
            "cycles_completed": self.state.cycles_completed,
            "max_cycles": self.config.max_cycles,
            "total_waited": self.state.total_waited,
            "elapsed_time": self.state.get_elapsed_time(),
            "last_delay": self.state.last_delay,
            "last_delay_source": self.state.last_delay_source.value,
            "last_retry_after": self.state.last_retry_after,
            "remaining_cycles": max(0, self.config.max_cycles - self.state.current_cycle),
            "schedule": self.config.cycle_delays,
        }
    
    def reset(self):
        """Reset ladder state for new failure sequence."""
        self.state.reset()


# Predefined ladder configurations
HERMES_LADDER = LadderConfig(
    cycle_delays=[15.0, 30.0, 60.0, 60.0, 60.0],
    jitter_ratio=0.2,
    jitter_type="uniform",
    respect_retry_after=True,
    max_retry_after=120.0,
    max_cycles=5,
    require_no_visible_output=True,
    require_no_tool_activity=True,
)

CONSERVATIVE_LADDER = LadderConfig(
    cycle_delays=[30.0, 60.0, 120.0, 120.0, 120.0],
    jitter_ratio=0.25,
    jitter_type="uniform",
    respect_retry_after=True,
    max_retry_after=180.0,
    max_cycles=5,
)

AGGRESSIVE_LADDER = LadderConfig(
    cycle_delays=[5.0, 10.0, 20.0, 30.0, 30.0],
    jitter_ratio=0.1,
    jitter_type="uniform",
    respect_retry_after=True,
    max_retry_after=60.0,
    max_cycles=5,
)

INDEFINITE_LADDER = LadderConfig(
    cycle_delays=[10.0, 20.0, 30.0, 60.0, 60.0, 120.0, 120.0, 120.0],
    jitter_ratio=0.2,
    jitter_type="decorrelated",
    respect_retry_after=True,
    max_retry_after=300.0,
    max_cycles=0,  # Unlimited
    require_no_visible_output=True,
    require_no_tool_activity=True,
)


def create_ladder(profile: str = "hermes") -> RecoveryLadder:
    """Create a recovery ladder from a named profile."""
    profiles = {
        "hermes": HERMES_LADDER,
        "conservative": CONSERVATIVE_LADDER,
        "aggressive": AGGRESSIVE_LADDER,
        "indefinite": INDEFINITE_LADDER,
    }
    config = profiles.get(profile, HERMES_LADDER)
    return RecoveryLadder(config)