"""
Tests for PingMonitor - ping-based liveness detection.
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock

from guardian.core.ping_monitor import (
    PingMonitor,
    PingConfig,
    PingState,
    PingTransport,
    WebSocketPingTransport,
    InProcessPingTransport,
)


class MockTransport(PingTransport):
    """Mock transport for testing."""
    
    def __init__(self):
        self.connected_sessions = set()
        self.ping_results = {}  # session_id -> bool
        self.pong_results = {}  # session_id -> bool
        self.ping_calls = []
        self.pong_calls = []
    
    def set_connected(self, session_id: str, connected: bool):
        if connected:
            self.connected_sessions.add(session_id)
        else:
            self.connected_sessions.discard(session_id)
    
    def set_ping_result(self, session_id: str, result: bool):
        self.ping_results[session_id] = result
    
    def set_pong_result(self, session_id: str, result: bool):
        self.pong_results[session_id] = result
    
    async def send_ping(self, session_id: str) -> bool:
        self.ping_calls.append(session_id)
        return self.ping_results.get(session_id, True)
    
    async def wait_for_pong(self, session_id: str, timeout: float) -> bool:
        self.pong_calls.append((session_id, timeout))
        return self.pong_results.get(session_id, True)
    
    def is_connected(self, session_id: str) -> bool:
        return session_id in self.connected_sessions


@pytest.fixture
def mock_transport():
    return MockTransport()


@pytest.fixture
def ping_config():
    return PingConfig(
        ping_interval=0.01,  # Very fast for tests
        ping_timeout=0.1,
        max_missed_pings=2,
        stale_threshold=1.0,
    )


@pytest.fixture
def ping_monitor(mock_transport, ping_config):
    return PingMonitor(config=ping_config, transport=mock_transport)


class TestPingMonitor:
    """Tests for PingMonitor core functionality."""
    
    @pytest.mark.asyncio
    async def test_start_stop_monitoring(self, ping_monitor):
        """Test starting and stopping monitoring for a session."""
        ping_monitor.start_monitoring("session1")
        assert "session1" in ping_monitor.sessions
        
        ping_monitor.stop_monitoring("session1")
        assert "session1" not in ping_monitor.sessions
    
    @pytest.mark.asyncio
    async def test_successful_ping_pong(self, ping_monitor, mock_transport):
        """Test successful ping/pong cycle."""
        ping_monitor.start_monitoring("session1")
        mock_transport.set_connected("session1", True)
        mock_transport.set_ping_result("session1", True)
        mock_transport.set_pong_result("session1", True)
        
        # Run one monitoring cycle
        await ping_monitor._check_session("session1", ping_monitor.sessions["session1"])
        
        state = ping_monitor.sessions["session1"]
        assert state.consecutive_missed == 0
        assert state.total_pings_sent == 1
        assert state.total_pongs_received == 1
        assert state.is_stale is False
    
    @pytest.mark.asyncio
    async def test_failed_ping(self, ping_monitor, mock_transport):
        """Test failed ping increments missed counter."""
        ping_monitor.start_monitoring("session1")
        mock_transport.set_connected("session1", True)
        mock_transport.set_ping_result("session1", False)
        
        await ping_monitor._check_session("session1", ping_monitor.sessions["session1"])
        
        state = ping_monitor.sessions["session1"]
        assert state.consecutive_missed == 1
        assert state.total_pings_sent == 1
        assert state.total_pongs_received == 0
    
    @pytest.mark.asyncio
    async def test_stale_after_max_missed_pings(self, ping_monitor, mock_transport):
        """Test session marked stale after max missed pings."""
        stale_callback = Mock()
        ping_monitor.on_stale = stale_callback
        
        ping_monitor.start_monitoring("session1")
        mock_transport.set_connected("session1", True)
        mock_transport.set_ping_result("session1", False)
        
        # Miss 2 pings (max_missed_pings=2)
        await ping_monitor._check_session("session1", ping_monitor.sessions["session1"])
        await ping_monitor._check_session("session1", ping_monitor.sessions["session1"])
        
        state = ping_monitor.sessions["session1"]
        assert state.is_stale is True
        assert "Failed to send ping" in state.stale_reason
        stale_callback.assert_called_once_with("session1", state.stale_reason)
    
    @pytest.mark.asyncio
    async def test_stale_after_no_pong(self, ping_monitor, mock_transport):
        """Test session marked stale after no pong received."""
        stale_callback = Mock()
        ping_monitor.on_stale = stale_callback
        
        ping_monitor.start_monitoring("session1")
        mock_transport.set_connected("session1", True)
        mock_transport.set_ping_result("session1", True)
        mock_transport.set_pong_result("session1", False)
        
        # Miss 2 pongs (max_missed_pings=2)
        await ping_monitor._check_session("session1", ping_monitor.sessions["session1"])
        await ping_monitor._check_session("session1", ping_monitor.sessions["session1"])
        
        state = ping_monitor.sessions["session1"]
        assert state.is_stale is True
        assert "No pong received" in state.stale_reason
        stale_callback.assert_called_once_with("session1", state.stale_reason)
    
    @pytest.mark.asyncio
    async def test_transport_disconnected_fallback(self, ping_monitor, mock_transport):
        """Test fallback to time-based check when transport disconnected."""
        stale_callback = Mock()
        ping_monitor.on_stale = stale_callback
        
        ping_monitor.start_monitoring("session1")
        mock_transport.set_connected("session1", False)  # Disconnected
        
        # Need to wait for stale_threshold (1.0s in test config)
        # We can manipulate the state directly
        state = ping_monitor.sessions["session1"]
        state.last_pong_time = 0  # Very old
        
        await ping_monitor._check_session("session1", state)
        
        assert state.is_stale is True
        assert "Transport disconnected" in state.stale_reason


class TestWebSocketPingTransport:
    """Tests for WebSocketPingTransport."""
    
    @pytest.mark.asyncio
    async def test_send_ping_success(self):
        """Test successful ping send."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        
        transport = WebSocketPingTransport({"session1": mock_ws})
        
        result = await transport.send_ping("session1")
        
        assert result is True
        mock_ws.ping.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_send_ping_closed(self):
        """Test ping send on closed connection."""
        mock_ws = AsyncMock()
        mock_ws.closed = True
        
        transport = WebSocketPingTransport({"session1": mock_ws})
        
        result = await transport.send_ping("session1")
        
        assert result is False
        mock_ws.ping.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_wait_for_pong(self):
        """Test pong wait returns connection status."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        
        transport = WebSocketPingTransport({"session1": mock_ws})
        
        result = await transport.wait_for_pong("session1", 10.0)
        
        assert result is True
    
    def test_is_connected(self):
        """Test connection status check."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        
        transport = WebSocketPingTransport({"session1": mock_ws})
        
        assert transport.is_connected("session1") is True
        
        mock_ws.closed = True
        assert transport.is_connected("session1") is False
        
        # Unknown session
        assert transport.is_connected("session2") is False


class TestInProcessPingTransport:
    """Tests for InProcessPingTransport."""
    
    @pytest.mark.asyncio
    async def test_send_ping_with_agent(self):
        """Test ping send to in-process agent."""
        mock_agent = AsyncMock()
        mock_agent.ping = AsyncMock(return_value=None)
        
        transport = InProcessPingTransport({"session1": mock_agent})
        
        result = await transport.send_ping("session1")
        
        assert result is True
        mock_agent.ping.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_send_ping_no_ping_method(self):
        """Test ping send when agent has no ping method."""
        mock_agent = Mock()
        # No ping method
        
        transport = InProcessPingTransport({"session1": mock_agent})
        
        result = await transport.send_ping("session1")
        
        assert result is True  # Returns True if agent exists
    
    @pytest.mark.asyncio
    async def test_send_ping_no_agent(self):
        """Test ping send when no agent for session."""
        transport = InProcessPingTransport({})
        
        result = await transport.send_ping("session1")
        
        assert result is False
    
    def test_is_connected(self):
        """Test connection status check."""
        mock_agent = Mock()
        
        transport = InProcessPingTransport({"session1": mock_agent})
        
        assert transport.is_connected("session1") is True
        assert transport.is_connected("session2") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])