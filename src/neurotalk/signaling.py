from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping

import contextlib
import websockets
from websockets.client import WebSocketClientProtocol
from websockets.server import WebSocketServer, WebSocketServerProtocol

from .config import SessionConfig
from .exceptions import NeuroTalkError


@dataclass(slots=True, frozen=True)
class SignalingMessage:
    """Message exchanged via the signaling channel."""

    type: str
    payload: Mapping[str, Any]
    sender: str | None = None

    def to_json(self, room: str, peer_id: str) -> str:
        return json.dumps(
            {
                "type": self.type,
                "payload": list(self.payload.items()),
                "sender": self.sender or peer_id,
                "room": room,
            },
        )

    @classmethod
    def from_json(cls, message: str) -> "SignalingMessage":
        data = json.loads(message)
        payload = dict(data.get("payload", ()))
        return cls(type=data["type"], payload=payload, sender=data.get("sender"))


class BaseSignaling:
    """Minimal signaling interface."""

    async def connect(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def send(self, message: SignalingMessage) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def receive(self) -> SignalingMessage:  # pragma: no cover - interface
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class SignalingClient(BaseSignaling):
    """WebSocket signaling client that connects to the NeuroTalk signaling service."""

    def __init__(self, config: SessionConfig) -> None:
        self._config = config
        self._connection: WebSocketClientProtocol | None = None
        self._receive_queue: asyncio.Queue[SignalingMessage] = asyncio.Queue()
        self._listener: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        signaling = self._config.signaling
        ssl_context: ssl.SSLContext | None
        if signaling.url.startswith("wss://") and signaling.verify_tls:
            ssl_context = ssl.create_default_context()
        elif signaling.url.startswith("wss://") and not signaling.verify_tls:
            ssl_context = ssl._create_unverified_context()
        else:
            ssl_context = None

        headers = dict(signaling.headers)
        if signaling.token:
            headers.setdefault("Authorization", f"Bearer {signaling.token}")

        self._connection = await websockets.connect(
            signaling.url,
            extra_headers=headers,
            ssl=ssl_context,
            open_timeout=5,
        )

        join_payload = {
            "type": "join",
            "room": signaling.room,
            "peer_id": self._config.peer_id,
        }
        await self._connection.send(json.dumps(join_payload))
        join_response = SignalingMessage.from_json(await self._connection.recv())
        if join_response.type != "join-ack":
            raise NeuroTalkError("Failed to join signaling room.")

        self._listener = asyncio.create_task(self._listen())

    async def send(self, message: SignalingMessage) -> None:
        if not self._connection:
            raise NeuroTalkError("Signaling connection is not initialised.")
        await self._connection.send(message.to_json(self._config.signaling.room, self._config.peer_id))

    async def receive(self) -> SignalingMessage:
        return await self._receive_queue.get()

    async def close(self) -> None:
        if self._listener:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None

        if self._connection:
            await self._connection.close()
            self._connection = None

    async def _listen(self) -> None:
        assert self._connection is not None
        try:
            async for raw_message in self._connection:
                message = SignalingMessage.from_json(raw_message)
                await self._receive_queue.put(message)
        except websockets.ConnectionClosedOK:
            pass
        except websockets.ConnectionClosedError:
            pass


class SignalingServer:
    """Pre-built signaling service implementation."""

    def __init__(self) -> None:
        self._rooms: MutableMapping[str, MutableMapping[str, WebSocketServerProtocol]] = {}
        self._lock = asyncio.Lock()

    async def serve(self, host: str = "0.0.0.0", port: int = 8765, **kwargs: Any) -> WebSocketServer:
        return await websockets.serve(self._handler, host, port, **kwargs)

    async def _handler(self, websocket: WebSocketServerProtocol) -> None:
        join_raw = await websocket.recv()
        join_data = json.loads(join_raw)
        if join_data.get("type") != "join":
            await websocket.close(code=4000, reason="Expected join message.")
            return

        room = join_data.get("room")
        peer_id = join_data.get("peer_id")
        if not room or not peer_id:
            await websocket.close(code=4001, reason="Join message missing room or peer_id.")
            return

        async with self._lock:
            room_members = self._rooms.setdefault(room, {})
            room_members[peer_id] = websocket

        await websocket.send(json.dumps({"type": "join-ack", "payload": [], "sender": None, "room": room}))

        try:
            async for raw_message in websocket:
                message = SignalingMessage.from_json(raw_message)
                await self._broadcast(room, peer_id, message)
        finally:
            async with self._lock:
                members = self._rooms.get(room, {})
                members.pop(peer_id, None)
                if not members:
                    self._rooms.pop(room, None)

    async def _broadcast(self, room: str, sender_id: str, message: SignalingMessage) -> None:
        async with self._lock:
            members = list(self._rooms.get(room, {}).items())

        broadcast_payload = message.to_json(room, sender_id)
        for peer_id, socket in members:
            if peer_id == sender_id:
                continue
            try:
                await socket.send(broadcast_payload)
            except websockets.ConnectionClosed:
                continue
