"""
Raw ACP Event Capture - captures ALL raw WebSocket frames from Goose
without any parsing or filtering by Guardian.
"""
import asyncio
import websockets
import json
import time
from datetime import datetime
from pathlib import Path

async def capture_raw_acp_events():
    """Connect to Goose ACP and capture all raw events."""
    
    uri = 'ws://127.0.0.1:3284/acp'
    output_file = f"C:/tmp/goose-test/raw_acp_capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    try:
        async with websockets.connect(uri, ping_interval=10) as ws:
            print(f"Connected to {uri}")
            print(f"Capturing to: {output_file}")
            
            # Send initialize
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "elicitation": {"form": {}},
                        "_meta": {
                            "goose": {
                                "mcpHostCapabilities": ["filesystem.read", "filesystem.write", "shell.execute"],
                                "customNotifications": True,
                                "recipeParameterRequests": True,
                            }
                        }
                    },
                    "clientInfo": {
                        "name": "raw-capture",
                        "version": "1.0.0"
                    }
                }
            }
            await ws.send(json.dumps(init_request))
            print("Sent initialize request")
            
            # Wait for initialize response
            response = await asyncio.wait_for(ws.recv(), timeout=10.0)
            data = json.loads(response)
            print(f"Initialize response: {json.dumps(data)[:500]}")
            
            # Create session
            session_request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/new",
                "params": {"mcpServers": [], "cwd": r"C:\tmp\goose-test"}
            }
            await ws.send(json.dumps(session_request))
            print("Sent session/new request")
            
            response = await asyncio.wait_for(ws.recv(), timeout=10.0)
            data = json.loads(response)
            session_id = data.get('result', {}).get('sessionId')
            print(f"Session created: {session_id}")
            
            # Now send a prompt that should trigger tools
            prompt_request = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": "Create a file test_output.txt with content: hello from raw capture"}]
                }
            }
            await ws.send(json.dumps(prompt_request))
            print("Sent session/prompt request")
            
            # Capture all events for 60 seconds
            start_time = time.time()
            event_count = 0
            
            with open(output_file, 'w') as f:
                while time.time() - start_time < 60:
                    try:
                        raw_frame = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        timestamp = datetime.now().isoformat()
                        
                        # Parse to check structure but store raw
                        try:
                            parsed = json.loads(raw_frame)
                            # Also store parsed for analysis
                            msg_type = "unknown"
                            if isinstance(parsed, list):
                                msg_type = "array"
                            elif isinstance(parsed, dict):
                                if "method" in parsed and "id" in parsed:
                                    msg_type = "request"
                                elif "method" in parsed and "id" not in parsed:
                                    msg_type = "notification"
                                elif "id" in parsed and ("result" in parsed or "error" in parsed):
                                    msg_type = "response"
                            
                            record = {
                                "timestamp": timestamp,
                                "elapsed": time.time() - start_time,
                                "msg_type": msg_type,
                                "raw": raw_frame,
                                "parsed": parsed
                            }
                        except json.JSONDecodeError:
                            record = {
                                "timestamp": timestamp,
                                "elapsed": time.time() - start_time,
                                "msg_type": "invalid_json",
                                "raw": raw_frame
                            }
                        
                        f.write(json.dumps(record) + "\n")
                        f.flush()
                        event_count += 1
                        
                        # Print summary
                        print(f"[{event_count}] {msg_type} at {timestamp} ({time.time() - start_time:.1f}s elapsed)")
                        
                    except asyncio.TimeoutError:
                        print("Timeout waiting for event, continuing...")
                        continue
                    except websockets.exceptions.ConnectionClosed as e:
                        print(f"Connection closed: {e.code} {e.reason}")
                        break
                    except Exception as e:
                        print(f"Error: {e}")
                        break
            
            print(f"Capture complete. {event_count} events saved to {output_file}")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(capture_raw_acp_events())