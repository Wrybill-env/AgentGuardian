"""Recovery Ladder - Bounded retry policy with jitter and eligibility gates."""

import random
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, List

from .types import FailureType, LadderDelaySource


@dataclass
class LadderState:
    """Mutable state of the recovery ladder."""
    current_cycle: int = 0
    cycles_completed: int = 0
    total_waited: float = 0.0
    last_delay: float = 0.0
    last_delay_source: LadderDelaySource = LadderDelaySource.JITTERED_SCHEDULE
    last_retry_after: float = 0.0
    last_visible_output: bool = False
    last_tool_activity: bool = False
    eligibility_failures: int = 0


@dataclass
class LadderConfig:
    """Configuration for recovery ladder behavior."""
    cycle_delays: List[float] = field(default_factory=lambda: [15.0, 30.0, 60.0, 60.0, 60.0])
    jitter_ratio: float = 0.2
    jitter_type: str = "uniform"
    respect_retry_after: bool = True
    max_retry_after: float = 120.0
    max_cycles: int = 5
    require_no_visible_output: bool = True
    require_no_tool_activity: bool = True
    eligible_failure_types: List[str] = field(default_factory=lambda: [
        "network_transient",
        "rate_limit",
        "transient_provider",
        "stream_stale",
        "connection_reset",
        "timeout",
        "server_error",
    ])
    custom_eligibility_check: Optional[Callable[[], bool]] = None


HERMES_LADDER = LadderConfig(
    cycle_delays=[15.0, 30.0, 60.0, 60.0, 60.0],
    jitter_ratio=0.2,
    jitter_type="uniform",
    respect_retry_after=True,
    max_retry_after=120.0,
    max_cycles=5,
    require_no_visible_output=True,
    require_no_tool_activity=True,
    eligible_failure_types=[
        "network_transient",
        "rate_limit",
        "transient_provider",
        "stream_stale",
        "connection_reset",
        "timeout",
        "server_error",
    ],
)


class RecoveryLadder:
    """
    Bounded exponential backoff with jitter and eligibility gates.

    Implements the retry policy for Guardian recovery:
    - Only retries eligible failure types
    - Requires no visible output (assistant chunks) for safe retry
    - Requires no tool activity for safe retry
    - Respects Retry-After headers when available
    - Applies configurable jitter
    - Tracks cycle state for budget management
    """

    def __init__(self, config: Optional[LadderConfig] = None):
        self.config = config or LadderConfig()
        self.state = LadderState()

    def is_eligible(
        self,
        failure_type: str,
        visible_output_delivered: bool,
        tool_activity_observed: bool,
        custom_check: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """
        Check if a failure is eligible for retry via the ladder.

        Args:
            failure_type: The classified failure type string
            visible_output_delivered: Whether assistant output was delivered
            tool_activity_observed: Whether tool execution started
            custom_check: Optional additional eligibility check

        Returns:
            True if eligible for retry
        """
        # Check failure type eligibility
        if failure_type not in self.config.eligible_failure_types:
            return False

        # Check visible output gate
        if self.config.require_no_visible_output and visible_output_delivered:
            return False

        # Check tool activity gate
        if self.config.require_no_tool_activity and tool_activity_observed:
            return False

        # Check max cycles
        if self.state.current_cycle >= self.config.max_cycles:
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
        failure_type: str = "",
    ) -> float:
        """
        Get delay for next recovery cycle.

        Args:
            retry_after: Optional Retry-After value from provider (seconds)
            visible_output_delivered: For eligibility check
            tool_activity_observed: For eligibility check
            failure_type: The classified failure type for eligibility check

        Returns:
            Delay in seconds, or -1 if not eligible
        """
        # Check eligibility first
        if not self.is_eligible(
            failure_type=failure_type,
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
        target_cycle = cycle if cycle is not None else self.state.current_cycle
        if target_cycle < len(self.config.cycle_delays):
            base_delay = self.config.cycle_delays[target_cycle]
        else:
            base_delay = self.config.cycle_delays[-1]

        return {
            "cycle": target_cycle,
            "base_delay": base_delay,
            "max_delay_with_jitter": base_delay * (1 + self.config.jitter_ratio),
            "min_delay_with_jitter": base_delay * (1 - self.config.jitter_ratio) if self.config.jitter_type != "full" else 0,
        }

    def reset(self):
        """Reset ladder state for new recovery sequence."""
        self.state = LadderState()

    def get_status(self) -> Dict[str, Any]:
        """Get current ladder status."""
        return {
            "current_cycle": self.state.current_cycle,
            "cycles_completed": self.state.cycles_completed,
            "total_waited": self.state.total_waited,
            "last_delay": self.state.last_delay,
            "last_delay_source": self.state.last_delay_source.value,
            "last_retry_after": self.state.last_retry_after,
            "max_cycles": self.config.max_cycles,
            "cycles_remaining": max(0, self.config.max_cycles - self.state.current_cycle),
            "eligible_failure_types": self.config.eligible_failure_types,
        }


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

# The ladder has no "unlimited" convention: max_cycles is a hard cap and
# `current_cycle >= max_cycles` makes a ladder ineligible. The previous value of
# 0 therefore made this profile *never* eligible (0 >= 0), which contradicted its
# name. A genuinely unbounded retry is unsafe, so this profile is bounded by a
# large-but-finite cycle cap; the real hard stop is the RecoveryBudget
# (max_total_recovery_time / max_total_attempts) enforced by the executor.
INDEFINITE_LADDER = LadderConfig(
    cycle_delays=[10.0, 20.0, 30.0, 60.0, 60.0, 120.0, 120.0, 120.0],
    jitter_ratio=0.2,
    jitter_type="decorrelated",
    respect_retry_after=True,
    max_retry_after=300.0,
    max_cycles=100,
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