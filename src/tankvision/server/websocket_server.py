"""WebSocket server that pushes MoE state to the Browser Source overlay."""

import asyncio
import json
import logging
from pathlib import Path

import websockets
from aiohttp import web

from tankvision.calculation.moe_calculator import MoeState

logger = logging.getLogger(__name__)

OVERLAY_DIR = Path(__file__).parent.parent / "overlay"


class MoeWebSocketServer:
    """Serves the overlay static files over HTTP and pushes MoE data over WebSocket.

    Args:
        ws_port: Port for the WebSocket server.
        http_port: Port for the static HTTP server (serves overlay HTML/CSS/JS).
    """

    def __init__(self, ws_port: int = 5174, http_port: int = 5173) -> None:
        self.ws_port = ws_port
        self.http_port = http_port
        self._clients: set[websockets.WebSocketServerProtocol] = set()
        self._ws_server: websockets.WebSocketServer | None = None
        self._http_runner: web.AppRunner | None = None

    async def start(self) -> None:
        """Start both the WebSocket and HTTP servers."""
        # WebSocket server
        self._ws_server = await websockets.serve(
            self._ws_handler,
            "localhost",
            self.ws_port,
        )
        logger.info("WebSocket server started on ws://localhost:%d", self.ws_port)

        # HTTP static file server
        app = web.Application()
        app.router.add_get("/", self._serve_index)
        app.router.add_static("/", OVERLAY_DIR, show_index=False)

        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        site = web.TCPSite(self._http_runner, "localhost", self.http_port)
        await site.start()
        logger.info("HTTP server started on http://localhost:%d", self.http_port)

    async def stop(self) -> None:
        """Shut down both servers."""
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()

        if self._http_runner is not None:
            await self._http_runner.cleanup()

        logger.info("Servers stopped")

    async def broadcast(self, state: MoeState) -> None:
        """Send the current MoE state to all connected overlay clients."""
        if not self._clients:
            return

        message = json.dumps(state.to_dict())
        disconnected = set()

        for client in self._clients:
            try:
                await client.send(message)
            except websockets.ConnectionClosed:
                disconnected.add(client)

        self._clients -= disconnected

    async def _ws_handler(self, websocket: websockets.WebSocketServerProtocol) -> None:
        """Handle a new WebSocket connection from an overlay client."""
        self._clients.add(websocket)
        logger.info("Overlay client connected (%d total)", len(self._clients))
        try:
            async for _ in websocket:
                # Overlay is receive-only; ignore any messages from it
                pass
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)
            logger.info("Overlay client disconnected (%d remaining)", len(self._clients))

    async def _serve_index(self, request: web.Request) -> web.FileResponse:
        """Serve the overlay index.html."""
        index_path = OVERLAY_DIR / "index.html"
        if index_path.exists():
            return web.FileResponse(index_path)
        return web.Response(text="Overlay not found", status=404)
