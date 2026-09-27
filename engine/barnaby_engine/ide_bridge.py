import asyncio
import json
import websockets
from typing import Set, Dict, Any, Optional

class IdeBridge:
    """
    WebSocket bridge for IDE extension communication.
    
    Allows VS Code, Cursor, and Claude Desktop extensions to connect
    and receive real-time updates from Barnaby.
    """
    
    def __init__(self, port: int = 8765):
        self.port = port
        self.is_running = False
        self._server: Optional[asyncio.Server] = None
        self._clients: Set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    async def start(self) -> None:
        """Start the WebSocket server."""
        if self.is_running:
            return
        
        self.is_running = True
        self._loop = asyncio.get_event_loop()
        
        self._server = await websockets.serve(
            self._handle_client,
            "127.0.0.1",
            self.port,
            max_size=10 * 1024 * 1024,  # 10MB max message size
        )
    
    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # Close all client connections
        for client in self._clients:
            await client.close()
        self._clients.clear()
        
        # Close server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
    
    async def _handle_client(self, websocket) -> None:
        """Handle a new client connection."""
        self._clients.add(websocket)
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    # Handle IDE commands here if needed
                    await self._handle_ide_message(websocket, data)
                except json.JSONDecodeError:
                    pass  # Ignore invalid JSON
        finally:
            self._clients.discard(websocket)
    
    async def _handle_ide_message(self, websocket, data: Dict[str, Any]) -> None:
        """
        Handle incoming messages from IDE extensions.
        
        Args:
            websocket: The client WebSocket connection
            data: Parsed JSON message from IDE
        """
        # IDE can send context updates, commands, etc.
        # For now, just acknowledge
        if data.get("type") == "ide_context_update":
            # Would trigger context update in orchestrator
            pass
    
    async def broadcast(self, event: Dict[str, Any]) -> None:
        """
        Broadcast an event to all connected IDE clients.
        
        Args:
            event: The event data to broadcast
        """
        if not self._clients:
            return
        
        message = json.dumps(event)
        # Send to all clients concurrently
        tasks = [client.send(message) for client in self._clients]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_client_count(self) -> int:
        """Get the number of connected IDE clients."""
        return len(self._clients)
