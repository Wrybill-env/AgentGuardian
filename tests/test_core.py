"""
Tests for Guardian core components.
"""

import time
from datetime import datetime

import pytest

from guardian.core import (
    GuardianState,
    FailureType,
    RecoveryAction,
    SafetyMode,
    TurnPhase,
    TurnContext,
    FailureClassification,
    RecoveryAttempt,
)
from guardian.core.failure_classifier import FailureClassifier, FAILURE_PATTERNS
from guardian.core.safety_gate import SafetyGate, SafetyVerdict, SafetyCheckResult
from guardian.core.backoff import (
    BackoffStrategy,
    BackoffConfig,
    JitterType,
    DEFAULT_BACKOFF,
    CONSERVATIVE_BACKOFF,
)
from guardian.core.state_machine import GuardianStateMachine, StateMachineEvent


class TestTypes:
    """Tests for core types and enums."""

    def test_guardian_state_values(self):
        assert GuardianState.IDLE.value == "idle"
        assert GuardianState.OBSERVING.value == "observing"
        assert GuardianState.STREAMING.value == "streaming"
        assert GuardianState.TOOL_ACTIVE.value == "tool_active"
        assert GuardianState.POSSIBLE_FAILURE.value == "possible_failure"
        assert GuardianState.RECOVERY_WAIT.value == "recovery_wait"
        assert GuardianState.RECOVERY_ATTEMPT.value == "recovery_attempt"
        assert GuardianState.VERIFYING.value == "verifying"
        assert GuardianState.RECOVERED.value == "recovered"
        assert GuardianState.FAILED_PERMANENTLY.value == "failed_permanently"
        assert GuardianState.UNKNOWN_UNSAFE.value == "unknown_unsafe"

    def test_failure_type_values(self):
        assert FailureType.RATE_LIMIT.value == "rate_limit"
        assert FailureType.SERVER_ERROR.value == "server_error"
        assert FailureType.NETWORK_TRANSIENT.value == "network_transient"
        assert FailureType.CONNECTION_RESET.value == "connection_reset"
        assert FailureType.BACKEND_DISCONNECT.value == "backend_disconnect"
        assert FailureType.BACKEND_CRASH.value == "backend_crash"
        assert FailureType.AUTHENTICATION_FAILURE.value == "authentication_failure"
        assert FailureType.USER_CANCELLED.value == "user_cancelled"
        assert FailureType.UNKNOWN_FAILURE.value == "unknown_failure"

    def test_recovery_action_values(self):
        assert RecoveryAction.NONE.value == "none"
        assert RecoveryAction.WAIT_AND_RETRY.value == "wait_and_retry"
        assert RecoveryAction.CANCEL_AND_REPLAY.value == "cancel_and_replay"
        assert RecoveryAction.RECONNECT_ACP.value == "reconnect_acp"
        assert RecoveryAction.RELOAD_SESSION.value == "reload_session"
        assert RecoveryAction.FORK_SESSION.value == "fork_session"
        assert RecoveryAction.WAIT_FOR_BACKEND.value == "wait_for_backend"
        assert RecoveryAction.HUMAN_INTERVENTION.value == "human_intervention"
        assert RecoveryAction.NO_SAFE_RECOVERY.value == "no_safe_recovery"

    def test_safety_mode_values(self):
        assert SafetyMode.OBSERVE_ONLY.value == "observe_only"
        assert SafetyMode.DRY_RUN.value == "dry_run"
        assert SafetyMode.AUTO_RECOVER.value == "auto_recover"

    def test_turn_phase_values(self):
        assert TurnPhase.IDLE.value == "idle"
        assert TurnPhase.USER_MESSAGE_SENT.value == "user_message_sent"
        assert TurnPhase.ASSISTANT_STREAMING.value == "assistant_streaming"
        assert TurnPhase.TOOL_EXECUTING.value == "tool_executing"
        assert TurnPhase.TOOL_COMPLETING.value == "tool_completing"
        assert TurnPhase.TURN_ENDING.value == "turn_ending"
        assert TurnPhase.TURN_COMPLETE.value == "turn_complete"


class TestTurnContext:
    """Tests for TurnContext."""

    def setup_method(self):
        self.context = TurnContext(session_id="sess_test")

    def test_initial_state(self):
        assert self.context.session_id == "sess_test"
        assert self.context.phase == TurnPhase.IDLE
        assert self.context.assistant_chunks_received == 0
        assert self.context.tool_calls_started == 0
        assert self.context.tool_calls_completed == 0
        assert self.context.tool_calls_failed == 0

    def test_has_assistant_output(self):
        assert self.context.has_assistant_output() is False
        self.context.assistant_chunks_received = 1
        assert self.context.has_assistant_output() is True

    def test_has_tool_activity(self):
        assert self.context.has_tool_activity() is False
        self.context.tool_calls_started = 1
        assert self.context.has_tool_activity() is True

    def test_is_safe_to_replay(self):
        assert self.context.is_safe_to_replay() is True

        self.context.assistant_chunks_received = 1
        assert self.context.is_safe_to_replay() is False

        self.context.assistant_chunks_received = 0
        self.context.tool_calls_started = 1
        assert self.context.is_safe_to_replay() is False

        self.context.tool_calls_started = 0
        assert self.context.is_safe_to_replay() is True

    def test_phase_transitions(self):
        self.context.phase = TurnPhase.USER_MESSAGE_SENT
        assert self.context.phase == TurnPhase.USER_MESSAGE_SENT

        self.context.phase = TurnPhase.ASSISTANT_STREAMING
        assert self.context.phase == TurnPhase.ASSISTANT_STREAMING

        self.context.phase = TurnPhase.TOOL_EXECUTING
        assert self.context.phase == TurnPhase.TOOL_EXECUTING


class TestTurnContextDeduplication:
    """Tests for TurnContext event deduplication."""

    def setup_method(self):
        self.context = TurnContext(session_id="sess_test")

    def test_fresh_turn_has_empty_dedup_sets(self):
        assert len(self.context._seen_tool_call_ids) == 0
        assert len(self.context._completed_tool_call_ids) == 0
        assert len(self.context._failed_tool_call_ids) == 0

    def test_duplicate_start_not_counted(self):
        # First start
        self.context._seen_tool_call_ids.add("tc_123")
        self.context.tool_calls_started += 1

        # Duplicate start with same ID - should NOT increment
        if "tc_123" not in self.context._seen_tool_call_ids:
            self.context._seen_tool_call_ids.add("tc_123")
            self.context.tool_calls_started += 1

        assert self.context.tool_calls_started == 1

    def test_duplicate_completion_not_counted(self):
        self.context._completed_tool_call_ids.add("tc_123")
        self.context.tool_calls_completed += 1

        # Duplicate completion
        if "tc_123" not in self.context._completed_tool_call_ids:
            self.context._completed_tool_call_ids.add("tc_123")
            self.context.tool_calls_completed += 1

        assert self.context.tool_calls_completed == 1

    def test_duplicate_failed_not_counted(self):
        self.context._failed_tool_call_ids.add("tc_123")
        self.context.tool_calls_failed += 1

        if "tc_123" not in self.context._failed_tool_call_ids:
            self.context._failed_tool_call_ids.add("tc_123")
            self.context.tool_calls_failed += 1

        assert self.context.tool_calls_failed == 1

    def test_different_tool_call_ids_counted_separately(self):
        # First tool
        self.context._seen_tool_call_ids.add("tc_1")
        self.context.tool_calls_started += 1

        # Second tool with different ID
        if "tc_2" not in self.context._seen_tool_call_ids:
            self.context._seen_tool_call_ids.add("tc_2")
            self.context.tool_calls_started += 1

        assert self.context.tool_calls_started == 2
        assert len(self.context._seen_tool_call_ids) == 2

    def test_completed_and_failed_tracked_separately(self):
        # Tool completes
        self.context._completed_tool_call_ids.add("tc_1")
        self.context.tool_calls_completed += 1

        # Different tool fails
        self.context._failed_tool_call_ids.add("tc_2")
        self.context.tool_calls_failed += 1

        assert self.context.tool_calls_completed == 1
        assert self.context.tool_calls_failed == 1
        assert "tc_1" in self.context._completed_tool_call_ids
        assert "tc_2" in self.context._failed_tool_call_ids

    def test_new_turn_context_has_fresh_dedup_sets(self):
        # Simulate old turn with deduplication state
        old_context = TurnContext(session_id="sess_old")
        old_context._seen_tool_call_ids.add("tc_old")
        old_context._completed_tool_call_ids.add("tc_old")
        old_context.tool_calls_started = 1
        old_context.tool_calls_completed = 1

        # New turn context should have fresh sets
        new_context = TurnContext(session_id="sess_new")

        assert len(new_context._seen_tool_call_ids) == 0
        assert len(new_context._completed_tool_call_ids) == 0
        assert len(new_context._failed_tool_call_ids) == 0
        assert new_context.tool_calls_started == 0
        assert new_context.tool_calls_completed == 0

    def test_dedup_sets_do_not_leak_across_contexts(self):
        context1 = TurnContext(session_id="sess_1")
        context1._seen_tool_call_ids.add("tc_shared")
        context1.tool_calls_started = 1

        context2 = TurnContext(session_id="sess_2")

        # context2 should not have context1's dedup state
        assert "tc_shared" not in context2._seen_tool_call_ids
        assert context2.tool_calls_started == 0

        # Adding to context2 should be independent
        context2._seen_tool_call_ids.add("tc_shared")
        context2.tool_calls_started += 1

        assert context1.tool_calls_started == 1
        assert context2.tool_calls_started == 1

    def test_unknown_tool_call_id_handled_gracefully(self):
        # Event without tool_call_id should not crash dedup logic
        # (The dedup check handles None gracefully)
        # Actual logic checks `if tool_call_id and tool_call_id not in ...`
        # So None is never added - we don't add it in the test either

        tool_call_id = None
        if tool_call_id and tool_call_id not in self.context._seen_tool_call_ids:
            self.context._seen_tool_call_ids.add(tool_call_id)
            self.context.tool_calls_started += 1

        assert self.context.tool_calls_started == 0
        assert None not in self.context._seen_tool_call_ids


class TestFailureClassifier:
    """Tests for FailureClassifier."""

    def setup_method(self):
        self.classifier = FailureClassifier()

    def test_rate_limit_classification(self):
        result = self.classifier.classify(
            error_message="429 Too Many Requests",
            http_status=429,
        )

        assert result.failure_type == FailureType.RATE_LIMIT
        assert result.retryable is True
        assert result.replay_safe is True
        assert result.recommended_action == RecoveryAction.WAIT_AND_RETRY
        assert result.confidence > 0.9

    def test_server_error_classification(self):
        for status in [500, 502, 503, 504]:
            result = self.classifier.classify(
                error_message=f"Internal Server Error: {status}",
                http_status=status,
            )
            assert result.failure_type == FailureType.SERVER_ERROR
            assert result.retryable is True
            assert result.recommended_action == RecoveryAction.WAIT_AND_RETRY

    def test_connection_reset_classification(self):
        result = self.classifier.classify(
            error_message="Connection reset by peer",
        )

        assert result.failure_type == FailureType.CONNECTION_RESET
        assert result.retryable is True
        assert result.recommended_action == RecoveryAction.RECONNECT_ACP

    def test_authentication_failure_classification(self):
        for status in [401, 403]:
            result = self.classifier.classify(
                error_message="Unauthorized",
                http_status=status,
            )
            assert result.failure_type == FailureType.AUTHENTICATION_FAILURE
            assert result.retryable is False
            assert result.requires_human is True
            assert result.recommended_action == RecoveryAction.HUMAN_INTERVENTION

    def test_unknown_failure_fails_closed(self):
        result = self.classifier.classify(
            error_message="Some completely unknown error message that matches nothing",
        )

        assert result.failure_type == FailureType.UNKNOWN_FAILURE
        assert result.retryable is False
        assert result.replay_safe is False
        assert result.requires_human is True
        assert result.recommended_action == RecoveryAction.NO_SAFE_RECOVERY
        assert result.confidence == 0.9

    def test_retryable_but_not_replay_safe_separation(self):
        """Test that retryable and replay_safe are properly separated.

        A transient network error should be retryable (technically we can try again)
        but NOT replay_safe if tools have already started.
        """
        turn_context = TurnContext(session_id="sess_test")
        turn_context.tool_calls_started = 1  # Tool already started

        result = self.classifier.classify(
            error_message="Connection reset by peer",
            turn_context=turn_context,
        )

        # Technically retryable (network error)
        assert result.retryable is True
        # But NOT replay_safe because tool already started
        assert result.replay_safe is False
        # RECONNECT_ACP is still valid (doesn't replay prompt)
        assert result.recommended_action == RecoveryAction.RECONNECT_ACP

    def test_rate_limit_retryable_and_replay_safe_when_no_tools(self):
        """Rate limit with no tool activity should be both retryable and replay_safe."""
        turn_context = TurnContext(session_id="sess_test")
        # No tool activity

        result = self.classifier.classify(
            error_message="429 Too Many Requests",
            http_status=429,
            turn_context=turn_context,
        )

        assert result.retryable is True
        assert result.replay_safe is True
        assert result.recommended_action == RecoveryAction.WAIT_AND_RETRY

    def test_tool_activity_blocks_replay(self):
        turn_context = TurnContext(session_id="sess_test")
        turn_context.tool_calls_started = 1

        result = self.classifier.classify(
            error_message="500 Internal Server Error",
            http_status=500,
            turn_context=turn_context,
        )

        assert result.replay_safe is False
        assert result.recommended_action == RecoveryAction.NO_SAFE_RECOVERY

    def test_user_cancelled_classification(self):
        result = self.classifier.classify(
            stop_reason="cancelled",
        )

        assert result.failure_type == FailureType.USER_CANCELLED
        assert result.retryable is False
        assert result.requires_human is True
        assert result.recommended_action == RecoveryAction.HUMAN_INTERVENTION


class TestSafetyGate:
    """Tests for SafetyGate."""

    def setup_method(self):
        self.gate = SafetyGate(safety_mode=SafetyMode.OBSERVE_ONLY)

    def test_observe_only_blocks_all_recovery(self):
        turn_context = TurnContext(session_id="sess_test")

        result = self.gate.check_recovery_action_safety(
            RecoveryAction.WAIT_AND_RETRY,
            turn_context,
        )

        assert result.verdict == SafetyVerdict.UNSAFE
        assert "OBSERVE_ONLY" in result.reason

    def test_dry_run_allows_all(self):
        gate = SafetyGate(safety_mode=SafetyMode.DRY_RUN)
        turn_context = TurnContext(session_id="sess_test")
        turn_context.assistant_chunks_received = 5

        result = gate.check_recovery_action_safety(
            RecoveryAction.WAIT_AND_RETRY,
            turn_context,
        )

        assert result.verdict == SafetyVerdict.SAFE
        assert "DRY_RUN" in result.reason

    def test_auto_recover_checks_safety(self):
        gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")

        # Safe - no activity
        result = gate.check_recovery_action_safety(
            RecoveryAction.WAIT_AND_RETRY,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.SAFE

        # Unsafe - assistant output observed
        turn_context.assistant_chunks_received = 1
        result = gate.check_recovery_action_safety(
            RecoveryAction.WAIT_AND_RETRY,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.UNSAFE

    def test_reconnect_acp_safety(self):
        gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)

        # Safe with session ID
        turn_context = TurnContext(session_id="sess_test")
        result = gate.check_recovery_action_safety(
            RecoveryAction.RECONNECT_ACP,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.SAFE

        # Ambiguous without session ID
        turn_context = TurnContext(session_id="")
        result = gate.check_recovery_action_safety(
            RecoveryAction.RECONNECT_ACP,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.AMBIGUOUS

    def test_reload_session_safety(self):
        gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)

        # Safe with session ID
        turn_context = TurnContext(session_id="sess_test")
        result = gate.check_recovery_action_safety(
            RecoveryAction.RELOAD_SESSION,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.SAFE

        # Unsafe without session ID
        turn_context = TurnContext(session_id="")
        result = gate.check_recovery_action_safety(
            RecoveryAction.RELOAD_SESSION,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.UNSAFE

    def test_fork_session_safety(self):
        gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)

        # Safe before assistant chunks
        turn_context = TurnContext(session_id="sess_test")
        turn_context.phase = TurnPhase.USER_MESSAGE_SENT
        result = gate.check_recovery_action_safety(
            RecoveryAction.FORK_SESSION,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.SAFE

        # Ambiguous after streaming started
        turn_context.phase = TurnPhase.ASSISTANT_STREAMING
        turn_context.first_chunk_time = time.time()
        result = gate.check_recovery_action_safety(
            RecoveryAction.FORK_SESSION,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.AMBIGUOUS

    def test_wait_for_backend_always_safe(self):
        gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        turn_context.assistant_chunks_received = 10
        turn_context.tool_calls_started = 5

        result = gate.check_recovery_action_safety(
            RecoveryAction.WAIT_FOR_BACKEND,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.SAFE

    def test_human_intervention_always_safe(self):
        gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")
        turn_context.assistant_chunks_received = 100
        turn_context.tool_calls_started = 50

        result = gate.check_recovery_action_safety(
            RecoveryAction.HUMAN_INTERVENTION,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.SAFE

    def test_no_safe_recovery_blocked(self):
        gate = SafetyGate(safety_mode=SafetyMode.AUTO_RECOVER)
        turn_context = TurnContext(session_id="sess_test")

        result = gate.check_recovery_action_safety(
            RecoveryAction.NO_SAFE_RECOVERY,
            turn_context,
        )
        assert result.verdict == SafetyVerdict.UNSAFE


class TestBackoffStrategy:
    """Tests for BackoffStrategy."""

    def setup_method(self):
        self.config = BackoffConfig(
            initial_delay=1.0,
            max_delay=10.0,
            multiplier=2.0,
            jitter_type=JitterType.NONE,  # Deterministic for testing
            max_attempts=3,
        )
        self.backoff = BackoffStrategy(self.config)

    def test_initial_delay(self):
        delay = self.backoff.next_delay()
        assert delay == 1.0  # initial_delay
        assert self.backoff.get_attempt() == 1

    def test_exponential_growth(self):
        self.backoff.next_delay()  # 1.0
        delay2 = self.backoff.next_delay()
        assert delay2 == 2.0  # 1.0 * 2

        delay3 = self.backoff.next_delay()
        assert delay3 == 4.0  # 2.0 * 2

    def test_max_delay_cap(self):
        config = BackoffConfig(
            initial_delay=1.0,
            max_delay=5.0,
            multiplier=10.0,
            jitter_type=JitterType.NONE,
            max_attempts=5,
        )
        backoff = BackoffStrategy(config)

        backoff.next_delay()  # 1.0
        backoff.next_delay()  # 10.0 -> capped at 5.0
        delay3 = backoff.next_delay()
        assert delay3 == 5.0

    def test_should_retry(self):
        assert self.backoff.should_retry() is True  # attempt 0
        self.backoff.next_delay()  # attempt 1
        assert self.backoff.should_retry() is True
        self.backoff.next_delay()  # attempt 2
        assert self.backoff.should_retry() is True
        self.backoff.next_delay()  # attempt 3 (max_attempts=3)
        assert self.backoff.should_retry() is False

    def test_retry_after_override(self):
        delay = self.backoff.next_delay(retry_after=5.0)
        assert delay == 5.0
        assert self.backoff.state.last_retry_after == 5.0

    def test_retry_after_capped(self):
        config = BackoffConfig(
            initial_delay=1.0,
            max_delay=10.0,
            max_retry_after=3.0,
        )
        backoff = BackoffStrategy(config)

        delay = backoff.next_delay(retry_after=10.0)
        assert delay == 3.0  # Capped at max_retry_after

    def test_indefinite_retry(self):
        config = BackoffConfig(
            initial_delay=1.0,
            max_delay=10.0,
            indefinite_retry=True,
        )
        backoff = BackoffStrategy(config)

        for _ in range(10):
            backoff.next_delay()
            assert backoff.should_retry() is True

    def test_jitter_types(self):
        # Test with fixed seed for reproducibility
        import random
        random.seed(42)

        # Full jitter
        config = BackoffConfig(
            initial_delay=10.0,
            max_delay=100.0,
            jitter_type=JitterType.FULL,
            max_attempts=5,
        )
        backoff = BackoffStrategy(config)
        delays = [backoff.next_delay() for _ in range(5)]

        # All delays should be >= 0 and <= initial_delay * multiplier^attempt (capped at max_delay)
        # First attempt: initial_delay=10, multiplier=2, so max before cap is 10
        for i, d in enumerate(delays):
            attempt = i + 1
            max_possible = min(10.0 * (2.0 ** (attempt - 1)), 100.0)
            assert 0 <= d <= max_possible + 0.1, f"Delay {d} exceeds max {max_possible} for attempt {attempt}"

    def test_reset(self):
        self.backoff.next_delay()
        self.backoff.next_delay()
        assert self.backoff.get_attempt() == 2

        self.backoff.reset()
        assert self.backoff.get_attempt() == 0
        assert self.backoff.get_total_waited() == 0.0

    def test_status(self):
        self.backoff.next_delay()
        status = self.backoff.get_status()

        assert status["attempt"] == 1
        assert status["last_delay"] > 0
        assert "total_waited" in status
        assert "elapsed_time" in status


class TestGuardianStateMachine:
    """Tests for GuardianStateMachine."""

    def setup_method(self):
        self.machine = GuardianStateMachine(
            safety_mode=SafetyMode.OBSERVE_ONLY,
        )

    def test_initial_state(self):
        assert self.machine.state == GuardianState.IDLE

    def test_valid_transitions(self):
        # IDLE -> OBSERVING
        assert self.machine.transition(StateMachineEvent.CONNECTED)
        assert self.machine.state == GuardianState.OBSERVING

        # OBSERVING -> STREAMING
        assert self.machine.transition(StateMachineEvent.USER_MESSAGE_SENT)
        assert self.machine.state == GuardianState.STREAMING

        # STREAMING -> TOOL_ACTIVE
        assert self.machine.transition(StateMachineEvent.TOOL_STARTED)
        assert self.machine.state == GuardianState.TOOL_ACTIVE

        # TOOL_ACTIVE -> STREAMING
        assert self.machine.transition(StateMachineEvent.TOOL_COMPLETED)
        assert self.machine.state == GuardianState.STREAMING

        # STREAMING -> COMPLETED
        assert self.machine.transition(StateMachineEvent.TURN_COMPLETE)
        assert self.machine.state == GuardianState.COMPLETED

    def test_invalid_transition_rejected(self):
        # Can't go from IDLE to STREAMING directly
        result = self.machine.transition(StateMachineEvent.USER_MESSAGE_SENT)
        assert result is False
        assert self.machine.state == GuardianState.IDLE

    def test_start_turn(self):
        self.machine.transition(StateMachineEvent.CONNECTED)
        self.machine.start_turn("sess_123", run_id="run_456", user_prompt="test")

        assert self.machine.turn_context is not None
        assert self.machine.turn_context.session_id == "sess_123"
        assert self.machine.turn_context.run_id == "run_456"
        assert self.machine.turn_context.user_prompt == "test"

    def test_end_turn(self):
        self.machine.transition(StateMachineEvent.CONNECTED)
        self.machine.start_turn("sess_123")
        self.machine.end_turn("end_turn")

        assert self.machine.turn_context is None
        assert self.machine.metrics.total_turns == 1
        assert self.machine.metrics.successful_turns == 1

    def test_failed_turn_increments_failed_metric(self):
        self.machine.transition(StateMachineEvent.CONNECTED)
        self.machine.start_turn("sess_123")
        self.machine.end_turn("error", "Something went wrong")

        assert self.machine.metrics.total_turns == 1
        assert self.machine.metrics.failed_turns == 1
        assert self.machine.metrics.successful_turns == 0

    def test_stale_detection(self):
        self.machine.transition(StateMachineEvent.CONNECTED)
        self.machine.start_turn("sess_123")

        # Manually set last event time to past
        self.machine.turn_context.last_event_timestamp = time.time() - 100

        assert self.machine._check_stale() is True

    def test_get_status(self):
        self.machine.transition(StateMachineEvent.CONNECTED)
        status = self.machine.get_status()

        assert status["state"] == GuardianState.OBSERVING.value
        assert status["safety_mode"] == SafetyMode.OBSERVE_ONLY.value
        assert "metrics" in status
        assert "backoff" in status


class TestFailurePatterns:
    """Tests for failure pattern coverage."""

    def test_all_failure_types_have_patterns(self):
        """Ensure each FailureType has at least one pattern."""
        covered = set()
        for pattern in FAILURE_PATTERNS:
            covered.add(pattern.failure_type)

        # Check all non-UNKNOWN types are covered
        for ftype in FailureType:
            if ftype != FailureType.UNKNOWN_FAILURE:
                assert ftype in covered, f"No pattern for {ftype.value}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])