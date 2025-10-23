from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from neurotalk import SessionConfig, SignalingClient, SignalingConfig, SignalingMessage, SignalingServer


@pytest_asyncio.fixture
async def signaling_endpoint() -> tuple[SignalingServer, int]:
    server = SignalingServer()
    ws_server = await server.serve("127.0.0.1", 0)
    port = ws_server.sockets[0].getsockname()[1]
    try:
        yield server, port
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_signaling_broadcast(signaling_endpoint: tuple[SignalingServer, int]) -> None:
    _, port = signaling_endpoint
    config_a = SessionConfig(peer_id="A", signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room1"))
    config_b = SessionConfig(peer_id="B", signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room1"))

    client_a = SignalingClient(config_a)
    client_b = SignalingClient(config_b)

    await client_a.connect()
    await client_b.connect()

    await client_a.send(SignalingMessage(type="control", payload={"key": "value"}))

    message = await asyncio.wait_for(client_b.receive(), timeout=2)
    assert message.type == "control"
    assert message.payload == {"key": "value"}
    assert message.sender == "A"

    await client_a.close()
    await client_b.close()
