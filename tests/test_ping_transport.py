"""
Deterministic tests for the WebSocket ping transport's pong semantics.

These verify the fixes:
  * send_ping initiates a ping WITHOUT blocking on a half-open peer;
  * wait_for_pong performs a *bounded* pong wait (timeout => False);
  * a closed socket never reports success;
  * a late/never pong is discarded rather than marking the stream healthy.

No real network is used.
"""

import asyncio

import pytest

from guardian.core.ping_monitor import WebSocketPingTransport


async def _immediate_pong():
    return None


async def _never_pong():
    # Never completes -> simulates a half-open socket.
    await asyncio.Event().wait()


class FakeWS:
    """Minimal websocket stand-in: ping() returns an awaitable."""

    def __init__(self, behavior="resolve"):
        self.closed = False
        self.behavior = behavior
        self.ping_calls = 0

    def ping(self):
        self.ping_calls += 1
        return _never_pong() if self.behavior == "never" else _immediate_pong()


@pytest.mark.asyncio
async def test_pong_resolves_true():
    ws = FakeWS("resolve")
    transport = WebSocketPingTransport({"s": ws})

    assert await transport.send_ping("s") is True
    assert await transport.wait_for_pong("s", 0.5) is True
    assert ws.ping_calls == 1, "exactly one ping should be issued per send"


@pytest.mark.asyncio
async def test_half_open_socket_times_out_false():
    ws = FakeWS("never")
    transport = WebSocketPingTransport({"s": ws})

    # send_ping must return promptly (it does not await the pong).
    assert await transport.send_ping("s") is True
    # The pong never arrives -> must be reported as False, not a false positive.
    assert await transport.wait_for_pong("s", 0.05) is False


@pytest.mark.asyncio
async def test_wait_for_pong_without_prior_ping_still_pings():
    ws = FakeWS("resolve")
    transport = WebSocketPingTransport({"s": ws})

    assert await transport.wait_for_pong("s", 0.5) is True
    assert ws.ping_calls == 1


@pytest.mark.asyncio
async def test_closed_socket_never_reports_success():
    ws = FakeWS("resolve")
    ws.closed = True
    transport = WebSocketPingTransport({"s": ws})

    assert await transport.send_ping("s") is False
    assert await transport.wait_for_pong("s", 0.5) is False
    assert ws.ping_calls == 0


@pytest.mark.asyncio
async def test_late_pong_is_discarded():
    ws = FakeWS("never")
    transport = WebSocketPingTransport({"s": ws})

    assert await transport.send_ping("s") is True
    assert await transport.wait_for_pong("s", 0.05) is False  # times out, pending dropped

    # A subsequent wait must issue a FRESH ping, not adopt the stale pong.
    ws.behavior = "resolve"
    assert await transport.wait_for_pong("s", 0.5) is True
    assert ws.ping_calls == 2


@pytest.mark.asyncio
async def test_unknown_session_fails_closed():
    transport = WebSocketPingTransport({})
    assert await transport.send_ping("missing") is False
    assert await transport.wait_for_pong("missing", 0.1) is False
