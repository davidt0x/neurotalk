from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np

from neurotalk.config import AudioConfig, NetworkConfig, RecordingConfig, SessionConfig
from neurotalk.control import ControlMessageType
from neurotalk.session import ConversationSession

Callback = Callable[[Any, int, Any, int], None]


class FakeInputStream:
    def __init__(self, callback: Callback):
        self._callback: Callback = callback

    def start_stream(self) -> None:
        pass

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        pass

    def is_active(self) -> bool:
        return False

    def emit(self, data: bytes) -> None:
        frames = len(data) // 2
        array = np.frombuffer(data, dtype=np.int16).reshape(frames, 1)
        self._callback(array, frames, None, 0)


class FakeOutputStream:
    def __init__(self, callback: Callback, chunk_bytes: int, channels: int):
        self._callback: Callback = callback
        self.chunk_bytes = chunk_bytes
        self.channels = channels

    def start_stream(self) -> None:
        pass

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        pass

    def is_active(self) -> bool:
        return False

    def emit(self) -> bytes:
        frames = self.chunk_bytes // 2
        array = np.zeros((frames, self.channels), dtype=np.int16)
        self._callback(array, frames, None, 0)
        return cast(bytes, array.tobytes())


class FakeStreamFactory:
    def __init__(self) -> None:
        self.input_stream: FakeInputStream | None = None
        self.output_stream: FakeOutputStream | None = None
        self.chunk_bytes = 0

    def open_input_stream(
        self, config: AudioConfig, callback: Callback
    ) -> FakeInputStream:
        self.chunk_bytes = config.chunk_frames * config.channels * 2
        stream = FakeInputStream(callback)
        self.input_stream = stream
        return stream

    def open_output_stream(
        self, config: AudioConfig, callback: Callback
    ) -> FakeOutputStream:
        self.chunk_bytes = config.chunk_frames * config.channels * 2
        stream = FakeOutputStream(callback, self.chunk_bytes, config.channels)
        self.output_stream = stream
        return stream

    def terminate(self) -> None:
        pass


def build_config(
    base_ports: tuple[int, int, int],
    remote_ports: tuple[int, int, int],
    nat_role: int,
    tmp_path: Path,
) -> SessionConfig:
    network = NetworkConfig(
        local_ports=base_ports,
        remote_hint=("127.0.0.1", *remote_ports),
        nat_role=nat_role,
        punch_timeout_s=3.0,
        stun_servers=(),
    )
    audio = AudioConfig(chunk_frames=128)
    recording = RecordingConfig(directory=tmp_path)
    return SessionConfig(
        participant_id="001" if nat_role == 1 else "002",
        role="A" if nat_role == 1 else "B",
        network=network,
        audio=audio,
        recording=recording,
    )


def connect_pair(
    session_a: ConversationSession, session_b: ConversationSession
) -> None:
    thread_a = threading.Thread(target=session_a.connect)
    thread_b = threading.Thread(target=session_b.connect)
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()


def teardown_session(session: ConversationSession) -> None:
    session.close()


def test_end_to_end(tmp_path: Path) -> None:
    ports_a = (45002, 45001, 45003)
    ports_b = (46002, 46001, 46003)

    events_a: list[tuple[ControlMessageType, object | None]] = []
    events_b: list[tuple[ControlMessageType, object | None]] = []

    session_a = ConversationSession(
        build_config(ports_a, ports_b, nat_role=1, tmp_path=tmp_path),
        control_handler=lambda t, p: events_a.append((t, p)),
        stream_factory=FakeStreamFactory(),
    )
    session_b = ConversationSession(
        build_config(ports_b, ports_a, nat_role=0, tmp_path=tmp_path),
        control_handler=lambda t, p: events_b.append((t, p)),
        stream_factory=FakeStreamFactory(),
    )

    connect_pair(session_a, session_b)

    try:
        factory_a = session_a.state.stream_factory
        factory_b = session_b.state.stream_factory
        assert isinstance(factory_a, FakeStreamFactory)
        assert isinstance(factory_b, FakeStreamFactory)

        sample = b"\x01\x02" * 4
        session_a.start_segment("local_turn")
        assert factory_a.input_stream is not None
        factory_a.input_stream.emit(sample)

        chunk = b""
        for _ in range(20):
            assert factory_b.output_stream is not None
            chunk = factory_b.output_stream.emit()
            if chunk.startswith(sample):
                break
            time.sleep(0.05)
        assert chunk.startswith(sample)
        baseline_chunk = chunk

        session_a.enable_transmit(False)
        assert factory_a.input_stream is not None
        factory_a.input_stream.emit(sample)
        assert factory_b.output_stream is not None
        chunk_after_disable = factory_b.output_stream.emit()
        assert chunk_after_disable == baseline_chunk

        session_a.enable_transmit(True)
        assert factory_a.input_stream is not None
        factory_a.input_stream.emit(sample)
        chunk_after_enable = b""
        for _ in range(20):
            assert factory_b.output_stream is not None
            chunk_after_enable = factory_b.output_stream.emit()
            if chunk_after_enable.startswith(sample):
                break
            time.sleep(0.05)
        assert chunk_after_enable.startswith(sample)
        session_a.stop_segment()

        session_a.pass_turn(run_time=1.0, phase_time=1.0)

        deadline = time.time() + 2.0
        while time.time() < deadline:
            if any(evt[0] == ControlMessageType.TURN_PASS for evt in events_b):
                break
            time.sleep(0.1)
        assert any(evt[0] == ControlMessageType.TURN_PASS for evt in events_b)

    finally:
        teardown_session(session_a)
        teardown_session(session_b)
        segments = session_a.export_segments(tmp_path / "segments")
        assert segments.get("local")
