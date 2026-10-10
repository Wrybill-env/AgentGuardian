"""Guardian State Machine - Core watchdog logic."""

import asyncio
import time
from typing import Optional, Callable, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

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
)
from .failure_classifier import FailureClassifier
from .safety_gate import SafetyGate, SafetyVerdict
from .backoff import BackoffStrategy, BackoffConfig, DEFAULT_BACKOFF
from .recovery_executor import RecoveryExecutor
from .recovery_ladder import RecoveryLadder, LadderConfig
from .adapter_capabilities import AgentAdapter
from .recovery_budget import RecoveryBudgetManager
from .ping_monitor import PingMonitor, PingConfig, InProcessPingTransport, WebSocketPingTransport


class StateMachineEvent(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    USER_MESSAGE_SENT = "user_message_sent"
    ASSISTANT_CHUNK = "assistant_chunk"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    TURN_COMPLETE = "turn_complete"
    TURN_ERROR = "turn_error"
    TURN_CANCELLED = "turn_cancelled"
    TURN_MAX_TURNS = "turn_max_turns"
    FAILURE_DETECTED = "failure_detected"
    STALE_DETECTED = "stale_detected"
    BACKOFF_COMPLETE = "backoff_complete"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETE = "recovery_complete"
    RECOVERY_FAILED = "recovery_failed"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    MANUAL_OVERRIDE = "manual_override"
    SHUTDOWN = "shutdown"


@dataclass
class StateTransition:
    from_state: GuardianState
    to_state: GuardianState
    event: StateMachineEvent
    timestamp: datetime
    details: dict = field(default_factory=dict)


class GuardianStateMachine:
    VALID_TRANSITIONS = {
        GuardianState.IDLE: {StateMachineEvent.CONNECTED: GuardianState.OBSERVING},
        GuardianState.OBSERVING: {
            StateMachineEvent.USER_MESSAGE_SENT: GuardianState.STREAMING,
            StateMachineEvent.DISCONNECTED: GuardianState.IDLE,
            StateMachineEvent.FAILURE_DETECTED: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.STALE_DETECTED: GuardianState.POSSIBLE_FAILURE,
        },
        GuardianState.STREAMING: {
            StateMachineEvent.ASSISTANT_CHUNK: GuardianState.STREAMING,
            StateMachineEvent.TOOL_STARTED: GuardianState.TOOL_ACTIVE,
            StateMachineEvent.TURN_COMPLETE: GuardianState.COMPLETED,
            StateMachineEvent.TURN_ERROR: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.TURN_CANCELLED: GuardianState.COMPLETED,
            StateMachineEvent.TURN_MAX_TURNS: GuardianState.COMPLETED,
            StateMachineEvent.FAILURE_DETECTED: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.STALE_DETECTED: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.DISCONNECTED: GuardianState.POSSIBLE_FAILURE,
        },
        GuardianState.TOOL_ACTIVE: {
            StateMachineEvent.TOOL_COMPLETED: GuardianState.STREAMING,
            StateMachineEvent.TOOL_FAILED: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.ASSISTANT_CHUNK: GuardianState.STREAMING,
            StateMachineEvent.TURN_COMPLETE: GuardianState.COMPLETED,
            StateMachineEvent.TURN_ERROR: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.TURN_CANCELLED: GuardianState.COMPLETED,
            StateMachineEvent.TURN_MAX_TURNS: GuardianState.COMPLETED,
            StateMachineEvent.FAILURE_DETECTED: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.STALE_DETECTED: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.DISCONNECTED: GuardianState.POSSIBLE_FAILURE,
        },
        GuardianState.COMPLETING: {
            StateMachineEvent.TURN_COMPLETE: GuardianState.COMPLETED,
            StateMachineEvent.TURN_ERROR: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.TURN_CANCELLED: GuardianState.COMPLETED,
            StateMachineEvent.TURN_MAX_TURNS: GuardianState.COMPLETED,
            StateMachineEvent.DISCONNECTED: GuardianState.POSSIBLE_FAILURE,
        },
        GuardianState.COMPLETED: {
            StateMachineEvent.USER_MESSAGE_SENT: GuardianState.STREAMING,
            StateMachineEvent.DISCONNECTED: GuardianState.IDLE,
            StateMachineEvent.FAILURE_DETECTED: GuardianState.POSSIBLE_FAILURE,
        },
        GuardianState.POSSIBLE_FAILURE: {
            StateMachineEvent.BACKOFF_COMPLETE: GuardianState.RECOVERY_WAIT,
            StateMachineEvent.RECOVERY_STARTED: GuardianState.RECOVERY_ATTEMPT,
            StateMachineEvent.MANUAL_OVERRIDE: GuardianState.FAILED_PERMANENTLY,
            StateMachineEvent.DISCONNECTED: GuardianState.IDLE,
        },
        GuardianState.RECOVERY_WAIT: {
            StateMachineEvent.BACKOFF_COMPLETE: GuardianState.RECOVERY_ATTEMPT,
            StateMachineEvent.RECOVERY_STARTED: GuardianState.RECOVERY_ATTEMPT,
            StateMachineEvent.MANUAL_OVERRIDE: GuardianState.FAILED_PERMANENTLY,
            StateMachineEvent.DISCONNECTED: GuardianState.IDLE,
        },
        GuardianState.RECOVERY_ATTEMPT: {
            StateMachineEvent.RECOVERY_COMPLETE: GuardianState.VERIFYING,
            StateMachineEvent.RECOVERY_FAILED: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.DISCONNECTED: GuardianState.IDLE,
        },
        GuardianState.VERIFYING: {
            StateMachineEvent.VERIFICATION_PASSED: GuardianState.RECOVERED,
            StateMachineEvent.VERIFICATION_FAILED: GuardianState.POSSIBLE_FAILURE,
            StateMachineEvent.DISCONNECTED: GuardianState.IDLE,
        },
        GuardianState.RECOVERED: {
            StateMachineEvent.USER_MESSAGE_SENT: GuardianState.STREAMING,
            StateMachineEvent.DISCONNECTED: GuardianState.IDLE,
        },
        GuardianState.FAILED_PERMANENTLY: {
            StateMachineEvent.MANUAL_OVERRIDE: GuardianState.OBSERVING,
            StateMachineEvent.DISCONNECTED: GuardianState.IDLE,
        },
        GuardianState.UNKNOWN_UNSAFE: {
            StateMachineEvent.MANUAL_OVERRIDE: GuardianState.OBSERVING,
            StateMachineEvent.DISCONNECTED: GuardianState.IDLE,
        },
    }

    def __init__(
        self,
        safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY,
        backoff_config: Optional[BackoffConfig] = None,
        stale_threshold: float = 60.0,
        on_state_change: Optional[Callable[[GuardianState, GuardianState, StateMachineEvent], None]] = None,
        on_failure: Optional[Callable[[FailureClassification, TurnContext], None]] = None,
        on_recovery_action: Optional[Callable[[RecoveryAction, dict], Any]] = None,
        on_recovery_result: Optional[Callable[[bool, dict], None]] = None,
        adapter: Optional[AgentAdapter] = None,
    ):
        self.current_state = GuardianState.IDLE
        self.safety_mode = safety_mode
        self.stale_threshold = stale_threshold

        self.failure_classifier = FailureClassifier()
        self.safety_gate = SafetyGate(safety_mode)
        self.backoff = BackoffStrategy(backoff_config or DEFAULT_BACKOFF)
        self.ladder = RecoveryLadder(LadderConfig(
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
        ))

        self.recovery_executor = RecoveryExecutor(
            safety_gate=self.safety_gate,
            budget_manager=RecoveryBudgetManager(),
            ladder=self.ladder,
            default_mode=safety_mode,
        )

        self.adapter = adapter
        self.observer = None
        self.on_state_change = on_state_change
        self.on_failure = on_failure
        self.on_recovery_action = on_recovery_action
        self.on_recovery_result = on_recovery_result

        # Ping monitor for liveness detection
        self._ping_monitor: Optional[PingMonitor] = None
        self._ping_transport: Optional[Any] = None

        self.turn_context: Optional[TurnContext] = None
        self.current_failure: Optional[FailureClassification] = None
        self.recovery_history: List[RecoveryAttempt] = []
        self.transition_history: List[StateTransition] = []
        self.metrics = GuardianMetrics()

        self._last_event_time = time.time()
        self._stale_check_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def state(self) -> GuardianState:
        return self.current_state

    def transition(self, event: StateMachineEvent, details: dict = None) -> bool:
        valid_next = self.VALID_TRANSITIONS.get(self.current_state, {})
        next_state = valid_next.get(event)
        if next_state is None:
            print(f"Invalid transition: {self.current_state.value} --{event.value}--> ???")
            return False
        old_state = self.current_state
        self.current_state = next_state
        transition = StateTransition(
            from_state=old_state, to_state=next_state, event=event,
            timestamp=datetime.now(), details=details or {},
        )
        self.transition_history.append(transition)
        if self.on_state_change:
            try:
                self.on_state_change(old_state, next_state, event)
            except Exception as e:
                print(f"State change callback error: {e}")
        return True

    def handle_event(self, event: StateMachineEvent, data: dict = None) -> bool:
        if self.turn_context:
            self._update_turn_context(event, data)
        if self._check_stale():
            self.transition(StateMachineEvent.STALE_DETECTED, {"detected_at": time.time()})
            self._handle_failure({"error_message": f"Stream stale - no events for {self.stale_threshold}s"})
            return True
        if event == StateMachineEvent.DISCONNECTED:
            self.transition(StateMachineEvent.DISCONNECTED, {})
            return True
        if event in (StateMachineEvent.TURN_ERROR, StateMachineEvent.TURN_CANCELLED, StateMachineEvent.TURN_MAX_TURNS):
            self.transition(event, data)
            return True
        elif event == StateMachineEvent.FAILURE_DETECTED:
            return self._handle_failure(data)
        elif event == StateMachineEvent.BACKOFF_COMPLETE:
            return self._attempt_recovery()
        elif event == StateMachineEvent.RECOVERY_COMPLETE:
            return self._verify_recovery(data)
        elif event == StateMachineEvent.RECOVERY_FAILED:
            return self._handle_recovery_failure(data)
        return self.transition(event, data)

    def _update_turn_context(self, event: StateMachineEvent, data: dict = None):
        if not self.turn_context: return
        data = data or {}
        if event == StateMachineEvent.USER_MESSAGE_SENT:
            self.turn_context.phase = TurnPhase.USER_MESSAGE_SENT
            self.turn_context.user_prompt = data.get("prompt")
            self.turn_context.first_chunk_time = None
            self.turn_context.last_assistant_chunk_time = None
            self.turn_context.increment_generation()
        elif event == StateMachineEvent.ASSISTANT_CHUNK:
            if self.turn_context.phase == TurnPhase.USER_MESSAGE_SENT:
                self.turn_context.phase = TurnPhase.ASSISTANT_STREAMING
                self.turn_context.first_chunk_time = time.time()
            self.turn_context.assistant_chunks_received += 1
            self.turn_context.last_assistant_chunk_time = time.time()
            self.turn_context.last_event_time = time.time()
            self.turn_context.increment_generation()
        elif event == StateMachineEvent.TOOL_STARTED:
            tool_call_id = data.get("tool_call_id")
            if tool_call_id and tool_call_id not in self.turn_context._seen_tool_call_ids:
                self.turn_context._seen_tool_call_ids.add(tool_call_id)
                self.turn_context.tool_calls_started += 1
            self.turn_context.phase = TurnPhase.TOOL_EXECUTING
            self.turn_context.last_tool_event_time = time.time()
            self.turn_context.last_event_time = time.time()
            self.turn_context.increment_generation()
        elif event in (StateMachineEvent.TOOL_COMPLETED, StateMachineEvent.TOOL_FAILED):
            tool_call_id = data.get("tool_call_id")
            if event == StateMachineEvent.TOOL_COMPLETED:
                if tool_call_id and tool_call_id not in self.turn_context._completed_tool_call_ids:
                    self.turn_context._completed_tool_call_ids.add(tool_call_id)
                    self.turn_context.tool_calls_completed += 1
            else:
                if tool_call_id and tool_call_id not in self.turn_context._failed_tool_call_ids:
                    self.turn_context._failed_tool_call_ids.add(tool_call_id)
                    self.turn_context.tool_calls_failed += 1
            self.turn_context.last_tool_event_time = time.time()
            self.turn_context.last_event_time = time.time()
            self.turn_context.increment_generation()
        elif event in (StateMachineEvent.TURN_COMPLETE, StateMachineEvent.TURN_ERROR,
                       StateMachineEvent.TURN_CANCELLED, StateMachineEvent.TURN_MAX_TURNS):
            self.turn_context.phase = TurnPhase.TURN_ENDING
            self.turn_context.stop_reason = data.get("stop_reason") if data else None
            self.turn_context.error_message = data.get("error_message") if data else None
            self.turn_context.last_event_time = time.time()
            self.turn_context.increment_generation()

    def _check_stale(self) -> bool:
        if not self.turn_context: return False
        if self.current_state not in (GuardianState.STREAMING, GuardianState.TOOL_ACTIVE, GuardianState.OBSERVING):
            return False
        # Check ping monitor first if available
        if self._ping_monitor and self.turn_context.session_id:
            if self._ping_monitor.is_stale(self.turn_context.session_id):
                return True
        # Fallback to time-based check
        elapsed = time.time() - self.turn_context.last_event_timestamp
        return elapsed > self.stale_threshold

    def start_turn(self, session_id: str, run_id: str = None, user_prompt: str = None):
        self.turn_context = TurnContext(session_id=session_id, run_id=run_id, user_prompt=user_prompt)
        self._last_event_time = time.time()
        self.transition(StateMachineEvent.USER_MESSAGE_SENT, {"session_id": session_id})

        # Start ping monitoring for this session
        if self._ping_monitor:
            self._ping_monitor.start_monitoring(session_id)

    def end_turn(self, stop_reason: str = "end_turn", error: str = None):
        if not self.turn_context: return
        self.metrics.total_turns += 1
        if stop_reason == "end_turn": self.metrics.successful_turns += 1
        else: self.metrics.failed_turns += 1
        self.transition(StateMachineEvent.TURN_COMPLETE if stop_reason == "end_turn" else StateMachineEvent.TURN_ERROR,
            {"stop_reason": stop_reason, "error": error})
        # Stop ping monitoring for this session
        if self._ping_monitor and self.turn_context:
            self._ping_monitor.stop_monitoring(self.turn_context.session_id)
        self.turn_context = None

    def _handle_failure(self, data: dict = None) -> bool:
        data = data or {}
        error_message = data.get("error_message")
        http_status = data.get("http_status")
        stop_reason = data.get("stop_reason")
        classification = self.failure_classifier.classify(
            error_message=error_message, http_status=http_status,
            stop_reason=stop_reason, turn_context=self.turn_context)
        self.current_failure = classification
        if self.on_failure:
            try: self.on_failure(classification, self.turn_context)
            except Exception as e: print(f"Failure callback error: {e}")
        print(f"FAILURE DETECTED: {classification.failure_type.value} (retryable={classification.retryable}, replay_safe={classification.replay_safe}, action={classification.recommended_action.value})")
        if classification.recommended_action == RecoveryAction.NO_SAFE_RECOVERY:
            self.transition(StateMachineEvent.FAILURE_DETECTED, {"failure": classification.failure_type.value, "reason": "No safe recovery"})
            self.transition(StateMachineEvent.MANUAL_OVERRIDE, {"reason": "No safe recovery path"})
            return True
        if classification.recommended_action == RecoveryAction.HUMAN_INTERVENTION:
            self.transition(StateMachineEvent.FAILURE_DETECTED, {"failure": classification.failure_type.value})
            if self.safety_mode in (SafetyMode.DRY_RUN, SafetyMode.AUTO_RECOVER):
                print(f"DRY_RUN/AUTO_RECOVER: Would request human intervention for {classification.failure_type.value}")
            self.transition(StateMachineEvent.MANUAL_OVERRIDE, {"reason": "Human intervention required"})
            return True
        if classification.recommended_action in (RecoveryAction.WAIT_AND_RETRY, RecoveryAction.CANCEL_AND_REPLAY):
            self.transition(StateMachineEvent.FAILURE_DETECTED, {"failure": classification.failure_type.value, "backoff_delay": 0})
            visible_output = self.turn_context.assistant_chunks_received > 0 if self.turn_context else False
            tool_activity = self.turn_context.tool_calls_started > 0 if self.turn_context else False
            delay = self.ladder.get_next_delay(
                retry_after=None,
                visible_output_delivered=visible_output,
                tool_activity_observed=tool_activity,
                failure_type=classification.failure_type.value,
            )
            if delay < 0:
                print(f"Recovery ladder not eligible for {classification.failure_type.value}")
                self.transition(StateMachineEvent.MANUAL_OVERRIDE, {"reason": "Recovery ladder not eligible"})
                return True
            print(f"Starting backoff: {delay:.1f}s (cycle {self.ladder.state.current_cycle + 1})")
            self.transition(StateMachineEvent.BACKOFF_COMPLETE, {"failure": classification.failure_type.value, "backoff_delay": delay})
            return True
        elif classification.recommended_action == RecoveryAction.RECONNECT_ACP:
            self.transition(StateMachineEvent.FAILURE_DETECTED, {"failure": classification.failure_type.value})
            self._attempt_recovery()
            return True
        elif classification.recommended_action == RecoveryAction.WAIT_FOR_BACKEND:
            self.transition(StateMachineEvent.FAILURE_DETECTED, {"failure": classification.failure_type.value})
            self.current_state = GuardianState.RECOVERY_WAIT
            return True
        return False

    def _attempt_recovery(self) -> bool:
        if not self.current_failure: return False
        action = self.current_failure.recommended_action
        if self.safety_mode == SafetyMode.OBSERVE_ONLY:
            print(f"OBSERVE_ONLY: Would attempt {action.value}")
            self.current_state = GuardianState.POSSIBLE_FAILURE
            return False
        if self.safety_mode == SafetyMode.DRY_RUN:
            print(f"DRY_RUN: Would attempt {action.value}")
            safety_result = self.safety_gate.check_recovery_action_safety(action=action, turn_context=self.turn_context, failure_classification=self.current_failure)
            if safety_result.verdict == SafetyVerdict.UNSAFE:
                print(f"RECOVERY BLOCKED (DRY_RUN): {safety_result.reason}")
                self.current_state = GuardianState.POSSIBLE_FAILURE
                return False
            if safety_result.verdict == SafetyVerdict.AMBIGUOUS:
                print(f"RECOVERY AMBIGUOUS (DRY_RUN): {safety_result.reason}")
                self.current_state = GuardianState.UNKNOWN_UNSAFE
                return False
            print(f"DRY_RUN: Would attempt {action.value} (safety={safety_result.verdict.value})")
            self.current_state = GuardianState.POSSIBLE_FAILURE
            return False
        if self.adapter is None:
            print("AUTO_RECOVER: No adapter configured, cannot execute recovery")
            self.current_state = GuardianState.POSSIBLE_FAILURE
            return False
        print(f"RECOVERY ATTEMPT: {action.value}")
        self.transition(StateMachineEvent.RECOVERY_STARTED, {"action": action.value, "failure": self.current_failure.failure_type.value})
        attempt = RecoveryAttempt(attempt_number=len(self.recovery_history)+1, failure_type=self.current_failure.failure_type, action_taken=action, timestamp=datetime.now(), delay_seconds=self.backoff.state.last_delay, success=False)
        self.recovery_history.append(attempt)
        self.metrics.recovery_attempts += 1
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = loop.create_task(self._execute_recovery_async())
                print(f"RECOVERY SCHEDULED: {action.value}")
                return True
            else:
                result = loop.run_until_complete(self._execute_recovery_async())
                return result
        except Exception as e:
            print(f"Recovery execution error: {e}")
            self.current_state = GuardianState.POSSIBLE_FAILURE
            return False

    async def _execute_recovery_async(self) -> bool:
        if not self.current_failure or not self.adapter or not self.turn_context: return False
        from .recovery_planner import RecoveryPlanner, RecoveryContext
        from .recovery_ladder import RecoveryLadder, LadderConfig
        from .recovery_budget import RecoveryBudgetManager, RecoveryBudgetConfig
        from .backoff import BackoffStrategy, BackoffConfig
        ladder = self.ladder if hasattr(self, 'ladder') else RecoveryLadder(LadderConfig())
        budget_manager = self.recovery_executor.budget_manager
        backoff = self.backoff
        planner = RecoveryPlanner(safety_gate=self.safety_gate, budget_manager=budget_manager, ladder=ladder, backoff=backoff)
        context = RecoveryContext(
            session=SessionState(session_id=self.turn_context.session_id, turn_states=[], created_at=0),
            current_turn=TurnState(turn_id=self.turn_context.run_id or "unknown", session_id=self.turn_context.session_id, phase=TurnPhase(self.turn_context.phase.value), evidence=None),
            failure_classification=self.current_failure,
            adapter_capabilities={cap.value: True for cap in self.adapter.capabilities.get_available()},
            adapter_name=self.adapter.capabilities.adapter_name,
            safety_mode=self.safety_mode,
        )
        plan = planner.create_plan(context)
        result = await self.recovery_executor.execute_plan(plan=plan, adapter=self.adapter, current_session_state=self.turn_context, current_stream_state={"partial_tool_names": self.observer.get_session_state(self.turn_context.session_id).get_partial_tool_names() if self.observer and self.turn_context else []})
        if result.success:
            self.metrics.successful_recoveries += 1
            self.transition(StateMachineEvent.RECOVERY_COMPLETE, {"action": action.value, "result": result.result.value})
            return True
        else:
            self.metrics.failed_recoveries += 1
            self.transition(StateMachineEvent.RECOVERY_FAILED, {"action": action.value, "error": result.message})
            return False

    def _verify_recovery(self, data: dict = None) -> bool:
        data = data or {}
        success = data.get("success", True)
        if success:
            self.metrics.successful_recoveries += 1
            self.transition(StateMachineEvent.VERIFICATION_PASSED, {})
            print("RECOVERY VERIFIED: Success")
            return True
        else:
            self.transition(StateMachineEvent.VERIFICATION_FAILED, {"reason": data.get("reason", "Verification failed")})
            print("RECOVERY VERIFICATION FAILED")
            return False

    def _handle_recovery_failure(self, data: dict = None):
        data = data or {}
        error = data.get("error", "Recovery failed")
        if not self.backoff.should_retry():
            print(f"MAX RECOVERY ATTEMPTS REACHED: {len(self.recovery_history)}")
            self.current_state = GuardianState.FAILED_PERMANENTLY
            return
        self.current_state = GuardianState.POSSIBLE_FAILURE
        print(f"RECOVERY FAILED: {error}, will retry after backoff")

    def get_status(self) -> dict:
        return {
            "state": self.current_state.value, "safety_mode": self.safety_mode.value,
            "turn_context": {"session_id": self.turn_context.session_id if self.turn_context else None, "phase": self.turn_context.phase.value if self.turn_context else None, "assistant_chunks": self.turn_context.assistant_chunks_received if self.turn_context else 0, "tool_calls_started": self.turn_context.tool_calls_started if self.turn_context else 0, "safe_to_replay": self.turn_context.is_safe_to_replay() if self.turn_context else True} if self.turn_context else None,
            "current_failure": {"type": self.current_failure.failure_type.value, "recommended_action": self.current_failure.recommended_action.value, "retryable": self.current_failure.retryable, "replay_safe": self.current_failure.replay_safe} if self.current_failure else None,
            "backoff": self.backoff.get_status(),
            "metrics": {"total_turns": self.metrics.total_turns, "successful_turns": self.metrics.successful_turns, "failed_turns": self.metrics.failed_turns, "recovery_attempts": self.metrics.recovery_attempts, "successful_recoveries": self.metrics.successful_recoveries, "failed_recoveries": self.metrics.failed_recoveries},
            "recovery_history_count": len(self.recovery_history), "transition_count": len(self.transition_history),
        }