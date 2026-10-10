"""
Agent Guardian - AI Coding Agent Reliability Layer

A safety-first reliability layer for AI coding agents.
Provides observation, failure detection, and recovery for Hermes, Goose, OpenCode, and FreeBuff.
"""

__version__ = "0.1.0"
__author__ = "Agent Guardian Project"

# Core exports
from guardian.core import (
    GuardianStateMachine,
    GuardianConfig,
    SafetyMode,
    TurnContext,
    TurnPhase,
    StateMachineEvent,
    FailureClassification,
    RecoveryAction,
    SafetyVerdict,
)

# Adapter exports
from guardian.hermes import HermesAdapter, HermesAdapterConfig
from guardian.goose import GooseAdapter, GooseAdapterConfig
from guardian.opencode import OpenCodeAdapter, OpenCodeAdapterConfig
from guardian.freebuff import FreeBuffAdapter, FreeBuffAdapterConfig