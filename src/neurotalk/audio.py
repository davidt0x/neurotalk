"""
Audio transport primitives for NeuroTalk.

This module wraps PyAudio stream configuration, packet metadata, and recording
plumbing. Concrete implementations will be filled in during subsequent
milestones; for now we provide the public surface area and core data models.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
import logging
from typing import Callable, Optional, Protocol

from .config import AudioConfig

try:
    import pyaudio  # type: ignore
except ImportError:  # pragma: no cover - optional dependency in tooling
    pyaudio = None


class InputStream(Protocol):
    def start_stream(self) -> None: ...
    def stop_stream(self) -> None: ...
    def close(self) -> None: ...
    def is_active(self) -> bool: ...


class OutputStream(Protocol):
    def start_stream(self) -> None: ...
    def stop_stream(self) -> None: ...
    def close(self) -> None: ...
    def is_active(self) -> bool: ...


class StreamFactory(Protocol):
    def open_input_stream(self, config: AudioConfig, callback: Callable[..., tuple[None, int]]) -> InputStream: ...
    def open_output_stream(self, config: AudioConfig, callback: Callable[..., tuple[bytes, int]]) -> OutputStream: ...
    def terminate(self) -> None: ...


PacketCallback = Callable[["AudioPacket"], None]


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


class PyAudioStreamFactory:
    """Concrete stream factory backed by `pyaudio.PyAudio`."""

    def __init__(self) -> None:
        if pyaudio is None:  # pragma: no cover - optional dependency
            raise RuntimeError("PyAudio is required for the default stream factory.")
        self._pa = pyaudio.PyAudio()

    def open_input_stream(
        self,
        config: AudioConfig,
        callback: Callable[..., tuple[None, int]],
    ) -> InputStream:
        return self._pa.open(
            format=config.format_tag,
            channels=config.channels,
            rate=config.sample_rate_hz,
            input=True,
            frames_per_buffer=config.chunk_frames,
            stream_callback=callback,
            start=False,
        )

    def open_output_stream(
        self,
        config: AudioConfig,
        callback: Callable[..., tuple[bytes, int]],
    ) -> OutputStream:
        return self._pa.open(
            format=config.format_tag,
            channels=config.channels,
            rate=config.sample_rate_hz,
            output=True,
            frames_per_buffer=config.chunk_frames,
            stream_callback=callback,
            start=False,
        )

    def terminate(self) -> None:
        self._pa.terminate()


class AudioInputWorker:
    """Thread-managed microphone capture using a configurable stream factory."""

    def __init__(
        self,
        config: AudioConfig,
        on_packet: PacketCallback,
        recorder: Optional[Recorder] = None,
        stream_factory: Optional[StreamFactory] = None,
    ):
        self.config = config
        self._on_packet = on_packet
        self._recorder = recorder
        self._factory = stream_factory or PyAudioStreamFactory()
        self._owns_factory = stream_factory is None
        self._stream: Optional[InputStream] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._transmit_enabled = True
        self._counter = 0
        self._last_error: Optional[Exception] = None

    @property
    def last_error(self) -> Optional[Exception]:
        return self._last_error

    def enable_transmit(self, enabled: bool) -> None:
        self._transmit_enabled = enabled

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._stream:
            self._stream = self._factory.open_input_stream(self.config, self._callback)
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="AudioInputWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._stream:
            try:
                self._stream.stop_stream()
            except Exception as exc:  # pragma: no cover - defensive shutdown
                logging.debug("AudioInputWorker.stop_stream failed: %s", exc, exc_info=exc)
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        if not self._stream:
            return
        try:
            self._stream.start_stream()
            while self._running.is_set() and self._stream.is_active():
                time.sleep(0.05)
        finally:
            if self._stream:
                try:
                    self._stream.stop_stream()
                except Exception as exc:  # pragma: no cover - defensive shutdown
                    logging.debug("AudioInputWorker loop stop_stream failed: %s", exc, exc_info=exc)

    def _callback(self, in_data, frame_count, time_info, status_flags):
        timestamp = time.time()
        self._counter += 1
        packet = AudioPacket(pcm=in_data, counter=self._counter, timestamp=timestamp)
        try:
            if self._recorder:
                self._recorder.write(packet)
            if self._transmit_enabled:
                self._on_packet(packet)
        except Exception as exc:  # store error and request abort
            self._last_error = exc
            if pyaudio is not None:
                return None, pyaudio.paAbort
            return None, 0
        if pyaudio is not None:
            return None, pyaudio.paContinue
        return None, 0

    def close(self) -> None:
        self.stop()
        if self._stream:
            try:
                self._stream.close()
            finally:
                self._stream = None
        if self._recorder:
            self._recorder.close()
        if self._owns_factory:
            self._factory.terminate()


class AudioOutputWorker:
    """Thread-managed speaker playback with internal buffering."""

    def __init__(
        self,
        config: AudioConfig,
        recorder: Optional[Recorder] = None,
        stream_factory: Optional[StreamFactory] = None,
    ):
        self.config = config
        self._recorder = recorder
        self._factory = stream_factory or PyAudioStreamFactory()
        self._owns_factory = stream_factory is None
        self._stream: Optional[OutputStream] = None
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._queue: queue.Queue[AudioPacket] = queue.Queue()
        self._playback_enabled = True
        self._silence = bytes(self._expected_bytes)
        self._last_chunk = self._silence
        self._last_error: Optional[Exception] = None

    @property
    def _expected_bytes(self) -> int:
        bytes_per_sample = 2  # paInt16
        return self.config.chunk_frames * self.config.channels * bytes_per_sample

    @property
    def last_error(self) -> Optional[Exception]:
        return self._last_error

    def enable_playback(self, enabled: bool) -> None:
        self._playback_enabled = enabled

    def enqueue(self, packet: AudioPacket) -> None:
        self._queue.put(packet)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._stream:
            self._stream = self._factory.open_output_stream(self.config, self._callback)
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="AudioOutputWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._stream:
            try:
                self._stream.stop_stream()
            except Exception as exc:  # pragma: no cover - defensive shutdown
                logging.debug("AudioOutputWorker.stop_stream failed: %s", exc, exc_info=exc)
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        if not self._stream:
            return
        try:
            self._stream.start_stream()
            while self._running.is_set() and self._stream.is_active():
                time.sleep(0.05)
        finally:
            if self._stream:
                try:
                    self._stream.stop_stream()
                except Exception as exc:  # pragma: no cover - defensive shutdown
                    logging.debug("AudioOutputWorker loop stop_stream failed: %s", exc, exc_info=exc)

    def _callback(self, in_data, frame_count, time_info, status_flags):
        packet: Optional[AudioPacket] = None
        try:
            packet = self._queue.get_nowait()
        except queue.Empty:
            packet = None

        if packet is not None:
            data = self._normalize(packet.pcm)
            if self._recorder:
                try:
                    self._recorder.write(packet)
                except Exception as exc:
                    self._last_error = exc
            self._last_chunk = data
        else:
            data = self._last_chunk

        playback_chunk = data if self._playback_enabled else self._silence
        if pyaudio is not None:
            return playback_chunk, pyaudio.paContinue
        return playback_chunk, 0

    def _normalize(self, data: bytes) -> bytes:
        expected = self._expected_bytes
        if len(data) == expected:
            return data
        if len(data) > expected:
            return data[:expected]
        return data + bytes(expected - len(data))

    def close(self) -> None:
        self.stop()
        if self._stream:
            try:
                self._stream.close()
            finally:
                self._stream = None
        if self._recorder:
            self._recorder.close()
        if self._owns_factory:
            self._factory.terminate()
