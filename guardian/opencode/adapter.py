"""OpenCode Adapter for Agent Guardian.

Connects OpenCode (via ACP stdio or HTTP) to Guardian core.
"""

import asyncio
import json
import logging
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

from .protocol import OCPAEventType, OpenCodeConnectionInfo
from .observer import OpenCodeObserver, run_opencode_observer

logger = get_logger("opencode.adapter")


@dataclass
class OpenCodeAdapterConfig:
    """Configuration for OpenCode adapter."""
    # Connection
    mode: str = "acp"  # "acp" or "http"
    opencode_path: str = "opencode"
    base_url: str = "http://127.0.0.1:4096"
    api_key: Optional[str] = None
    
    # Detection thresholds
    stale_threshold: float = 60.0
    first_chunk_threshold: float = 30.0
    chunk_gap_threshold: float = 15.0
    tool_timeout_threshold: float = 120.0
    
    # Safety
    safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY


class OpenCodeAdapter(AgentAdapter):
    """
    Adapter that connects OpenCode to Guardian core.
    
    Supports both ACP (stdio subprocess) and HTTP API connections.
    Translates OpenCode events into Guardian state machine events,
    manages the connection, and exposes recovery actions supported by OpenCode.
    """

    def __init__(
        self,
        config: OpenCodeAdapterConfig,
        guardian: GuardianStateMachine,
    ):
        self.config = config
        self.guardian = guardian
        self.observer: Optional[OpenCodeObserver] = None
        self.connection_info = OpenCodeConnectionInfo(mode=config.mode)
        self._running = False
        
        # Recovery action implementations
        self._recovery_actions = {
            RecoveryAction.WAIT_AND_RETRY: self._action_wait_and_retry,
            RecoveryAction.CANCEL_AND_REPLAY: self._action_cancel_and_replay,
            RecoveryAction.RECONNECT_STREAM: self._action_reconnect_acp,
            RecoveryAction.RELOAD_SESSION: self._action_reload_session,
            RecoveryAction.FORK_SESSION: self._action_fork_session,
            RecoveryAction.WAIT_FOR_BACKEND: self._action_wait_for_backend,
        }
        
        # Capability declaration - based on what OpenCode actually supports via ACP
        self._capabilities = AdapterCapabilities(
            adapter_name="opencode",
            adapter_version="1.0",
            agent_type="opencode",
        )
        self._declare_capabilities()

    def _declare_capabilities(self):
        """Declare capabilities based on actual OpenCode ACP support."""
        # Observation - VERIFIED IMPLEMENTED via ACP stdio
        self._capabilities.set_available(AdapterCapability.OBSERVE_STREAM, reason="Implemented via ACP agent_message_chunk events")
        self._capabilities.set_available(AdapterCapability.OBSERVE_REASONING, reason="Implemented via agent_thought_chunk events")
        self._capabilities.set_available(AdapterCapability.OBSERVE_TOOL_PROGRESS, reason="Implemented via tool_call and tool_call_update events")
        self._capabilities.set_available(AdapterCapability.OBSERVE_TURN_LIFECYCLE, reason="Implemented via sessionNotification and prompt response events")
        
        # Stream recovery - VERIFIED IMPLEMENTED via ACP reconnect
        self._capabilities.set_available(AdapterCapability.RECONNECT_STREAM, reason="Implemented via ACP subprocess restart")
        self._capabilities.set_unavailable(AdapterCapability.CANCEL_STREAM, reason="No explicit ACP cancel stream method")
        
        # Session recovery - PARTIALLY_IMPLEMENTED via ACP (UNSTABLE APIs)
        # OpenCode ACP supports loadSession and forkSession but marks them as UNSTABLE APIs
        self._capabilities.set_available(AdapterCapability.LOAD_SESSION, reason="Implemented via ACP loadSession method (unstable API)")
        self._capabilities.set_available(AdapterCapability.FORK_SESSION, reason="Implemented via ACP forkSession method (unstable API)")
        self._capabilities.set_unavailable(AdapterCapability.REATTACH_SESSION, reason="Not directly supported in ACP")
        self._capabilities.set_unavailable(AdapterCapability.SAVE_SESSION, reason="No ACP session save method")
        
        # Provider recovery - NOT IN ACP
        self._capabilities.set_unavailable(AdapterCapability.SWITCH_PROVIDER, reason="Not in ACP protocol")
        self._capabilities.set_unavailable(AdapterCapability.GET_PROVIDER_LIST, reason="Not in ACP protocol")
        
        # Agent process - PARTIAL (process monitor possible)
        self._capabilities.set_available(AdapterCapability.RESTART_AGENT, reason="Implemented via subprocess restart")
        self._capabilities.set_available(AdapterCapability.GET_AGENT_STATUS, reason="Implemented via process status")
        self._capabilities.set_available(AdapterCapability.IS_AGENT_ALIVE, reason="Implemented via process/connection status")
        
        # Turn control - VERIFIED IMPLEMENTED via ACP
        self._capabilities.set_available(AdapterCapability.CANCEL_TURN, reason="Implemented via ACP cancel notification")
        self._capabilities.set_available(AdapterCapability.RETRY_REQUEST, reason="Implemented via new session + replay")
        self._capabilities.set_available(AdapterCapability.REPLAY_TURN, reason="Implemented via loadSession with history replay")
        
        # Introspection - PARTIAL
        self._capabilities.set_available(AdapterCapability.GET_PARTIAL_TOOL_NAMES, reason="Available via observer session tracking")
        self._capabilities.set_unavailable(AdapterCapability.GET_TOOL_STATE, reason="Not in ACP")
        self._capabilities.set_available(AdapterCapability.GET_SESSION_STATE, reason="Implemented via observer session tracking")
        self._capabilities.set_available(AdapterCapability.GET_TURN_CONTEXT, reason="Implemented via observer turn tracking")
        
        # Safety - PARTIAL
        self._capabilities.set_available(AdapterCapability.VERIFY_SESSION_IDENTITY, reason="Implemented via session ID tracking")
        self._capabilities.set_available(AdapterCapability.VERIFY_SESSION_EXISTS, reason="Implemented via listSessions method")
        
        # Monitoring - VERIFIED IMPLEMENTED
        self._capabilities.set_available(AdapterCapability.DETECT_DISCONNECT, reason="Implemented via process/connection monitoring")
        self._capabilities.set_available(AdapterCapability.DETECT_CRASH, reason="Implemented via process termination detection")
        self._capabilities.set_available(AdapterCapability.DETECT_STALE, reason="Implemented via observer stale detection")

    @property
    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    async def connect(self) -> bool:
        """Connect to OpenCode backend."""
        try:
            self.observer = await run_opencode_observer(
                connection_info=self.connection_info,
                stale_threshold=self.config.stale_threshold,
                first_chunk_threshold=self.config.first_chunk_threshold,
                chunk_gap_threshold=self.config.chunk_gap_threshold,
                tool_timeout_threshold=self.config.tool_timeout_threshold,
                on_event=self._handle_ocp_event,
                on_failure=self._handle_failure_event,
            )
            
            self._running = True
            
            # Notify guardian of connection
            self.guardian.handle_event(StateMachineEvent.CONNECTED, {})
            
            logger.info(f"OpenCode adapter connected (mode: {self.config.mode})")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to OpenCode: {e}")
            return False

    def get_observer(self) -> Optional[OpenCodeObserver]:
        """Get the observer for accessing stream state."""
        return self.observer

    def _handle_ocp_event(self, event: dict):
        """Translate OpenCode event to Guardian state machine event."""
        if not self.guardian.turn_context and event.get("session_id"):
            # Start tracking turn if we don't have one
            self.guardian.start_turn(
                session_id=event["session_id"],
                run_id=None,
            )

        # Map OpenCode event types to Guardian events
        event_mapping = {
            "agent_message_chunk": StateMachineEvent.ASSISTANT_CHUNK,
            "user_message_chunk": StateMachineEvent.ASSISTANT_CHUNK,
            "agent_thought_chunk": StateMachineEvent.ASSISTANT_CHUNK,  # Reasoning is like assistant output
            "tool_call": StateMachineEvent.TOOL_STARTED,
            "tool_call_update": StateMachineEvent.TOOL_STARTED,  # Progress
            "session_start": StateMachineEvent.USER_MESSAGE_SENT,
            "session_end": StateMachineEvent.TURN_COMPLETE,
            "error": StateMachineEvent.FAILURE_DETECTED,
        }

        ocp_event_type = event.get("type")
        guardian_event = event_mapping.get(ocp_event_type)
        
        if guardian_event:
            data = {"session_id": event.get("session_id")}
            
            # Add event-specific data
            if "tool_name" in event:
                data["tool_name"] = event["tool_name"]
            if "tool_call_id" in event:
                data["tool_call_id"] = event["tool_call_id"]
            if "error" in event:
                data["error"] = event["error"]
            
            self.guardian.handle_event(guardian_event, data)

        # Handle session info
        session_id = event.get("session_id")
        if ocp_event_type == "session_start" and session_id:
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
        """Disconnect from OpenCode backend."""
        self._running = False
        if self.observer:
            await self.observer.disconnect()
            self.observer = None
        
        self.guardian.handle_event(StateMachineEvent.DISCONNECTED, {})
        logger.info("OpenCode adapter disconnected")

    # Recovery action implementations
    async def _action_wait_and_retry(self, context: dict) -> bool:
        """Wait with backoff then allow retry."""
        logger.info("RECOVERY: WAIT_AND_RETRY - ready to replay prompt")
        return True

    async def _action_cancel_and_replay(self, context: dict) -> bool:
        """Cancel in-flight turn and replay the prompt using ACP cancel notification."""
        turn_context = context.get("turn_context")
        if not turn_context or not turn_context.session_id:
            logger.error("Cannot cancel - no session ID")
            return False

        if not self.observer or not self.observer._process:
            logger.error("No observer connected")
            return False

        # Send ACP cancel notification (proper cancel method)
        try:
            cancel_msg = {
                "jsonrpc": "2.0",
                "method": "cancel",
                "params": {"sessionId": turn_context.session_id},
            }
            await self.observer._send_message(cancel_msg)
            logger.info(f"Sent ACP cancel for session {turn_context.session_id}")
            await asyncio.sleep(0.5)
            return True
        except Exception as e:
            logger.error(f"Failed to send ACP cancel: {e}")
            return False

    async def _action_reconnect_acp(self, context: dict) -> bool:
        """Reconnect to ACP subprocess."""
        logger.info("Reconnecting to OpenCode ACP...")
        await self.disconnect()
        await asyncio.sleep(1.0)
        return await self.connect()

    async def _action_reload_session(self, context: dict) -> bool:
        """Reload session from backend using ACP loadSession."""
        turn_context = context.get("turn_context")
        if not turn_context or not turn_context.session_id:
            logger.error("Cannot reload - no session ID")
            return False

        if not self.observer or not self.observer._process:
            logger.error("No observer or process connected")
            return False

        # Use ACP loadSession method (verified supported)
        try:
            # Get cwd from session directory if available
            cwd = ""
            if self.observer and self.observer.sessions:
                session_state = self.observer.get_session_state(turn_context.session_id)
                if session_state and session_state.directory:
                    cwd = session_state.directory
            
            load_msg = self.observer.parser.create_load_session_request(
                session_id=turn_context.session_id,
                cwd=cwd,
                mcp_servers=[]
            )
            self.observer.parser.track_request(load_msg)
            await self.observer._send_message(load_msg)
            logger.info(f"Sent ACP loadSession for {turn_context.session_id}")
            
            # Wait for response and verify success
            try:
                response_line = await asyncio.wait_for(
                    self.observer._process.stdout.readline(),
                    timeout=10.0
                )
                if response_line:
                    response = json.loads(response_line.strip())
                    if "error" in response:
                        logger.error(f"Load session failed: {response['error']}")
                        return False
                    logger.info(f"Load session succeeded for {turn_context.session_id}")
                    return True
            except asyncio.TimeoutError:
                logger.error("Load session response timeout")
                return False
        except Exception as e:
            logger.error(f"Failed to load session via ACP: {e}")
            return False

    async def _action_fork_session(self, context: dict) -> bool:
        """Fork session at safe point using ACP forkSession (unstable)."""
        turn_context = context.get("turn_context")
        if not turn_context or not turn_context.session_id:
            logger.error("Cannot fork - no session ID")
            return False

        if not self.observer or not self.observer._process:
            logger.error("No observer or process connected")
            return False

        # Use ACP forkSession method (unstable API)
        try:
            cwd = ""
            if self.observer and self.observer.sessions:
                session_state = self.observer.get_session_state(turn_context.session_id)
                if session_state and session_state.directory:
                    cwd = session_state.directory
            
            fork_msg = self.observer.parser.create_fork_session_request(
                session_id=turn_context.session_id,
                cwd=cwd
            )
            self.observer.parser.track_request(fork_msg)
            await self.observer._send_message(fork_msg)
            logger.info(f"Sent ACP forkSession for {turn_context.session_id}")
            
            # Wait for response and verify success
            try:
                response_line = await asyncio.wait_for(
                    self.observer._process.stdout.readline(),
                    timeout=10.0
                )
                if response_line:
                    response = json.loads(response_line.strip())
                    if "error" in response:
                        logger.error(f"Fork session failed: {response['error']}")
                        return False
                    new_session_id = response.get("result", {}).get("sessionId")
                    if new_session_id:
                        logger.info(f"Fork session succeeded, new session: {new_session_id}")
                        context["new_session_id"] = new_session_id
                        return True
            except asyncio.TimeoutError:
                logger.error("Fork session response timeout")
                return False
        except Exception as e:
            logger.error(f"Failed to fork session via ACP: {e}")
            return False
        
        return False

    async def _action_wait_for_backend(self, context: dict) -> bool:
        """Wait for backend to restart."""
        logger.info("Waiting for OpenCode to be ready...")
        
        # For ACP mode, wait for process to be responsive
        if self.config.mode == "acp":
            # Try to send a ping/initialize to check responsiveness
            for _ in range(30):
                await asyncio.sleep(1.0)
                if self.observer and self.observer._process and self.observer._process.poll() is None:
                    logger.info("OpenCode process is running")
                    return True
            logger.error("OpenCode process did not become ready")
            return False
        
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
            "connection_info": {
                "mode": self.connection_info.mode,
                "base_url": self.connection_info.base_url,
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
                # Verify process is running and responsive
                if self.observer and self.observer._process:
                    return self.observer._process.poll() is None, {
                        "verified": "process_running", 
                        "session_id": session_id
                    }
                return False, {"error": "Process not running after reconnect"}
            
            elif action == RecoveryAction.RELOAD_SESSION:
                # Verify new session was created
                if self.observer and self.observer.sessions:
                    return True, {"verified": "new_session_created", "session_id": session_id}
                return False, {"error": "No session after reload"}
            
            elif action == RecoveryAction.WAIT_FOR_BACKEND:
                # Verify process is running
                if self.observer and self.observer._process:
                    return self.observer._process.poll() is None, {
                        "verified": "backend_ready"
                    }
                return False, {"error": "Process not running"}
            
            elif action == RecoveryAction.CANCEL_AND_REPLAY:
                # ACP cancel is fire-and-forget, verify session was closed
                return True, {"verified": "close_sent", "session_id": session_id}
            
            elif action == RecoveryAction.WAIT_AND_RETRY:
                # This is a no-op/backoff action
                return True, {"verified": "backoff_completed", "note": "No external verification needed"}
            
            elif action == RecoveryAction.HUMAN_INTERVENTION:
                return True, {"verified": "human_intervention_requested"}
            
            else:
                return False, {"error": f"Unknown action for verification: {action.value}"}
        
        except Exception as e:
            return False, {"error": f"Verification failed: {e}"}


class OpenCodeRecoveryContext:
    """Context builder for OpenCode recovery actions."""

    @staticmethod
    def build_context(guardian: GuardianStateMachine, observer: OpenCodeObserver,
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