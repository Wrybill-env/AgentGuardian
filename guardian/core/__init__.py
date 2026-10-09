"""
Guardian Core - Agent-agnostic watchdog core.

This module contains the core state machine, failure classification,
safety gates, and recovery framework that is independent of any
specific agent implementation.
"""

from .types import (
    GuardianState,
    FailureType,
    RecoveryAction,
    SafetyMode,
    TurnPhase,
    TurnContext,
    FailureClassification,
    RecoveryAttempt,
    GuardianMetrics,
    SafetyVerdict,
)
from .failure_classifier import FailureClassifier
from .safety_gate import SafetyGate
from .backoff import BackoffStrategy, BackoffConfig, JitterType, DEFAULT_BACKOFF
from .state_machine import GuardianStateMachine, StateMachineEvent
from .config import GuardianConfig

# New reliability foundation modules
from .event_model import (
    EventCategory,
    StreamEventType,
    ToolEventType,
    TurnEventType,
    ConnectionEventType,
    FailureEventType,
    RecoveryEventType,
    BaseEvent,
    StreamEvent,
    ToolEvent,
    TurnEvent,
    ConnectionEvent,
    FailureEvent,
    RecoveryEvent,
    AgentEvent,
    create_stream_event,
    create_tool_event,
    create_turn_event,
    create_failure_event,
    create_recovery_event,
)
from .session_state import (
    TurnPhase,
    SideEffectCategory,
    SideEffectEvidence,
    TurnState,
    SessionState,
    create_session,
)
from .stream_state import (
    StreamPhase,
    StaleReason,
    StreamState,
    create_stream_state,
)
from .failure_model import (
    FailureCategory,
    FailureType,
    FailureEvidence,
    RecoveryHint,
    FailureClassification,
    FailurePattern,
    GENERIC_FAILURE_PATTERNS,
    DEFAULT_RECOVERY_HINTS,
    FAILURE_TYPE_TO_CATEGORY,
    FailureClassifier,
)
from .recovery_budget import (
    BudgetExhaustedReason,
    RecoveryBudgetConfig,
    RecoveryAttemptRecord,
    RecoveryBudget,
    RecoveryBudgetManager,
)
from .recovery_ladder import (
    LadderDelaySource,
    LadderConfig,
    LadderState,
    RecoveryLadder,
    HERMES_LADDER,
    CONSERVATIVE_LADDER,
    AGGRESSIVE_LADDER,
    INDEFINITE_LADDER,
    create_ladder,
)
from .recovery_planner import (
    RecoveryAction,
    RecoveryPlan,
    RecoveryContext,
    RecoveryPlanner,
    ActionRecommendation,
)
from .recovery_executor import (
    RecoveryExecutor,
    RecoveryExecutionContext,
    RecoveryExecutionResult,
    ExecutionResult,
)
from .adapter_capabilities import (
    AdapterCapability,
    CapabilityResult,
    AdapterCapabilities,
    AgentAdapter,
    validate_capabilities,
    HERMES_CAPABILITIES,
    GOOSE_CAPABILITIES,
    create_capabilities,
)

__all__ = [
    # Types
    "GuardianState",
    "FailureType",
    "RecoveryAction",
    "SafetyMode",
    "TurnPhase",
    "TurnContext",
    "FailureClassification",
    "RecoveryAttempt",
    "GuardianMetrics",
    "SafetyVerdict",
    # Components
    "FailureClassifier",
    "SafetyGate",
    "BackoffStrategy",
    "BackoffConfig",
    "JitterType",
    "DEFAULT_BACKOFF",
    "CONSERVATIVE_BACKOFF",
    "AGGRESSIVE_BACKOFF",
    "INDEFINITE_BACKOFF",
    "GuardianStateMachine",
    "StateMachineEvent",
    "GuardianConfig",
    # Event model
    "EventCategory",
    "StreamEventType",
    "ToolEventType",
    "TurnEventType",
    "ConnectionEventType",
    "FailureEventType",
    "RecoveryEventType",
    "BaseEvent",
    "StreamEvent",
    "ToolEvent",
    "TurnEvent",
    "ConnectionEvent",
    "FailureEvent",
    "RecoveryEvent",
    "AgentEvent",
    "create_stream_event",
    "create_tool_event",
    "create_turn_event",
    "create_failure_event",
    "create_recovery_event",
    # Session state
    "TurnPhase",
    "SideEffectCategory",
    "SideEffectEvidence",
    "TurnState",
    "SessionState",
    "create_session",
    # Stream state
    "StreamPhase",
    "StaleReason",
    "StreamState",
    "create_stream_state",
    # Failure model
    "FailureCategory",
    "FailureType",
    "FailureEvidence",
    "RecoveryHint",
    "FailureClassification",
    "FailurePattern",
    "GENERIC_FAILURE_PATTERNS",
    "DEFAULT_RECOVERY_HINTS",
    "FAILURE_TYPE_TO_CATEGORY",
    "FailureClassifier",
    # Recovery budget
    "BudgetExhaustedReason",
    "RecoveryBudgetConfig",
    "RecoveryAttemptRecord",
    "RecoveryBudget",
    "RecoveryBudgetManager",
    # Recovery ladder
    "LadderDelaySource",
    "LadderConfig",
    "LadderState",
    "RecoveryLadder",
    "HERMES_LADDER",
    "CONSERVATIVE_LADDER",
    "AGGRESSIVE_LADDER",
    "INDEFINITE_LADDER",
    "create_ladder",
    # Recovery planner
    "RecoveryAction",
    "RecoveryPlan",
    "RecoveryContext",
    "RecoveryPlanner",
    "ActionRecommendation",
    # Recovery executor
    "RecoveryExecutor",
    "RecoveryExecutionContext",
    "RecoveryExecutionResult",
    "ExecutionResult",
    # Adapter capabilities
    "AdapterCapability",
    "CapabilityResult",
    "AdapterCapabilities",
    "AgentAdapter",
    "validate_capabilities",
    "HERMES_CAPABILITIES",
    "GOOSE_CAPABILITIES",
    "create_capabilities",
    # Failure model
    "FAILURE_TYPE_TO_CATEGORY",
]