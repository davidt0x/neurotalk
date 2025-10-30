"""
High-level session orchestration.

`ConversationSession` coordinates the network sockets, audio workers, control
channel, and recording hooks exposed elsewhere in the package.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .config import SessionConfig
from .control import (
    ControlMessageType,
    TurnPassPayload,
    DEBUG_READY,
    DEBUG_STOP,
    classify_payload,
)
from .network import NetworkConfig, SocketBundle, configure_nonblocking, hole_punch, open_sockets


ControlHandler = Callable[[ControlMessageType, object | None], None]


@dataclass
class SessionState:
    """Mutable state tracked across the lifetime of a session."""

    sockets: Optional[SocketBundle] = None
    start_time_common: Optional[float] = None
    transmit_enabled: bool = True
    receive_enabled: bool = True


class ConversationSession:
    """
    Entry point for experiments to manage a NeuroTalk link.

    Typical usage::

        config = SessionConfig(participant_id="011", role="A")
        with ConversationSession(config) as session:
            session.connect()
            session.sync_start(delay_seconds=12.0)
            session.enable_transmit(True)

    The concrete audio transport will be integrated in a later milestone; the
    current class focuses on network and control scaffolding.
    """

    def __init__(self, config: SessionConfig, *, control_handler: Optional[ControlHandler] = None):
        self.config = config
        self.state = SessionState()
        self._control_handler = control_handler
        self._control_thread: Optional[threading.Thread] = None
        self._control_running = threading.Event()
        self._control_queue: "queue.Queue[tuple[ControlMessageType, object | None]]" = queue.Queue()

    # ---- context manager -------------------------------------------------
    def __enter__(self) -> "ConversationSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        """Create sockets, punch through NAT, and begin control polling."""

        if self.state.sockets:
            return

        net_cfg: NetworkConfig = self.config.network
        bundle = open_sockets(net_cfg)
        if net_cfg.stun_servers:
            from .network import run_stun_diagnostics

            run_stun_diagnostics(net_cfg.stun_servers)

        remote = hole_punch(bundle, net_cfg)
        from .network import flush_pending

        configure_nonblocking(bundle)
        flush_pending(bundle)
        self.state.sockets = bundle
        self._start_control_loop()
        # TODO: initialize audio workers

    def close(self) -> None:
        """Terminate control loop and close sockets."""

        self._stop_control_loop()
        bundle = self.state.sockets
        if bundle is not None:
            bundle.close()
            self.state.sockets = None
        # TODO: tear down audio resources

    # ---- control loop ----------------------------------------------------
    def _start_control_loop(self) -> None:
        if self._control_thread and self._control_thread.is_alive():
            return
        self._control_running.set()
        self._control_thread = threading.Thread(target=self._control_loop, name="NeuroTalkControl", daemon=True)
        self._control_thread.start()

    def _stop_control_loop(self) -> None:
        self._control_running.clear()
        if self._control_thread:
            self._control_thread.join(timeout=1.0)
            self._control_thread = None

    def _control_loop(self) -> None:
        sockets = self.state.sockets
        if sockets is None:
            return
        while self._control_running.is_set():
            try:
                data = sockets.control.recv(1024)
            except OSError:
                break
            except BlockingIOError:
                continue
            except TimeoutError:
                continue
            if not data:
                continue
            try:
                msg_type, payload = classify_payload(data)
            except ValueError:
                continue
            self._control_queue.put((msg_type, payload))
            if self._control_handler:
                self._control_handler(msg_type, payload)

    # ---- control helpers -------------------------------------------------
    def next_control_event(self, timeout: Optional[float] = None) -> tuple[ControlMessageType, object | None]:
        """
        Block until the next control event arrives (or timeout occurs).
        """

        return self._control_queue.get(timeout=timeout)

    def send_turn_pass(self, payload: TurnPassPayload) -> None:
        sockets = self.state.sockets
        if not sockets:
            raise RuntimeError("Session not connected")
        remote_ip, port_in, port_out, port_comm = sockets.remote
        sockets.control.sendto(payload.pack(), (remote_ip, port_comm))

    def enable_transmit(self, enabled: bool) -> None:
        self.state.transmit_enabled = enabled
        # TODO: toggle microphone stream without stopping recording

    def enable_receive(self, enabled: bool) -> None:
        self.state.receive_enabled = enabled
        # TODO: toggle speaker stream while still recording incoming packets

    # ---- synchronization -------------------------------------------------
    def sync_start(self, delay_seconds: float) -> float:
        """
        Perform start-time negotiation and return the agreed start timestamp.
        """

        sockets = self.state.sockets
        if not sockets:
            raise RuntimeError("Session not connected")

        remote_ip, _, _, port_comm = sockets.remote
        deadline = time.time() + self.config.network.punch_timeout_s
        local_time = time.time()
        sockets.control.sendto(b"syncTimeNow", (remote_ip, port_comm))
        partner_time = None

        while time.time() < deadline and partner_time is None:
            try:
                msg_type, payload = self.next_control_event(timeout=0.1)
            except queue.Empty:
                sockets.control.sendto(b"syncTimeNow", (remote_ip, port_comm))
                continue
            if msg_type == ControlMessageType.SYNC_REQUEST:
                sockets.control.sendto(b"syncTimeNow", (remote_ip, port_comm))
            elif msg_type == ControlMessageType.SYNC_TIMESTAMP:
                partner_time = payload.value  # type: ignore[assignment]

        if partner_time is None:
            raise TimeoutError("Failed to obtain partner timestamp during sync")

        common = max(local_time, partner_time) + delay_seconds
        self.state.start_time_common = common
        return common

    # ---- debug mode -----------------------------------------------------
    def run_debug_mode(
        self,
        *,
        ready_timeout: float = 5.0,
        duration: Optional[float] = None,
        poll_interval: float = 0.5,
        on_ready: Optional[Callable[[], None]] = None,
    ) -> None:
        """Coordinate a pre-experiment debug window with the remote partner.

        The method signals readiness over the control channel, waits for the partner
        to acknowledge, then blocks until either `duration` seconds elapse or a
        `DEBUG_STOP` message is received from the partner. At shutdown, a final
        `DEBUG_STOP` message is sent to ensure both sides exit cleanly.
        """

        sockets = self.state.sockets
        if sockets is None:
            raise RuntimeError("Session not connected")

        remote_ip, _, _, port_comm = sockets.remote

        def send(token: bytes) -> None:
            sockets.control.sendto(token, (remote_ip, port_comm))

        send(DEBUG_READY)

        partner_ready = False
        partner_requested_stop = False
        deadline = time.time() + ready_timeout

        while time.time() < deadline and not partner_ready and not partner_requested_stop:
            try:
                msg_type, _ = self.next_control_event(timeout=poll_interval)
            except queue.Empty:
                send(DEBUG_READY)
                continue
            if msg_type == ControlMessageType.DEBUG_READY:
                partner_ready = True
            elif msg_type == ControlMessageType.DEBUG_STOP:
                partner_requested_stop = True

        if partner_requested_stop:
            send(DEBUG_STOP)
            return

        if not partner_ready:
            raise TimeoutError("Partner did not respond to debug ready signal")

        if on_ready:
            on_ready()

        if duration is not None and duration > 0:
            end_time = time.time() + duration
            while time.time() < end_time and not partner_requested_stop:
                try:
                    msg_type, _ = self.next_control_event(timeout=poll_interval)
                except queue.Empty:
                    continue
                if msg_type == ControlMessageType.DEBUG_STOP:
                    partner_requested_stop = True

        send(DEBUG_STOP)

        if not partner_requested_stop:
            stop_deadline = time.time() + ready_timeout
            while time.time() < stop_deadline:
                try:
                    msg_type, _ = self.next_control_event(timeout=poll_interval)
                except queue.Empty:
                    continue
                if msg_type == ControlMessageType.DEBUG_STOP:
                    break
