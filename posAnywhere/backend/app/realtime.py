"""Realtime tier — in-process publish/subscribe over WebSockets.

The architecture (§3) shows a dedicated Realtime/WebSocket gateway backed by
Redis pub/sub. To keep the modular monolith runnable with zero infrastructure,
this module implements the same fan-out pattern in memory.

Channels:
  * "order:<token>"  -> per-order updates streamed to the customer tracking page.
  * "dispatch"       -> fleet-wide driver GPS + assignment updates for the POS map.

Swapping this class for a Redis-backed implementation later would not change
any caller, because every module talks only to the `manager` singleton.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    """Tracks active WebSocket connections grouped by channel and broadcasts."""

    def __init__(self) -> None:
        # channel name -> set of connected websockets subscribed to it.
        self._channels: dict[str, set[WebSocket]] = defaultdict(set)
        # Guards mutation of the channel registry across concurrent tasks.
        self._lock = asyncio.Lock()

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        """Accept a new socket and subscribe it to the given channel."""
        await websocket.accept()
        async with self._lock:
            self._channels[channel].add(websocket)

    async def disconnect(self, channel: str, websocket: WebSocket) -> None:
        """Remove a socket from a channel (called when the client drops)."""
        async with self._lock:
            self._channels[channel].discard(websocket)
            if not self._channels[channel]:
                self._channels.pop(channel, None)

    async def broadcast(self, channel: str, message: dict) -> None:
        """Send a JSON message to every socket subscribed to a channel.

        Dead sockets are collected and pruned so a single broken client never
        blocks delivery to the others.
        """
        async with self._lock:
            sockets = list(self._channels.get(channel, set()))

        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                # Client likely disconnected mid-send; mark for cleanup.
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._channels.get(channel, set()).discard(ws)


# Single shared instance imported by routers that need to publish/subscribe.
manager = ConnectionManager()


def order_channel(tracking_token: str) -> str:
    """Build the channel name for a specific order's tracking stream."""
    return f"order:{tracking_token}"


# Channel used for fleet-wide dispatch/GPS updates (the POS live map).
DISPATCH_CHANNEL = "dispatch"
