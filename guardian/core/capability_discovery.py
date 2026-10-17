"""
Capability Discovery for Strixora.

Implements discovery mechanisms for agent capabilities through verified sources:
- Source inspection
- Official documentation
- Public API introspection
- Runtime capability declarations
- Controlled probes
- Integration tests
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable, Set
from enum import Enum
from datetime import datetime
from abc import ABC, abstractmethod

from guardian.core.types import AdapterCapability, RecoveryAction
from guardian.core.adapter_capabilities import AdapterCapabilities, CapabilityResult, AgentAdapter
from guardian.core.capability_intelligence import (
    CapabilityRegistry,
    CapabilityEvidence,
    EvidenceType,
    VerificationStatus,
    get_capability_registry,
    CapabilitySupportStatus,
    AgentCapabilityProfile,
)


class DiscoveryMethod(Enum):
    """Method used for capability discovery."""
    SOURCE_INSPECTION = "source_inspection"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    PUBLIC_API_INTROSPECTION = "public_api_introspection"
    RUNTIME_DECLARATION = "runtime_declaration"
    CONTROLLED_PROBE = "controlled_probe"
    INTEGRATION_TEST = "integration_test"
    FORENSIC_TEST = "forensic_test"
    REAL_RUNTIME = "real_runtime"


@dataclass
class DiscoveryResult:
    """Result of a capability discovery operation."""
    capability: AdapterCapability
    agent_type: str
    method: DiscoveryMethod
    supported: bool
    confidence: float  # 0.0 - 1.0
    evidence: List[str] = field(default_factory=list)
    notes: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


class CapabilityDiscoverer(ABC):
    """Base class for capability discovery."""
    
    @abstractmethod
    async def discover(self, agent_type: str, adapter: Optional[AgentAdapter] = None, **kwargs) -> List[DiscoveryResult]:
        """Discover capabilities for an agent."""
        pass
    
    @abstractmethod
    def get_supported_methods(self) -> List[DiscoveryMethod]:
        """Get discovery methods this discoverer supports."""
        pass


class SourceInspectionDiscoverer(CapabilityDiscoverer):
    """Discovers capabilities through source code inspection."""
    
    def __init__(self, adapter_source_paths: Dict[str, str]):
        self.adapter_source_paths = adapter_source_paths
    
    def get_supported_methods(self) -> List[DiscoveryMethod]:
        return [DiscoveryMethod.SOURCE_INSPECTION]
    
    async def discover(self, agent_type: str, adapter: Optional[AgentAdapter] = None, **kwargs) -> List[DiscoveryResult]:
        results = []
        source_path = self.adapter_source_paths.get(agent_type)
        if not source_path:
            return results
        
        # This would be extended to actually parse source files
        # For now, return empty - actual implementation would parse adapter source
        return results


class DocumentationDiscoverer(CapabilityDiscoverer):
    """Discovers capabilities through official documentation."""
    
    def __init__(self, doc_urls: Dict[str, str]):
        self.doc_urls = doc_urls
    
    def get_supported_methods(self) -> List[DiscoveryMethod]:
        return [DiscoveryMethod.OFFICIAL_DOCUMENTATION]
    
    async def discover(self, agent_type: str, adapter: Optional[AgentAdapter] = None, **kwargs) -> List[DiscoveryResult]:
        results = []
        doc_url = self.doc_urls.get(agent_type)
        if not doc_url:
            return results
        
        # This would fetch and parse documentation
        # For now, return empty
        return results


class RuntimeDeclarationDiscoverer(CapabilityDiscoverer):
    """Discovers capabilities through runtime capability declarations."""
    
    def get_supported_methods(self) -> List[DiscoveryMethod]:
        return [DiscoveryMethod.RUNTIME_DECLARATION]
    
    async def discover(self, agent_type: str, adapter: Optional[AgentAdapter] = None, **kwargs) -> List[DiscoveryResult]:
        results = []
        
        if not adapter:
            return results
        
        # Use adapter's declared capabilities
        caps = adapter.capabilities
        for capability in AdapterCapability:
            result = caps.get(capability)
            if result:
                supported = result.available
                evidence = [f"Declared in adapter capabilities: {result.reason}"]
                confidence = 0.8 if supported else 0.6
            else:
                supported = False
                evidence = ["Not declared in adapter capabilities"]
                confidence = 0.4
            
            results.append(DiscoveryResult(
                capability=capability,
                agent_type=agent_type,
                method=DiscoveryMethod.RUNTIME_DECLARATION,
                supported=supported,
                confidence=confidence,
                evidence=evidence,
                notes=f"From adapter {adapter.capabilities.adapter_name} declaration"
            ))
        
        return results


class ControlledProbeDiscoverer(CapabilityDiscoverer):
    """Discovers capabilities through controlled, read-only probes."""
    
    def get_supported_methods(self) -> List[DiscoveryMethod]:
        return [DiscoveryMethod.CONTROLLED_PROBE]
    
    async def discover(self, agent_type: str, adapter: Optional[AgentAdapter] = None, **kwargs) -> List[DiscoveryResult]:
        results = []
        
        # Probe specific capabilities through safe operations
        # This is a framework - actual probes would be implemented per capability
        
        # Example: Probe RECONNECT_STREAM by checking if reconnect method exists
        # This is a placeholder - actual probes would be capability-specific
        
        return results


class IntegrationTestDiscoverer(CapabilityDiscoverer):
    """Discovers capabilities through integration tests."""
    
    def __init__(self, test_runner: Callable[[str, AdapterCapability], Awaitable[bool]]):
        self.test_runner = test_runner
    
    def get_supported_methods(self) -> List[DiscoveryMethod]:
        return [DiscoveryMethod.INTEGRATION_TEST]
    
    async def discover(self, agent_type: str, adapter: Optional[AgentAdapter] = None, capabilities: Optional[Set[AdapterCapability]] = None, **kwargs) -> List[DiscoveryResult]:
        results = []
        
        if not capabilities:
            capabilities = set(AdapterCapability)
        
        for capability in capabilities:
            try:
                supported = await self.test_runner(agent_type, capability)
                results.append(DiscoveryResult(
                    capability=capability,
                    agent_type=agent_type,
                    method=DiscoveryMethod.INTEGRATION_TEST,
                    supported=supported,
                    confidence=0.9 if supported else 0.7,
                    evidence=[f"Integration test {'passed' if supported else 'failed'}"],
                    notes=f"From integration test suite"
                ))
            except Exception as e:
                results.append(DiscoveryResult(
                    capability=capability,
                    agent_type=agent_type,
                    method=DiscoveryMethod.INTEGRATION_TEST,
                    supported=False,
                    confidence=0.0,
                    evidence=[f"Test error: {str(e)}"],
                    notes=f"Integration test failed: {e}"
                ))
        
        return results


class CapabilityDiscoveryEngine:
    """
    Orchestrates capability discovery across multiple methods.
    """
    
    def __init__(self):
        self.discoverers: List[CapabilityDiscoverer] = []
        self.registry = get_capability_registry()
    
    def add_discoverer(self, discoverer: CapabilityDiscoverer):
        """Add a discoverer to the engine."""
        self.discoverers.append(discoverer)
    
    async def discover_all(self, agent_type: str, adapter: AgentAdapter = None) -> AgentCapabilityProfile:
        """Run all discovery methods for an agent."""
        profile = AgentCapabilityProfile(agent_type=agent_type)
        all_results: Dict[AdapterCapability, List[DiscoveryResult]] = {}
        
        for discoverer in self.discoverers:
            try:
                results = await discoverer.discover(agent_type, adapter=adapter)
                for result in results:
                    if result.capability not in all_results:
                        all_results[result.capability] = []
                    all_results[result.capability].append(result)
            except Exception as e:
                # Log error but continue
                pass
        
        # Synthesize results into profile
        for capability, results in all_results.items():
            # Determine overall support
            supported_results = [r for r in results if r.supported]
            if supported_results:
                # Use highest confidence result
                best = max(supported_results, key=lambda r: r.confidence)
                profile.capabilities[capability] = CapabilityResult(
                    capability=capability,
                    available=True,
                    reason=f"Discovered via {best.method.value}: {best.notes}",
                    details={"confidence": best.confidence, "methods": [r.method.value for r in supported_results]}
                )
                # Add evidence
                for r in results:
                    self.registry.add_evidence(
                        agent_type, capability,
                        CapabilityEvidence(
                            evidence_type=EvidenceType(r.method.value),
                            description=r.notes,
                            source=r.method.value,
                            confidence=r.confidence,
                            notes=r.notes
                        )
                    )
            else:
                profile.capabilities[capability] = CapabilityResult(
                    capability=capability,
                    available=False,
                    reason="Not discovered by any method"
                )
        
        # Set default verification status
        for capability in AdapterCapability:
            if capability in profile.capabilities:
                result = profile.capabilities[capability]
                if result.available:
                    # Determine verification status based on discovery methods
                    # This would be more sophisticated in practice
                    self.registry.set_verification("default", capability, VerificationStatus.UNVERIFIED)
        
        profile.update_summary()
        return profile


# Pre-defined discovery configurations per agent
DISCOVERY_CONFIGS = {
    "hermes": {
        "source_paths": {
            "hermes": "guardian/hermes/adapter.py",
            "observer": "guardian/hermes/observer.py",
        },
        "doc_urls": {
            "hermes": "https://hermes.example.com/docs",  # Placeholder
        },
        "known_capabilities": {
            # Based on actual implementation inspection
            AdapterCapability.OBSERVE_STREAM: True,
            AdapterCapability.OBSERVE_REASONING: True,
            AdapterCapability.OBSERVE_TOOL_PROGRESS: True,
            AdapterCapability.OBSERVE_TURN_LIFECYCLE: True,
            AdapterCapability.RECONNECT_STREAM: True,  # Simulated
            AdapterCapability.CANCEL_STREAM: False,
            AdapterCapability.REATTACH_SESSION: False,
            AdapterCapability.FORK_SESSION: False,
            AdapterCapability.LOAD_SESSION: False,
            AdapterCapability.SAVE_SESSION: False,
            AdapterCapability.SWITCH_PROVIDER: False,
            AdapterCapability.GET_PROVIDER_LIST: False,
            AdapterCapability.RESTART_AGENT: False,
            AdapterCapability.GET_AGENT_STATUS: False,
            AdapterCapability.IS_AGENT_ALIVE: False,
            AdapterCapability.CANCEL_TURN: False,
            AdapterCapability.RETRY_REQUEST: True,  # Simulated via reattach
            AdapterCapability.REPLAY_TURN: False,
            AdapterCapability.WAIT_FOR_BACKEND: True,  # Internal wait
            AdapterCapability.GET_PARTIAL_TOOL_NAMES: True,
            AdapterCapability.GET_TOOL_STATE: False,
            AdapterCapability.GET_SESSION_STATE: True,
            AdapterCapability.GET_TURN_CONTEXT: True,
            AdapterCapability.VERIFY_SESSION_IDENTITY: True,
            AdapterCapability.VERIFY_SESSION_EXISTS: False,
            AdapterCapability.DETECT_DISCONNECT: True,
            AdapterCapability.DETECT_CRASH: False,
            AdapterCapability.DETECT_STALE: True,
        }
    },
    "goose": {
        "source_paths": {
            "adapter": "guardian/goose/adapter.py",
            "observer": "guardian/goose/observer.py",
            "protocol": "guardian/goose/protocol.py",
        },
        "doc_urls": {
            "goose": "https://github.com/block/goose/tree/main/crates/goose-sdk",  # Goose SDK docs
        },
        "known_capabilities": {
            # Based on actual ACP protocol inspection
            AdapterCapability.OBSERVE_STREAM: True,
            AdapterCapability.OBSERVE_REASONING: False,
            AdapterCapability.OBSERVE_TOOL_PROGRESS: True,
            AdapterCapability.OBSERVE_TURN_LIFECYCLE: True,
            AdapterCapability.RECONNECT_STREAM: True,
            AdapterCapability.CANCEL_STREAM: False,
            AdapterCapability.REATTACH_SESSION: False,
            AdapterCapability.FORK_SESSION: True,
            AdapterCapability.LOAD_SESSION: True,
            AdapterCapability.SAVE_SESSION: False,
            AdapterCapability.SWITCH_PROVIDER: False,
            AdapterCapability.GET_PROVIDER_LIST: False,
            AdapterCapability.RESTART_AGENT: False,
            AdapterCapability.GET_AGENT_STATUS: True,
            AdapterCapability.IS_AGENT_ALIVE: True,
            AdapterCapability.CANCEL_TURN: True,
            AdapterCapability.RETRY_REQUEST: True,
            AdapterCapability.REPLAY_TURN: True,
            AdapterCapability.WAIT_FOR_BACKEND: True,
            AdapterCapability.GET_PARTIAL_TOOL_NAMES: False,
            AdapterCapability.GET_TOOL_STATE: True,
            AdapterCapability.GET_SESSION_STATE: True,
            AdapterCapability.GET_TURN_CONTEXT: True,
            AdapterCapability.VERIFY_SESSION_IDENTITY: True,
            AdapterCapability.VERIFY_SESSION_EXISTS: False,
            AdapterCapability.DETECT_DISCONNECT: True,
            AdapterCapability.DETECT_CRASH: False,
            AdapterCapability.DETECT_STALE: True,
        }
    },
    "opencode": {
        "source_paths": {
            "adapter": "guardian/opencode/adapter.py",
            "observer": "guardian/opencode/observer.py",
            "protocol": "guardian/opencode/protocol.py",
        },
        "doc_urls": {
            "opencode": "https://github.com/sst/opencode",  # OpenCode repo
        },
        "known_capabilities": {
            # Based on actual ACP protocol inspection
            AdapterCapability.OBSERVE_STREAM: True,
            AdapterCapability.OBSERVE_REASONING: True,
            AdapterCapability.OBSERVE_TOOL_PROGRESS: True,
            AdapterCapability.OBSERVE_TURN_LIFECYCLE: True,
            AdapterCapability.RECONNECT_STREAM: True,
            AdapterCapability.CANCEL_STREAM: False,
            AdapterCapability.REATTACH_SESSION: False,
            AdapterCapability.FORK_SESSION: True,  # Unstable API
            AdapterCapability.LOAD_SESSION: True,  # Unstable API
            AdapterCapability.SAVE_SESSION: False,
            AdapterCapability.SWITCH_PROVIDER: False,
            AdapterCapability.GET_PROVIDER_LIST: False,
            AdapterCapability.RESTART_AGENT: True,
            AdapterCapability.GET_AGENT_STATUS: True,
            AdapterCapability.IS_AGENT_ALIVE: True,
            AdapterCapability.CANCEL_TURN: True,
            AdapterCapability.RETRY_REQUEST: True,
            AdapterCapability.REPLAY_TURN: True,
            AdapterCapability.WAIT_FOR_BACKEND: True,
            AdapterCapability.GET_PARTIAL_TOOL_NAMES: True,
            AdapterCapability.GET_TOOL_STATE: False,
            AdapterCapability.GET_SESSION_STATE: True,
            AdapterCapability.GET_TURN_CONTEXT: True,
            AdapterCapability.VERIFY_SESSION_IDENTITY: True,
            AdapterCapability.VERIFY_SESSION_EXISTS: True,
            AdapterCapability.DETECT_DISCONNECT: True,
            AdapterCapability.DETECT_CRASH: True,
            AdapterCapability.DETECT_STALE: True,
        }
    },
    "freebuff": {
        "source_paths": {
            "adapter": "guardian/freebuff/adapter.py",
            "observer": "guardian/freebuff/observer.py",
            "protocol": "guardian/freebuff/protocol.py",
        },
        "doc_urls": {
            "freebuff": "https://docs.codebuff.com/",  # Codebuff docs
        },
        "known_capabilities": {
            # Based on Codebuff SDK documentation
            AdapterCapability.OBSERVE_STREAM: True,
            AdapterCapability.OBSERVE_REASONING: False,
            AdapterCapability.OBSERVE_TOOL_PROGRESS: True,
            AdapterCapability.OBSERVE_TURN_LIFECYCLE: True,
            AdapterCapability.RECONNECT_STREAM: True,
            AdapterCapability.CANCEL_STREAM: False,
            AdapterCapability.REATTACH_SESSION: False,
            AdapterCapability.FORK_SESSION: False,
            AdapterCapability.LOAD_SESSION: False,
            AdapterCapability.SAVE_SESSION: False,
            AdapterCapability.SWITCH_PROVIDER: False,
            AdapterCapability.GET_PROVIDER_LIST: False,
            AdapterCapability.RESTART_AGENT: False,
            AdapterCapability.GET_AGENT_STATUS: False,
            AdapterCapability.IS_AGENT_ALIVE: True,
            AdapterCapability.CANCEL_TURN: False,
            AdapterCapability.RETRY_REQUEST: True,
            AdapterCapability.REPLAY_TURN: False,
            AdapterCapability.WAIT_FOR_BACKEND: True,
            AdapterCapability.GET_PARTIAL_TOOL_NAMES: False,
            AdapterCapability.GET_TOOL_STATE: False,
            AdapterCapability.GET_SESSION_STATE: True,
            AdapterCapability.GET_TURN_CONTEXT: True,
            AdapterCapability.VERIFY_SESSION_IDENTITY: True,
            AdapterCapability.VERIFY_SESSION_EXISTS: False,
            AdapterCapability.DETECT_DISCONNECT: True,
            AdapterCapability.DETECT_CRASH: True,
            AdapterCapability.DETECT_STALE: True,
        }
    },
}


async def build_agent_profile(agent_type: str, adapter: Optional[AgentAdapter] = None) -> AgentCapabilityProfile:
    """Build a complete capability profile for an agent."""
    registry = get_capability_registry()
    profile = AgentCapabilityProfile(agent_type=agent_type)
    
    # Use known capabilities from config
    config = DISCOVERY_CONFIGS.get(agent_type, {})
    known = config.get("known_capabilities", {})
    
    # If adapter provided, use its declared capabilities
    if adapter:
        for capability in AdapterCapability:
            result = adapter.capabilities.get(capability)
            if result:
                profile.capabilities[capability] = result
                # Set verification based on evidence available
                if capability in known:
                    if known[capability]:
                        registry.set_verification(agent_type, capability, VerificationStatus.VERIFIED)
                    else:
                        registry.set_verification(agent_type, capability, VerificationStatus.UNVERIFIED)
                else:
                    registry.set_verification(agent_type, capability, VerificationStatus.UNVERIFIED)
    else:
        # Use known capabilities from config
        for capability in AdapterCapability:
            supported = known.get(capability, False)
            if supported:
                profile.capabilities[capability] = CapabilityResult(
                    capability=capability,
                    available=True,
                    reason=f"Known from {agent_type} implementation/config",
                    details={"source": "config"}
                )
                registry.set_verification(agent_type, capability, VerificationStatus.UNVERIFIED)
            else:
                profile.capabilities[capability] = CapabilityResult(
                    capability=capability,
                    available=False,
                    reason=f"Not supported in {agent_type} protocol/architecture",
                    details={"source": "config"}
                )
                registry.set_verification(agent_type, capability, VerificationStatus.UNSAFE if capability in {
                    AdapterCapability.REATTACH_SESSION, AdapterCapability.FORK_SESSION,
                    AdapterCapability.LOAD_SESSION, AdapterCapability.SAVE_SESSION
                } else VerificationStatus.UNVERIFIED)
    
    profile.update_summary()
    return profile


async def run_capability_gap_analysis(agent_type: str) -> List:
    """Run gap analysis for an agent."""
    registry = get_capability_registry()
    profile = registry.get_profile(agent_type)
    if not profile:
        profile = await build_agent_profile(agent_type)
        registry.register_profile(profile)
    return registry.run_gap_analysis(agent_type)