from __future__ import annotations

import contextlib
import socket
import threading
import time

import pytest

from neurotalk.audio import AudioPacket
from neurotalk.config import NetworkConfig, SessionConfig
from neurotalk.control import HEARTBEAT
from neurotalk.network import SocketBundle
from neurotalk.session import (
    ConversationSession,
    SessionFault,
    SessionFaultError,
    SessionFaultSource,
)


def make_bundle() -> tuple[SocketBundle, tuple[socket.socket, socket.socket, socket.socket]]:
    inbound_local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    outbound_local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    control_local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inbound_remote = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    outbound_remote = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    control_remote = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    for sock in (
        inbound_local,
        outbound_local,
        control_local,
        inbound_remote,
        outbound_remote,
        control_remote,
    ):
        sock.bind(("127.0.0.1", 0))

    for sock in (inbound_local, outbound_local, control_local):
        sock.settimeout(0.1)

    bundle = SocketBundle(
        inbound=inbound_local,
        outbound=outbound_local,
        control=control_local,
        remote=(
            "127.0.0.1",
            inbound_remote.getsockname()[1],
            outbound_remote.getsockname()[1],
            control_remote.getsockname()[1],
        ),
    )
    return bundle, (inbound_remote, outbound_remote, control_remote)


def wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("Timed out waiting for condition")


def close_sockets(*sockets: socket.socket) -> None:
    for sock in sockets:
        with contextlib.suppress(OSError):
            sock.close()


def test_raise_if_faulted_raises_fault_error() -> None:
    session = ConversationSession(SessionConfig())
    fault = SessionFault(
        source=SessionFaultSource.PEER_TIMEOUT,
        message="peer timed out",
        timestamp=time.time(),
    )
    session.state.fault = fault

    with pytest.raises(SessionFaultError) as exc_info:
        session.raise_if_faulted()

    assert exc_info.value.fault == fault


def test_next_control_event_raises_fault_instead_of_queue_empty() -> None:
    session = ConversationSession(SessionConfig())
    session.state.fault = SessionFault(
        source=SessionFaultSource.PEER_TIMEOUT,
        message="peer timed out",
        timestamp=time.time(),
    )

    with pytest.raises(SessionFaultError):
        session.next_control_event(timeout=0.0)


def test_record_fault_keeps_first_fault() -> None:
    session = ConversationSession(SessionConfig())
    first = RuntimeError("first fault")
    second = RuntimeError("second fault")

    session._record_fault(SessionFaultSource.AUDIO_SEND, "first message", first)
    session._record_fault(SessionFaultSource.CONTROL_RECEIVE, "second message", second)

    fault = session.get_fault()
    assert fault is not None
    assert fault.source is SessionFaultSource.AUDIO_SEND
    assert fault.message == "first message"
    assert fault.cause is first


def test_control_loop_consumes_heartbeat_without_queueing() -> None:
    handled: list[tuple[object, object | None]] = []
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(
        SessionConfig(network=NetworkConfig(peer_timeout_s=5.0)),
        control_handler=lambda t, p: handled.append((t, p)),
    )
    session.state.sockets = bundle
    session._start_control_loop()

    try:
        remote_sockets[2].sendto(HEARTBEAT, bundle.control.getsockname())
        wait_until(lambda: session.state.last_peer_activity_monotonic is not None)
        assert session._control_queue.empty()
        assert handled == []
    finally:
        session._stop_control_loop()
        close_sockets(
            bundle.inbound,
            bundle.outbound,
            bundle.control,
            *remote_sockets,
        )


def test_control_loop_records_socket_fault() -> None:
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(SessionConfig())
    session.state.sockets = bundle
    session._start_control_loop()

    try:
        bundle.control.close()
        wait_until(lambda: session.get_fault() is not None)
        fault = session.get_fault()
        assert fault is not None
        assert fault.source is SessionFaultSource.CONTROL_RECEIVE
    finally:
        session._stop_control_loop()
        close_sockets(bundle.inbound, bundle.outbound, *remote_sockets)


def test_close_does_not_report_spurious_faults() -> None:
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(SessionConfig())
    session.state.sockets = bundle
    session._start_control_loop()

    try:
        session.close()
        assert session.get_fault() is None
    finally:
        close_sockets(*remote_sockets)


def test_heartbeat_timeout_sets_fault() -> None:
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(
        SessionConfig(network=NetworkConfig(peer_timeout_s=0.2))
    )
    session.state.sockets = bundle
    session._record_peer_activity()
    session._start_health_monitor()

    try:
        wait_until(lambda: session.get_fault() is not None, timeout=1.0)
        fault = session.get_fault()
        assert fault is not None
        assert fault.source is SessionFaultSource.PEER_TIMEOUT
    finally:
        session.close()
        close_sockets(*remote_sockets)


def test_handle_outbound_packet_records_fault_on_send_error() -> None:
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(SessionConfig())
    session.state.sockets = bundle
    bundle.outbound.close()

    try:
        session._handle_outbound_packet(
            AudioPacket(pcm=b"\x00\x00", counter=1, timestamp=time.time())
        )
        fault = session.get_fault()
        assert fault is not None
        assert fault.source is SessionFaultSource.AUDIO_SEND
    finally:
        close_sockets(bundle.inbound, bundle.control, *remote_sockets)


def test_receive_audio_loop_records_fault_on_socket_error() -> None:
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(SessionConfig())
    session.state.sockets = bundle
    session.state.receiver_running = threading.Event()
    session.state.receiver_running.set()
    bundle.inbound.close()

    try:
        session._receive_audio_loop()
        fault = session.get_fault()
        assert fault is not None
        assert fault.source is SessionFaultSource.AUDIO_RECEIVE
    finally:
        close_sockets(bundle.outbound, bundle.control, *remote_sockets)
