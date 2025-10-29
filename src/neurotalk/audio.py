"""
Audio transport primitives for NeuroTalk.

This module wraps PyAudio stream configuration, packet metadata, and recording
plumbing. Concrete implementations will be filled in during subsequent
milestones; for now we provide the public surface area and core data models.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from .config import AudioConfig, RecordingConfig
from .control import THANKS

try:
    import pyaudio  # type: ignore
except ImportError:  # pragma: no cover - optional dependency in tooling
    pyaudio = None


PacketCallback = Callable[[bytes], None]


@dataclass(slots=True)
class AudioPacket:
    """
    Represents a single PCM packet plus metadata appended by the sender.

    Attributes
    ----------
    pcm:
        Raw PCM bytes (no metadata suffix).
    counter:
        Monotonic packet counter.
    timestamp:
        Wall-clock timestamp taken just after reading the microphone buffer.
    """

    pcm: bytes
    counter: int
    timestamp: float


@dataclass
class RecorderTarget:
    """Description of an on-disk recording destination."""

    path: str
    channels: int
    sample_rate_hz: int


class Recorder(Protocol):
    """Protocol for classes that can persist audio packets."""

    def write(self, packet: AudioPacket) -> None:
        ...

    def close(self) -> None:
        ...


class RawPCMRecorder:
    """
    Minimal recorder that writes PCM bytes into a binary file.

    Legacy CONV/DIAD behaviour captured microphone and playback streams into
    the same handle; we expose separate recorders so they can be routed
    independently.
    """

    def __init__(self, target: RecorderTarget):
        self._target = target
        self._fh = open(target.path, "wb")

    def write(self, packet: AudioPacket) -> None:
        self._fh.write(packet.pcm)

    def close(self) -> None:
        self._fh.close()


class AudioInputWorker:
    """
    Placeholder for the microphone capture loop.

    The final implementation will mirror `inputProcess` in the legacy script,
    but expose hooks for debug mode, selective transmission, and monitoring.
    """

    def __init__(
        self,
        config: AudioConfig,
        on_packet: PacketCallback,
        recorder: Optional[Recorder] = None,
    ):
        self.config = config
        self._on_packet = on_packet
        self._recorder = recorder
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="AudioInputWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        # TODO: integrate PyAudio non-blocking stream
        counter = 0
        while self._running.is_set():
            time.sleep(0.01)
            counter += 1
            dummy = AudioPacket(b"", counter, time.time())
            self._recorder.write(dummy) if self._recorder else None
            self._on_packet(dummy.pcm)

    def close(self) -> None:
        self.stop()
        if self._recorder:
            self._recorder.close()


class AudioOutputWorker:
    """
    Placeholder for the speaker playback loop.
    """

    def __init__(
        self,
        config: AudioConfig,
        recorder: Optional[Recorder] = None,
    ):
        self.config = config
        self._recorder = recorder
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def enqueue(self, packet: AudioPacket) -> None:
        if self._recorder:
            self._recorder.write(packet)
        # TODO: feed audio buffer for playback

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="AudioOutputWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        while self._running.is_set():
            time.sleep(0.01)

    def close(self) -> None:
        self.stop()
        if self._recorder:
            self._recorder.close()
