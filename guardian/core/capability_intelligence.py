"""
Capability Intelligence System for Agent Guardian.

Provides capability registry, evidence tracking, verification statuses,
gap analysis, and automated discovery for agent adapters.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from enum import Enum
from datetime import datetime
from abc import ABC, abstractmethod

from guardian.core.types import AdapterCapability, RecoveryAction
from guardian.core.adapter_capabilities import AdapterCapabilities, CapabilityResult, AgentAdapter


class CapabilitySupportStatus(Enum):
    """Support status for a capability."""
    SUPPORTED = "supported"           # Fully implemented and verified
    PARTIAL = "partial"               # Partially implemented or needs verification
    UNSUPPORTED = "unsupported"       # Not available in agent protocol/architecture
    UNKNOWN = "unknown"               # Cannot determine


class VerificationStatus(Enum):
    """Verification status for a capability."""
    VERIFIED = "verified"             # Implementation inspected + behavior verified
    MOCK_VERIFIED = "mock_verified"   # Demonstrated through controlled mock/fixture tests
    SIMULATED = "simulated"           # Only callback reattach or process restart
    UNVERIFIED = "unverified"         # Insufficient evidence
    UNSAFE = "unsafe"                 # Violates safety requirement


class EvidenceType(Enum):
    """Type of evidence for capability support."""
    SOURCE_INSPECTION = "source_inspection"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    PUBLIC_API_INTROSPECTION = "public_api_introspection"
    RUNTIME_DECLARATION = "runtime_declaration"
    CONTROLLED_PROBE = "controlled_probe"
    INTEGRATION_TEST = "integration_test"
    FORENSIC_TEST = "forensic_test"
    REAL_RUNTIME = "real_runtime"


@dataclass
class CapabilityEvidence:
    """Evidence supporting a capability claim."""
    evidence_type: EvidenceType
    description: str
    source: str
    timestamp: datetime = field(default_factory=datetime.now)
    confidence: float = 1.0  # 0.0 - 1.0
    notes: str = ""


@dataclass
class CapabilityRequirement:
    """Required behavioral guarantees for a capability."""
    description: str
    safety_implication: str = ""
    dependencies: List[str] = field(default_factory=list)


@dataclass
class CapabilityGap:
    """Analysis of a capability gap."""
    capability: AdapterCapability
    desired: str
    native_support: CapabilitySupportStatus
    native_verification: VerificationStatus
    guardian_provided: bool
    gap_description: str
    resolution_strategy: str  # "reuse_native", "core_generic", "adapter_specific", "optional_module", "blocked"
    safety_risk: str
    priority: int  # 1 = highest
    estimated_effort: str
    blocker: Optional[str] = None


@dataclass
class AgentCapabilityProfile:
    """Complete capability profile for an agent."""
    agent_type: str
    agent_version: Optional[str] = None
    adapter_name: str = ""
    last_updated: datetime = field(default_factory=datetime.now)
    
    # Capability matrix
    capabilities: Dict[AdapterCapability, CapabilityResult] = field(default_factory=dict)
    
    # Evidence and verification
    evidence: Dict[AdapterCapability, List[CapabilityEvidence]] = field(default_factory=dict)
    verification_status: Dict[AdapterCapability, VerificationStatus] = field(default_factory=dict)
    
    # Gap analysis
    gaps: List[CapabilityGap] = field(default_factory=list)
    
    # Summary
    total_capabilities: int = 0
    supported_count: int = 0
    partial_count: int = 0
    unsupported_count: int = 0
    unknown_count: int = 0
    verified_count: int = 0
    mock_verified_count: int = 0
    simulated_count: int = 0
    unverified_count: int = 0
    unsafe_count: int = 0
    
    def update_summary(self):
        """Update summary counts."""
        self.total_capabilities = len(self.capabilities)
        self.supported_count = sum(1 for r in self.capabilities.values() if r.available)
        self.partial_count = sum(1 for r in self.capabilities.values() if not r.available and "partial" in r.reason.lower())
        self.unsupported_count = sum(1 for r in self.capabilities.values() if not r.available)
        self.unknown_count = sum(1 for cap in AdapterCapability if cap not in self.capabilities)
        
        self.verified_count = sum(1 for v in self.verification_status.values() if v == VerificationStatus.VERIFIED)
        self.mock_verified_count = sum(1 for v in self.verification_status.values() if v == VerificationStatus.MOCK_VERIFIED)
        self.simulated_count = sum(1 for v in self.verification_status.values() if v == VerificationStatus.SIMULATED)
        self.unverified_count = sum(1 for v in self.verification_status.values() if v == VerificationStatus.UNVERIFIED)
        self.unsafe_count = sum(1 for v in self.verification_status.values() if v == VerificationStatus.UNSAFE)


class CapabilityRegistry:
    """
    Central capability registry with evidence tracking and verification.
    
    Manages capability definitions, evidence, verification statuses,
    and gap analysis across all supported agents.
    """
    
    def __init__(self):
        self.capability_definitions: Dict[AdapterCapability, CapabilityRequirement] = {}
        self.profiles: Dict[str, AgentCapabilityProfile] = {}
        self.gap_analyzer = CapabilityGapAnalyzer()
        self._load_standard_requirements()
    
    def _load_standard_requirements(self):
        """Load standard capability requirements."""
        # Observation capabilities
        self.capability_definitions[AdapterCapability.OBSERVE_STREAM] = CapabilityRequirement(
            description="Observe assistant text stream chunks in real-time",
            safety_implication="Required for detecting output before tool execution"
        )
        self.capability_definitions[AdapterCapability.OBSERVE_REASONING] = CapabilityRequirement(
            description="Observe reasoning/thinking chunks separately from output",
            safety_implication="Distinguishes reasoning from visible output"
        )
        self.capability_definitions[AdapterCapability.OBSERVE_TOOL_PROGRESS] = CapabilityRequirement(
            description="Observe tool call start, progress, completion, failure",
            safety_implication="Critical for detecting tool execution side effects"
        )
        self.capability_definitions[AdapterCapability.OBSERVE_TURN_LIFECYCLE] = CapabilityRequirement(
            description="Observe turn start, completion, cancellation, errors",
            safety_implication="Required for turn state tracking"
        )
        
        # Stream recovery
        self.capability_definitions[AdapterCapability.RECONNECT_STREAM] = CapabilityRequirement(
            description="Re-establish streaming connection after disconnect",
            safety_implication="Safe recovery - no prompt replay",
            dependencies=["OBSERVE_STREAM"]
        )
        self.capability_definitions[AdapterCapability.CANCEL_STREAM] = CapabilityRequirement(
            description="Cancel an in-flight stream",
            safety_implication="May interrupt side effects - requires verification"
        )
        
        # Session recovery
        self.capability_definitions[AdapterCapability.REATTACH_SESSION] = CapabilityRequirement(
            description="Reattach to existing session after disconnect",
            safety_implication="Preserves history - safe if session identity verified",
            dependencies=["VERIFY_SESSION_IDENTITY"]
        )
        self.capability_definitions[AdapterCapability.FORK_SESSION] = CapabilityRequirement(
            description="Fork session at safe point before current turn",
            safety_implication="Safe fork point must be before current turn's user message",
            dependencies=["VERIFY_SESSION_IDENTITY"]
        )
        self.capability_definitions[AdapterCapability.LOAD_SESSION] = CapabilityRequirement(
            description="Load session from backend (replaces current)",
            safety_implication="Replaces state - safe if history preserved",
            dependencies=["VERIFY_SESSION_IDENTITY", "VERIFY_SESSION_EXISTS"]
        )
        self.capability_definitions[AdapterCapability.SAVE_SESSION] = CapabilityRequirement(
            description="Save current session state",
            safety_implication="No direct safety impact"
        )
        
        # Provider recovery
        self.capability_definitions[AdapterCapability.SWITCH_PROVIDER] = CapabilityRequirement(
            description="Switch to different LLM provider",
            safety_implication="Must preserve session context",
            dependencies=["REATTACH_SESSION"]
        )
        self.capability_definitions[AdapterCapability.GET_PROVIDER_LIST] = CapabilityRequirement(
            description="List available providers",
            safety_implication="No safety impact"
        )
        
        # Agent process
        self.capability_definitions[AdapterCapability.RESTART_AGENT] = CapabilityRequirement(
            description="Restart agent process",
            safety_implication="Full state reset - requires session recovery",
            dependencies=["REATTACH_SESSION"]
        )
        self.capability_definitions[AdapterCapability.GET_AGENT_STATUS] = CapabilityRequirement(
            description="Get agent process/health status",
            safety_implication="No safety impact"
        )
        self.capability_definitions[AdapterCapability.IS_AGENT_ALIVE] = CapabilityRequirement(
            description="Check if agent is running",
            safety_implication="Used for liveness detection"
        )
        
        # Turn control
        self.capability_definitions[AdapterCapability.CANCEL_TURN] = CapabilityRequirement(
            description="Cancel current turn",
            safety_implication="Must cancel before side effects - verify capability",
            dependencies=["OBSERVE_TOOL_PROGRESS"]
        )
        self.capability_definitions[AdapterCapability.RETRY_REQUEST] = CapabilityRequirement(
            description="Retry the current request",
            safety_implication="May replay prompt - only safe if no side effects",
            dependencies=["OBSERVE_STREAM", "OBSERVE_TOOL_PROGRESS"]
        )
        self.capability_definitions[AdapterCapability.REPLAY_TURN] = CapabilityRequirement(
            description="Replay the turn from beginning",
            safety_implication="Full replay - only safe if no side effects",
            dependencies=["OBSERVE_STREAM", "OBSERVE_TOOL_PROGRESS"]
        )
        self.capability_definitions[AdapterCapability.WAIT_FOR_BACKEND] = CapabilityRequirement(
            description="Wait for backend to become available",
            safety_implication="Safe - no prompt replay",
            dependencies=[]
        )
        
        # Introspection
        self.capability_definitions[AdapterCapability.GET_PARTIAL_TOOL_NAMES] = CapabilityRequirement(
            description="Get names of tools that may have started",
            safety_implication="Critical for detecting partial tool execution"
        )
        self.capability_definitions[AdapterCapability.GET_TOOL_STATE] = CapabilityRequirement(
            description="Get detailed state of specific tool call",
            safety_implication="Helps determine if tool completed"
        )
        self.capability_definitions[AdapterCapability.GET_SESSION_STATE] = CapabilityRequirement(
            description="Get complete session state",
            safety_implication="Required for state verification"
        )
        self.capability_definitions[AdapterCapability.GET_TURN_CONTEXT] = CapabilityRequirement(
            description="Get current turn context",
            safety_implication="Required for safety gate decisions"
        )
        
        # Safety
        self.capability_definitions[AdapterCapability.VERIFY_SESSION_IDENTITY] = CapabilityRequirement(
            description="Verify session identity after reconnect/reload",
            safety_implication="Mandatory for safe session recovery",
            dependencies=["GET_SESSION_STATE"]
        )
        self.capability_definitions[AdapterCapability.VERIFY_SESSION_EXISTS] = CapabilityRequirement(
            description="Verify session exists on backend",
            safety_implication="Required before load/fork operations",
            dependencies=["GET_SESSION_STATE"]
        )
        
        # Monitoring
        self.capability_definitions[AdapterCapability.DETECT_DISCONNECT] = CapabilityRequirement(
            description="Detect stream/connection disconnect",
            safety_implication="Triggers failure detection"
        )
        self.capability_definitions[AdapterCapability.DETECT_CRASH] = CapabilityRequirement(
            description="Detect agent process crash",
            safety_implication="Distinguishes crash from disconnect"
        )
        self.capability_definitions[AdapterCapability.DETECT_STALE] = CapabilityRequirement(
            description="Detect stalled stream",
            safety_implication="Triggers stale stream recovery"
        )
    
    def register_profile(self, profile: AgentCapabilityProfile):
        """Register an agent capability profile."""
        profile.update_summary()
        self.profiles[profile.agent_type] = profile
    
    def get_profile(self, agent_type: str) -> Optional[AgentCapabilityProfile]:
        """Get capability profile for an agent."""
        return self.profiles.get(agent_type)
    
    def get_all_profiles(self) -> Dict[str, AgentCapabilityProfile]:
        """Get all registered profiles."""
        return self.profiles.copy()
    
    def add_evidence(self, agent_type: str, capability: AdapterCapability, evidence: CapabilityEvidence):
        """Add evidence for a capability."""
        profile = self.get_profile(agent_type)
        if not profile:
            profile = AgentCapabilityProfile(agent_type=agent_type)
            self.register_profile(profile)
        
        if capability not in profile.evidence:
            profile.evidence[capability] = []
        profile.evidence[capability].append(evidence)
    
    def set_verification(self, agent_type: str, capability: AdapterCapability, status: VerificationStatus):
        """Set verification status for a capability."""
        profile = self.get_profile(agent_type)
        if not profile:
            profile = AgentCapabilityProfile(agent_type=agent_type)
            self.register_profile(profile)
        profile.verification_status[capability] = status
        profile.update_summary()
    
    def run_gap_analysis(self, agent_type: str) -> List[CapabilityGap]:
        """Run gap analysis for an agent."""
        profile = self.get_profile(agent_type)
        if not profile:
            return []
        return self.gap_analyzer.analyze(profile)
    
    def get_capability_matrix(self) -> Dict[str, Dict[str, Any]]:
        """Get capability matrix across all agents."""
        matrix = {}
        for agent_type, profile in self.profiles.items():
            matrix[agent_type] = {
                "supported": profile.supported_count,
                "partial": profile.partial_count,
                "unsupported": profile.unsupported_count,
                "unknown": profile.unknown_count,
                "verified": profile.verified_count,
                "mock_verified": profile.mock_verified_count,
                "simulated": profile.simulated_count,
                "unverified": profile.unverified_count,
                "unsafe": profile.unsafe_count,
                "capabilities": {
                    cap.value: {
                        "available": result.available,
                        "reason": result.reason,
                        "verification": profile.verification_status.get(cap, VerificationStatus.UNVERIFIED).value,
                        "evidence_count": len(profile.evidence.get(cap, []))
                    }
                    for cap, result in profile.capabilities.items()
                }
            }
        return matrix


class CapabilityGapAnalyzer:
    """Analyzes capability gaps and recommends resolutions."""
    
    def __init__(self):
        self.registry = None  # Will be set by CapabilityRegistry
    
    def analyze(self, profile: AgentCapabilityProfile) -> List[CapabilityGap]:
        """Analyze gaps in capability profile."""
        gaps = []
        
        # Define priority capabilities that are critical for safety
        critical_capabilities = {
            AdapterCapability.OBSERVE_STREAM,
            AdapterCapability.OBSERVE_TOOL_PROGRESS,
            AdapterCapability.OBSERVE_TURN_LIFECYCLE,
            AdapterCapability.RECONNECT_STREAM,
            AdapterCapability.VERIFY_SESSION_IDENTITY,
            AdapterCapability.GET_SESSION_STATE,
            AdapterCapability.DETECT_STALE,
            AdapterCapability.DETECT_DISCONNECT,
        }
        
        for capability in AdapterCapability:
            result = profile.capabilities.get(capability)
            verification = profile.verification_status.get(capability, VerificationStatus.UNVERIFIED)
            
            # Determine native support
            native_support = CapabilitySupportStatus.SUPPORTED if result and result.available else (
                CapabilitySupportStatus.PARTIAL if result and not result.available and "partial" in result.reason.lower() else
                CapabilitySupportStatus.UNSUPPORTED if result and not result.available else
                CapabilitySupportStatus.UNKNOWN
            )
            
            # Determine gap
            is_critical = capability in critical_capabilities
            is_supported = result and result.available
            is_verified = verification in (VerificationStatus.VERIFIED, VerificationStatus.MOCK_VERIFIED)
            
            if not is_supported and is_critical:
                # Critical capability missing - gap
                resolution = self._determine_resolution(capability, profile)
                gaps.append(CapabilityGap(
                    capability=capability,
                    desired="Full support with verification",
                    native_support=native_support,
                    native_verification=verification,
                    guardian_provided=False,
                    gap_description=f"Critical capability {capability.value} is not natively supported or verified",
                    resolution_strategy=resolution,
                    safety_risk="HIGH - Critical safety capability missing",
                    priority=1,
                    estimated_effort="Medium",
                    blocker="May require protocol changes or agent cooperation"
                ))
            elif not is_supported and not is_critical:
                # Non-critical capability missing - gap but lower priority
                resolution = self._determine_resolution(capability, profile)
                gaps.append(CapabilityGap(
                    capability=capability,
                    desired="Support with verification",
                    native_support=native_support,
                    native_verification=verification,
                    guardian_provided=False,
                    gap_description=f"Capability {capability.value} is not natively supported",
                    resolution_strategy=resolution,
                    safety_risk="MEDIUM - Useful but not critical for safety",
                    priority=2,
                    estimated_effort="Low",
                    blocker=None
                ))
            elif is_supported and not is_verified:
                # Supported but unverified - verification gap
                gaps.append(CapabilityGap(
                    capability=capability,
                    desired="Verified implementation",
                    native_support=native_support,
                    native_verification=verification,
                    guardian_provided=False,
                    gap_description=f"Capability {capability.value} declared but not verified",
                    resolution_strategy="verify",
                    safety_risk="MEDIUM - Unverified capability may not work as expected",
                    priority=2,
                    estimated_effort="Low",
                    blocker="Requires controlled testing"
                ))
            elif is_supported and is_verified and verification == VerificationStatus.SIMULATED:
                # Simulated only - not real protocol recovery
                gaps.append(CapabilityGap(
                    capability=capability,
                    desired="Real protocol recovery",
                    native_support=native_support,
                    native_verification=verification,
                    guardian_provided=True,
                    gap_description=f"Capability {capability.value} only simulated (callback reattach)",
                    resolution_strategy="implement_real",
                    safety_risk="MEDIUM - Simulation may not reflect real behavior",
                    priority=3,
                    estimated_effort="High",
                    blocker="Requires protocol-level support"
                ))
        
        return gaps
    
    def _determine_resolution(self, capability: AdapterCapability, profile: AgentCapabilityProfile) -> str:
        """Determine resolution strategy for a gap."""
        # Check if Guardian core can provide generically
        generic_core_capabilities = {
            AdapterCapability.DETECT_STALE,
            AdapterCapability.DETECT_DISCONNECT,
            AdapterCapability.WAIT_FOR_BACKEND,
        }
        
        if capability in generic_core_capabilities:
            return "core_generic"
        
        # Check if adapter-specific implementation is needed
        adapter_specific = {
            AdapterCapability.RECONNECT_STREAM,
            AdapterCapability.CANCEL_TURN,
            AdapterCapability.RETRY_REQUEST,
            AdapterCapability.REPLAY_TURN,
            AdapterCapability.REATTACH_SESSION,
            AdapterCapability.FORK_SESSION,
            AdapterCapability.LOAD_SESSION,
            AdapterCapability.SAVE_SESSION,
            AdapterCapability.VERIFY_SESSION_IDENTITY,
            AdapterCapability.VERIFY_SESSION_EXISTS,
            AdapterCapability.GET_SESSION_STATE,
            AdapterCapability.GET_TURN_CONTEXT,
        }
        
        if capability in adapter_specific:
            return "adapter_specific"
        
        # Check if optional module makes sense
        optional_module = {
            AdapterCapability.SWITCH_PROVIDER,
            AdapterCapability.GET_PROVIDER_LIST,
            AdapterCapability.RESTART_AGENT,
            AdapterCapability.GET_AGENT_STATUS,
            AdapterCapability.IS_AGENT_ALIVE,
            AdapterCapability.GET_PARTIAL_TOOL_NAMES,
            AdapterCapability.GET_TOOL_STATE,
        }
        
        if capability in optional_module:
            return "optional_module"
        
        # Default to blocked if protocol doesn't support
        return "blocked"


# Global registry instance
_global_registry: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    """Get global capability registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = CapabilityRegistry()
    return _global_registry


# Import enums
from enum import Enum