"""WebSocket connection manager — fan-out events to all connected UI clients.

Background (non-async) code calls ``broadcast_threadsafe()`` to push events
from MCP panel tools, the study logger, or the viewport bridge. Active
WebSocket connections are driven by FastAPI's async handlers in
``server.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import WebSocket

from .schemas import WsEvent

logger = logging.getLogger("FurniturePlugin.WS")


class ConnectionManager:
    """Tracks active WebSocket clients and broadcasts serialised events."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        # Lock is created lazily — instantiating asyncio.Lock() here would
        # crash at module import time when no event loop is running yet
        # (happens when this module is loaded from the backend thread
        # before uvicorn spins up its loop).
        self._lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the event loop so sync threads can dispatch into it."""
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._get_lock():
            self._clients.add(ws)
        logger.info("WS client connected — %d total", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._get_lock():
            self._clients.discard(ws)
        logger.info("WS client disconnected — %d remaining", len(self._clients))

    async def send_to(self, ws: WebSocket, event: WsEvent) -> None:
        try:
            await ws.send_text(event.model_dump_json())
        except Exception as e:
            logger.warning("WS send_to failed: %s", e)
            await self.disconnect(ws)

    async def broadcast(self, event: WsEvent) -> None:
        """Async broadcast — call from within the FastAPI event loop."""
        message = event.model_dump_json()
        async with self._get_lock():
            targets = list(self._clients)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception as e:
                logger.warning("WS broadcast failed: %s", e)
                dead.append(ws)
        if dead:
            async with self._get_lock():
                for ws in dead:
                    self._clients.discard(ws)

    def broadcast_threadsafe(self, event: WsEvent) -> None:
        """Dispatch a broadcast from a non-async thread (sync context).

        Used by MCP panel tools, study logger, and viewport bridge callbacks
        that run on Rhino's UI thread or the MCP server's TCP handler.
        """
        if self._loop is None or not self._loop.is_running():
            logger.debug("broadcast_threadsafe: loop not running, event dropped")
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(event), self._loop)

    @property
    def client_count(self) -> int:
        return len(self._clients)


# Singleton
manager = ConnectionManager()
