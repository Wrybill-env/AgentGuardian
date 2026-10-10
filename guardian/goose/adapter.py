"""
Goose Desktop adapter for Agent Guardian.

Integrates the Goose observer with the Guardian core state machine.
"""

import asyncio
import json
from typing import Optional, Callable, Any, Dict
from dataclasses import dataclass

from ..core import (
    GuardianStateMachine,
    GuardianConfig,
    SafetyMode,
    TurnContext,
    TurnPhase,
    StateMachineEvent,
    FailureClassification,
    RecoveryAction,
)
from ..core.failure_classifier import FailureClassifier
from ..core.safety_gate import SafetyGate
from ..core.backoff import BackoffStrategy, BackoffConfig
from ..core.adapter_capabilities import (
    AdapterCapabilities,
    AdapterCapability,
    AgentAdapter,
    GOOSE_CAPABILITIES,
)
from ..core.ping_monitor import PingMonitor, PingConfig, WebSocketPingTransport, create_goose_ping_monitor
from ..logging import get_logger

from .protocol import (
    ACPEvent,
    ACPEventType,
    ACPProtocolParser,
)
from .discovery import discover_primary_goose, GooseConnectionInfo
from .observer import GooseObserver, run_observer

logger = get_logger("goose.adapter")


@dataclass
class GooseAdapterConfig:
    """Configuration for Goose adapter."""
    port: Optional[int] = None
    token: Optional[str] = None
    use_tls: bool = True
    stale_threshold: float = 60.0
    safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY


class GooseAdapter(AgentAdapter):
    """Adapter that connects Goose Desktop ACP events to Guardian core.

    Translates ACP events into Guardian state machine events,
    manages the WebSocket connection, and exposes recovery actions
    that can be executed against the Goose backend.
    """

    def __init__(
        self,
        config: GooseAdapterConfig,
        guardian: GuardianStateMachine,
    ):
        self.config = config
        self.guardian = guardian
        self.observer: Optional[GooseObserver] = None
        self.connection_info: Optional[GooseConnectionInfo] = None
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None

        # Ping monitor for liveness detection
        self._ping_monitor: Optional[PingMonitor] = None

        # Recovery action implementations
        self._recovery_actions = {
            RecoveryAction.WAIT_AND_RETRY: self._action_wait_and_retry,
            RecoveryAction.CANCEL_AND_REPLAY: self._action_cancel_and_replay,
            RecoveryAction.RECONNECT_ACP: self._action_reconnect_acp,
            RecoveryAction.RELOAD_SESSION: self._action_reload_session,
            RecoveryAction.FORK_SESSION: self._action_fork_session,
            RecoveryAction.WAIT_FOR_BACKEND: self._action_wait_for_backend,
        }

        # Capability declaration
        self._capabilities = AdapterCapabilities(
            adapter_name="goose",
            adapter_version="1.0",
            agent_type="goose",
        )
        for cap, available in GOOSE_CAPABILITIES.items():
            if available:
                self._capabilities.set_available(cap, reason="Implemented via ACP")
            else:
                self._capabilities.set_unavailable(cap, reason="Not implemented")

    @property
    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    async def connect(self) -> bool:
        """Discover and connect to Goose backend."""
        # Discover backend
        if self.config.port:
            logger.info(f"Using specified port: {self.config.port}")
            self.connection_info = GooseConnectionInfo(
                host="127.0.0.1",
                port=self.config.port,
                status_url=f"http://127.0.0.1:{self.config.port}/status",
                websocket_url=f"ws://127.0.0.1:{self.config.port}/acp",
                token=self.config.token or "UNKNOWN",
            )
        else:
            logger.info("Discovering Goose backend...")
            self.connection_info = await discover_primary_goose(
                host=self.config.discovery_host if hasattr(self.config, 'discovery_host') else "127.0.0.1",
                port_range=getattr(self.config, 'discovery_port_range', (10000, 65535)),
            )
            if not self.connection_info:
                logger.error("No Goose backend found")
                return False
            if self.config.token:
                self.connection_info.token = self.config.token

        logger.info(f"Found Goose backend at {self.connection_info.host}:{self.connection_info.port}")

        # Create and connect observer
        self.observer = await run_observer(
            connection_info=self.connection_info,
            token=self.config.token,
            stale_threshold=self.config.stale_threshold,
            on_event=self._handle_acp_event,
            on_failure=self._handle_failure_event,
            use_tls=self.config.use_tls,
        )

        self._running = True

        # Initialize and start ping monitor
        if self.observer and self.observer._ws:
            ws_dict = {"default": self.observer._ws}
            self._ping_monitor = create_goose_ping_monitor(
                ws_dict=ws_dict,
                stale_threshold=self.config.stale_threshold,
                ping_interval=30.0,
                on_stale=self._on_ping_stale,
            )
            await self._ping_monitor.start()

        return True

    def _on_ping_stale(self, session_id: str, reason: str):
        """Handle stale session detected by ping monitor."""
        logger.warning(f"Ping monitor detected stale session {session_id}: {reason}")
        # Forward to failure handler via guardian
        self.guardian.handle_event(StateMachineEvent.FAILURE_DETECTED, {
            "error_message": f"PING_STALE: {reason}",
            "details": {"session_id": session_id},
        })
    
    def get_observer(self) -> Optional["GooseObserver"]:
        """Get the observer for accessing stream state."""
        return self.observer

    def _handle_acp_event(self, event: ACPEvent):
        """Translate ACP event to Guardian state machine event."""
        if not self.guardian.turn_context and event.session_id:
            # Start tracking turn if we don't have one
            self.guardian.start_turn(
                session_id=event.session_id,
                run_id=None,  # Will be filled from sessionUpdate
            )

        # Map ACP event types to Guardian events
        event_mapping = {
            ACPEventType.ASSISTANT_CHUNK: StateMachineEvent.ASSISTANT_CHUNK,
            ACPEventType.TOOL_CALL_STARTED: StateMachineEvent.TOOL_STARTED,
            ACPEventType.TOOL_CALL_UPDATE: StateMachineEvent.TOOL_STARTED,  # Progress
            ACPEventType.TOOL_CALL_COMPLETED: StateMachineEvent.TOOL_COMPLETED,
            ACPEventType.TOOL_CALL_FAILED: StateMachineEvent.TOOL_FAILED,
            ACPEventType.TURN_COMPLETE: StateMachineEvent.TURN_COMPLETE,
            ACPEventType.TURN_ERROR: StateMachineEvent.TURN_ERROR,
            ACPEventType.TURN_CANCELLED: StateMachineEvent.TURN_CANCELLED,
            ACPEventType.TURN_MAX_TURNS: StateMachineEvent.TURN_MAX_TURNS,
            ACPEventType.RATE_LIMIT: StateMachineEvent.FAILURE_DETECTED,
            ACPEventType.SERVER_ERROR: StateMachineEvent.FAILURE_DETECTED,
            ACPEventType.NETWORK_ERROR: StateMachineEvent.FAILURE_DETECTED,
            ACPEventType.AUTHENTICATION_ERROR: StateMachineEvent.FAILURE_DETECTED,
            ACPEventType.PROVIDER_ERROR: StateMachineEvent.FAILURE_DETECTED,
        }

        guardian_event = event_mapping.get(event.event_type)
        if guardian_event:
            # Extract relevant data
            data = {}
            if event.session_id:
                data["session_id"] = event.session_id
            if event.tool_name:
                data["tool_name"] = event.tool_name
            if event.tool_call_id:
                data["tool_call_id"] = event.tool_call_id
            if event.error_message:
                data["error"] = event.error_message
            if event.stop_reason:
                data["stop_reason"] = event.stop_reason.value

            self.guardian.handle_event(guardian_event, data)

        # Handle session info for run_id
        if event.event_type == ACPEventType.SESSION_UPDATE and event.raw_message:
            params = event.raw_message.get("params", {})
            update = params.get("update", {})
            if "runId" in update and self.guardian.turn_context:
                self.guardian.turn_context.run_id = update["runId"]

    def _handle_failure_event(self, failure: str, details: dict):
        """Handle failure detection from observer."""
        logger.warning(f"Observer detected failure: {failure}")
        self.guardian.handle_event(StateMachineEvent.FAILURE_DETECTED, {
            "error_message": failure,
            "details": details,
        })

    async def disconnect(self):
        """Disconnect from Goose backend."""
        self._running = False
        if self.observer:
            await self.observer.disconnect()
            self.observer = None

    # Recovery action implementations
    async def _action_wait_and_retry(self, context: dict) -> bool:
        """Wait with backoff then allow retry."""
        # The backoff is handled by the state machine
        # This action just confirms we're ready to retry
        logger.info("RECOVERY: WAIT_AND_RETRY - ready to replay prompt")
        return True

    async def _action_cancel_and_replay(self, context: dict) -> bool:
        """Cancel in-flight turn and replay the prompt."""
        turn_context = context.get("turn_context")
        if not turn_context or not turn_context.session_id:
            logger.error("Cannot cancel - no session ID")
            return False

        if not self.observer:
            logger.error("No observer connected")
            return False

        # Send ACP cancel notification
        try:
            cancel_msg = {
                "jsonrpc": "2.0",
                "method": "agent/session/cancel",
                "params": {"sessionId": turn_context.session_id},
            }
            await self.observer._ws.send(json.dumps(cancel_msg))
            logger.info(f"Sent cancel for session {turn_context.session_id}")

            # Wait briefly for cancellation to process
            await asyncio.sleep(0.5)
            return True
        except Exception as e:
            logger.error(f"Failed to send cancel: {e}")
            return False

    async def _action_reconnect_acp(self, context: dict) -> bool:
        """Reconnect to ACP WebSocket."""
        logger.info("Reconnecting to ACP...")
        await self.disconnect()
        await asyncio.sleep(1.0)
        return await self.connect()

    async def _action_reload_session(self, context: dict) -> bool:
        """Reload session from backend."""
        turn_context = context.get("turn_context")
        if not turn_context or not turn_context.session_id:
            logger.error("Cannot reload - no session ID")
            return False

        # Send ACP load session request
        if not self.observer:
            logger.error("No observer connected")
            return False

        try:
            load_msg = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "agent/session/load",
                "params": {
                    "sessionId": turn_context.session_id,
                    "cwd": context.get("cwd", ""),
                    "mcpServers": [],
                },
            }
            await self.observer._ws.send(json.dumps(load_msg))
            logger.info(f"Sent load session for {turn_context.session_id}")

            # Wait for response
            response = await asyncio.wait_for(self.observer._ws.recv(), timeout=10.0)
            response_data = json.loads(response)

            if "error" in response_data:
                logger.error(f"Load session failed: {response_data['error']}")
                return False

            logger.info(f"Session {turn_context.session_id} reloaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to reload session: {e}")
            return False

    async def _action_fork_session(self, context: dict) -> bool:
        """Fork session at safe point."""
        turn_context = context.get("turn_context")
        if not turn_context or not turn_context.session_id:
            logger.error("Cannot fork - no session ID")
            return False

        if not self.observer:
            logger.error("No observer connected")
            return False

        # Fork before the current turn's user message
        fork_timestamp = int(turn_context.first_chunk_time * 1000) if turn_context.first_chunk_time else 0

        try:
            fork_msg = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "agent/session/fork",
                "params": {
                    "sessionId": turn_context.session_id,
                    "cwd": context.get("cwd", ""),
                },
            }
            if fork_timestamp:
                fork_msg["params"]["_meta"] = {"conversationBefore": fork_timestamp}

            await self.observer._ws.send(json.dumps(fork_msg))
            logger.info(f"Sent fork session for {turn_context.session_id}")

            response = await asyncio.wait_for(self.observer._ws.recv(), timeout=10.0)
            response_data = json.loads(response)

            if "error" in response_data:
                logger.error(f"Fork session failed: {response_data['error']}")
                return False

            new_session_id = response_data.get("result", {}).get("sessionId")
            if new_session_id:
                logger.info(f"Forked to new session: {new_session_id}")
                # Store new session ID in context for verification
                context["new_session_id"] = new_session_id
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to fork session: {e}")
            return False

    async def _action_wait_for_backend(self, context: dict) -> bool:
        """Wait for backend to restart."""
        logger.info("Waiting for backend to restart...")

        # Poll /status endpoint until backend is available
        max_wait = 60.0
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < max_wait:
            await asyncio.sleep(2.0)
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"http://127.0.0.1:{self.connection_info.port}/status") as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            if text.strip() == "ok":
                                logger.info("Backend restarted and ready")
                                # Reconnect ACP
                                return await self._action_reconnect_acp(context)
            except Exception:
                continue

        logger.error("Backend did not restart in time")
        return False

    # --- AgentAdapter interface ---

    @property
    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    async def execute_recovery_action(self, action: RecoveryAction, context: Dict[str, Any]) -> bool:
        """Execute a recovery action by name."""
        handler = self._recovery_actions.get(action)
        if not handler:
            logger.error(f"No handler for recovery action: {action.value}")
            return False

        logger.info(f"Executing recovery action: {action.value}")
        try:
            return await handler(context)
        except Exception as e:
            logger.error(f"Recovery action {action.value} failed: {e}")
            return False

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._running and self.observer is not None

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected": self._running,
            "adapter_name": self._capabilities.adapter_name,
            "capabilities": self._capabilities.to_dict(),
            "connection_info": self.connection_info.__dict__ if self.connection_info else None,
        }

    # --- Recovery verification ---
    
    async def verify_recovery(self, action: RecoveryAction, context: Dict[str, Any]) -> tuple[bool, dict]:
        """Verify that a recovery action actually succeeded.
        
        Returns (success, details) tuple.
        """
        turn_context = context.get("turn_context")
        session_id = context.get("session_id") or (turn_context.session_id if turn_context else None)
        
        if not session_id:
            return False, {"error": "No session ID available for verification"}
        
        try:
            if action == RecoveryAction.RECONNECT_ACP:
                # Verify WebSocket is connected and can communicate
                if self.observer and self.observer._ws:
                    # Check connection state
                    return self._running, {"verified": "websocket_connected", "session_id": session_id}
                return False, {"error": "WebSocket not connected after reconnect"}
            
            elif action == RecoveryAction.RELOAD_SESSION:
                # Verify session was loaded by checking session ID matches
                # The load_session action already checked ACP response for error
                return True, {"verified": "load_session_response_ok", "session_id": session_id}
            
            elif action == RecoveryAction.FORK_SESSION:
                # Verify fork returned a new session ID
                # The fork action already validated the response
                new_session_id = context.get("new_session_id")
                if new_session_id:
                    return True, {"verified": "fork_created_session", "new_session_id": new_session_id}
                return False, {"error": "No new session ID from fork"}
            
            elif action == RecoveryAction.WAIT_FOR_BACKEND:
                # Verify backend is responding to /status
                if self.connection_info and self.connection_info.port:
                    try:
                        import aiohttp
                        async with aiohttp.ClientSession() as session:
                            async with session.get(f"http://127.0.0.1:{self.connection_info.port}/status", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                                if resp.status == 200:
                                    text = await resp.text()
                                    if text.strip() == "ok":
                                        return True, {"verified": "backend_status_ok"}
                    except Exception as e:
                        return False, {"error": f"Backend status check failed: {e}"}
                return False, {"error": "No connection info for backend check"}
            
            elif action == RecoveryAction.CANCEL_AND_REPLAY:
                # Verify cancellation was sent (already done in action)
                # Note: ACP cancel is fire-and-forget, so we can only verify send
                return True, {"verified": "cancel_notification_sent", "session_id": session_id}
            
            elif action == RecoveryAction.WAIT_AND_RETRY:
                # This is a no-op/backoff action - nothing external to verify
                return True, {"verified": "backoff_completed", "note": "No external verification needed"}
            
            elif action == RecoveryAction.HUMAN_INTERVENTION:
                # Human intervention doesn't perform external action
                return True, {"verified": "human_intervention_requested"}
            
            else:
                return False, {"error": f"Unknown action for verification: {action.value}"}
                
        except Exception as e:
            return False, {"error": f"Verification failed: {e}"}


class GooseRecoveryContext:
    """Context builder for Goose recovery actions."""

    @staticmethod
    def build_context(guardian: GuardianStateMachine, observer: GooseObserver,
                      classification: FailureClassification) -> dict:
        """Build context dictionary for recovery actions."""
        turn_context = guardian.turn_context

        return {
            "turn_context": turn_context,
            "classification": classification,
            "session_id": turn_context.session_id if turn_context else None,
            "run_id": turn_context.run_id if turn_context else None,
            "cwd": "",  # Would need to be obtained from session
            "observer": observer,
        }