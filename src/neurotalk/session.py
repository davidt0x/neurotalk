"""
High-level session orchestration.

`ConversationSession` coordinates the network sockets, audio workers, control
channel, and recording hooks exposed elsewhere in the package.
"""

from __future__ import annotations

import logging
import queue
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple, Dict, List

from .config import SessionConfig
from .control import (
    ControlMessageType,
    TurnPassPayload,
    DEBUG_READY,
    DEBUG_STOP,
    THANKS,
    classify_payload,
)
from .network import NetworkConfig, SocketBundle, configure_nonblocking, hole_punch, open_sockets
from .audio import AudioInputWorker, AudioOutputWorker, SoundDeviceStreamFactory, StreamFactory, AudioPacket
from .records import RecorderTarget, WavRecorder


ControlHandler = Callable[[ControlMessageType, object | None], None]


@dataclass
class SessionState:
    """Mutable state tracked across the lifetime of a session."""

    sockets: Optional[SocketBundle] = None
    start_time_common: Optional[float] = None
    transmit_enabled: bool = True
    receive_enabled: bool = True
    input_worker: Optional[AudioInputWorker] = None
    output_worker: Optional[AudioOutputWorker] = None
    stream_factory: Optional[StreamFactory] = None
    receiver_thread: Optional[threading.Thread] = None
    receiver_running: Optional[threading.Event] = None
    local_recorder: Optional[WavRecorder] = None
    remote_recorder: Optional[WavRecorder] = None
    owns_stream_factory: bool = False


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
        control_handler: Optional[ControlHandler] = None,
        stream_factory: Optional[StreamFactory] = None,
    ):
        self.config = config
        self.state = SessionState()
        self._control_handler = control_handler
        self._control_thread: Optional[threading.Thread] = None
        self._control_running = threading.Event()
        self._control_queue: "queue.Queue[tuple[ControlMessageType, object | None]]" = queue.Queue()
        self._stream_factory_override = stream_factory

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
        self._initialize_audio()

    def close(self) -> None:
        """Terminate control loop and close sockets."""

        self._stop_control_loop()
        bundle = self.state.sockets
        if bundle is not None:
            self._send_goodbye_packets(bundle)
        self._shutdown_audio()
        bundle = self.state.sockets
        if bundle is not None:
            bundle.close()
            self.state.sockets = None

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

    def pass_turn(self, *, run_time: float, phase_time: float, wall_time: Optional[float] = None) -> None:
        """Notify the remote peer that control has been passed to them."""

        now = wall_time if wall_time is not None else time.time()
        payload = TurnPassPayload(now, run_time, phase_time)
        self.send_turn_pass(payload)

    def start_segment(self, label: str, *, metadata: Optional[dict[str, object]] = None) -> None:
        """Begin a labeled segment for both local and remote recordings."""

        if self.state.local_recorder:
            self.state.local_recorder.start_segment(label, metadata=metadata)
        if self.state.remote_recorder:
            self.state.remote_recorder.start_segment(label, metadata=metadata)

    def stop_segment(self) -> None:
        """End the current segment for both local and remote recordings."""

        if self.state.local_recorder:
            self.state.local_recorder.stop_segment()
        if self.state.remote_recorder:
            self.state.remote_recorder.stop_segment()

    def export_segments(self, destination: Optional[Path] = None, pattern: str = "{role}_{index:02d}_{label}.wav") -> Dict[str, List[Path]]:
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

        results: Dict[str, List[Path]] = {}
        for role, recorder in (("local", self.state.local_recorder), ("remote", self.state.remote_recorder)):
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

    def enable_transmit(self, enabled: bool) -> None:
        self.state.transmit_enabled = enabled
        if self.state.input_worker:
            self.state.input_worker.enable_transmit(enabled)

    def enable_receive(self, enabled: bool) -> None:
        self.state.receive_enabled = enabled
        if self.state.output_worker:
            self.state.output_worker.enable_playback(enabled)

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

        # Ensure the control loop remains active after debug negotiations.
        if not self._control_running.is_set() or not (self._control_thread and self._control_thread.is_alive()):
            self._start_control_loop()

    # ---- audio helpers --------------------------------------------------
    def _initialize_audio(self) -> None:
        sockets = self.state.sockets
        if sockets is None:
            return

        audio_cfg = self.config.audio

        if self.state.stream_factory:
            factory = self.state.stream_factory
            owns_factory = self.state.owns_stream_factory
        else:
            factory = self._stream_factory_override or SoundDeviceStreamFactory()
            owns_factory = self._stream_factory_override is None
            self.state.stream_factory = factory
            self.state.owns_stream_factory = owns_factory

        local_recorder, remote_recorder = self._create_recorders()
        self.state.local_recorder = local_recorder
        self.state.remote_recorder = remote_recorder

        output_worker = AudioOutputWorker(audio_cfg, recorder=remote_recorder, stream_factory=factory)
        input_worker = AudioInputWorker(audio_cfg, self._handle_outbound_packet, recorder=local_recorder, stream_factory=factory)

        output_worker.enable_playback(self.state.receive_enabled)
        input_worker.enable_transmit(self.state.transmit_enabled)

        output_worker.start()
        input_worker.start()

        self.state.output_worker = output_worker
        self.state.input_worker = input_worker

        receiver_running = threading.Event()
        receiver_running.set()
        self.state.receiver_running = receiver_running
        receiver_thread = threading.Thread(target=self._receive_audio_loop, name="NeuroTalkAudioRecv", daemon=True)
        self.state.receiver_thread = receiver_thread
        receiver_thread.start()

    def _handle_outbound_packet(self, packet: AudioPacket) -> None:
        sockets = self.state.sockets
        if not sockets:
            return
        payload = self._encode_packet(packet)
        remote_ip, port_in, _, _ = sockets.remote
        try:
            sockets.outbound.sendto(payload, (remote_ip, port_in))
        except OSError as exc:
            logging.debug("Failed to send audio packet: %s", exc, exc_info=exc)

    def _receive_audio_loop(self) -> None:
        sockets = self.state.sockets
        if sockets is None:
            return
        event = self.state.receiver_running
        if event is None:
            return
        while event.is_set():
            try:
                data = sockets.inbound.recv(65536)
            except (BlockingIOError, TimeoutError):
                continue
            except OSError:
                break
            if not data:
                continue
            if data == THANKS:
                break
            packet = self._decode_packet(data)
            if packet is None:
                continue
            output = self.state.output_worker
            if output:
                output.enqueue(packet)

    def _encode_packet(self, packet: AudioPacket) -> bytes:
        return packet.pcm + struct.pack("<l", packet.counter) + struct.pack("<d", packet.timestamp)

    def _decode_packet(self, payload: bytes) -> Optional[AudioPacket]:
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

        if self.state.input_worker:
            self.state.input_worker.close()
            self.state.input_worker = None
        if self.state.output_worker:
            self.state.output_worker.close()
            self.state.output_worker = None

        if self.state.stream_factory:
            if self.state.owns_stream_factory:
                try:
                    self.state.stream_factory.terminate()
                except Exception as exc:
                    logging.debug("Stream factory termination failed: %s", exc, exc_info=exc)
            self.state.stream_factory = None
            self.state.owns_stream_factory = False

        if self.state.local_recorder:
            self.state.local_recorder.stop_segment()
            if not getattr(self.state.local_recorder, "_closed", False):
                self.state.local_recorder.close()
        if self.state.remote_recorder:
            self.state.remote_recorder.stop_segment()
            if not getattr(self.state.remote_recorder, "_closed", False):
                self.state.remote_recorder.close()

    def _create_recorders(self) -> Tuple[Optional[WavRecorder], Optional[WavRecorder]]:
        recording_cfg = self.config.recording
        directory: Path = recording_cfg.directory
        directory.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base_name = f"{self.config.participant_id}_{self.config.role}_{timestamp}"

        audio_cfg = self.config.audio

        local_path = recording_cfg.local_track
        if local_path is None:
            local_path = directory / f"{base_name}_local.wav"
        else:
            local_path = local_path if local_path.is_absolute() else directory / local_path

        remote_path = recording_cfg.remote_track
        if remote_path is None:
            remote_path = directory / f"{base_name}_remote.wav"
        else:
            remote_path = remote_path if remote_path.is_absolute() else directory / remote_path

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
