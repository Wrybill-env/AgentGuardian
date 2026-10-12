"""
Durable state persistence for Agent Guardian.

Provides atomic, validated persistence of session and turn state with
schema versioning, corruption detection, and fail-closed loading.
"""

import json
import os
import tempfile
import hashlib
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Dict, List, Union
from enum import Enum

from .types import TurnPhase, SideEffectCategory
from .session_state import SessionState, TurnState, SideEffectEvidence, create_session


# Persistence format version - increment on breaking schema changes
PERSISTENCE_VERSION = 1


class PersistenceError(Exception):
    """Base exception for persistence errors."""
    pass


class CorruptStateError(PersistenceError):
    """Persisted state failed validation."""
    pass


class IncompatibleStateError(PersistenceError):
    """Persisted state schema version mismatch."""
    pass


class SessionMismatchError(PersistenceError):
    """Persisted state session_id doesn't match current session."""
    pass


class StatePersister:
    """
    Handles atomic, validated persistence of Guardian session state.

    Features:
    - Atomic writes via temp file + rename
    - Schema versioning for forward compatibility
    - SHA256 checksum for corruption detection
    - Session identity verification on load
    - Fail-closed behavior on any validation error
    - No secrets in persisted state
    """

    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path).expanduser()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def _compute_checksum(self, data: dict) -> str:
        """Compute SHA256 checksum of serializable data (excluding checksum field)."""
        # Create a copy without the checksum field
        data_copy = {k: v for k, v in data.items() if k != "checksum"}
        serialized = json.dumps(data_copy, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()

    def _serialize_evidence(self, evidence: SideEffectEvidence) -> dict:
        """Serialize SideEffectEvidence to dict."""
        return {
            "assistant_chunks_received": evidence.assistant_chunks_received,
            "visible_text_delivered": evidence.visible_text_delivered,
            "reasoning_chunks_received": evidence.reasoning_chunks_received,
            "tools_started": evidence.tools_started,
            "tools_completed": evidence.tools_completed,
            "tools_failed": evidence.tools_failed,
            "partial_tool_names": evidence.partial_tool_names,
            "first_chunk_timestamp": evidence.first_chunk_timestamp,
            "last_chunk_timestamp": evidence.last_chunk_timestamp,
            "last_tool_timestamp": evidence.last_tool_timestamp,
            "turn_start_timestamp": evidence.turn_start_timestamp,
            "turn_phase": evidence.turn_phase.value,
            "stop_reason": evidence.stop_reason,
            "error_message": evidence.error_message,
        }

    def _deserialize_evidence(self, data: dict) -> SideEffectEvidence:
        """Deserialize dict to SideEffectEvidence."""
        evidence = SideEffectEvidence()
        evidence.assistant_chunks_received = data.get("assistant_chunks_received", 0)
        evidence.visible_text_delivered = data.get("visible_text_delivered", False)
        evidence.reasoning_chunks_received = data.get("reasoning_chunks_received", 0)
        evidence.tools_started = data.get("tools_started", [])
        evidence.tools_completed = data.get("tools_completed", [])
        evidence.tools_failed = data.get("tools_failed", [])
        evidence.partial_tool_names = data.get("partial_tool_names", [])
        evidence.first_chunk_timestamp = data.get("first_chunk_timestamp")
        evidence.last_chunk_timestamp = data.get("last_chunk_timestamp")
        evidence.last_tool_timestamp = data.get("last_tool_timestamp")
        evidence.turn_start_timestamp = data.get("turn_start_timestamp", datetime.now().timestamp())
        evidence.turn_phase = TurnPhase(data.get("turn_phase", "idle"))
        evidence.stop_reason = data.get("stop_reason")
        evidence.error_message = data.get("error_message")
        return evidence

    def _serialize_turn(self, turn: TurnState) -> dict:
        """Serialize TurnState to dict."""
        return {
            "session_id": turn.session_id,
            "turn_id": turn.turn_id,
            "run_id": turn.run_id,
            "user_prompt": turn.user_prompt,
            "evidence": self._serialize_evidence(turn.evidence),
            "recovery_attempts": turn.recovery_attempts,
            "recovery_cycles": turn.recovery_cycles,
            "created_at": turn.created_at,
            "updated_at": turn.updated_at,
        }

    def _deserialize_turn(self, data: dict) -> TurnState:
        """Deserialize dict to TurnState."""
        turn = TurnState(
            session_id=data["session_id"],
            turn_id=data.get("turn_id"),
            run_id=data.get("run_id"),
            user_prompt=data.get("user_prompt"),
        )
        turn.evidence = self._deserialize_evidence(data["evidence"])
        turn.recovery_attempts = data.get("recovery_attempts", 0)
        turn.recovery_cycles = data.get("recovery_cycles", 0)
        turn.created_at = data.get("created_at", turn.created_at)
        turn.updated_at = data.get("updated_at", turn.updated_at)
        return turn

    def _serialize_session(self, session: SessionState) -> dict:
        """Serialize SessionState to dict (excluding secrets)."""
        # Filter out any sensitive fields that shouldn't be persisted
        safe_data = {
            "version": PERSISTENCE_VERSION,
            "session_id": session.session_id,
            "agent_type": session.agent_type,
            "current_turn": self._serialize_turn(session.current_turn) if session.current_turn else None,
            "turn_history": [self._serialize_turn(t) for t in session.turn_history],
            "total_turns": session.total_turns,
            "successful_turns": session.successful_turns,
            "failed_turns": session.failed_turns,
            "created_at": session.created_at,
            "last_activity": session.last_activity,
            "is_persistent": session.is_persistent,
            "active_recovery": session.active_recovery,
            "recovery_start_time": session.recovery_start_time,
            "timestamp": datetime.now().isoformat(),
        }
        # Add checksum
        safe_data["checksum"] = self._compute_checksum(safe_data)
        return safe_data

    def _deserialize_session(self, data: dict) -> SessionState:
        """Deserialize dict to SessionState."""
        # Verify version
        version = data.get("version", 0)
        if version != PERSISTENCE_VERSION:
            raise IncompatibleStateError(
                f"Persisted state version {version} != current version {PERSISTENCE_VERSION}"
            )

        # Verify checksum
        expected_checksum = data.get("checksum")
        if not expected_checksum:
            raise CorruptStateError("Missing checksum in persisted state")

        actual_checksum = self._compute_checksum(data)
        if actual_checksum != expected_checksum:
            raise CorruptStateError("Checksum mismatch - persisted state is corrupt")

        session = SessionState(
            session_id=data["session_id"],
            agent_type=data.get("agent_type", "unknown"),
        )
        session.total_turns = data.get("total_turns", 0)
        session.successful_turns = data.get("successful_turns", 0)
        session.failed_turns = data.get("failed_turns", 0)
        session.created_at = data.get("created_at", session.created_at)
        session.last_activity = data.get("last_activity", session.last_activity)
        session.is_persistent = data.get("is_persistent", True)
        session.active_recovery = data.get("active_recovery", False)
        session.recovery_start_time = data.get("recovery_start_time")

        # Restore current turn
        current_turn_data = data.get("current_turn")
        if current_turn_data:
            session.current_turn = self._deserialize_turn(current_turn_data)

        # Restore turn history
        session.turn_history = [
            self._deserialize_turn(t) for t in data.get("turn_history", [])
        ]

        return session

    def save(self, session: SessionState) -> None:
        """
        Atomically save session state to disk.

        Uses temp file + atomic rename to prevent partial writes.
        On Windows, concurrent writes may fail with AccessDenied - caller should retry.
        """
        if not session.is_persistent:
            return

        data = self._serialize_session(session)

        # Write to temp file first
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=self.storage_path.parent,
            prefix=f".{self.storage_path.name}.tmp.",
            suffix=".json",
            delete=False,
        ) as tmp:
            json.dump(data, tmp, separators=(",", ":"))
            tmp_path = Path(tmp.name)

        try:
            # Atomic rename (POSIX) or replace (Windows)
            tmp_path.replace(self.storage_path)
        except PermissionError:
            # On Windows, concurrent replace can fail - clean up and re-raise
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        except Exception:
            # Clean up temp file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def load(self, session_id: str) -> Optional[SessionState]:
        """
        Load and validate session state from disk.

        Returns None if no state file exists (not an error).
        Raises PersistenceError on validation failure (corrupt, incompatible, etc).
        """
        if not self.storage_path.exists():
            return None

        try:
            with open(self.storage_path, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise CorruptStateError(f"Invalid JSON: {e}")

        # Verify session ID matches
        if data.get("session_id") != session_id:
            raise SessionMismatchError(
                f"Persisted session_id '{data.get('session_id')}' != requested '{session_id}'"
            )

        return self._deserialize_session(data)

    def delete(self) -> None:
        """Delete persisted state file."""
        try:
            self.storage_path.unlink(missing_ok=True)
        except Exception:
            pass


def create_persisted_session(
    session_id: str,
    agent_type: str,
    storage_path: str,
) -> tuple[SessionState, Optional[SessionState]]:
    """
    Create a session and attempt to load persisted state.

    Returns:
        (session, loaded_state) where loaded_state is the restored SessionState
        if persistence was successful, or None if no valid state was found.

        The returned session has verified_fresh context set appropriately.
    """
    persister = StatePersister(storage_path)

    # Try to load persisted state
    loaded = None
    try:
        loaded = persister.load(session_id)
    except PersistenceError as e:
        # Log but don't fail - fail closed means we just don't get verified_fresh
        # In production, log this: logger.warning(f"Failed to load persisted state: {e}")
        pass

    # Create fresh session
    session = create_session(session_id, agent_type)
    session.is_persistent = True
    session.storage_path = storage_path

    # If we loaded valid state, restore it into the session
    if loaded:
        # Restore session metadata
        session.total_turns = loaded.total_turns
        session.successful_turns = loaded.successful_turns
        session.failed_turns = loaded.failed_turns
        session.created_at = loaded.created_at
        session.last_activity = loaded.last_activity
        session.turn_history = loaded.turn_history
        session.active_recovery = loaded.active_recovery
        session.recovery_start_time = loaded.recovery_start_time

        # If there was a current turn, restore it
        if loaded.current_turn:
            session.current_turn = loaded.current_turn

    return session, loaded


# Convenience function for state machine integration
def create_turn_context_from_session(
    session: SessionState,
    loaded_state: Optional[SessionState],
    run_id: Optional[str] = None,
    user_prompt: Optional[str] = None,
) -> "TurnContext":
    """
    Create a TurnContext from session, with verified_fresh set based on
    whether we successfully loaded persisted state.
    """
    from .types import TurnContext, TurnPhase

    # Start or continue turn
    if session.current_turn:
        turn = session.current_turn
    else:
        turn = session.start_turn(run_id=run_id or "", user_prompt=user_prompt or "")

    # verified_fresh is True ONLY if we successfully loaded and validated persisted state
    verified_fresh = loaded_state is not None

    ctx = TurnContext(
        session_id=session.session_id,
        run_id=turn.run_id,
        phase=turn.evidence.turn_phase,
        assistant_chunks_received=turn.evidence.assistant_chunks_received,
        tool_calls_started=len(turn.evidence.tools_started),
        tool_calls_completed=len(turn.evidence.tools_completed),
        tool_calls_failed=len(turn.evidence.tools_failed),
        last_event_timestamp=turn.evidence.last_chunk_timestamp or turn.evidence.last_tool_timestamp or turn.evidence.turn_start_timestamp,
        last_assistant_chunk_time=turn.evidence.last_chunk_timestamp,
        last_tool_event_time=turn.evidence.last_tool_timestamp,
        first_chunk_time=turn.evidence.first_chunk_timestamp,
        user_prompt=turn.user_prompt,
        stop_reason=turn.evidence.stop_reason,
        error_message=turn.evidence.error_message,
        state_generation=0,
        verified_fresh=verified_fresh,
    )

    # Copy deduplication sets if we have them
    # Note: These would need to be persisted too for full fidelity

    return ctx