"""
Goose Desktop integration module.
"""

from .discovery import (
    discover_goose_backends,
    discover_primary_goose,
    DiscoveryResult,
    GooseConnectionInfo,
)
from .observer import (
    GooseObserver,
    SessionState,
    FailureDetector,
    run_observer,
)
from .protocol import (
    ACPEvent,
    ACPEventType,
    ACPProtocolParser,
    GooseConnectionInfo as ProtocolConnectionInfo,
    StopReason,
)
from .adapter import (
    GooseAdapter,
    GooseAdapterConfig,
    GooseRecoveryContext,
)

__all__ = [
    # Discovery
    "discover_goose_backends",
    "discover_primary_goose",
    "DiscoveryResult",
    "GooseConnectionInfo",
    # Observer
    "GooseObserver",
    "SessionState",
    "FailureDetector",
    "run_observer",
    # Protocol
    "ACPEvent",
    "ACPEventType",
    "ACPProtocolParser",
    "ProtocolConnectionInfo",
    "StopReason",
    # Adapter
    "GooseAdapter",
    "GooseAdapterConfig",
    "GooseRecoveryContext",
]