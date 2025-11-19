from __future__ import annotations

import queue
import socket
import threading
import time

import pytest

from neurotalk.config import NetworkConfig, SessionConfig
from neurotalk.control import SYNC_REQUEST, SyncTimestamp
from neurotalk.network import SocketBundle
from neurotalk.session import ConversationSession


def test_sync_start_roundtrip():
    """`ConversationSession.sync_start` should negotiate a shared start timestamp."""

    # Prepare paired UDP sockets.
    control_local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    control_remote = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    control_local.bind(("127.0.0.1", 0))
    control_remote.bind(("127.0.0.1", 0))

    # Inbound/outbound sockets are unused in this test but create them to satisfy the bundle.
    inbound_local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    outbound_local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inbound_local.bind(("127.0.0.1", 0))
    outbound_local.bind(("127.0.0.1", 0))

    bundle = SocketBundle(
        inbound=inbound_local,
        outbound=outbound_local,
        control=control_local,
        remote=(
            "127.0.0.1",
            inbound_local.getsockname()[1],
            outbound_local.getsockname()[1],
            control_remote.getsockname()[1],
        ),
    )

    # Setup session with the preconstructed sockets (skip connect()).
    config = SessionConfig(
        participant_id="001", role="A", network=NetworkConfig(punch_timeout_s=5.0)
    )
    session = ConversationSession(config)
    session.state.sockets = bundle
    session._start_control_loop()

    # Remote responder thread.
    received_sync_requests: queue.Queue[float] = queue.Queue()
    barrier = threading.Event()

    def responder():
        while not barrier.is_set():
            try:
                data, addr = control_remote.recvfrom(1024)
            except TimeoutError:
                continue
            if data == SYNC_REQUEST:
                received_sync_requests.put(time.time())
                control_remote.sendto(SYNC_REQUEST, addr)
                # respond with timestamp shortly after
                ts = SyncTimestamp(time.time())
                control_remote.sendto(ts.pack(), addr)
                return

    control_remote.settimeout(0.1)
    thread = threading.Thread(target=responder, daemon=True)
    thread.start()

    try:
        delay = 10.0  # seconds
        agreed = session.sync_start(delay_seconds=delay)
    finally:
        barrier.set()
        thread.join(timeout=1.0)
        session._stop_control_loop()
        control_local.close()
        control_remote.close()
        inbound_local.close()
        outbound_local.close()

    assert session.state.start_time_common is not None
    assert session.state.start_time_common == pytest.approx(agreed)
    # Ensure the agreed time is at least delay seconds in the future from when remote sent response.
    partner_time = received_sync_requests.get()
    assert agreed >= partner_time + delay - 0.1
