"""
Audio transport primitives for NeuroTalk.

This module wraps sounddevice stream configuration, packet metadata, and recording
plumbing.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import sounddevice as sd

from .config import AudioConfig
from .records import Recorder


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
    def open_input_stream(
        self, config: AudioConfig, callback: Callable[..., None]
    ) -> InputStream: ...
    def open_output_stream(
        self, config: AudioConfig, callback: Callable[..., None]
    ) -> OutputStream: ...
    def terminate(self) -> None: ...


PacketCallback = Callable[["AudioPacket"], None]


@dataclass(slots=True)
class AudioPacket:
    pcm: bytes
    counter: int
    timestamp: float


class RecorderTarget:
    path: str
    channels: int
    sample_rate_hz: int


class Recorder(Protocol):
    def write(self, packet: AudioPacket) -> None: ...
    def close(self) -> None: ...


class SoundDeviceInputStream:
    def __init__(self, stream: sd.InputStream):
        self._stream = stream

    def start_stream(self) -> None:
        self._stream.start()

    def stop_stream(self) -> None:
        self._stream.stop()

    def close(self) -> None:
        self._stream.close()

    def is_active(self) -> bool:
        return self._stream.active


class SoundDeviceOutputStream:
    def __init__(self, stream: sd.OutputStream):
        self._stream = stream

    def start_stream(self) -> None:
        self._stream.start()

    def stop_stream(self) -> None:
        self._stream.stop()

    def close(self) -> None:
        self._stream.close()

    def is_active(self) -> bool:
        return self._stream.active


class SoundDeviceStreamFactory:
    def open_input_stream(
        self, config: AudioConfig, callback: Callable[..., None]
    ) -> InputStream:
        stream = sd.InputStream(
            samplerate=config.sample_rate_hz,
            channels=config.channels,
            dtype="int16",
            blocksize=config.chunk_frames,
            callback=callback,
        )
        return SoundDeviceInputStream(stream)

    def open_output_stream(
        self, config: AudioConfig, callback: Callable[..., None]
    ) -> OutputStream:
        stream = sd.OutputStream(
            samplerate=config.sample_rate_hz,
            channels=config.channels,
            dtype="int16",
            blocksize=config.chunk_frames,
            callback=callback,
        )
        return SoundDeviceOutputStream(stream)

    def terminate(self) -> None:
        # sounddevice does not require explicit termination
        pass


class AudioInputWorker:
    def __init__(
        self,
        config: AudioConfig,
        on_packet: PacketCallback,
        recorder: Recorder | None = None,
        stream_factory: StreamFactory | None = None,
    ) -> None:
        self.config = config
        self._on_packet = on_packet
        self._recorder = recorder
        self._factory = stream_factory or SoundDeviceStreamFactory()
        self._owns_factory = stream_factory is None
        self._stream: InputStream | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._transmit_enabled = True
        self._counter = 0
        self._last_error: Exception | None = None

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    def enable_transmit(self, enabled: bool) -> None:
        self._transmit_enabled = enabled

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._stream:
            self._stream = self._factory.open_input_stream(self.config, self._callback)
        self._running.set()
        self._thread = threading.Thread(
            target=self._loop, name="AudioInputWorker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._stream:
            try:
                self._stream.stop_stream()
            except Exception as exc:  # pragma: no cover
                logging.debug(
                    "AudioInputWorker.stop_stream failed: %s", exc, exc_info=exc
                )
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
                except Exception as exc:  # pragma: no cover
                    logging.debug(
                        "AudioInputWorker loop stop_stream failed: %s",
                        exc,
                        exc_info=exc,
                    )

    def _callback(self, in_data, frame_count, time_info, status_flags) -> None:
        if status_flags:
            logging.debug("AudioInput status: %s", status_flags)
        if isinstance(in_data, (bytes, bytearray)):
            pcm = bytes(in_data)
        else:
            pcm = np.asarray(in_data, dtype=np.int16).tobytes()
        self._counter += 1
        packet = AudioPacket(pcm=pcm, counter=self._counter, timestamp=time.time())
        try:
            if self._recorder:
                self._recorder.write(packet)
            if self._transmit_enabled:
                self._on_packet(packet)
        except Exception as exc:
            self._last_error = exc

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
    def __init__(
        self,
        config: AudioConfig,
        recorder: Recorder | None = None,
        stream_factory: StreamFactory | None = None,
    ) -> None:
        self.config = config
        self._recorder = recorder
        self._factory = stream_factory or SoundDeviceStreamFactory()
        self._owns_factory = stream_factory is None
        self._stream: OutputStream | None = None
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[AudioPacket] = queue.Queue()
        self._playback_enabled = True
        self._silence = self._silence_bytes(config.chunk_frames)
        self._last_chunk = self._silence
        self._counter = 0
        self._last_error: Exception | None = None

    def _silence_bytes(self, frames: int) -> bytes:
        array = np.zeros((frames, self.config.channels), dtype=np.int16)
        return array.tobytes()

    @property
    def _expected_bytes(self) -> int:  # used by tests
        return self.config.chunk_frames * self.config.channels * 2

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
        self._thread = threading.Thread(
            target=self._loop, name="AudioOutputWorker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._stream:
            try:
                self._stream.stop_stream()
            except Exception as exc:  # pragma: no cover
                logging.debug(
                    "AudioOutputWorker.stop_stream failed: %s", exc, exc_info=exc
                )
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
                except Exception as exc:  # pragma: no cover
                    logging.debug(
                        "AudioOutputWorker loop stop_stream failed: %s",
                        exc,
                        exc_info=exc,
                    )

    def _callback(self, out_data, frame_count, time_info, status_flags) -> None:
        if status_flags:
            logging.debug("AudioOutput status: %s", status_flags)
        try:
            packet = self._queue.get_nowait()
        except queue.Empty:
            packet = None

        if packet is not None:
            playback = self._normalize(packet.pcm, frame_count)
            self._last_chunk = playback
            self._counter += 1
            if self._recorder:
                try:
                    self._recorder.write(packet)
                except Exception as exc:
                    logging.debug("Recorder write failed: %s", exc, exc_info=exc)
                    self._last_error = exc
        else:
            playback = self._normalize(self._last_chunk, frame_count)

        if not self._playback_enabled:
            playback = self._silence_bytes(frame_count)

        array = np.frombuffer(playback, dtype=np.int16).reshape(
            frame_count, self.config.channels
        )
        out_data[:] = array

    def _normalize(self, data: bytes, frames: int) -> bytes:
        expected = frames * self.config.channels * 2
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

    @property
    def last_error(self) -> Exception | None:
        return self._last_error
