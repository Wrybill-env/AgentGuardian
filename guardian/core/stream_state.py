"""
Stream lifecycle state tracking for Strixora.

Tracks streaming-specific state including timing for stale detection.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import time

from .types import (
    StreamPhase,
    StaleReason,
)


@dataclass
class StreamState:
    """Tracks streaming state with timing for stale detection."""
    session_id: str
    turn_id: Optional[str] = None
    
    # Phase
    phase: StreamPhase = StreamPhase.IDLE
    
    # Timing
    stream_start_time: Optional[float] = None
    first_chunk_time: Optional[float] = None
    last_chunk_time: Optional[float] = None
    last_reasoning_time: Optional[float] = None
    last_tool_time: Optional[float] = None
    last_event_time: Optional[float] = None
    
    # Chunk tracking
    chunks_received: int = 0
    reasoning_chunks_received: int = 0
    visible_chunks: int = 0  # Non-whitespace text chunks
    
    # Tool overlap
    tool_active: bool = False
    tool_start_time: Optional[float] = None
    tool_name: Optional[str] = None
    
    # Stale detection
    stale_detected: bool = False
    stale_reason: Optional[StaleReason] = None
    stale_since: Optional[float] = None
    
    # Configuration (set by adapter/profile)
    first_chunk_threshold: float = 30.0
    chunk_gap_threshold: float = 15.0
    tool_timeout_threshold: float = 120.0
    general_stale_threshold: float = 60.0
    
    def start_stream(self):
        """Mark stream as started."""
        self.phase = StreamPhase.WAITING_FIRST_CHUNK
        self.stream_start_time = time.time()
        self.last_event_time = time.time()
        self.stale_detected = False
        self.stale_reason = None
        self.stale_since = None
    
    def record_first_chunk(self, text: str = "", is_reasoning: bool = False):
        """Record first chunk received."""
        if self.phase == StreamPhase.WAITING_FIRST_CHUNK:
            self.phase = StreamPhase.STREAMING
        self.first_chunk_time = time.time()
        self.last_chunk_time = time.time()
        self.last_event_time = time.time()
        self.chunks_received += 1
        if is_reasoning:
            self.reasoning_chunks_received += 1
        elif text and text.strip():
            self.visible_chunks += 1
        self.stale_detected = False
    
    def record_chunk(self, text: str = "", is_reasoning: bool = False):
        """Record subsequent chunk."""
        self.last_chunk_time = time.time()
        self.last_event_time = time.time()
        self.chunks_received += 1
        if is_reasoning:
            self.reasoning_chunks_received += 1
            self.last_reasoning_time = time.time()
        elif text and text.strip():
            self.visible_chunks += 1
        self.stale_detected = False
    
    def record_reasoning(self, text: str = ""):
        """Record reasoning chunk."""
        self.last_reasoning_time = time.time()
        self.last_event_time = time.time()
        self.reasoning_chunks_received += 1
    
    def record_tool_start(self, tool_name: str):
        """Record tool execution start (may overlap with streaming)."""
        self.tool_active = True
        self.tool_start_time = time.time()
        self.tool_name = tool_name
        self.last_tool_time = time.time()
        self.last_event_time = time.time()
    
    def record_tool_progress(self):
        """Record tool progress."""
        self.last_tool_time = time.time()
        self.last_event_time = time.time()
    
    def record_tool_end(self):
        """Record tool completion."""
        self.tool_active = False
        self.tool_start_time = None
        self.tool_name = None
        self.last_event_time = time.time()
    
    def complete_stream(self):
        """Mark stream as completed."""
        self.phase = StreamPhase.COMPLETED
        self.last_event_time = time.time()
        self.stale_detected = False
    
    def error_stream(self, reason: str = "error"):
        """Mark stream as errored."""
        self.phase = StreamPhase.ERROR
        self.last_event_time = time.time()
        self.stale_detected = False
    
    def cancel_stream(self):
        """Mark stream as cancelled."""
        self.phase = StreamPhase.CANCELLED
        self.last_event_time = time.time()
        self.stale_detected = False
    
    def check_stale(self) -> List[StaleReason]:
        """
        Check for stale conditions.
        
        Returns list of detected stale reasons (can be multiple).
        """
        if self.phase not in (StreamPhase.WAITING_FIRST_CHUNK, StreamPhase.STREAMING, StreamPhase.STALLED):
            return []
        
        now = time.time()
        stale_reasons = []
        
        # First chunk timeout
        if self.phase == StreamPhase.WAITING_FIRST_CHUNK and self.stream_start_time:
            if now - self.stream_start_time > self.first_chunk_threshold:
                stale_reasons.append(StaleReason.FIRST_CHUNK_TIMEOUT)
        
        # Chunk gap (only after first chunk received)
        if self.first_chunk_time and self.last_chunk_time:
            if now - self.last_chunk_time > self.chunk_gap_threshold:
                stale_reasons.append(StaleReason.CHUNK_GAP)
        
        # Tool timeout
        if self.tool_active and self.tool_start_time:
            if now - self.tool_start_time > self.tool_timeout_threshold:
                stale_reasons.append(StaleReason.TOOL_TIMEOUT)
        
        # General stale (any active phase, no events)
        if self.last_event_time and now - self.last_event_time > self.general_stale_threshold:
            stale_reasons.append(StaleReason.GENERAL_STALE)
        
        # Update stale state
        if stale_reasons and not self.stale_detected:
            self.stale_detected = True
            self.stale_reason = stale_reasons[0]  # Primary reason
            self.stale_since = now
            self.phase = StreamPhase.STALLED
        elif not stale_reasons:
            self.stale_detected = False
            self.stale_reason = None
            self.stale_since = None
            if self.phase == StreamPhase.STALLED:
                self.phase = StreamPhase.STREAMING
        
        return stale_reasons
    
    def time_since_first_chunk(self) -> Optional[float]:
        if self.first_chunk_time:
            return time.time() - self.first_chunk_time
        return None
    
    def time_since_last_chunk(self) -> Optional[float]:
        if self.last_chunk_time:
            return time.time() - self.last_chunk_time
        return None
    
    def time_since_stream_start(self) -> Optional[float]:
        if self.stream_start_time:
            return time.time() - self.stream_start_time
        return None
    
    def time_since_last_event(self) -> Optional[float]:
        if self.last_event_time:
            return time.time() - self.last_event_time
        return None
    
    def time_since_tool_start(self) -> Optional[float]:
        if self.tool_start_time:
            return time.time() - self.tool_start_time
        return None
    
    def has_visible_output(self) -> bool:
        return self.visible_chunks > 0
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "phase": self.phase.value,
            "chunks_received": self.chunks_received,
            "reasoning_chunks": self.reasoning_chunks_received,
            "visible_chunks": self.visible_chunks,
            "tool_active": self.tool_active,
            "tool_name": self.tool_name,
            "stale_detected": self.stale_detected,
            "stale_reason": self.stale_reason.value if self.stale_reason else None,
            "time_since_last_chunk": self.time_since_last_chunk(),
            "time_since_first_chunk": self.time_since_first_chunk(),
            "time_since_stream_start": self.time_since_stream_start(),
            "time_since_last_event": self.time_since_last_event(),
            "time_since_tool_start": self.time_since_tool_start(),
            "has_visible_output": self.has_visible_output(),
        }


def create_stream_state(session_id: str, turn_id: str = None) -> StreamState:
    """Create a new stream state."""
    return StreamState(session_id=session_id, turn_id=turn_id)