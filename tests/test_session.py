from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio

from neurotalk import EventHandlers, Session, SessionConfig, SessionState, SessionStateError, SignalingConfig, SignalingServer


@pytest_asyncio.fixture
async def signaling_server() -> tuple[SignalingServer, int]:
    server = SignalingServer()
    ws_server = await server.serve("127.0.0.1", 0)
    port = ws_server.sockets[0].getsockname()[1]
    try:
        yield server, port
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.fixture
def base_config(signaling_server: tuple[SignalingServer, int]) -> SessionConfig:
    _, port = signaling_server
    return SessionConfig(
        peer_id="dyad01-A",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room1"),
        initiator=True,
        stun_servers=(),
    )


@pytest.mark.asyncio
async def test_session_lifecycle(signaling_server: tuple[SignalingServer, int]) -> None:
    _, port = signaling_server
    config_a = SessionConfig(
        peer_id="dyad01-A",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room1"),
        initiator=True,
        stun_servers=(),
    )
    config_b = SessionConfig(
        peer_id="dyad01-B",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room1"),
        initiator=False,
        stun_servers=(),
    )

    transitions: list[SessionState] = []
    handlers = EventHandlers(on_state_change=lambda state: transitions.append(state))
    session_a = Session(config_a, handlers)
    session_b = Session(config_b)

    assert session_a.state is SessionState.IDLE
    await session_a.connect()
    await session_b.connect()
    assert session_a.state is SessionState.CONNECTED
    await session_a.wait_transport_ready()
    await session_b.wait_transport_ready()

    await asyncio.gather(session_a.sync_start(delay_seconds=0.0), session_b.sync_start(delay_seconds=0.0))
    assert session_a.state is SessionState.READY
    assert session_b.state is SessionState.READY

    await asyncio.gather(session_a.close(), session_b.close())
    await asyncio.gather(session_a.wait_closed(), session_b.wait_closed())
    assert session_a.state is SessionState.CLOSED
    assert session_a.closed
    assert SessionState.CONNECTED in transitions
    assert SessionState.READY in transitions
    assert transitions[-1] is SessionState.CLOSED


@pytest.mark.asyncio
async def test_connect_twice_raises(base_config: SessionConfig) -> None:
    session = Session(base_config)
    await session.connect()
    with pytest.raises(SessionStateError):
        await session.connect()
    await session.close()


@pytest.mark.asyncio
async def test_sync_start_negative_duration(base_config: SessionConfig) -> None:
    session = Session(base_config)
    await session.connect()
    with pytest.raises(ValueError):
        await session.sync_start(-1.0)
    await session.close()


@pytest.mark.asyncio
async def test_handle_control_invokes_callback(base_config: SessionConfig) -> None:
    received: list[dict[str, Any]] = []
    handlers = EventHandlers(on_control=lambda payload: received.append(dict(payload)))
    session = Session(base_config, handlers=handlers)
    await session.handle_control({"topic": "turn", "value": "A"})
    assert received == [{"topic": "turn", "value": "A"}]


@pytest.mark.asyncio
async def test_control_message_roundtrip(signaling_server: tuple[SignalingServer, int]) -> None:
    _, port = signaling_server
    received: list[dict[str, Any]] = []
    event = asyncio.Event()

    def on_control(payload: Any) -> None:
        received.append(dict(payload))
        event.set()

    config_a = SessionConfig(
        peer_id="dyad01-A",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room1"),
        initiator=True,
        stun_servers=(),
    )
    config_b = SessionConfig(
        peer_id="dyad01-B",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room1"),
        initiator=False,
        stun_servers=(),
    )

    session_a = Session(config_a)
    session_b = Session(config_b, handlers=EventHandlers(on_control=on_control))

    await session_a.connect()
    await session_b.connect()
    await session_a.wait_transport_ready()
    await session_b.wait_transport_ready()

    await session_a.send_control({"topic": "turn", "value": "B"})
    await asyncio.wait_for(event.wait(), timeout=2)

    assert received == [{"topic": "turn", "value": "B"}]

    await session_a.close()
    await session_b.close()


@pytest.mark.asyncio
async def test_webrtc_connection_established(signaling_server: tuple[SignalingServer, int]) -> None:
    _, port = signaling_server
    config_a = SessionConfig(
        peer_id="dyad01-A",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room-webrtc"),
        initiator=True,
        stun_servers=(),
    )
    config_b = SessionConfig(
        peer_id="dyad01-B",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room-webrtc"),
        initiator=False,
        stun_servers=(),
    )
    session_a = Session(config_a)
    session_b = Session(config_b)

    await asyncio.gather(session_a.connect(), session_b.connect())
    await session_a.wait_transport_ready()
    await session_b.wait_transport_ready()

    assert session_a.state is SessionState.CONNECTED
    assert session_b.state is SessionState.CONNECTED

    await asyncio.gather(session_a.close(), session_b.close())


@pytest.mark.asyncio
async def test_close_is_idempotent(base_config: SessionConfig) -> None:
    session = Session(base_config)
    await session.close()
    await session.close()
    assert session.closed


@pytest.mark.asyncio
async def test_sync_start_concurrent(signaling_server: tuple[SignalingServer, int]) -> None:
    _, port = signaling_server
    config_a = SessionConfig(
        peer_id="dyad01-A",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room-sync"),
        initiator=True,
        stun_servers=(),
    )
    config_b = SessionConfig(
        peer_id="dyad01-B",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room-sync"),
        initiator=False,
        stun_servers=(),
    )

    session_a = Session(config_a)
    session_b = Session(config_b)

    await asyncio.gather(session_a.connect(), session_b.connect())
    await asyncio.wait_for(
        asyncio.gather(session_a.sync_start(0.0), session_b.sync_start(0.0)),
        timeout=5,
    )
    assert session_a.state is SessionState.READY
    assert session_b.state is SessionState.READY
    await asyncio.gather(session_a.close(), session_b.close())


@pytest.mark.asyncio
async def test_sync_start_late_joiner(signaling_server: tuple[SignalingServer, int]) -> None:
    _, port = signaling_server
    config_a = SessionConfig(
        peer_id="dyad01-A",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room-late"),
        initiator=True,
        stun_servers=(),
    )
    config_b = SessionConfig(
        peer_id="dyad01-B",
        signaling=SignalingConfig(url=f"ws://127.0.0.1:{port}", room="room-late"),
        initiator=False,
        stun_servers=(),
    )

    session_a = Session(config_a)
    session_b = Session(config_b)

    await asyncio.gather(session_a.connect(), session_b.connect())
    task_a = asyncio.create_task(session_a.sync_start(0.0))
    await asyncio.sleep(0.05)
    await session_b.sync_start(0.0)
    await asyncio.wait_for(task_a, timeout=5)
    assert session_a.state is SessionState.READY
    assert session_b.state is SessionState.READY
    await asyncio.gather(session_a.close(), session_b.close())
