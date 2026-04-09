from __future__ import annotations

import contextlib
import logging
import queue
import socket
import threading
import time
from typing import Any, cast

import pytest

from neurotalk.audio import AudioPacket
from neurotalk.config import NetworkConfig, SessionConfig
from neurotalk.control import HEARTBEAT, THANKS
from neurotalk.network import SocketBundle
from neurotalk.session import (
    AUDIO_KEEPALIVE_COUNTER,
    ConversationSession,
    SessionFault,
    SessionFaultError,
    SessionFaultSource,
)


def make_bundle() -> tuple[
    SocketBundle, tuple[socket.socket, socket.socket, socket.socket]
]:
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


def test_next_control_event_zero_timeout_does_not_log_queue_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = ConversationSession(SessionConfig())
    session._control_running.set()

    with (
        caplog.at_level(logging.DEBUG, logger="neurotalk.session"),
        pytest.raises(queue.Empty),
    ):
        session.next_control_event(timeout=0.0)

    assert "next_control_event timeout after 0.0" not in caplog.text


def test_next_control_event_nonzero_timeout_logs_queue_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = ConversationSession(SessionConfig())
    session._control_running.set()

    with (
        caplog.at_level(logging.DEBUG, logger="neurotalk.session"),
        pytest.raises(queue.Empty),
    ):
        session.next_control_event(timeout=0.1)

    assert "next_control_event timeout after 0.1" in caplog.text


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


def test_heartbeat_warning_logs_without_fault(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(
        SessionConfig(network=NetworkConfig(peer_timeout_s=None, peer_warning_s=0.2))
    )
    session.state.sockets = bundle
    session._record_peer_activity()

    try:
        with caplog.at_level(logging.WARNING, logger="neurotalk.session"):
            session._start_health_monitor()
            wait_until(
                lambda: "continuing without fail-fast" in caplog.text,
                timeout=1.0,
            )
        assert session.get_fault() is None
    finally:
        session.close()
        close_sockets(*remote_sockets)


def test_peer_warning_logs_recovery_once_activity_returns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(
        SessionConfig(network=NetworkConfig(peer_timeout_s=None, peer_warning_s=0.2))
    )
    session.state.sockets = bundle
    session._record_peer_activity()

    try:
        with caplog.at_level(logging.INFO, logger="neurotalk.session"):
            session._start_health_monitor()
            wait_until(
                lambda: "continuing without fail-fast" in caplog.text,
                timeout=1.0,
            )
            session._record_peer_activity()
            wait_until(
                lambda: "Peer activity restored after" in caplog.text,
                timeout=1.0,
            )
        assert session.get_fault() is None
    finally:
        session.close()
        close_sockets(*remote_sockets)


def test_control_activity_updates_control_timestamp_and_counter() -> None:
    session = ConversationSession(SessionConfig())

    before = time.monotonic()
    session._record_control_activity()

    assert session.state.control_packets_received == 1
    assert session.state.last_control_activity_monotonic is not None
    assert session.state.last_control_activity_monotonic >= before
    assert session.state.last_peer_activity_monotonic is not None


def test_audio_silence_warning_logs_when_receive_enabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = ConversationSession(
        SessionConfig(network=NetworkConfig(peer_timeout_s=None, peer_warning_s=0.2))
    )
    now = time.monotonic()
    session.state.receive_enabled = True
    session.state.last_audio_receive_monotonic = now - 0.3
    session.state.last_control_activity_monotonic = now - 0.05

    with caplog.at_level(logging.WARNING, logger="neurotalk.session"):
        session._maybe_warn_audio_path_silence(now)

    assert "No inbound audio packets received" in caplog.text
    assert "last control activity 0.1s ago" in caplog.text
    assert session.state.inbound_audio_warning_logged is True


def test_inbound_audio_restore_logs_after_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = ConversationSession(SessionConfig())
    session.state.inbound_audio_warning_logged = True
    session.state.last_audio_receive_monotonic = time.monotonic() - 0.4

    with caplog.at_level(logging.INFO, logger="neurotalk.session"):
        session._record_inbound_audio_activity()

    assert "Inbound audio restored after" in caplog.text
    assert session.state.audio_packets_received == 1
    assert session.state.inbound_audio_warning_logged is False


def test_transport_stats_debug_logs_packet_deltas(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = ConversationSession(SessionConfig())
    now = time.monotonic()
    session.state.control_packets_received = 5
    session.state.audio_packets_sent = 7
    session.state.audio_packets_received = 3
    session.state.last_control_activity_monotonic = now - 0.2
    session.state.last_audio_send_monotonic = now - 0.4
    session.state.last_audio_receive_monotonic = now - 0.6

    with caplog.at_level(logging.DEBUG, logger="neurotalk.session"):
        session._maybe_log_transport_stats(now)

    assert "transport stats" in caplog.text
    assert "control_rx=5" in caplog.text
    assert "audio_tx=7" in caplog.text
    assert "audio_rx=3" in caplog.text


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


def test_receive_audio_loop_records_peer_closed_on_thanks() -> None:
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(SessionConfig())
    session.state.sockets = bundle
    session.state.receiver_running = threading.Event()
    session.state.receiver_running.set()

    receiver = threading.Thread(target=session._receive_audio_loop, daemon=True)
    receiver.start()

    try:
        remote_sockets[1].sendto(THANKS, bundle.inbound.getsockname())
        wait_until(lambda: session.get_fault() is not None)
        fault = session.get_fault()
        assert fault is not None
        assert fault.source is SessionFaultSource.PEER_CLOSED
    finally:
        session.state.receiver_running.clear()
        receiver.join(timeout=1.0)
        close_sockets(bundle.inbound, bundle.outbound, bundle.control, *remote_sockets)


def test_heartbeat_loop_sends_audio_keepalive_when_transmit_disabled() -> None:
    bundle, remote_sockets = make_bundle()
    session = ConversationSession(SessionConfig())
    session.state.sockets = bundle
    session.state.transmit_enabled = False
    remote_sockets[0].settimeout(1.5)

    try:
        session._start_health_monitor()
        payload = remote_sockets[0].recv(65536)
        packet = session._decode_packet(payload)
        assert packet is not None
        assert packet.counter == AUDIO_KEEPALIVE_COUNTER
        assert packet.pcm == b"\x00" * (
            session.config.audio.chunk_frames * session.config.audio.channels * 2
        )
    finally:
        session.close()
        close_sockets(*remote_sockets)


def test_receive_audio_loop_drops_keepalive_packets_from_output() -> None:
    class DummyOutputWorker:
        def __init__(self) -> None:
            self.packets: list[AudioPacket] = []

        def enqueue(self, packet: AudioPacket) -> None:
            self.packets.append(packet)

    bundle, remote_sockets = make_bundle()
    session = ConversationSession(SessionConfig())
    session.state.sockets = bundle
    output_worker = DummyOutputWorker()
    session.state.output_worker = cast(Any, output_worker)
    session.state.receiver_running = threading.Event()
    session.state.receiver_running.set()

    receiver = threading.Thread(target=session._receive_audio_loop, daemon=True)
    receiver.start()

    try:
        keepalive = session._make_audio_keepalive_packet()
        remote_sockets[1].sendto(
            session._encode_packet(keepalive), bundle.inbound.getsockname()
        )
        wait_until(lambda: session.state.audio_packets_received == 1)
        assert output_worker.packets == []
    finally:
        session.state.receiver_running.clear()
        receiver.join(timeout=1.0)
        close_sockets(bundle.inbound, bundle.outbound, bundle.control, *remote_sockets)
