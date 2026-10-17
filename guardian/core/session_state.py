"""
Generic session and turn state tracking for Strixora.

Agent-agnostic state model that tracks side-effect evidence for safety decisions.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set
import time

from .types import (
    TurnPhase,
    SideEffectCategory,
)


@dataclass
class SideEffectEvidence:
    """Tracks all observed side-effect evidence in current turn."""
    # Assistant output
    assistant_chunks_received: int = 0
    visible_text_delivered: bool = False
    reasoning_chunks_received: int = 0
    
    # Tool activity
    tools_started: List[str] = field(default_factory=list)
    tools_completed: List[str] = field(default_factory=list)
    tools_failed: List[str] = field(default_factory=list)
    partial_tool_names: List[str] = field(default_factory=list)
    
    # Tool call ID tracking (for deduplication across restarts)
    seen_tool_call_ids: List[str] = field(default_factory=list)
    completed_tool_call_ids: List[str] = field(default_factory=list)
    failed_tool_call_ids: List[str] = field(default_factory=list)
    # Map tool_name -> list of tool_call_ids for that tool
    tool_call_id_map: Dict[str, List[str]] = field(default_factory=dict)
    
    # State generation for TOCTOU protection
    state_generation: int = 0
    
    # Timing
    first_chunk_timestamp: Optional[float] = None
    last_chunk_timestamp: Optional[float] = None
    last_tool_timestamp: Optional[float] = None
    turn_start_timestamp: float = field(default_factory=time.time)
    
    # State
    turn_phase: TurnPhase = TurnPhase.IDLE
    stop_reason: Optional[str] = None
    error_message: Optional[str] = None
    
    def get_seen_tool_call_ids(self) -> Set[str]:
        return set(self.seen_tool_call_ids)
    
    def get_completed_tool_call_ids(self) -> Set[str]:
        return set(self.completed_tool_call_ids)
    
    def get_failed_tool_call_ids(self) -> Set[str]:
        return set(self.failed_tool_call_ids)
    
    def get_tool_call_ids_for_tool(self, tool_name: str) -> List[str]:
        return self.tool_call_id_map.get(tool_name, [])
    
    def add_seen_tool_call_id(self, tool_call_id: str, tool_name: str):
        if tool_call_id not in self.seen_tool_call_ids:
            self.seen_tool_call_ids.append(tool_call_id)
        if tool_name not in self.tool_call_id_map:
            self.tool_call_id_map[tool_name] = []
        if tool_call_id not in self.tool_call_id_map[tool_name]:
            self.tool_call_id_map[tool_name].append(tool_call_id)
    
    def mark_tool_completed(self, tool_call_id: str, tool_name: str):
        if tool_call_id not in self.completed_tool_call_ids:
            self.completed_tool_call_ids.append(tool_call_id)
    
    def mark_tool_failed(self, tool_call_id: str, tool_name: str):
        if tool_call_id not in self.failed_tool_call_ids:
            self.failed_tool_call_ids.append(tool_call_id)
    
    def get_categories(self) -> List[SideEffectCategory]:
        """Get all observed side-effect categories."""
        categories = []
        
        if self.assistant_chunks_received > 0:
            if self.visible_text_delivered:
                categories.append(SideEffectCategory.VISIBLE_OUTPUT)
            elif self.reasoning_chunks_received > 0 and self.assistant_chunks_received == 0:
                categories.append(SideEffectCategory.REASONING_ONLY)
            else:
                categories.append(SideEffectCategory.ASSISTANT_CHUNKS)
        
        if self.tools_started:
            categories.append(SideEffectCategory.TOOL_STARTED)
        if self.tools_completed:
            categories.append(SideEffectCategory.TOOL_COMPLETED)
        if self.tools_failed:
            categories.append(SideEffectCategory.TOOL_FAILED)
        if self.partial_tool_names:
            categories.append(SideEffectCategory.PARTIAL_TOOL_ARGS)
        
        if not categories:
            categories.append(SideEffectCategory.NO_EVIDENCE)
        
        return categories
    
    def has_any_side_effect(self) -> bool:
        """Check if ANY side effect has been observed."""
        return (
            self.assistant_chunks_received > 0 or
            len(self.tools_started) > 0 or
            len(self.partial_tool_names) > 0
        )
    
    def has_unknown_tool_state(self) -> bool:
        """Check if tool state is ambiguous."""
        # Tool started but not confirmed completed/failed
        started_not_finished = set(self.tools_started) - set(self.tools_completed) - set(self.tools_failed)
        return len(started_not_finished) > 0
    
    def is_safe_to_replay(self) -> bool:
        """
        Determine if replaying the user prompt is safe.
        
        Safe ONLY if no side effects have been observed.
        This is the primary safety gate.
        """
        return not self.has_any_side_effect()


@dataclass
class TurnState:
    """Complete state for a single turn."""
    session_id: str
    turn_id: Optional[str] = None
    run_id: Optional[str] = None
    user_prompt: Optional[str] = None
    evidence: SideEffectEvidence = field(default_factory=SideEffectEvidence)
    recovery_attempts: int = 0
    recovery_cycles: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # State generation for TOCTOU protection (persisted)
    state_generation: int = 0

    def update_timestamp(self):
        self.updated_at = time.time()

    def increment_generation(self) -> int:
        """Increment state generation for TOCTOU protection."""
        self.state_generation += 1
        return self.state_generation

    def record_assistant_chunk(self, text: str = "", is_reasoning: bool = False):
        """Record an assistant chunk."""
        self.evidence.assistant_chunks_received += 1
        self.evidence.last_chunk_timestamp = time.time()
        if self.evidence.first_chunk_timestamp is None:
            self.evidence.first_chunk_timestamp = time.time()
        if text and text.strip() and not is_reasoning:
            self.evidence.visible_text_delivered = True
        if is_reasoning:
            self.evidence.reasoning_chunks_received += 1
        self.evidence.turn_phase = TurnPhase.ASSISTANT_STREAMING
        self.update_timestamp()
        self.increment_generation()

    def record_tool_started(self, tool_name: str, tool_call_id: str = None):
        """Record tool execution start."""
        self.evidence.tools_started.append(tool_name)
        if tool_call_id:
            self.evidence.partial_tool_names.append(tool_name)
            self.evidence.add_seen_tool_call_id(tool_call_id, tool_name)
        self.evidence.last_tool_timestamp = time.time()
        self.evidence.turn_phase = TurnPhase.TOOL_EXECUTING
        self.update_timestamp()
        self.increment_generation()

    def record_tool_progress(self, tool_name: str):
        """Record tool progress."""
        self.evidence.last_tool_timestamp = time.time()
        self.update_timestamp()
        self.increment_generation()

    def record_tool_completed(self, tool_name: str, tool_call_id: str = None):
        """Record tool completion."""
        if tool_name in self.evidence.tools_started:
            self.evidence.tools_completed.append(tool_name)
        if tool_call_id:
            self.evidence.mark_tool_completed(tool_call_id, tool_name)
            if tool_name in self.evidence.partial_tool_names:
                self.evidence.partial_tool_names.remove(tool_name)
        self.evidence.last_tool_timestamp = time.time()
        self.evidence.turn_phase = TurnPhase.ASSISTANT_STREAMING
        self.update_timestamp()
        self.increment_generation()

    def record_tool_failed(self, tool_name: str, tool_call_id: str = None):
        """Record tool failure."""
        if tool_name in self.evidence.tools_started:
            self.evidence.tools_failed.append(tool_name)
        if tool_call_id:
            self.evidence.mark_tool_failed(tool_call_id, tool_name)
            if tool_name in self.evidence.partial_tool_names:
                self.evidence.partial_tool_names.remove(tool_name)
        self.evidence.last_tool_timestamp = time.time()
        self.update_timestamp()
        self.increment_generation()
    
    def record_turn_end(self, stop_reason: str = "end_turn", error: str = None):
        """Record turn completion."""
        self.evidence.turn_phase = TurnPhase.TURN_ENDING
        self.evidence.stop_reason = stop_reason
        self.evidence.error_message = error
        if stop_reason == "end_turn":
            self.evidence.turn_phase = TurnPhase.TURN_COMPLETE
        elif stop_reason == "error":
            self.evidence.turn_phase = TurnPhase.TURN_ERROR
        elif stop_reason == "cancelled":
            self.evidence.turn_phase = TurnPhase.TURN_CANCELLED
        self.update_timestamp()
    
    def reset_for_next_turn(self):
        """Reset evidence for next turn (preserve session/turn IDs)."""
        self.evidence = SideEffectEvidence()
        self.evidence.turn_start_timestamp = time.time()
        self.recovery_attempts = 0
        self.recovery_cycles = 0
        self.update_timestamp()


@dataclass
class SessionState:
    """Agent-agnostic session state tracking."""
    session_id: str
    agent_type: str = "unknown"  # "hermes", "goose", etc.
    current_turn: Optional[TurnState] = None
    turn_history: List[TurnState] = field(default_factory=list)
    total_turns: int = 0
    successful_turns: int = 0
    failed_turns: int = 0
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    
    # Persistence
    is_persistent: bool = True
    storage_path: Optional[str] = None
    
    # Recovery
    active_recovery: bool = False
    recovery_start_time: Optional[float] = None
    
    def start_turn(self, turn_id: str = None, run_id: str = None, user_prompt: str = None) -> TurnState:
        """Start a new turn."""
        turn = TurnState(
            session_id=self.session_id,
            turn_id=turn_id,
            run_id=run_id,
            user_prompt=user_prompt,
        )
        self.current_turn = turn
        self.last_activity = time.time()
        return turn
    
    def end_turn(self, stop_reason: str = "end_turn", error: str = None):
        """End current turn and move to history."""
        if self.current_turn:
            self.current_turn.record_turn_end(stop_reason, error)
            self.turn_history.append(self.current_turn)
            self.total_turns += 1
            if stop_reason == "end_turn":
                self.successful_turns += 1
            else:
                self.failed_turns += 1
            self.current_turn = None
        self.last_activity = time.time()
    
    def get_safety_status(self) -> Dict[str, Any]:
        """Get current safety evaluation."""
        if not self.current_turn:
            return {"safe_to_replay": True, "reason": "No active turn"}
        
        evidence = self.current_turn.evidence
        safe = evidence.is_safe_to_replay()
        
        reason = "No side effects observed"
        if evidence.visible_text_delivered:
            reason = f"Visible assistant output delivered ({evidence.assistant_chunks_received} chunks)"
        elif evidence.assistant_chunks_received > 0:
            reason = f"Assistant chunks received ({evidence.assistant_chunks_received})"
        elif evidence.tools_started:
            reason = f"Tool activity started ({len(evidence.tools_started)} tools)"
        elif evidence.partial_tool_names:
            reason = f"Partial tool calls in progress ({evidence.partial_tool_names})"
        
        return {
            "safe_to_replay": safe,
            "reason": reason,
            "categories": [c.value for c in evidence.get_categories()],
            "assistant_chunks": evidence.assistant_chunks_received,
            "visible_output": evidence.visible_text_delivered,
            "tools_started": evidence.tools_started,
            "tools_completed": evidence.tools_completed,
            "partial_tools": evidence.partial_tool_names,
            "turn_phase": evidence.turn_phase.value,
            "unknown_tool_state": evidence.has_unknown_tool_state(),
        }
    
    def time_since_last_activity(self) -> float:
        return time.time() - self.last_activity


# Convenience functions
def create_session(session_id: str, agent_type: str = "unknown") -> SessionState:
    """Create a new session."""
    return SessionState(session_id=session_id, agent_type=agent_type)