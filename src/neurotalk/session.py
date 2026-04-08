"""
High-level session orchestration.

`ConversationSession` coordinates the network sockets, audio workers, control
channel, and recording hooks exposed elsewhere in the package.
"""

from __future__ import annotations

import logging
import math
import queue
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import TracebackType

from neurotalk.audio import (
    AudioInputWorker,
    AudioOutputWorker,
    AudioPacket,
    MockStreamFactory,
    SoundDeviceStreamFactory,
    StreamFactory,
)
from neurotalk.config import NetworkConfig, SessionConfig
from neurotalk.control import (
    DEBUG_READY,
    DEBUG_STOP,
    HEARTBEAT,
    THANKS,
    TURN_TAKE_PREFIX,
    ControlMessageType,
    SyncTimestamp,
    TurnPassPayload,
    classify_payload,
)
from neurotalk.network import (
    SocketBundle,
    configure_nonblocking,
    flush_pending,
    hole_punch,
    open_sockets,
    run_stun_diagnostics,
)
from neurotalk.records import RecorderTarget, WavRecorder, mix_turn_recordings

logger = logging.getLogger(__name__)

ControlHandler = Callable[[ControlMessageType, object | None], None]
HEARTBEAT_INTERVAL_S = 1.0


class SessionFaultSource(str, Enum):
    AUDIO_INPUT = "audio_input"
    AUDIO_OUTPUT = "audio_output"
    AUDIO_SEND = "audio_send"
    AUDIO_RECEIVE = "audio_receive"
    CONTROL_RECEIVE = "control_receive"
    PEER_CLOSED = "peer_closed"
    PEER_TIMEOUT = "peer_timeout"


@dataclass(frozen=True, slots=True)
class SessionFault:
    source: SessionFaultSource
    message: str
    timestamp: float
    cause: Exception | None = None


class SessionFaultError(RuntimeError):
    """Raised when a session enters a fatal faulted state."""

    def __init__(self, fault: SessionFault):
        self.fault = fault
        super().__init__(self._build_message(fault))

    @staticmethod
    def _build_message(fault: SessionFault) -> str:
        return f"{fault.source.value}: {fault.message}"


def _ensure_logging_configured(default_level: int = logging.INFO) -> None:
    """
    Install a basic logging configuration if the application did not set one.

    Uses Rich for nicer output when available.
    """

    root = logging.getLogger()
    if root.handlers:
        return
    try:
        from rich.logging import RichHandler  # noqa: PLC0415

        logging.basicConfig(
            level=default_level,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=False, markup=True)],
        )
    except Exception:  # pragma: no cover - only hit if Rich missing/broken
        logging.basicConfig(
            level=default_level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )


def _normalize_label(label: str | None) -> str | None:
    if label is None:
        return None
    cleaned = label.strip().replace(" ", "_")
    sanitized = "".join(
        ch if ch.isalnum() or ch in "-._" else "_" for ch in cleaned
    ).strip("_-.")
    return sanitized or None


@dataclass
class SessionState:
    """Mutable state tracked across the lifetime of a session."""

    sockets: SocketBundle | None = None
    start_time_common: float | None = None
    transmit_enabled: bool = True
    receive_enabled: bool = True
    playback_gain: float = 1.0
    input_worker: AudioInputWorker | None = None
    output_worker: AudioOutputWorker | None = None
    stream_factory: StreamFactory | None = None
    receiver_thread: threading.Thread | None = None
    receiver_running: threading.Event | None = None
    local_recorder: WavRecorder | None = None
    remote_recorder: WavRecorder | None = None
    mix_path: Path | None = None
    owns_stream_factory: bool = False
    fault: SessionFault | None = None
    last_peer_activity_monotonic: float | None = None
    last_control_activity_monotonic: float | None = None
    last_audio_send_monotonic: float | None = None
    last_audio_receive_monotonic: float | None = None
    peer_warning_logged: bool = False
    inbound_audio_warning_logged: bool = False
    outbound_audio_warning_logged: bool = False
    control_packets_received: int = 0
    audio_packets_sent: int = 0
    audio_packets_received: int = 0
    last_transport_stats_log_monotonic: float | None = None
    last_logged_control_packets_received: int = 0
    last_logged_audio_packets_sent: int = 0
    last_logged_audio_packets_received: int = 0
    heartbeat_thread: threading.Thread | None = None
    heartbeat_running: threading.Event | None = None


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

    def __init__(
        self,
        config: SessionConfig,
        *,
        control_handler: ControlHandler | None = None,
        recording_enabled: bool = True,
        recording_label: str | None = None,
        stream_factory: StreamFactory | None = None,
    ):
        self.config = config
        self.state = SessionState()
        self._control_handler = control_handler
        self._control_thread: threading.Thread | None = None
        self._control_running = threading.Event()
        self._control_queue: queue.Queue[tuple[ControlMessageType, object | None]] = (
            queue.Queue()
        )
        self._stream_factory_override = stream_factory
        self._recording_enabled = recording_enabled
        self._recording_label = _normalize_label(recording_label)
        self._fault_lock = threading.Lock()
        self._closing = threading.Event()
        try:
            self.state.playback_gain = float(self.config.audio.playback_gain)
        except Exception:
            # Fallback for older configs without the field.
            self.state.playback_gain = 1.0

    # ---- context manager -------------------------------------------------
    def __enter__(self) -> ConversationSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        """Create sockets, punch through NAT, and begin control polling."""

        if self.state.sockets is not None:
            return
        _ensure_logging_configured(logging.INFO)
        self._closing.clear()
        with self._fault_lock:
            self.state.fault = None
        self.state.last_peer_activity_monotonic = None
        self.state.last_control_activity_monotonic = None
        self.state.last_audio_send_monotonic = None
        self.state.last_audio_receive_monotonic = None
        self.state.peer_warning_logged = False
        self.state.inbound_audio_warning_logged = False
        self.state.outbound_audio_warning_logged = False
        self.state.control_packets_received = 0
        self.state.audio_packets_sent = 0
        self.state.audio_packets_received = 0
        self.state.last_transport_stats_log_monotonic = None
        self.state.last_logged_control_packets_received = 0
        self.state.last_logged_audio_packets_sent = 0
        self.state.last_logged_audio_packets_received = 0

        net_cfg: NetworkConfig = self.config.network
        logger.info(
            "[network] Initializing session sockets remote_hint=%s nat_role=%s",
            net_cfg.remote_hint,
            net_cfg.nat_role,
        )
        bundle = open_sockets(net_cfg)
        try:
            if net_cfg.stun_servers:
                run_stun_diagnostics(net_cfg.stun_servers)

            remote = hole_punch(bundle, net_cfg)

            configure_nonblocking(bundle)
            flush_pending(bundle)
            logger.debug("connect resolved remote address: %s", remote)
            self.state.sockets = bundle
            self._record_peer_activity()
            self._start_control_loop()
            self._initialize_audio()
            self._start_health_monitor()
        except Exception:
            if self.state.sockets is None:
                bundle.close()
            else:
                self.close()
            raise

    def close(self) -> None:
        """Terminate control loop and close sockets."""

        self._closing.set()
        try:
            self._stop_health_monitor()
            self._stop_control_loop()
            bundle = self.state.sockets
            if bundle is not None:
                self._send_goodbye_packets(bundle)
            self._shutdown_audio()
            bundle = self.state.sockets
            if bundle is not None:
                bundle.close()
                self.state.sockets = None
        finally:
            self._closing.clear()

    # ---- control loop ----------------------------------------------------
    def _start_control_loop(self) -> None:
        if self._control_thread and self._control_thread.is_alive():
            return
        self._control_running.set()
        self._control_thread = threading.Thread(
            target=self._control_loop, name="NeuroTalkControl", daemon=True
        )
        self._control_thread.start()
        logger.debug("control loop thread started")

    def _stop_control_loop(self) -> None:
        self._control_running.clear()
        if self._control_thread:
            self._control_thread.join(timeout=1.0)
            self._control_thread = None

    def _start_health_monitor(self) -> None:
        if self.state.heartbeat_thread and self.state.heartbeat_thread.is_alive():
            return
        event = threading.Event()
        event.set()
        self.state.heartbeat_running = event
        thread = threading.Thread(
            target=self._heartbeat_loop, name="NeuroTalkHeartbeat", daemon=True
        )
        self.state.heartbeat_thread = thread
        thread.start()

    def _stop_health_monitor(self) -> None:
        event = self.state.heartbeat_running
        if event is not None:
            event.clear()
        thread = self.state.heartbeat_thread
        if thread is not None:
            thread.join(timeout=1.0)
        self.state.heartbeat_thread = None
        self.state.heartbeat_running = None

    def _control_loop(self) -> None:
        sockets = self.state.sockets
        if sockets is None:
            return
        logger.debug("control_loop entering remote=%s", sockets.remote)
        while self._control_running.is_set():
            try:
                data = sockets.control.recv(1024)
            except TimeoutError:
                continue
            except BlockingIOError:
                continue
            except OSError as exc:
                if not self._closing.is_set():
                    self._record_fault(
                        SessionFaultSource.CONTROL_RECEIVE,
                        "Control socket receive failed",
                        exc,
                    )
                break
            if not data:
                logger.debug("control_loop received empty datagram")
                continue
            logger.debug("control_loop raw len=%s data=%r", len(data), data)
            try:
                msg_type, payload = classify_payload(data)
            except ValueError:
                logger.debug(
                    "control_loop failed to classify payload len=%s", len(data)
                )
                continue
            self._record_control_activity()
            if msg_type is ControlMessageType.HEARTBEAT:
                continue
            logger.debug("control_loop received %s", msg_type)
            self._auto_respond_control(msg_type)
            self._control_queue.put((msg_type, payload))
            if self._control_handler:
                self._control_handler(msg_type, payload)
        logger.debug("control_loop exiting")

    def _auto_respond_control(self, msg_type: ControlMessageType) -> None:
        if msg_type is ControlMessageType.SYNC_REQUEST:
            sockets = self.state.sockets
            if sockets is None:
                return
            remote_ip, _, _, port_comm = sockets.remote
            timestamp = SyncTimestamp(time.time()).pack()
            try:
                sockets.control.sendto(timestamp, (remote_ip, port_comm))
            except OSError as exc:
                logger.debug("sync timestamp send failed: %s", exc, exc_info=exc)

    # ---- control helpers -------------------------------------------------
    def next_control_event(
        self, timeout: float | None = None
    ) -> tuple[ControlMessageType, object | None]:
        """
        Block until the next control event arrives (or timeout occurs).
        """

        self.raise_if_faulted()
        if not self._control_running.is_set():
            logger.debug("next_control_event called while control loop inactive")
        try:
            event = self._control_queue.get(timeout=timeout)
            logger.debug("next_control_event -> %s", event[0])
            return event
        except queue.Empty:
            if timeout not in (0, 0.0):
                logger.debug("next_control_event timeout after %s", timeout)
            self.raise_if_faulted()
            raise

    def get_fault(self) -> SessionFault | None:
        return self.state.fault

    def raise_if_faulted(self) -> None:
        fault = self.state.fault
        if fault is not None:
            raise SessionFaultError(fault)

    def send_turn_pass(self, payload: TurnPassPayload) -> None:
        logger.debug("send_turn_pass attempting send: %s", payload)
        sockets = self.state.sockets
        if sockets is None:
            msg = "Session not connected"
            raise RuntimeError(msg)
        remote_ip, _, _, port_comm = sockets.remote
        try:
            sockets.control.sendto(payload.pack(), (remote_ip, port_comm))
            logger.debug("send_turn_pass sent to %s:%s", remote_ip, port_comm)
        except OSError as exc:
            logger.debug("send_turn_pass send failed: %s", exc, exc_info=exc)
            raise

    def send_turn_take(self, payload: TurnPassPayload) -> None:
        logger.debug("send_turn_take attempting send: %s", payload)
        sockets = self.state.sockets
        if sockets is None:
            msg = "Session not connected"
            raise RuntimeError(msg)
        remote_ip, _, _, port_comm = sockets.remote
        try:
            sockets.control.sendto(
                payload.pack(prefix=TURN_TAKE_PREFIX), (remote_ip, port_comm)
            )
            logger.debug("send_turn_take sent to %s:%s", remote_ip, port_comm)
        except OSError as exc:
            logger.debug("send_turn_take send failed: %s", exc, exc_info=exc)
            raise

    def pass_turn(
        self,
        *,
        run_time: float,
        phase_time: float,
        wall_time: float | None = None,
        turn_id: int | None = None,
    ) -> None:
        """Notify the remote peer that control has been passed to them."""

        now = wall_time if wall_time is not None else time.time()
        payload = TurnPassPayload(now, run_time, phase_time, turn_id=turn_id)
        self.send_turn_pass(payload)

    def take_turn(
        self,
        *,
        run_time: float,
        phase_time: float,
        wall_time: float | None = None,
        turn_id: int | None = None,
    ) -> None:
        """Notify the remote peer that we are taking control of the turn."""

        now = wall_time if wall_time is not None else time.time()
        payload = TurnPassPayload(now, run_time, phase_time, turn_id=turn_id)
        self.send_turn_take(payload)

    def start_segment(
        self,
        label: str,
        *,
        metadata: dict[str, object] | None = None,
        target: str | tuple[str, ...] = "both",
    ) -> None:
        """Begin a labeled segment for the requested recording targets."""

        targets = self._resolve_segment_targets(target)
        logger.debug(
            "start_segment label=%s targets=%s metadata_keys=%s",
            label,
            sorted(targets),
            None if metadata is None else sorted(metadata),
        )
        if "local" in targets and self.state.local_recorder:
            self.state.local_recorder.start_segment(label, metadata=metadata)
        if "remote" in targets and self.state.remote_recorder:
            self.state.remote_recorder.start_segment(label, metadata=metadata)

    def stop_segment(self, *, target: str | tuple[str, ...] = "both") -> None:
        """End the current segment for the requested recording targets."""

        targets = self._resolve_segment_targets(target)
        logger.debug("stop_segment targets=%s", sorted(targets))
        if "local" in targets and self.state.local_recorder:
            self.state.local_recorder.stop_segment()
        if "remote" in targets and self.state.remote_recorder:
            self.state.remote_recorder.stop_segment()

    def _resolve_segment_targets(self, target: str | tuple[str, ...]) -> set[str]:
        if isinstance(target, str):
            if target == "both":
                return {"local", "remote"}
            target_set = {target}
        else:
            target_set = set(target)
        invalid = target_set.difference({"local", "remote"})
        if invalid:
            msg = f"Unknown segment target(s): {sorted(invalid)}"
            raise ValueError(msg)
        return target_set

    def export_segments(
        self,
        destination: Path | None = None,
        pattern: str = "{role}_{index:02d}_{label}.wav",
    ) -> dict[str, list[Path]]:
        """Write per-segment WAV files for local and remote recordings.

        Parameters
        ----------
        destination:
            Directory where segment files should be written. Defaults to a
            `segments` folder under the recording directory.
        pattern:
            Filename pattern. May include `{role}`, `{index}`, and `{label}`.
        """

        if destination is None:
            destination = self.config.recording.directory / "segments"
        destination.mkdir(parents=True, exist_ok=True)

        results: dict[str, list[Path]] = {}
        for role, recorder in (
            ("local", self.state.local_recorder),
            ("remote", self.state.remote_recorder),
        ):
            if recorder and recorder.segments:
                recorder.stop_segment()
                if not getattr(recorder, "_closed", False):
                    recorder.close()
                role_pattern = pattern.replace("{role}", role)
                role_dir = destination / role
                role_dir.mkdir(parents=True, exist_ok=True)
                paths = recorder.split_segments(role_dir, pattern=role_pattern)
                results[role] = paths
        return results

    def export_mix_track(self, destination: Path | None = None) -> Path | None:
        """
        Write a mixed-down WAV that alternates between speaker and listener audio.

        Returns the generated file path, or ``None`` when mixdown cannot be produced.
        """

        local = self.state.local_recorder
        remote = self.state.remote_recorder
        if local is None or remote is None:
            return None
        mix_path = destination or self.state.mix_path
        if mix_path is None:
            return None

        if not getattr(local, "_closed", False):
            local.close()
        if not getattr(remote, "_closed", False):
            remote.close()

        return mix_turn_recordings(
            destination=mix_path, local_recorder=local, remote_recorder=remote
        )

    def enable_transmit(self, enabled: bool) -> None:
        logger.debug(
            "enable_transmit %s -> %s",
            self.state.transmit_enabled,
            enabled,
        )
        self.state.transmit_enabled = enabled
        self.state.outbound_audio_warning_logged = False
        if enabled:
            self.state.last_audio_send_monotonic = time.monotonic()
        if self.state.input_worker:
            self.state.input_worker.enable_transmit(enabled)

    def enable_receive(self, enabled: bool) -> None:
        logger.debug(
            "enable_receive %s -> %s",
            self.state.receive_enabled,
            enabled,
        )
        self.state.receive_enabled = enabled
        self.state.inbound_audio_warning_logged = False
        if enabled:
            self.state.last_audio_receive_monotonic = time.monotonic()
        if self.state.output_worker:
            self.state.output_worker.enable_playback(enabled)

    def enable_recording(self, enabled: bool) -> None:
        self._recording_enabled = enabled
        if self.state.input_worker:
            self.state.input_worker.enable_recording(enabled)
        if self.state.output_worker:
            self.state.output_worker.enable_recording(enabled)

    def set_playback_gain(self, gain: float) -> None:
        if not math.isfinite(gain) or gain < 0:
            msg = f"gain must be a finite non-negative float, got {gain!r}"
            raise ValueError(msg)
        logger.info("Setting playback gain to %.3f", gain)
        self.state.playback_gain = gain
        if self.state.output_worker is not None:
            self.state.output_worker.set_gain(gain)

    def get_playback_gain(self) -> float:
        return self.state.playback_gain

    # ---- synchronization -------------------------------------------------
    def sync_start(self, delay_seconds: float) -> float:
        """
        Perform start-time negotiation and return the agreed start timestamp.
        """

        sockets = self.state.sockets
        if sockets is None:
            msg = "Session not connected"
            raise RuntimeError(msg)

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
            elif msg_type == ControlMessageType.SYNC_TIMESTAMP and isinstance(
                payload, SyncTimestamp
            ):
                partner_time = payload.value

        if partner_time is None:
            msg = "Failed to obtain partner timestamp during sync"
            raise TimeoutError(msg)

        common = max(local_time, partner_time) + delay_seconds
        self.state.start_time_common = common
        return common

    # ---- debug mode -----------------------------------------------------
    def run_debug_mode(
        self,
        *,
        ready_timeout: float = 5.0,
        duration: float | None = None,
        poll_interval: float = 0.5,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        """Coordinate a pre-experiment debug window with the remote partner.

        The method signals readiness over the control channel, waits for the partner
        to acknowledge, then blocks until either `duration` seconds elapse or a
        `DEBUG_STOP` message is received from the partner. At shutdown, a final
        `DEBUG_STOP` message is sent to ensure both sides exit cleanly.
        """

        sockets = self.state.sockets
        if sockets is None:
            msg = "Session not connected"
            raise RuntimeError(msg)

        remote_ip, _, _, port_comm = sockets.remote

        def send(token: bytes) -> None:
            sockets.control.sendto(token, (remote_ip, port_comm))

        send(DEBUG_READY)

        partner_ready = False
        partner_requested_stop = False
        deadline = time.time() + ready_timeout

        while (
            time.time() < deadline and not partner_ready and not partner_requested_stop
        ):
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
            msg = "Partner did not respond to debug ready signal"
            raise TimeoutError(msg)

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

        # Ensure the control loop remains active after debug negotiations.
        thread = self._control_thread
        if not self._control_running.is_set() or not (
            thread is not None and thread.is_alive()
        ):
            self._start_control_loop()

    # ---- audio helpers --------------------------------------------------
    def _initialize_audio(self) -> None:
        sockets = self.state.sockets
        if sockets is None:
            return

        audio_cfg = self.config.audio

        if self.state.stream_factory is not None:
            factory = self.state.stream_factory
            owns_factory = self.state.owns_stream_factory
        else:
            if self._stream_factory_override is not None:
                factory = self._stream_factory_override
                owns_factory = False
            else:
                factory = (
                    MockStreamFactory()
                    if audio_cfg.mock_devices
                    else SoundDeviceStreamFactory()
                )
                owns_factory = True
            self.state.stream_factory = factory
            self.state.owns_stream_factory = owns_factory

        local_recorder, remote_recorder = self._create_recorders()
        logger.debug(
            "recorders created local=%s remote=%s", local_recorder, remote_recorder
        )
        self.state.local_recorder = local_recorder
        self.state.remote_recorder = remote_recorder

        logger.debug("initializing audio workers")

        output_worker = AudioOutputWorker(
            audio_cfg,
            recorder=remote_recorder,
            stream_factory=factory,
            fatal_error_handler=lambda exc: self._record_fault(
                SessionFaultSource.AUDIO_OUTPUT,
                "Audio output worker failed",
                exc,
            ),
        )
        input_worker = AudioInputWorker(
            audio_cfg,
            self._handle_outbound_packet,
            recorder=local_recorder,
            stream_factory=factory,
            fatal_error_handler=lambda exc: self._record_fault(
                SessionFaultSource.AUDIO_INPUT,
                "Audio input worker failed",
                exc,
            ),
        )

        self.state.output_worker = output_worker
        self.state.input_worker = input_worker

        self.set_playback_gain(self.state.playback_gain)

        output_worker.enable_playback(self.state.receive_enabled)
        input_worker.enable_transmit(self.state.transmit_enabled)
        output_worker.enable_recording(self._recording_enabled)
        input_worker.enable_recording(self._recording_enabled)

        output_worker.start()
        input_worker.start()

        receiver_running = threading.Event()
        receiver_running.set()
        self.state.receiver_running = receiver_running
        receiver_thread = threading.Thread(
            target=self._receive_audio_loop, name="NeuroTalkAudioRecv", daemon=True
        )
        self.state.receiver_thread = receiver_thread
        receiver_thread.start()
        logger.debug("audio receiver thread launched")

    def _handle_outbound_packet(self, packet: AudioPacket) -> None:
        sockets = self.state.sockets
        if sockets is None:
            return
        payload = self._encode_packet(packet)
        remote_ip, port_in, _, _ = sockets.remote
        try:
            sockets.outbound.sendto(payload, (remote_ip, port_in))
        except OSError as exc:
            self._record_fault(
                SessionFaultSource.AUDIO_SEND,
                "Failed to send audio packet",
                exc,
            )
        else:
            self._record_outbound_audio_activity()

    def _receive_audio_loop(self) -> None:
        sockets = self.state.sockets
        if sockets is None:
            logger.debug("_receive_audio_loop started without sockets")
            return
        event = self.state.receiver_running
        if event is None:
            logger.debug("_receive_audio_loop missing event state")
            return
        while event.is_set():
            try:
                data = sockets.inbound.recv(65536)
            except (BlockingIOError, TimeoutError):
                continue
            except OSError as exc:
                if not self._closing.is_set():
                    self._record_fault(
                        SessionFaultSource.AUDIO_RECEIVE,
                        "Audio receive loop failed",
                        exc,
                    )
                break
            if not data:
                logger.debug("_receive_audio_loop got empty packet")
                continue
            if data == THANKS:
                logger.debug("_receive_audio_loop received THANKS sentinel")
                if not self._closing.is_set():
                    self._record_fault(
                        SessionFaultSource.PEER_CLOSED,
                        "Peer closed session",
                    )
                break
            self._record_peer_activity()
            packet = self._decode_packet(data)
            if packet is None:
                logger.debug(
                    "_receive_audio_loop dropped undecodable payload len=%s", len(data)
                )
                continue
            self._record_inbound_audio_activity()
            output = self.state.output_worker
            if output is not None:
                output.enqueue(packet)
            else:
                logger.debug("_receive_audio_loop missing output worker for packet")

    def _encode_packet(self, packet: AudioPacket) -> bytes:
        return (
            packet.pcm
            + struct.pack("<l", packet.counter)
            + struct.pack("<d", packet.timestamp)
        )

    def _decode_packet(self, payload: bytes) -> AudioPacket | None:
        if len(payload) < 12:
            return None
        counter = struct.unpack("<l", payload[-12:-8])[0]
        timestamp = struct.unpack("<d", payload[-8:])[0]
        pcm = payload[:-12]
        return AudioPacket(pcm=pcm, counter=counter, timestamp=timestamp)

    def _shutdown_audio(self) -> None:
        event = self.state.receiver_running
        if event is not None:
            event.clear()
        thread = self.state.receiver_thread
        if thread is not None:
            thread.join(timeout=1.0)
        self.state.receiver_thread = None
        self.state.receiver_running = None

        if self.state.input_worker is not None:
            self.state.input_worker.close()
            self.state.input_worker = None
        if self.state.output_worker is not None:
            self.state.output_worker.close()
            self.state.output_worker = None

        if self.state.stream_factory is not None:
            if self.state.owns_stream_factory:
                try:
                    self.state.stream_factory.terminate()
                except Exception as exc:
                    logger.debug(
                        "Stream factory termination failed: %s", exc, exc_info=exc
                    )
            self.state.stream_factory = None
            self.state.owns_stream_factory = False

        if self.state.local_recorder is not None:
            self.state.local_recorder.stop_segment()
            if not getattr(self.state.local_recorder, "_closed", False):
                self.state.local_recorder.close()
        if self.state.remote_recorder is not None:
            self.state.remote_recorder.stop_segment()
            if not getattr(self.state.remote_recorder, "_closed", False):
                self.state.remote_recorder.close()

    def _create_recorders(self) -> tuple[WavRecorder | None, WavRecorder | None]:
        recording_cfg = self.config.recording
        directory: Path = recording_cfg.directory
        directory.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base_parts = [self.config.participant_id, self.config.role]
        if self._recording_label:
            base_parts.append(self._recording_label)
        base_parts.append(timestamp)
        base_name = "_".join(base_parts)

        audio_cfg = self.config.audio

        local_path = recording_cfg.local_track
        if local_path is None:
            local_path = directory / f"{base_name}_local.wav"
        else:
            local_path = (
                local_path if local_path.is_absolute() else directory / local_path
            )

        remote_path = recording_cfg.remote_track
        if remote_path is None:
            remote_path = directory / f"{base_name}_remote.wav"
        else:
            remote_path = (
                remote_path if remote_path.is_absolute() else directory / remote_path
            )

        mix_path = recording_cfg.mix_track
        if mix_path is None:
            mix_path = directory / f"{base_name}_mix.wav"
        else:
            mix_path = mix_path if mix_path.is_absolute() else directory / mix_path
        self.state.mix_path = mix_path

        local_target = RecorderTarget(
            path=local_path,
            channels=audio_cfg.channels,
            sample_rate_hz=audio_cfg.sample_rate_hz,
            sample_width_bytes=2,
        )
        remote_target = RecorderTarget(
            path=remote_path,
            channels=audio_cfg.channels,
            sample_rate_hz=audio_cfg.sample_rate_hz,
            sample_width_bytes=2,
        )

        return WavRecorder(local_target), WavRecorder(remote_target)

    def _send_goodbye_packets(self, bundle: SocketBundle) -> None:
        remote_ip, port_in, _, _ = bundle.remote
        for _ in range(3):
            try:
                bundle.outbound.sendto(THANKS, (remote_ip, port_in))
            except OSError:
                break

    def _record_peer_activity(self) -> None:
        previous = self.state.last_peer_activity_monotonic
        if self.state.peer_warning_logged and previous is not None:
            silence_s = max(0.0, time.monotonic() - previous)
            logger.info(
                "Peer activity restored after %.1fs of silence", silence_s
            )
            self.state.peer_warning_logged = False
        self.state.last_peer_activity_monotonic = time.monotonic()

    def _record_control_activity(self) -> None:
        self.state.control_packets_received += 1
        self.state.last_control_activity_monotonic = time.monotonic()
        self._record_peer_activity()

    def _record_outbound_audio_activity(self) -> None:
        now = time.monotonic()
        previous = self.state.last_audio_send_monotonic
        if self.state.outbound_audio_warning_logged and previous is not None:
            logger.info(
                "Outbound audio restored after %.1fs of silence",
                max(0.0, now - previous),
            )
            self.state.outbound_audio_warning_logged = False
        self.state.audio_packets_sent += 1
        self.state.last_audio_send_monotonic = now

    def _record_inbound_audio_activity(self) -> None:
        now = time.monotonic()
        previous = self.state.last_audio_receive_monotonic
        if self.state.inbound_audio_warning_logged and previous is not None:
            logger.info(
                "Inbound audio restored after %.1fs of silence",
                max(0.0, now - previous),
            )
            self.state.inbound_audio_warning_logged = False
        self.state.audio_packets_received += 1
        self.state.last_audio_receive_monotonic = now
        self._record_peer_activity()

    def _format_activity_age(self, value: float | None, now: float) -> str:
        if value is None:
            return "n/a"
        return f"{max(0.0, now - value):.1f}s"

    def _maybe_warn_audio_path_silence(self, now: float) -> None:
        warning_timeout = self.config.network.peer_warning_s
        if warning_timeout is None:
            return

        if (
            self.state.transmit_enabled
            and not self.state.outbound_audio_warning_logged
            and self.state.last_audio_send_monotonic is not None
            and (now - self.state.last_audio_send_monotonic) > warning_timeout
        ):
            logger.warning(
                "No outbound audio packets sent for %.1fs while transmit is enabled "
                "(last control activity %s ago)",
                now - self.state.last_audio_send_monotonic,
                self._format_activity_age(
                    self.state.last_control_activity_monotonic, now
                ),
            )
            self.state.outbound_audio_warning_logged = True

        if (
            self.state.receive_enabled
            and not self.state.inbound_audio_warning_logged
            and self.state.last_audio_receive_monotonic is not None
            and (now - self.state.last_audio_receive_monotonic) > warning_timeout
        ):
            logger.warning(
                "No inbound audio packets received for %.1fs while receive is enabled "
                "(last control activity %s ago)",
                now - self.state.last_audio_receive_monotonic,
                self._format_activity_age(
                    self.state.last_control_activity_monotonic, now
                ),
            )
            self.state.inbound_audio_warning_logged = True

    def _maybe_log_transport_stats(self, now: float) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        previous = self.state.last_transport_stats_log_monotonic
        if previous is not None and (now - previous) < HEARTBEAT_INTERVAL_S:
            return
        interval = HEARTBEAT_INTERVAL_S if previous is None else max(now - previous, 1e-6)
        control_delta = (
            self.state.control_packets_received
            - self.state.last_logged_control_packets_received
        )
        audio_tx_delta = (
            self.state.audio_packets_sent - self.state.last_logged_audio_packets_sent
        )
        audio_rx_delta = (
            self.state.audio_packets_received
            - self.state.last_logged_audio_packets_received
        )
        logger.debug(
            "transport stats dt=%.1fs control_rx=%d audio_tx=%d audio_rx=%d "
            "transmit=%s receive=%s control_age=%s audio_tx_age=%s audio_rx_age=%s",
            interval,
            control_delta,
            audio_tx_delta,
            audio_rx_delta,
            self.state.transmit_enabled,
            self.state.receive_enabled,
            self._format_activity_age(self.state.last_control_activity_monotonic, now),
            self._format_activity_age(self.state.last_audio_send_monotonic, now),
            self._format_activity_age(self.state.last_audio_receive_monotonic, now),
        )
        self.state.last_transport_stats_log_monotonic = now
        self.state.last_logged_control_packets_received = (
            self.state.control_packets_received
        )
        self.state.last_logged_audio_packets_sent = self.state.audio_packets_sent
        self.state.last_logged_audio_packets_received = self.state.audio_packets_received

    def _heartbeat_loop(self) -> None:
        event = self.state.heartbeat_running
        sockets = self.state.sockets
        if event is None or sockets is None:
            return
        last_send = 0.0
        remote_ip, _, _, port_comm = sockets.remote
        while event.is_set():
            if self._closing.is_set() or self.state.fault is not None:
                break
            now = time.monotonic()
            if now - last_send >= HEARTBEAT_INTERVAL_S:
                try:
                    sockets.control.sendto(HEARTBEAT, (remote_ip, port_comm))
                except OSError as exc:
                    logger.debug("Heartbeat send failed: %s", exc, exc_info=exc)
                last_send = now
            timeout = self.config.network.peer_timeout_s
            warning_timeout = self.config.network.peer_warning_s
            last_peer = self.state.last_peer_activity_monotonic
            if (
                warning_timeout is not None
                and last_peer is not None
                and not self.state.peer_warning_logged
                and (now - last_peer) > warning_timeout
            ):
                logger.warning(
                    "No peer activity observed for %.1fs; continuing without fail-fast",
                    now - last_peer,
                )
                self.state.peer_warning_logged = True
            if (
                timeout is not None
                and last_peer is not None
                and (now - last_peer) > timeout
            ):
                self._record_fault(
                    SessionFaultSource.PEER_TIMEOUT,
                    f"No peer activity observed for {timeout:.1f}s",
                )
                break
            self._maybe_warn_audio_path_silence(now)
            self._maybe_log_transport_stats(now)
            time.sleep(0.1)

    def _record_fault(
        self,
        source: SessionFaultSource,
        message: str,
        cause: Exception | None = None,
    ) -> None:
        if self._closing.is_set():
            return
        with self._fault_lock:
            if self.state.fault is not None:
                return
            fault = SessionFault(
                source=source,
                message=message,
                timestamp=time.time(),
                cause=cause,
            )
            self.state.fault = fault
        if cause is not None:
            logger.error(
                "[session fault] %s: %s (%s)",
                source.value,
                message,
                cause,
                exc_info=(type(cause), cause, cause.__traceback__),
            )
        else:
            logger.error("[session fault] %s: %s", source.value, message)
