"""
Ping-based liveness detection for Guardian.

Provides generic ping monitor that can be integrated with agent observers
to detect stale streams more accurately than time-based checks alone.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any
from abc import ABC, abstractmethod


@dataclass
class PingConfig:
    """Configuration for ping-based liveness detection."""
    ping_interval: float = 30.0          # Seconds between pings
    ping_timeout: float = 10.0           # Seconds to wait for pong
    max_missed_pings: int = 3            # Pings missed before marking stale
    stale_threshold: float = 60.0        # Fallback time-based threshold


@dataclass
class PingState:
    """Current state of ping monitoring for a session."""
    session_id: str
    last_ping_time: Optional[float] = None
    last_pong_time: Optional[float] = None
    consecutive_missed: int = 0
    total_pings_sent: int = 0
    total_pongs_received: int = 0
    is_stale: bool = False
    stale_reason: str = ""


class PingTransport(ABC):
    """Abstract transport for sending/receiving pings."""
    
    @abstractmethod
    async def send_ping(self, session_id: str) -> bool:
        """Send a ping. Returns True if sent successfully."""
        pass
    
    @abstractmethod
    async def wait_for_pong(self, session_id: str, timeout: float) -> bool:
        """Wait for pong response. Returns True if received within timeout."""
        pass
    
    @abstractmethod
    def is_connected(self, session_id: str) -> bool:
        """Check if transport is connected for session."""
        pass


class PingMonitor:
    """
    Generic ping-based liveness monitor.
    
    Integrates with agent observers to provide more accurate stale detection
    than time-based checks alone. Works with any transport that implements
    PingTransport interface.
    """
    
    def __init__(
        self,
        config: Optional[PingConfig] = None,
        transport: Optional[PingTransport] = None,
        on_stale: Optional[Callable[[str, str], None]] = None,
    ):
        self.config = config or PingConfig()
        self.transport = transport
        self.on_stale = on_stale
        self.sessions: Dict[str, PingState] = {}
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
    
    def start_monitoring(self, session_id: str):
        """Start ping monitoring for a session."""
        if session_id not in self.sessions:
            self.sessions[session_id] = PingState(session_id=session_id)
    
    def stop_monitoring(self, session_id: str):
        """Stop ping monitoring for a session."""
        if session_id in self.sessions:
            del self.sessions[session_id]
    
    async def start(self):
        """Start the monitoring loop."""
        self._running = True
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())
    
    async def stop(self):
        """Stop the monitoring loop."""
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
    
    async def _monitor_loop(self):
        """Main monitoring loop - sends pings and checks for stale sessions."""
        while self._running:
            try:
                await asyncio.sleep(self.config.ping_interval)
                await self._check_all_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log error but continue monitoring
                pass
    
    async def _check_all_sessions(self):
        """Check all monitored sessions for staleness."""
        for session_id, state in list(self.sessions.items()):
            await self._check_session(session_id, state)
    
    async def _check_session(self, session_id: str, state: PingState):
        """Check a single session for staleness using ping/pong."""
        if not self.transport or not self.transport.is_connected(session_id):
            # Fallback to time-based check if no transport
            if self._is_stale_time_based(state):
                await self._mark_stale(session_id, state, "Transport disconnected")
            return
        
        # Send ping
        try:
            sent = await self.transport.send_ping(session_id)
            state.total_pings_sent += 1
            state.last_ping_time = time.time()
            if not sent:
                state.consecutive_missed += 1
                if state.consecutive_missed >= self.config.max_missed_pings:
                    await self._mark_stale(session_id, state, f"Failed to send ping after {state.consecutive_missed} attempts")
                return
            
            # Wait for pong
            pong_received = await self.transport.wait_for_pong(session_id, self.config.ping_timeout)
            
            if pong_received:
                state.last_pong_time = time.time()
                state.consecutive_missed = 0
                state.total_pongs_received += 1
                state.is_stale = False
                state.stale_reason = ""
            else:
                state.consecutive_missed += 1
                if state.consecutive_missed >= self.config.max_missed_pings:
                    await self._mark_stale(session_id, state, f"No pong received after {state.consecutive_missed} pings")
        
        except Exception as e:
            state.consecutive_missed += 1
            if state.consecutive_missed >= self.config.max_missed_pings:
                await self._mark_stale(session_id, state, f"Ping error: {str(e)}")
    
    def _is_stale_time_based(self, state: PingState) -> bool:
        """Fallback time-based stale check."""
        if state.last_pong_time:
            elapsed = time.time() - state.last_pong_time
        elif state.last_ping_time:
            elapsed = time.time() - state.last_ping_time
        else:
            elapsed = float('inf')
        return elapsed > self.config.stale_threshold
    
    async def _mark_stale(self, session_id: str, state: PingState, reason: str):
        """Mark session as stale and trigger callback."""
        if not state.is_stale:
            state.is_stale = True
            state.stale_reason = reason
            if self.on_stale:
                try:
                    self.on_stale(session_id, reason)
                except Exception:
                    pass
    
    def get_session_state(self, session_id: str) -> Optional[PingState]:
        """Get ping state for a session."""
        return self.sessions.get(session_id)
    
    def is_stale(self, session_id: str) -> bool:
        """Check if session is marked stale."""
        state = self.sessions.get(session_id)
        return state.is_stale if state else False
    
    def get_status(self) -> Dict[str, Any]:
        """Get monitor status summary."""
        return {
            "running": self._running,
            "sessions_monitored": len(self.sessions),
            "config": {
                "ping_interval": self.config.ping_interval,
                "ping_timeout": self.config.ping_timeout,
                "max_missed_pings": self.config.max_missed_pings,
                "stale_threshold": self.config.stale_threshold,
            },
            "sessions": {
                sid: {
                    "consecutive_missed": state.consecutive_missed,
                    "total_pings_sent": state.total_pings_sent,
                    "total_pongs_received": state.total_pongs_received,
                    "is_stale": state.is_stale,
                    "stale_reason": state.stale_reason,
                }
                for sid, state in self.sessions.items()
            }
        }


class WebSocketPingTransport(PingTransport):
    """Ping transport for WebSocket connections (Goose)."""

    def __init__(self, ws_dict: Dict[str, Any]):
        self.ws_dict = ws_dict  # session_id -> websocket
        # session_id -> pending pong awaitable created by send_ping
        self._pong_futures: Dict[str, Any] = {}

    async def send_ping(self, session_id: str) -> bool:
        """
        Initiate a ping. The matching pong is awaited (time-boxed) in
        wait_for_pong. The awaitable is scheduled and stored instead of awaited
        here so a half-open peer cannot block the monitor indefinitely.
        """
        ws = self.ws_dict.get(session_id)
        if ws and not ws.closed:
            try:
                prev = self._pong_futures.pop(session_id, None)
                if prev is not None and not prev.done():
                    prev.cancel()
                # `ws.ping()` returns an awaitable that completes when the
                # matching pong arrives. Scheduling it sends the frame; the
                # bounded wait happens in wait_for_pong.
                self._pong_futures[session_id] = asyncio.ensure_future(ws.ping())
                return True
            except Exception:
                self._pong_futures.pop(session_id, None)
                return False
        return False

    async def wait_for_pong(self, session_id: str, timeout: float) -> bool:
        """
        Wait for the pong matching the last ping, bounded by ``timeout``.
        Falls back to issuing a fresh ping if send_ping was not called first.
        A half-open socket yields a timeout (False); a cancelled/late pong is
        ignored because the pending awaitable is popped before waiting.
        """
        ws = self.ws_dict.get(session_id)
        if not ws or ws.closed:
            self._pong_futures.pop(session_id, None)
            return False

        pending = self._pong_futures.pop(session_id, None)
        if pending is None:
            try:
                pending = ws.ping()
            except Exception:
                return False

        try:
            await asyncio.wait_for(pending, timeout=timeout)
            return True
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False
        except Exception:
            return False

    def is_connected(self, session_id: str) -> bool:
        ws = self.ws_dict.get(session_id)
        return ws is not None and not ws.closed


class InProcessPingTransport(PingTransport):
    """Ping transport for in-process agents (Hermes)."""
    
    def __init__(self, agent_dict: Dict[str, Any]):
        self.agent_dict = agent_dict  # session_id -> agent instance
    
    async def send_ping(self, session_id: str) -> bool:
        agent = self.agent_dict.get(session_id)
        if agent is None:
            return False
        
        # Check if agent has a callable ping method
        ping_method = getattr(agent, 'ping', None)
        if callable(ping_method):
            try:
                result = ping_method()
                if asyncio.iscoroutine(result):
                    await result
                return True
            except Exception:
                return False
        
        # Agent exists but no ping method - consider it responsive
        return True
    
    async def wait_for_pong(self, session_id: str, timeout: float) -> bool:
        # In-process ping - if send_ping succeeded, pong is received
        # For agents with async ping, we already awaited in send_ping
        return True
    
    def is_connected(self, session_id: str) -> bool:
        agent = self.agent_dict.get(session_id)
        return agent is not None


# Integration helpers
def create_hermes_ping_monitor(
    agent,
    stale_threshold: float = 60.0,
    ping_interval: float = 30.0,
    on_stale: Optional[Callable[[str, str], None]] = None,
) -> PingMonitor:
    """Create a ping monitor for Hermes in-process integration."""
    config = PingConfig(
        ping_interval=ping_interval,
        stale_threshold=stale_threshold,
    )
    transport = InProcessPingTransport({getattr(agent, 'session_id', 'default'): agent})
    return PingMonitor(config=config, transport=transport, on_stale=on_stale)


def create_goose_ping_monitor(
    ws_dict: Dict[str, Any],
    stale_threshold: float = 60.0,
    ping_interval: float = 30.0,
    on_stale: Optional[Callable[[str, str], None]] = None,
) -> PingMonitor:
    """Create a ping monitor for Goose WebSocket integration."""
    config = PingConfig(
        ping_interval=ping_interval,
        stale_threshold=stale_threshold,
    )
    transport = WebSocketPingTransport(ws_dict)
    return PingMonitor(config=config, transport=transport, on_stale=on_stale)