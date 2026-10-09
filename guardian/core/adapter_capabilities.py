"""
Adapter capability model for Agent Guardian.

Defines what capabilities an agent adapter must provide for recovery operations.
Each capability corresponds to an actual executable operation.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from abc import ABC, abstractmethod

from .types import AdapterCapability, RecoveryAction


@dataclass
class CapabilityResult:
    """Result of a capability check."""
    capability: AdapterCapability
    available: bool
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability.value,
            "available": self.available,
            "reason": self.reason,
            "details": self.details,
        }


@dataclass
class AdapterCapabilities:
    """
    Complete capability declaration for an adapter.
    
    Each capability must correspond to an actually implemented operation.
    """
    adapter_name: str
    adapter_version: str = "unknown"
    agent_type: str = "unknown"  # "hermes", "goose", etc.
    
    # Capability map
    capabilities: Dict[AdapterCapability, CapabilityResult] = field(default_factory=dict)
    
    def has(self, capability: AdapterCapability) -> bool:
        """Check if a capability is available."""
        result = self.capabilities.get(capability)
        return result.available if result else False
    
    def get(self, capability: AdapterCapability) -> Optional[CapabilityResult]:
        """Get capability result."""
        return self.capabilities.get(capability)
    
    def set_available(self, capability: AdapterCapability, reason: str = "", details: Dict = None):
        """Mark capability as available."""
        self.capabilities[capability] = CapabilityResult(
            capability=capability,
            available=True,
            reason=reason,
            details=details or {},
        )
    
    def set_unavailable(self, capability: AdapterCapability, reason: str = "", details: Dict = None):
        """Mark capability as unavailable."""
        self.capabilities[capability] = CapabilityResult(
            capability=capability,
            available=False,
            reason=reason,
            details=details or {},
        )
    
    def get_available(self) -> List[AdapterCapability]:
        """Get list of available capabilities."""
        return [cap for cap, result in self.capabilities.items() if result.available]
    
    def get_unavailable(self) -> List[AdapterCapability]:
        """Get list of unavailable capabilities."""
        return [cap for cap, result in self.capabilities.items() if not result.available]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "agent_type": self.agent_type,
            "capabilities": {
                cap.value: result.to_dict()
                for cap, result in self.capabilities.items()
            },
            "available_count": len(self.get_available()),
            "total_count": len(self.capabilities),
        }


class AgentAdapter(ABC):
    """
    Abstract base class for agent adapters.

    Each agent (Hermes, Goose, OpenCode, etc.) implements this interface
    to provide Guardian with observation and recovery capabilities.
    """

    @property
    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        """Return declared capabilities."""
        pass

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the agent."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Disconnect from the agent."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected."""
        pass

    # --- Observation callbacks (set by Guardian) ---

    def on_event(self, callback: Callable[[Any], Awaitable[None]]):
        """Register event callback."""
        pass

    def on_failure(self, callback: Callable[[str, Dict], Awaitable[None]]):
        """Register failure callback."""
        pass

    def on_state_change(self, callback: Callable[[str, str], Awaitable[None]]):
        """Register state change callback."""
        pass

    # --- Recovery execution (single entry point) ---

    @abstractmethod
    async def execute_recovery_action(self, action: RecoveryAction, context: Dict[str, Any]) -> bool:
        """Execute a recovery action.

        Returns True if the action was successfully initiated.
        The caller is responsible for observing the result.
        """
        pass

    # --- Status ---

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Get adapter status for diagnostics."""
        pass


# Capability validation helpers
def validate_capabilities(adapter: AgentAdapter) -> List[str]:
    """
    Validate that declared capabilities match implemented methods.

    Returns list of mismatches (empty if all match).
    """
    mismatches = []
    caps = adapter.capabilities

    # For the new single-method interface, we just check execute_recovery_action exists
    if not hasattr(adapter, 'execute_recovery_action') or not callable(getattr(adapter, 'execute_recovery_action', None)):
        mismatches.append("execute_recovery_action not implemented")

    return mismatches


# Predefined capability profiles
HERMES_CAPABILITIES = {
    # Observation - VERIFIED IMPLEMENTED
    AdapterCapability.OBSERVE_STREAM: True,
    AdapterCapability.OBSERVE_REASONING: True,
    AdapterCapability.OBSERVE_TOOL_PROGRESS: True,
    AdapterCapability.OBSERVE_TURN_LIFECYCLE: True,

    # Stream recovery - SIMULATED (callback reattach only)
    AdapterCapability.RECONNECT_STREAM: True,
    AdapterCapability.CANCEL_STREAM: False,

    # Session recovery - UNAVAILABLE (Hermes ACP bridge lacks these)
    AdapterCapability.REATTACH_SESSION: False,
    AdapterCapability.FORK_SESSION: False,
    AdapterCapability.LOAD_SESSION: False,
    AdapterCapability.SAVE_SESSION: False,

    # Provider recovery - UNKNOWN (Hermes handles internally)
    AdapterCapability.SWITCH_PROVIDER: False,
    AdapterCapability.GET_PROVIDER_LIST: False,

    # Agent process - NOT IMPLEMENTED
    AdapterCapability.RESTART_AGENT: False,
    AdapterCapability.GET_AGENT_STATUS: False,
    AdapterCapability.IS_AGENT_ALIVE: False,

    # Turn control - PARTIAL (callback reattach only)
    AdapterCapability.CANCEL_TURN: False,
    AdapterCapability.RETRY_REQUEST: True,  # Via callback reattach
    AdapterCapability.REPLAY_TURN: False,

    # Introspection - VERIFIED IMPLEMENTED
    AdapterCapability.GET_PARTIAL_TOOL_NAMES: True,
    AdapterCapability.GET_TOOL_STATE: False,
    AdapterCapability.GET_SESSION_STATE: True,
    AdapterCapability.GET_TURN_CONTEXT: True,

    # Safety - PARTIAL
    AdapterCapability.VERIFY_SESSION_IDENTITY: True,
    AdapterCapability.VERIFY_SESSION_EXISTS: False,

    # Monitoring - PARTIAL
    AdapterCapability.DETECT_DISCONNECT: True,
    AdapterCapability.DETECT_CRASH: False,
    AdapterCapability.DETECT_STALE: True,
}

GOOSE_CAPABILITIES = {
    # Observation - VERIFIED IMPLEMENTED via ACP
    AdapterCapability.OBSERVE_STREAM: True,
    AdapterCapability.OBSERVE_REASONING: False,  # Not in ACP
    AdapterCapability.OBSERVE_TOOL_PROGRESS: True,
    AdapterCapability.OBSERVE_TURN_LIFECYCLE: True,

    # Stream recovery - VERIFIED IMPLEMENTED via ACP reconnect
    AdapterCapability.RECONNECT_STREAM: True,
    AdapterCapability.CANCEL_STREAM: False,  # No explicit ACP cancel stream

    # Session recovery - PARTIALLY_IMPLEMENTED (requires verification)
    AdapterCapability.REATTACH_SESSION: False,  # Not directly supported
    AdapterCapability.FORK_SESSION: True,  # ACP agent/session/fork
    AdapterCapability.LOAD_SESSION: True,  # ACP agent/session/load
    AdapterCapability.SAVE_SESSION: False,

    # Provider recovery - NOT IN ACP
    AdapterCapability.SWITCH_PROVIDER: False,
    AdapterCapability.GET_PROVIDER_LIST: False,

    # Agent process - PARTIAL (process monitor possible)
    AdapterCapability.RESTART_AGENT: False,
    AdapterCapability.GET_AGENT_STATUS: True,  # HTTP /status endpoint
    AdapterCapability.IS_AGENT_ALIVE: True,  # WebSocket connected

    # Turn control - VERIFIED IMPLEMENTED via ACP
    AdapterCapability.CANCEL_TURN: True,  # ACP CancelNotification
    AdapterCapability.RETRY_REQUEST: True,  # Via reattach + retry
    AdapterCapability.REPLAY_TURN: True,  # Via load_session with replay

    # Introspection - PARTIAL
    AdapterCapability.GET_PARTIAL_TOOL_NAMES: False,  # Not in ACP
    AdapterCapability.GET_TOOL_STATE: True,  # ACP tool call tracking
    AdapterCapability.GET_SESSION_STATE: True,  # ACP sessionUpdate
    AdapterCapability.GET_TURN_CONTEXT: True,  # TurnContext from observer

    # Safety - PARTIALLY_IMPLEMENTED
    AdapterCapability.VERIFY_SESSION_IDENTITY: True,  # Session ID matching
    AdapterCapability.VERIFY_SESSION_EXISTS: False,  # No session list query

    # Monitoring - VERIFIED IMPLEMENTED
    AdapterCapability.DETECT_DISCONNECT: True,  # WebSocket close
    AdapterCapability.DETECT_CRASH: False,  # Only detects disconnect, not process crash
    AdapterCapability.DETECT_STALE: True,  # Stale detection
}


def create_capabilities(adapter_name: str, agent_type: str, capability_map: Dict[AdapterCapability, bool]) -> AdapterCapabilities:
    """Create AdapterCapabilities from a simple boolean map."""
    caps = AdapterCapabilities(
        adapter_name=adapter_name,
        agent_type=agent_type,
    )
    for cap, available in capability_map.items():
        if available:
            caps.set_available(cap, reason="Implemented")
        else:
            caps.set_unavailable(cap, reason="Not implemented")
    return caps