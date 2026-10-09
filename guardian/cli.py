"""
Command-line interface for Agent Guardian.
"""

import argparse
import asyncio
import json
import signal
import sys
from typing import Optional, List
import logging
from pathlib import Path

from guardian.logging import setup_logging, get_logger
from guardian.core import (
    GuardianStateMachine,
    GuardianConfig,
    SafetyMode,
    StateMachineEvent,
    FailureClassification,
    RecoveryAction,
    TurnContext,
)
from guardian.core.failure_classifier import FailureClassifier
from guardian.core.safety_gate import SafetyGate
from guardian.goose import (
    GooseAdapter,
    GooseAdapterConfig,
    discover_primary_goose,
    GooseConnectionInfo,
)
from guardian.goose.observer import run_observer
from guardian.goose.protocol import ACPEvent, ACPEventType
from guardian.hermes import (
    HermesAdapter,
    HermesAdapterConfig,
    HermesObserver,
    run_hermes_observer,
)

logger = get_logger("cli")


class GuardianCLI:
    """Main CLI application."""
    
    def __init__(self):
        self.guardian: Optional[GuardianStateMachine] = None
        self.adapter: Optional[GooseAdapter] = None
        self.observer = None
        self._shutdown = False
    
    def create_parser(self) -> argparse.ArgumentParser:
        """Create argument parser with global args and subcommands."""
        # Parent parser for global arguments
        global_parser = argparse.ArgumentParser(add_help=False)
        global_parser.add_argument(
            "-v", "--verbose",
            action="store_true",
            help="Enable verbose logging"
        )
        global_parser.add_argument(
            "--log-level",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            default="INFO",
            help="Set logging level"
        )
        global_parser.add_argument(
            "--config",
            help="Path to configuration file"
        )
        global_parser.add_argument(
            "--safety-mode",
            choices=["observe_only", "dry_run", "auto_recover"],
            default="observe_only",
            help="Safety mode (default: observe_only)"
        )
        
        # Main parser
        parser = argparse.ArgumentParser(
            prog="python -m guardian",
            description="Agent Guardian - Goose Desktop Watchdog",
            parents=[global_parser],
        )
        
        subparsers = parser.add_subparsers(dest="command", help="Commands")
        subparsers.required = True
        
        # observe command
        observe_parser = subparsers.add_parser(
            "observe",
            help="Observe agent events (read-only)",
            parents=[global_parser],
        )
        observe_subparsers = observe_parser.add_subparsers(dest="observe_target", help="Agent to observe")
        observe_subparsers.required = True
        
        # observe goose
        observe_goose = observe_subparsers.add_parser(
            "goose",
            help="Observe Goose Desktop ACP events (read-only)",
            parents=[global_parser],
        )
        observe_goose.add_argument(
            "--host",
            default="127.0.0.1",
            help="Host to scan for Goose backend (default: 127.0.0.1)"
        )
        observe_goose.add_argument(
            "--port-range",
            default="10000-65535",
            help="Port range to scan (default: 10000-65535)"
        )
        observe_goose.add_argument(
            "--port",
            type=int,
            help="Specific port to connect to (skips discovery)"
        )
        observe_goose.add_argument(
            "--token",
            help="ACP token (if known, otherwise auto-discovery attempted)"
        )
        observe_goose.add_argument(
            "--tls",
            action="store_true",
            help="Use TLS (wss://) for WebSocket connection"
        )
        observe_goose.add_argument(
            "--stale-threshold",
            type=float,
            default=60.0,
            help="Seconds before reporting stale stream (default: 60)"
        )
        observe_goose.add_argument(
            "--discovery-timeout",
            type=float,
            default=30.0,
            help="Maximum seconds to spend on discovery (default: 30)"
        )
        observe_goose.add_argument(
            "--no-auto-reconnect",
            action="store_true",
            help="Disable automatic reconnection on disconnect"
        )
        
        # observe hermes
        observe_hermes = observe_subparsers.add_parser(
            "hermes",
            help="Observe Hermes Agent events (read-only, in-process)",
            parents=[global_parser],
        )
        observe_hermes.add_argument(
            "--stale-threshold",
            type=float,
            default=60.0,
            help="Seconds before reporting stale stream (default: 60)"
        )
        observe_hermes.add_argument(
            "--first-chunk-threshold",
            type=float,
            default=30.0,
            help="Seconds before reporting first chunk timeout (default: 30)"
        )
        observe_hermes.add_argument(
            "--chunk-gap-threshold",
            type=float,
            default=15.0,
            help="Seconds between chunks before reporting gap (default: 15)"
        )
        observe_hermes.add_argument(
            "--tool-timeout-threshold",
            type=float,
            default=120.0,
            help="Seconds before reporting tool timeout (default: 120)"
        )
        
        # watch command (full state machine with recovery if enabled)
        watch_parser = subparsers.add_parser(
            "watch",
            help="Watch Goose with full state machine (auto-recovery if AUTO_RECOVER mode)",
            parents=[global_parser],
        )
        watch_parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Host to scan for Goose backend (default: 127.0.0.1)"
        )
        watch_parser.add_argument(
            "--port-range",
            default="10000-65535",
            help="Port range to scan (default: 10000-65535)"
        )
        watch_parser.add_argument(
            "--port",
            type=int,
            help="Specific port to connect to (skips discovery)"
        )
        watch_parser.add_argument(
            "--token",
            help="ACP token (if known)"
        )
        watch_parser.add_argument(
            "--tls",
            action="store_true",
            help="Use TLS (wss://) for WebSocket connection"
        )
        watch_parser.add_argument(
            "--stale-threshold",
            type=float,
            default=60.0,
            help="Seconds before reporting stale stream (default: 60)"
        )
        
        # doctor command - diagnostics
        doctor_parser = subparsers.add_parser(
            "doctor",
            help="Run diagnostics on Goose connection and Guardian config",
            parents=[global_parser],
        )
        doctor_parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Host to scan (default: 127.0.0.1)"
        )
        doctor_parser.add_argument(
            "--port",
            type=int,
            help="Specific port to test (skips discovery)"
        )
        doctor_parser.add_argument(
            "--token",
            help="ACP token for connection test"
        )
        doctor_parser.add_argument(
            "--tls",
            action="store_true",
            help="Use TLS for connection test"
        )
        
        # config command
        config_parser = subparsers.add_parser(
            "config",
            help="Manage Guardian configuration",
            parents=[global_parser],
        )
        config_subparsers = config_parser.add_subparsers(dest="config_command", help="Config commands")
        config_subparsers.required = True
        
        config_show = config_subparsers.add_parser("show", help="Show current configuration")
        config_set = config_subparsers.add_parser("set", help="Set configuration value")
        config_set.add_argument("key", help="Configuration key (dot notation)")
        config_set.add_argument("value", help="Value to set")
        config_save = config_subparsers.add_parser("save", help="Save current config to file")
        config_save.add_argument("--path", help="Path to save config file")
        
        return parser
    
    def run(self, argv: Optional[list] = None) -> int:
        """Main entry point."""
        parser = self.create_parser()
        args = parser.parse_args(argv)
        
        # Setup logging
        log_level = "DEBUG" if args.verbose else args.log_level
        setup_logging(level=log_level)
        
        if args.verbose:
            logging.getLogger("guardian").setLevel("DEBUG")
        
        # Parse safety mode
        safety_mode = SafetyMode(args.safety_mode)
        
        # Dispatch command
        if args.command == "observe":
            return asyncio.run(self.run_observe(args))
        elif args.command == "watch":
            return asyncio.run(self.run_watch(args))
        elif args.command == "doctor":
            return asyncio.run(self.run_doctor(args))
        elif args.command == "config":
            return self.run_config(args)
        
        logger.error(f"Unknown command: {args.command}")
        return 1
    
    async def run_observe(self, args: argparse.Namespace) -> int:
        """Run the observe command (read-only)."""
        if args.observe_target == "goose":
            return await self._run_observe_goose(args)
        elif args.observe_target == "hermes":
            return await self._run_observe_hermes(args)
        else:
            logger.error(f"Unknown observe target: {args.observe_target}")
            return 1
    
    async def _run_observe_goose(self, args: argparse.Namespace) -> int:
        """Run the observe command for Goose (read-only)."""
        connection_info = await self._get_connection_info(args)
        if not connection_info:
            return 1
        
        print(f"\n[Guardian] Connected to Goose at {connection_info.host}:{connection_info.port}")
        print(f"[Guardian] Observing ACP events (Ctrl+C to stop)...\n")
        
        try:
            self.observer = await run_observer(
                connection_info=connection_info,
                token=args.token,
                stale_threshold=args.stale_threshold,
                on_event=self._handle_event,
                on_failure=self._handle_failure,
                use_tls=args.tls,
            )
            
            print(f"[Guardian] Observer running. Press Ctrl+C to stop.\n")
            await self._wait_for_shutdown()
            
        except ConnectionError as e:
            logger.error(f"Connection failed: {e}")
            return 1
        except KeyboardInterrupt:
            print("\n[Guardian] Shutting down...")
        finally:
            if self.observer:
                await self.observer.disconnect()
        
        return 0
    
    async def _run_observe_hermes(self, args: argparse.Namespace) -> int:
        """Run the observe command for Hermes (read-only, in-process)."""
        # For Hermes, we need to import and find a running agent
        # This is a placeholder - in practice, you'd attach to a running Hermes instance
        print(f"\n[Guardian] Hermes in-process observer")
        print(f"[Guardian] Stale threshold: {args.stale_threshold}s")
        print(f"[Guardian] First chunk threshold: {args.first_chunk_threshold}s")
        print(f"[Guardian] Chunk gap threshold: {args.chunk_gap_threshold}s")
        print(f"[Guardian] Tool timeout threshold: {args.tool_timeout_threshold}s")
        print(f"[Guardian] NOTE: This requires a running Hermes agent to attach to.")
        print(f"[Guardian] In a real implementation, this would attach to a live agent instance.\n")
        
        # For now, just wait for shutdown to demonstrate the CLI structure
        try:
            await self._wait_for_shutdown()
        except KeyboardInterrupt:
            print("\n[Guardian] Shutting down...")
        
        return 0
    
    async def run_watch(self, args: argparse.Namespace) -> int:
            """Run the watch command with full state machine."""
            connection_info = await self._get_connection_info(args)
            if not connection_info:
                return 1

            # Create and connect adapter first
            adapter_config = GooseAdapterConfig(
                port=args.port,
                token=args.token,
                use_tls=args.tls,
                stale_threshold=args.stale_threshold,
                safety_mode=SafetyMode(args.safety_mode),
            )

            self.adapter = GooseAdapter(
                config=adapter_config,
                guardian=None,  # Will be set after guardian creation
            )

            # Create guardian state machine with the adapter
            self.guardian = GuardianStateMachine(
                safety_mode=SafetyMode(args.safety_mode),
                stale_threshold=args.stale_threshold,
                on_state_change=self._on_state_change,
                on_failure=self._on_failure,
                on_recovery_action=self._on_recovery_action,
                on_recovery_result=self._on_recovery_result,
                adapter=self.adapter,
            )

            # Set the guardian reference on the adapter
            self.adapter.guardian = self.guardian

            print(f"\n[Guardian] Watch mode: Connected to Goose at {connection_info.host}:{connection_info.port}")
            print(f"[Guardian] Safety mode: {args.safety_mode}")
            print(f"[Guardian] Watching with full state machine (Ctrl+C to stop)...\n")

            try:
                connected = await self.adapter.connect()
                if not connected:
                    logger.error("Failed to connect to Goose backend")
                    return 1

                print(f"[Guardian] Adapter connected. Watching with full state machine (Ctrl+C to stop)...\n")
                await self._wait_for_shutdown()

            except ConnectionError as e:
                logger.error(f"Connection failed: {e}")
                return 1
            except KeyboardInterrupt:
                print("\n[Guardian] Shutting down...")
            finally:
                if self.adapter:
                    await self.adapter.disconnect()
                if self.observer:
                    await self.observer.disconnect()

            return 0
    
    async def run_doctor(self, args: argparse.Namespace) -> int:
        """Run diagnostics."""
        print("\n[Guardian] Running diagnostics...\n")
        
        # Test discovery
        print("1. Testing backend discovery...")
        if args.port:
            print(f"   Using specified port: {args.port}")
        else:
            connection = await discover_primary_goose(host=args.host)
            if connection:
                print(f"   ✓ Found backend at {connection.host}:{connection.port}")
            else:
                print("   ✗ No backend found")
                return 1
        
        # Test connection
        print("\n2. Testing WebSocket connection...")
        try:
            # Quick connection test
            import websockets
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            port = args.port or 49684
            token = args.token or "test"
            ws_url = f"wss://127.0.0.1:{port}/acp?token={token}"
            
            ws = await asyncio.wait_for(
                websockets.connect(ws_url, ssl=ssl_context),
                timeout=5.0
            )
            await ws.close()
            print(f"   ✓ WebSocket connection successful")
        except Exception as e:
            print(f"   ✗ WebSocket connection failed: {e}")
        
        # Test handshake
        print("\n3. Testing ACP handshake...")
        try:
            import websockets
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            port = args.port or 49684
            token = args.token or "test"
            ws_url = f"wss://127.0.0.1:{port}/acp?token={token}"
            
            ws = await asyncio.wait_for(
                websockets.connect(ws_url, ssl=ssl_context),
                timeout=5.0
            )
            
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {"elicitation": {"form": {}}},
                    "clientInfo": {"name": "guardian-doctor", "version": "0.1.0"}
                }
            }
            await ws.send(json.dumps(init_req))
            response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            response_data = json.loads(response)
            await ws.close()
            
            if "result" in response_data:
                agent_info = response_data["result"].get("agentInfo", {})
                print(f"   ✓ Handshake successful - Agent: {agent_info.get('name')} v{agent_info.get('version')}")
            else:
                print(f"   ✗ Handshake failed: {response_data.get('error', 'unknown')}")
        except Exception as e:
            print(f"   ✗ Handshake failed: {e}")
        
        print("\n[Guardian] Diagnostics complete.\n")
        return 0
    
    def run_config(self, args: argparse.Namespace) -> int:
        """Handle config commands."""
        from guardian.core import GuardianConfig
        
        config = GuardianConfig.load()
        
        if args.config_command == "show":
            print("\n[Guardian] Current Configuration:")
            print(json.dumps(config.to_dict(), indent=2))
        elif args.config_command == "set":
            # Simple set - would need more robust implementation
            print(f"Setting {args.key} = {args.value} (not fully implemented)")
        elif args.config_command == "save":
            path = args.path or "~/.guardian/config.yaml"
            config.save(Path(args.path) if args.path else None)
            print(f"Configuration saved to {path}")
        
        return 0
    
    async def _get_connection_info(self, args) -> Optional[GooseConnectionInfo]:
        """Get connection info from args or discovery."""
        if args.port:
            host = args.host
            port = args.port
            return GooseConnectionInfo(
                host=host,
                port=port,
                status_url=f"http://{host}:{port}/status",
                websocket_url=f"ws://{host}:{port}/acp",
                token=args.token or "UNKNOWN",
            )
        
        logger.info("Discovering Goose backend...")
        connection_info = await discover_primary_goose(
            host=args.host,
            port_range=tuple(map(int, args.port_range.split("-"))) if hasattr(args, 'port_range') else (10000, 65535)
        )
        
        if not connection_info:
            logger.error("No Goose backend found. Is Goose Desktop running?")
            return None
        
        if args.token:
            connection_info.token = args.token
        
        return connection_info
    
    def _handle_event(self, event: ACPEvent):
        """Handle observed event."""
        pass
    
    def _handle_failure(self, failure: str, details: dict):
        """Handle detected failure."""
        session_id = details.get("session_id", "unknown")
        print(f"\n[!] FAILURE DETECTED [{session_id}]: {failure}")
    
    def _on_state_change(self, old_state, new_state, event):
        """Callback for state changes."""
        print(f"[STATE] {old_state.value} -> {new_state.value} (event: {event.value})")
    
    def _on_failure(self, classification: FailureClassification, turn_context: Optional[TurnContext]):
        """Callback for failure detection."""
        print(f"\n[FAILURE] {classification.failure_type.value}")
        print(f"  Retryable: {classification.retryable}")
        print(f"  Replay Safe: {classification.replay_safe}")
        print(f"  Action: {classification.recommended_action.value}")
        if turn_context:
            print(f"  Session: {turn_context.session_id}")
            print(f"  Phase: {turn_context.phase.value}")
            print(f"  Safe to Replay: {turn_context.is_safe_to_replay()}")
    
    def _on_recovery_action(self, action: RecoveryAction, context: dict):
        """Callback for recovery action."""
        print(f"\n[RECOVERY] Attempting: {action.value}")
        print(f"  Failure: {context.get('failure', 'unknown')}")
    
    def _on_recovery_result(self, success: bool, details: dict):
        """Callback for recovery result."""
        if success:
            print(f"[RECOVERY] ✓ Success")
        else:
            print(f"[RECOVERY] ✗ Failed: {details.get('reason', 'unknown')}")
    
    async def _wait_for_shutdown(self):
        """Wait for shutdown signal."""
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()
        
        def signal_handler():
            self._shutdown = True
            shutdown_event.set()
        
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                pass
        
        await shutdown_event.wait()


def main():
    """Entry point for python -m guardian."""
    cli = GuardianCLI()
    sys.exit(cli.run())


if __name__ == "__main__":
    main()