"""
Recovery planner for Agent Guardian.

Creates recovery plans based on failure classification, safety evaluation,
and available adapter capabilities.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable
import time

from .types import (
    RecoveryAction,
    SafetyVerdict,
    SafetyMode,
    FailureClassification,
)
from .session_state import SessionState, TurnState, SideEffectEvidence
from .safety_gate import SafetyGate, SafetyCheckResult
from .recovery_budget import RecoveryBudget, RecoveryBudgetConfig, RecoveryBudgetManager
from .recovery_ladder import RecoveryLadder, LadderConfig
from .backoff import BackoffStrategy, BackoffConfig
from .adapter_capabilities import AdapterCapability


@dataclass
class RecoveryPlan:
    """Complete recovery plan with all context."""
    # Identification
    plan_id: str
    failure_classification: FailureClassification
    session_id: str
    turn_id: Optional[str] = None
    
    # Safety evaluation
    safety_verdict: SafetyVerdict = SafetyVerdict.AMBIGUOUS
    safety_reason: str = ""
    safety_blocking_factors: List[str] = field(default_factory=list)
    
    # Plan
    recommended_action: RecoveryAction = RecoveryAction.NO_SAFE_RECOVERY
    alternative_actions: List[RecoveryAction] = field(default_factory=list)
    delay_seconds: float = 0.0
    cycle_number: int = 0
    max_cycles: int = 0
    
    # Budget
    budget_status: Dict[str, Any] = field(default_factory=dict)
    can_continue: bool = True
    
    # Capability check
    required_capabilities: List[str] = field(default_factory=list)
    available_capabilities: List[str] = field(default_factory=list)
    missing_capabilities: List[str] = field(default_factory=list)
    
    # Reasoning
    reason: str = ""
    confidence: float = 0.0
    
    # Timing
    created_at: float = field(default_factory=time.time)
    valid_until: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "failure_type": self.failure_classification.failure_type.value,
            "failure_category": self.failure_classification.category.value,
            "safety_verdict": self.safety_verdict.value,
            "safety_reason": self.safety_reason,
            "safety_blocking_factors": self.safety_blocking_factors,
            "recommended_action": self.recommended_action.value,
            "alternative_actions": [a.value for a in self.alternative_actions],
            "delay_seconds": self.delay_seconds,
            "cycle_number": self.cycle_number,
            "max_cycles": self.max_cycles,
            "budget_status": self.budget_status,
            "can_continue": self.can_continue,
            "required_capabilities": self.required_capabilities,
            "available_capabilities": self.available_capabilities,
            "missing_capabilities": self.missing_capabilities,
            "reason": self.reason,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "valid_until": self.valid_until,
        }


@dataclass
class RecoveryContext:
    """Context provided to planner for decision making."""
    # State
    session: SessionState
    current_turn: Optional[TurnState] = None
    
    # Failure
    failure_classification: Optional[FailureClassification] = None
    
    # Adapter capabilities
    adapter_capabilities: Dict[str, bool] = field(default_factory=dict)
    adapter_name: str = "unknown"
    
    # Configuration
    safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY
    recovery_budget_config: Optional[RecoveryBudgetConfig] = None
    ladder_config: Optional[LadderConfig] = None
    backoff_config: Optional[BackoffConfig] = None
    
    # History
    recovery_history: List[Dict[str, Any]] = field(default_factory=list)
    ladder_state: Optional[Dict[str, Any]] = None
    
    # Custom eligibility check
    custom_eligibility: Optional[Callable] = None


class RecoveryPlanner:
    """
    Plans recovery actions based on failure classification, safety, and capabilities.
    
    This is the decision engine - it does NOT execute recovery, only plans it.
    """
    
    def __init__(
        self,
        safety_gate: SafetyGate,
        budget_manager: RecoveryBudgetManager,
        ladder: RecoveryLadder,
        backoff: BackoffStrategy,
    ):
        self.safety_gate = safety_gate
        self.budget_manager = budget_manager
        self.ladder = ladder
        self.backoff = backoff
    
    def create_plan(self, context: RecoveryContext) -> RecoveryPlan:
        """
        Create a complete recovery plan.
        
        This is the main entry point - evaluates failure, safety, capabilities,
        and budget to produce an actionable plan.
        """
        plan_id = f"plan_{context.session.session_id}_{int(time.time() * 1000)}"
        
        # Start with default "no safe recovery" plan
        plan = RecoveryPlan(
            plan_id=plan_id,
            failure_classification=context.failure_classification,
            session_id=context.session.session_id,
            turn_id=context.current_turn.turn_id if context.current_turn else None,
        )
        
        # No failure = no recovery needed
        if not context.failure_classification:
            plan.recommended_action = RecoveryAction.NONE
            plan.reason = "No failure detected"
            plan.confidence = 1.0
            return plan
        
        failure = context.failure_classification
        
        # Step 1: Check budget
        budget = self.budget_manager.get_budget(failure.failure_type.value)
        if not budget:
            # Start new budget sequence
            budget = self.budget_manager.start_sequence(
                failure.failure_type.value,
                context.recovery_budget_config,
            )
        
        plan.budget_status = budget.get_status()
        plan.can_continue = budget.can_continue()
        plan.cycle_number = budget.recovery_cycles + 1
        plan.max_cycles = budget.config.max_recovery_cycles
        
        if not plan.can_continue:
            plan.recommended_action = RecoveryAction.NO_SAFE_RECOVERY
            plan.reason = f"Recovery budget exhausted: {budget.exhausted_reason.value if budget.exhausted_reason else 'unknown'}"
            plan.confidence = 1.0
            return plan
        
        # Step 2: Check safety for each possible action
        safety_results = self._evaluate_all_actions_safety(context, plan)
        
        # Step 3: Determine recommended action based on failure hints + safety
        recommended = self._select_recommended_action(
            failure, safety_results, context, plan
        )
        
        plan.recommended_action = recommended.action
        plan.alternative_actions = recommended.alternatives
        plan.delay_seconds = recommended.delay
        plan.safety_verdict = recommended.safety_verdict
        plan.safety_reason = recommended.safety_reason
        plan.safety_blocking_factors = recommended.blocking_factors
        plan.required_capabilities = recommended.required_capabilities
        plan.available_capabilities = recommended.available_capabilities
        plan.missing_capabilities = recommended.missing_capabilities
        plan.reason = recommended.reason
        plan.confidence = recommended.confidence
        
        # Step 4: If in DRY_RUN or OBSERVE_ONLY, ensure no execution
        if context.safety_mode in (SafetyMode.OBSERVE_ONLY, SafetyMode.DRY_RUN):
            if plan.recommended_action != RecoveryAction.NONE:
                plan.reason += f" [DRY_RUN/OBSERVE_ONLY: action would be {plan.recommended_action.value}]"
        
        return plan
    
    def _evaluate_all_actions_safety(
        self,
        context: RecoveryContext,
        plan: RecoveryPlan,
    ) -> Dict[RecoveryAction, SafetyCheckResult]:
        """Evaluate safety for all possible recovery actions."""
        turn_context = context.current_turn.evidence if context.current_turn else None
        # Convert TurnState evidence to TurnContext format for safety gate
        # For now, use a simple adapter
        
        results = {}
        
        # Get hinted actions from failure classification
        hinted_actions = failure.recovery_hints.recommended_actions
        
        # Map string actions to enum
        action_map = {
            "wait_and_retry": RecoveryAction.WAIT_AND_RETRY,
            "cancel_and_replay": RecoveryAction.CANCEL_AND_REPLAY,
            "reconnect_stream": RecoveryAction.RECONNECT_STREAM,
            "reconnect_acp": RecoveryAction.RECONNECT_STREAM,
            "reattach_session": RecoveryAction.REATTACH_SESSION,
            "reload_session": RecoveryAction.REATTACH_SESSION,
            "fork_session": RecoveryAction.FORK_SESSION,
            "switch_provider": RecoveryAction.SWITCH_PROVIDER,
            "wait_for_backend": RecoveryAction.WAIT_FOR_BACKEND,
            "restart_agent": RecoveryAction.RESTART_AGENT,
            "human_intervention": RecoveryAction.HUMAN_INTERVENTION,
        }
        
        for action_str in hinted_actions:
            action = action_map.get(action_str)
            if action:
                result = self.safety_gate.check_recovery_action_safety(
                    action=action,
                    turn_context=turn_context,
                    failure_classification=failure,
                )
                results[action] = result
        
        # Also check common actions not in hints
        for action in [RecoveryAction.WAIT_AND_RETRY, RecoveryAction.RECONNECT_STREAM, 
                       RecoveryAction.WAIT_FOR_BACKEND, RecoveryAction.HUMAN_INTERVENTION]:
            if action not in results:
                result = self.safety_gate.check_recovery_action_safety(
                    action=action,
                    turn_context=turn_context,
                    failure_classification=failure,
                )
                results[action] = result
        
        return results
    
    def _select_recommended_action(
        self,
        failure: FailureClassification,
        safety_results: Dict[RecoveryAction, SafetyCheckResult],
        context: RecoveryContext,
        plan: RecoveryPlan,
    ) -> "ActionRecommendation":
        """Select the best recovery action based on hints, safety, and capabilities."""
        
        # Get hinted actions in priority order
        hinted_actions = failure.recovery_hints.recommended_actions
        action_map = {
            "wait_and_retry": RecoveryAction.WAIT_AND_RETRY,
            "cancel_and_replay": RecoveryAction.CANCEL_AND_REPLAY,
            "reconnect_stream": RecoveryAction.RECONNECT_STREAM,
            "reconnect_acp": RecoveryAction.RECONNECT_STREAM,
            "reattach_session": RecoveryAction.REATTACH_SESSION,
            "reload_session": RecoveryAction.REATTACH_SESSION,
            "fork_session": RecoveryAction.FORK_SESSION,
            "switch_provider": RecoveryAction.SWITCH_PROVIDER,
            "wait_for_backend": RecoveryAction.WAIT_FOR_BACKEND,
            "restart_agent": RecoveryAction.RESTART_AGENT,
            "human_intervention": RecoveryAction.HUMAN_INTERVENTION,
        }
        
        # Evaluate each hinted action
        for action_str in hinted_actions:
            action = action_map.get(action_str)
            if not action:
                continue
            
            safety = safety_results.get(action)
            if not safety:
                continue
            
            # Check if adapter has capability
            capability = self._action_to_capability(action)
            has_capability = context.adapter_capabilities.get(capability, False)
            
            if safety.verdict == SafetyVerdict.SAFE and has_capability:
                # Calculate delay
                delay = self._calculate_delay(action, failure, context, plan)
                
                return ActionRecommendation(
                    action=action,
                    alternatives=[a for a in hinted_actions if action_map.get(a) != action],
                    delay=delay,
                    safety_verdict=SafetyVerdict.SAFE,
                    safety_reason=safety.reason,
                    blocking_factors=[],
                    required_capabilities=[capability],
                    available_capabilities=[capability],
                    missing_capabilities=[],
                    reason=f"Failure {failure.failure_type.value}: {action_str} is safe and supported",
                    confidence=failure.confidence * 0.9,
                )
            
            elif safety.verdict == SafetyVerdict.SAFE and not has_capability:
                # Safe but not supported
                return ActionRecommendation(
                    action=RecoveryAction.HUMAN_INTERVENTION,
                    alternatives=[action],
                    delay=0.0,
                    safety_verdict=SafetyVerdict.AMBIGUOUS,
                    safety_reason=f"Action {action.value} is safe but adapter lacks capability: {capability}",
                    blocking_factors=[f"Missing capability: {capability}"],
                    required_capabilities=[capability],
                    available_capabilities=[],
                    missing_capabilities=[capability],
                    reason=f"Cannot execute {action.value} - capability not available",
                    confidence=0.5,
                )
        
        # No safe hinted actions - check for WAIT_FOR_BACKEND (always safe)
        wait_safety = safety_results.get(RecoveryAction.WAIT_FOR_BACKEND)
        if wait_safety and wait_safety.verdict == SafetyVerdict.SAFE:
            return ActionRecommendation(
                action=RecoveryAction.WAIT_FOR_BACKEND,
                alternatives=[],
                delay=failure.recovery_hints.base_backoff_seconds,
                safety_verdict=SafetyVerdict.SAFE,
                safety_reason=wait_safety.reason,
                blocking_factors=[],
                required_capabilities=[],
                available_capabilities=[],
                missing_capabilities=[],
                reason="No replay-safe actions available; waiting for backend recovery",
                confidence=0.7,
            )
        
        # Check HUMAN_INTERVENTION (always safe)
        human_safety = safety_results.get(RecoveryAction.HUMAN_INTERVENTION)
        if human_safety:
            return ActionRecommendation(
                action=RecoveryAction.HUMAN_INTERVENTION,
                alternatives=[],
                delay=0.0,
                safety_verdict=SafetyVerdict.SAFE,
                safety_reason="Human intervention required - no safe automatic recovery",
                blocking_factors=safety_results.get(failure.recovery_hints.recommended_actions[0], SafetyCheckResult(
                    verdict=SafetyVerdict.UNSAFE, reason="", blocking_factors=[]
                )).blocking_factors if failure.recovery_hints.recommended_actions else [],
                required_capabilities=[],
                available_capabilities=[],
                missing_capabilities=[],
                reason="All recovery actions blocked by safety gate - human decision required",
                confidence=1.0,
            )
        
        # Fallback - no safe recovery
        return ActionRecommendation(
            action=RecoveryAction.NO_SAFE_RECOVERY,
            alternatives=[],
            delay=0.0,
            safety_verdict=SafetyVerdict.UNSAFE,
            safety_reason="No safe recovery path identified",
            blocking_factors=["All actions blocked by safety or capabilities"],
            required_capabilities=[],
            available_capabilities=[],
            missing_capabilities=[],
            reason="Recovery budget may remain but no action passes safety gate",
            confidence=1.0,
        )
    
    def _action_to_capability(self, action: RecoveryAction) -> str:
        """Map recovery action to adapter capability."""
        mapping = {
            RecoveryAction.WAIT_AND_RETRY: "retry_request",
            RecoveryAction.CANCEL_AND_REPLAY: "retry_request",  # Requires cancel + retry
            RecoveryAction.RECONNECT_STREAM: "reconnect_stream",
            RecoveryAction.REATTACH_SESSION: "reattach_session",
            RecoveryAction.FORK_SESSION: "fork_session",
            RecoveryAction.SWITCH_PROVIDER: "switch_provider",
            RecoveryAction.WAIT_FOR_BACKEND: "wait_for_backend",
            RecoveryAction.RESTART_AGENT: "restart_agent",
            RecoveryAction.HUMAN_INTERVENTION: "human_intervention",
        }
        return mapping.get(action, "unknown")
    
    def _calculate_delay(
        self,
        action: RecoveryAction,
        failure: FailureClassification,
        context: RecoveryContext,
        plan: RecoveryPlan,
    ) -> float:
        """Calculate delay for the recovery action."""
        
        # For ladder-eligible failures, use ladder
        if action in (RecoveryAction.WAIT_AND_RETRY, RecoveryAction.RECONNECT_STREAM, 
                      RecoveryAction.CANCEL_AND_REPLAY):
            # Check ladder eligibility
            turn = context.current_turn
            visible_output = turn.evidence.visible_text_delivered if turn else False
            tool_activity = len(turn.evidence.tools_started) > 0 if turn else False
            
            # Try ladder first
            ladder_delay = self.ladder.get_next_delay(
                visible_output_delivered=visible_output,
                tool_activity_observed=tool_activity,
            )
            
            if ladder_delay > 0:
                return ladder_delay
            
            # Fall back to backoff
            return self.backoff.next_delay()
        
        # For other actions, use hinted base backoff
        return failure.recovery_hints.base_backoff_seconds


@dataclass
class ActionRecommendation:
    """Internal recommendation from planner."""
    action: RecoveryAction
    alternatives: List[str]
    delay: float
    safety_verdict: SafetyVerdict
    safety_reason: str
    blocking_factors: List[str]
    required_capabilities: List[str]
    available_capabilities: List[str]
    missing_capabilities: List[str]
    reason: str
    confidence: float