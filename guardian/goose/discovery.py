"""
Goose Desktop backend discovery.

Discovers running Goose Desktop ACP backend on localhost by scanning
for the /status health endpoint and extracting connection information.
"""

import asyncio
import re
import socket
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse, parse_qs

import aiohttp

from ..logging import get_logger
from .protocol import GooseConnectionInfo

logger = get_logger("discovery")


# Default port range for Goose Desktop (based on research)
DEFAULT_PORT_RANGE = (10000, 65535)
# Health check timeout
HEALTH_CHECK_TIMEOUT = aiohttp.ClientTimeout(total=2.0)
# Status endpoint path
STATUS_PATH = "/status"
# ACP WebSocket path
ACP_WS_PATH = "/acp"


@dataclass
class DiscoveryResult:
    """Result of Goose backend discovery."""
    connections: List[GooseConnectionInfo]
    scanned_ports: int
    errors: List[str]


async def check_port(host: str, port: int) -> Optional[GooseConnectionInfo]:
    """
    Check if a port hosts a Goose backend by querying /status.
    
    Returns GooseConnectionInfo if found, None otherwise.
    """
    url = f"http://{host}:{port}{STATUS_PATH}"
    
    try:
        async with aiohttp.ClientSession(timeout=HEALTH_CHECK_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if text.strip().lower() == "ok":
                        # Found a Goose backend, now discover WebSocket URL and token
                        return await discover_ws_info(host, port, session)
    except (aiohttp.ClientError, asyncio.TimeoutError, socket.gaierror):
        pass
    
    return None


async def discover_ws_info(host: str, port: int, session: aiohttp.ClientSession) -> Optional[GooseConnectionInfo]:
    """
    Discover WebSocket URL and token by attempting WebSocket connection.
    
    The token is typically in the query string of the WebSocket URL.
    We attempt to connect to ws://host:port/acp and capture the redirect/upgrade.
    """
    # Try to get the token by attempting WebSocket upgrade
    # The token is usually passed as query param: ws://host:port/acp?token=SECRET
    # We need to discover it. Since we can't easily brute force,
    # we try to connect without token first to see if it gives us a hint,
    # or we check if there's a known pattern.
    
    # For now, we'll try common token discovery approaches:
    # 1. Check if there's a known token in the status response headers
    # 2. Try to connect to the WebSocket and see if it redirects
    
    # Since the token is generated per-lease and not easily discoverable
    # without Electron context, we'll return the connection info
    # and let the observer handle token discovery during connection attempt.
    
    # The research shows the token is in the URL query param.
    # We can't easily extract it without the Electron lease registry.
    # But we can return the base info and let the connection attempt
    # try to discover it.
    
    # For now, we'll attempt a WebSocket connection to see the auth failure
    # which might give us hints, or we can use the fact that the secret
    # is often a fixed pattern or we can extract it from the lease registry
    # if we had Electron access.
    
    # Since we don't have Electron access, we'll return what we know
    # and note that token discovery requires additional mechanism.
    
    websocket_url = f"ws://{host}:{port}{ACP_WS_PATH}"
    
    return GooseConnectionInfo(
        host=host,
        port=port,
        status_url=f"http://{host}:{port}{STATUS_PATH}",
        websocket_url=websocket_url,
        token="UNKNOWN"  # Will be discovered during connection
    )


async def discover_goose_backends(
    host: str = "127.0.0.1",
    port_range: tuple = DEFAULT_PORT_RANGE,
    max_concurrent: int = 50
) -> DiscoveryResult:
    """
    Discover all running Goose backends on the given host.
    
    Scans the port range for /status endpoint returning "ok".
    """
    connections = []
    errors = []
    scanned = 0
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def check_with_semaphore(port: int):
        nonlocal scanned
        async with semaphore:
            scanned += 1
            result = await check_port(host, port)
            if result:
                connections.append(result)
                logger.info(f"Found Goose backend at {host}:{port}")
    
    # Create tasks for all ports
    tasks = [check_with_semaphore(port) for port in range(port_range[0], port_range[1] + 1)]
    
    # Run with progress logging
    for i, task in enumerate(asyncio.as_completed(tasks)):
        try:
            await task
        except Exception as e:
            errors.append(f"Port {port_range[0] + i}: {e}")
        
        # Log progress occasionally
        if (i + 1) % 1000 == 0:
            logger.debug(f"Scanned {i + 1} ports...")
    
    logger.info(f"Discovery complete: scanned {scanned} ports, found {len(connections)} Goose backend(s)")
    
    return DiscoveryResult(
        connections=connections,
        scanned_ports=scanned,
        errors=errors
    )


async def discover_primary_goose(
    host: str = "127.0.0.1",
    port_range: tuple = DEFAULT_PORT_RANGE
) -> Optional[GooseConnectionInfo]:
    """
    Discover the primary (first found) Goose backend.
    
    Stops at first successful discovery for faster startup.
    """
    semaphore = asyncio.Semaphore(50)
    
    async def check_with_semaphore(port: int) -> Optional[GooseConnectionInfo]:
        async with semaphore:
            return await check_port(host, port)
    
    # Check ports in parallel batches
    batch_size = 50
    for batch_start in range(port_range[0], port_range[1] + 1, batch_size):
        batch_end = min(batch_start + batch_size, port_range[1] + 1)
        tasks = [check_with_semaphore(port) for port in range(batch_start, batch_end)]
        
        for task in asyncio.as_completed(tasks):
            result = await task
            if result:
                return result
    
    return None


def extract_token_from_ws_url(ws_url: str) -> Optional[str]:
    """Extract token from WebSocket URL query string."""
    try:
        parsed = urlparse(ws_url)
        query = parse_qs(parsed.query)
        token = query.get("token", [None])[0]
        return token
    except Exception:
        return None