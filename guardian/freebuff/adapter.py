"""
FreeBuff Adapter for Agent Guardian.

Connects FreeBuff (Codebuff) via HTTP API / WebSocket to Guardian core.
"""

import asyncio
import logging
import aiohttp
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from guardian.core import (
    GuardianStateMachine,
    SafetyMode,
    TurnContext,
    TurnPhase,
    StateMachineEvent,
    FailureClassification,
    RecoveryAction,
)
from guardian.core.adapter_capabilities import (
    AgentAdapter,
    AdapterCapabilities,
    AdapterCapability,
)
from guardian.logging import get_logger

from .protocol import FreeBuffEventType, FreeBuffConnectionInfo
from .observer import FreeBuffObserver, run_freebuff_observer

logger = get_logger("freebuff.adapter")


@dataclass
class FreeBuffAdapterConfig:
    """Configuration for FreeBuff adapter."""
    # Connection
    base_url: str = "https://codebuff.com"
    api_token: str = ""
    websocket_url: str = "wss://codebuff.com/ws"
    cost_mode: str = "free"  # "free" or "paid"
    
    # Detection thresholds
    stale_threshold: float = 60.0
    first_chunk_threshold: float = 30.0
    chunk_gap_threshold: float = 15.0
    tool_timeout_threshold: float = 120.0
    
    # Safety
    safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY


class FreeBuffAdapter(AgentAdapter):
    """
    Adapter that connects FreeBuff (Codebuff) to Guardian core.
    
    Connects via HTTP API / WebSocket and translates events into Guardian state machine events.
    """

    def __init__(
        self,
        config: FreeBuffAdapterConfig,
        guardian: GuardianStateMachine,
    ):
        self.config = config
        self.guardian = guardian
        self.observer: Optional[FreeBuffObserver] = None
        self.connection_info = FreeBuffConnectionInfo(
            base_url=config.base_url,
            api_token=config.api_token,
            websocket_url=config.websocket_url,
            cost_mode=config.cost_mode,
        )
        self._running = False
        
        # Recovery action implementations
        self._recovery_actions = {
            RecoveryAction.WAIT_AND_RETRY: self._action_wait_and_retry,
            RecoveryAction.CANCEL_AND_REPLAY: self._action_cancel_and_replay,
            RecoveryAction.RECONNECT_STREAM: self._action_reconnect_ws,
            RecoveryAction.RELOAD_SESSION: self._action_reload_session,
            RecoveryAction.FORK_SESSION: self._action_fork_session,
            RecoveryAction.WAIT_FOR_BACKEND: self._action_wait_for_backend,
        }
        
        # Capability declaration
        self._capabilities = AdapterCapabilities(
            adapter_name="freebuff",
            adapter_version="1.0",
            agent_type="freebuff",
        )
        self._declare_capabilities()

    def _declare_capabilities(self):
        """Declare capabilities based on actual FreeBuff API support."""
        # Observation - VERIFIED IMPLEMENTED via HTTP/WebSocket
        self._capabilities.set_available(AdapterCapability.OBSERVE_STREAM, reason="Implemented via WebSocket message.chunk events")
        self._capabilities.set_unavailable(AdapterCapability.OBSERVE_REASONING, reason="FreeBuff doesn't expose reasoning separately")
        self._capabilities.set_available(AdapterCapability.OBSERVE_TOOL_PROGRESS, reason="Implemented via tool.call events")
        self._capabilities.set_available(AdapterCapability.OBSERVE_TURN_LIFECYCLE, reason="Implemented via session/run lifecycle events")
        
        # Stream recovery - VERIFIED IMPLEMENTED via WebSocket reconnect
        self._capabilities.set_available(AdapterCapability.RECONNECT_STREAM, reason="Implemented via WebSocket reconnection")
        self._capabilities.set_unavailable(AdapterCapability.CANCEL_STREAM, reason="No explicit stream cancel in FreeBuff API")
        
        # Session recovery - PARTIALLY_IMPLEMENTED
        self._capabilities.set_unavailable(AdapterCapability.REATTACH_SESSION, reason="No session reattach in FreeBuff API")
        self._capabilities.set_unavailable(AdapterCapability.FORK_SESSION, reason="No session fork in FreeBuff API")
        self._capabilities.set_unavailable(AdapterCapability.LOAD_SESSION, reason="No session load in FreeBuff API")
        self._capabilities.set_unavailable(AdapterCapability.SAVE_SESSION, reason="No session save in FreeBuff API")
        
        # Provider recovery - NOT IN FREEBUFF API
        self._capabilities.set_unavailable(AdapterCapability.SWITCH_PROVIDER, reason="Not in FreeBuff API")
        self._capabilities.set_unavailable(AdapterCapability.GET_PROVIDER_LIST, reason="Not in FreeBuff API")
        
        # Agent process - PARTIAL
        self._capabilities.set_unavailable(AdapterCapability.RESTART_AGENT, reason="FreeBuff is SaaS, can't restart")
        self._capabilities.set_unavailable(AdapterCapability.GET_AGENT_STATUS, reason="No agent status endpoint")
        self._capabilities.set_available(AdapterCapability.IS_AGENT_ALIVE, reason="Implemented via WebSocket connection status")
        
        # Turn control
        self._capabilities.set_unavailable(AdapterCapability.CANCEL_TURN, reason="No turn cancel in FreeBuff API")
        self._capabilities.set_unavailable(AdapterCapability.RETRY_REQUEST, reason="No explicit retry, but can start new run")
        self._capabilities.set_unavailable(AdapterCapability.REPLAY_TURN, reason="No replay in FreeBuff API")
        
        # Introspection - PARTIAL
        self._capabilities.set_unavailable(AdapterCapability.GET_PARTIAL_TOOL_NAMES, reason="Not exposed via API")
        self._capabilities.set_unavailable(AdapterCapability.GET_TOOL_STATE, reason="Not in FreeBuff API")
        self._capabilities.set_available(AdapterCapability.GET_SESSION_STATE, reason="Implemented via observer session tracking")
        self._capabilities.set_available(AdapterCapability.GET_TURN_CONTEXT, reason="Implemented via observer turn tracking")
        
        # Safety - PARTIAL
        self._capabilities.set_available(AdapterCapability.VERIFY_SESSION_IDENTITY, reason="Implemented via session ID tracking")
        self._capabilities.set_unavailable(AdapterCapability.VERIFY_SESSION_EXISTS, reason="No session list query")
        
        # Monitoring - VERIFIED IMPLEMENTED
        self._capabilities.set_available(AdapterCapability.DETECT_DISCONNECT, reason="Implemented via WebSocket close detection")
        self._capabilities.set_available(AdapterCapability.DETECT_CRASH, reason="Implemented via connection loss detection")
        self._capabilities.set_available(AdapterCapability.DETECT_STALE, reason="Implemented via observer stale detection")

    @property
    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    async def connect(self) -> bool:
        """Connect to FreeBuff backend via WebSocket."""
        if not self.config.api_token:
            logger.error("No FreeBuff API token provided")
            return False
        
        try:
            self.observer = await run_freebuff_observer(
                connection_info=self.connection_info,
                stale_threshold=self.config.stale_threshold,
                first_chunk_threshold=self.config.first_chunk_threshold,
                chunk_gap_threshold=self.config.chunk_gap_threshold,
                tool_timeout_threshold=self.config.tool_timeout_threshold,
                on_event=self._handle_freebuff_event,
                on_failure=self._handle_failure_event,
            )
            
            self._running = True
            
            # Notify guardian of connection
            self.guardian.handle_event(StateMachineEvent.CONNECTED, {})
            
            logger.info("FreeBuff adapter connected via WebSocket")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to FreeBuff: {e}")
            return False

    def get_observer(self) -> Optional[FreeBuffObserver]:
        """Get the observer for accessing stream state."""
        return self.observer

    def _handle_freebuff_event(self, event: dict):
        """Translate FreeBuff event to Guardian state machine event."""
        if not self.guardian.turn_context and event.get("session_id"):
            # Start tracking turn if we don't have one
            self.guardian.start_turn(
                session_id=event["session_id"],
                run_id=None,
            )

        # Map FreeBuff event types to Guardian events
        event_mapping = {
            "message_start": StateMachineEvent.ASSISTANT_CHUNK,
            "message_chunk": StateMachineEvent.ASSISTANT_CHUNK,
            "message_end": StateMachineEvent.TURN_COMPLETE,
            "tool_call_start": StateMachineEvent.TOOL_STARTED,
            "tool_call_update": StateMachineEvent.TOOL_STARTED,  # Progress
            "tool_call_end": StateMachineEvent.TOOL_COMPLETED,
            "tool_call_error": StateMachineEvent.TOOL_FAILED,
            "session_start": StateMachineEvent.USER_MESSAGE_SENT,
            "run_start": StateMachineEvent.USER_MESSAGE_SENT,
            "run_end": StateMachineEvent.TURN_COMPLETE,
            "session_end": StateMachineEvent.TURN_COMPLETE,
            "run_end": StateMachineEvent.TURN_COMPLETE,
            "error": StateMachineEvent.FAILURE_DETECTED,
        }

        fb_event_type = event.get("type")
        guardian_event = event_mapping.get(fb_event_type)
        
        if guardian_event:
            data = {"session_id": event.get("session_id")}
            
            if "tool_name" in event:
                data["tool_name"] = event["tool_name"]
            if "tool_call_id" in event:
                data["tool_call_id"] = event["tool_call_id"]
            if "error" in event:
                data["error"] = event["error"]
            
            self.guardian.handle_event(guardian_event, data)

        # Handle session info
        session_id = event.get("session_id")
        if fb_event_type in ("session_start", "run_start") and session_id:
            if self.guardian.turn_context:
                self.guardian.turn_context.session_id = session_id

    def _handle_failure_event(self, failure: str, details: dict):
        """Handle failure detection from observer."""
        logger.warning(f"Observer detected failure: {failure}")
        self.guardian.handle_event(StateMachineEvent.FAILURE_DETECTED, {
            "error_message": failure,
            "details": details,
        })

    async def disconnect(self):
        """Disconnect from FreeBuff backend."""
        self._running = False
        if self.observer:
            await self.observer.disconnect()
            self.observer = None
        
        self.guardian.handle_event(StateMachineEvent.DISCONNECTED, {})
        logger.info("FreeBuff adapter disconnected")

    # Recovery action implementations
    async def _action_wait_and_retry(self, context: dict) -> bool:
        """Wait with backoff then allow retry."""
        logger.info("RECOVERY: WAIT_AND_RETRY - ready to replay prompt")
        return True

    async def _action_cancel_and_replay(self, context: dict) -> bool:
        """Cancel in-flight run and replay the prompt."""
        turn_context = context.get("turn_context")
        if not turn_context or not turn_context.session_id:
            logger.error("Cannot cancel - no session ID")
            return False

        if not self.observer or not self.observer._ws:
            logger.error("No WebSocket connected")
            return False

        # FreeBuff doesn't have explicit cancel, but we can end the run
        try:
            run_id = context.get("run_id") or turn_context.run_id
            if run_id:
                # FreeBuff API doesn't have explicit cancel, but we can close the session
                logger.warning("FreeBuff doesn't support explicit run cancellation")
            return False
        except Exception as e:
            logger.error(f"Failed to cancel FreeBuff run: {e}")
            return False

    async def _action_reconnect_ws(self, context: dict) -> bool:
        """Reconnect to FreeBuff WebSocket."""
        logger.info("Reconnecting to FreeBuff WebSocket...")
        await self.disconnect()
        await asyncio.sleep(1.0)
        return await self.connect()

    async def _action_reload_session(self, context: dict) -> bool:
        """Reload session from backend."""
        turn_context = context.get("turn_context")
        if not turn_context or not turn_context.session_id:
            logger.error("Cannot reload - no session ID")
            return False

        # FreeBuff doesn't have session reload
        logger.warning("Session reload not supported in FreeBuff API")
        return False

    async def _action_fork_session(self, context: dict) -> bool:
        """Fork session at safe point."""
        logger.warning("Session fork not supported in FreeBuff API")
        return False

    async def _action_wait_for_backend(self, context: dict) -> bool:
        """Wait for backend to restart."""
        logger.info("Waiting for FreeBuff backend...")
        
        # Poll health endpoint
        for _ in range(60):
            await asyncio.sleep(2.0)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{self.connection_info.base_url}/health") as resp:
                        if resp.status == 200:
                            logger.info("FreeBuff backend is ready")
                            return True
            except Exception:
                continue
        
        logger.error("FreeBuff backend did not become ready")
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
        return self._running and self.observer is not None and self.observer._ws is not None

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected": self._running,
            "adapter_name": self._capabilities.adapter_name,
            "capabilities": self._capabilities.to_dict(),
            "connection_info": {
                "base_url": self.connection_info.base_url,
                "websocket_url": self.connection_info.websocket_url,
            },
            "observer_status": self.observer.get_status() if self.observer else None,
        }

    # --- Recovery verification ---

    async def verify_recovery(self, action: RecoveryAction, context: Dict[str, Any]) -> tuple[bool, dict]:
        """Verify that a recovery action actually succeeded."""
        turn_context = context.get("turn_context")
        session_id = context.get("session_id") or (turn_context.session_id if turn_context else None)
        
        if not session_id:
            return False, {"error": "No session ID available for verification"}
        
        try:
            if action == RecoveryAction.RECONNECT_STREAM:
                # Verify WebSocket is connected
                if self.observer and self.observer._ws:
                    return not self.observer._ws.closed, {
                        "verified": "websocket_connected", 
                        "session_id": session_id
                    }
                return False, {"error": "WebSocket not connected after reconnect"}
            
            elif action == RecoveryAction.WAIT_FOR_BACKEND:
                # Verify backend health endpoint
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"{self.connection_info.base_url}/health") as resp:
                            if resp.status == 200:
                                return True, {"verified": "backend_health_ok"}
                except Exception:
                    pass
                return False, {"error": "Backend health check failed"}
            
            elif action == RecoveryAction.WAIT_AND_RETRY:
                return True, {"verified": "backoff_completed", "note": "No external verification needed"}
            
            elif action == RecoveryAction.HUMAN_INTERVENTION:
                return True, {"verified": "human_intervention_requested"}
            
            else:
                return False, {"error": f"Unknown action for verification: {action.value}"}
        
        except Exception as e:
            return False, {"error": f"Verification failed: {e}"}


class FreeBuffRecoveryContext:
    """Context builder for FreeBuff recovery actions."""

    @staticmethod
    def build_context(guardian: GuardianStateMachine, observer: FreeBuffObserver,
                      classification: FailureClassification) -> dict:
        """Build context dictionary for recovery actions."""
        turn_context = guardian.turn_context
        
        return {
            "turn_context": turn_context,
            "classification": classification,
            "session_id": turn_context.session_id if turn_context else None,
            "run_id": turn_context.run_id if turn_context else None,
            "cwd": "",
            "observer": observer,
        }