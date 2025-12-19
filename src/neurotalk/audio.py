"""
Audio transport primitives for NeuroTalk.

This module wraps sounddevice stream configuration, packet metadata, and recording
plumbing.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

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
        return bool(self._stream.active)


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
        return bool(self._stream.active)


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


class MockInputStream:
    """Sounddevice-compatible input stream that synthesizes silence."""

    def __init__(self, config: AudioConfig, callback: Callable[..., None]) -> None:
        self._config = config
        self._callback = callback
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    def start_stream(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop_stream(self) -> None:
        self._running.clear()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None

    def close(self) -> None:
        self.stop_stream()

    def is_active(self) -> bool:
        return self._running.is_set()

    def _loop(self) -> None:
        frames = self._config.chunk_frames
        interval = frames / float(self._config.sample_rate_hz)
        silence = np.zeros((frames, self._config.channels), dtype=np.int16)
        while self._running.is_set():
            self._callback(silence, frames, None, 0)
            time.sleep(interval)


class MockOutputStream:
    """Sounddevice-compatible output stream that discards audio."""

    def __init__(self, config: AudioConfig, callback: Callable[..., None]) -> None:
        self._config = config
        self._callback = callback
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    def start_stream(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop_stream(self) -> None:
        self._running.clear()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None

    def close(self) -> None:
        self.stop_stream()

    def is_active(self) -> bool:
        return self._running.is_set()

    def _loop(self) -> None:
        frames = self._config.chunk_frames
        interval = frames / float(self._config.sample_rate_hz)
        silent = np.zeros((frames, self._config.channels), dtype=np.int16)
        while self._running.is_set():
            self._callback(silent, frames, None, 0)
            time.sleep(interval)


class MockStreamFactory:
    """Factory that provides mock streams for audio-free testing."""

    def open_input_stream(
        self, config: AudioConfig, callback: Callable[..., None]
    ) -> MockInputStream:
        return MockInputStream(config, callback)

    def open_output_stream(
        self, config: AudioConfig, callback: Callable[..., None]
    ) -> MockOutputStream:
        return MockOutputStream(config, callback)

    def terminate(self) -> None:
        # Mock streams manage their own lifetimes.
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
        self._recording_enabled = True
        self._counter = 0
        self._last_error: Exception | None = None

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    def enable_transmit(self, enabled: bool) -> None:
        self._transmit_enabled = enabled

    def enable_recording(self, enabled: bool) -> None:
        self._recording_enabled = enabled

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self._stream is None:
            self._stream = self._factory.open_input_stream(self.config, self._callback)
        self._running.set()
        self._thread = threading.Thread(
            target=self._loop, name="AudioInputWorker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._stream is not None:
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
        if self._stream is None:
            return
        try:
            self._stream.start_stream()
            while self._running.is_set() and self._stream.is_active():
                time.sleep(0.05)
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                except Exception as exc:  # pragma: no cover
                    logging.debug(
                        "AudioInputWorker loop stop_stream failed: %s",
                        exc,
                        exc_info=exc,
                    )

    def _callback(
        self, in_data: Any, _frame_count: int, _time_info: Any, status_flags: int
    ) -> None:
        if status_flags:
            logging.debug("AudioInput status: %s", status_flags)
        if isinstance(in_data, (bytes, bytearray)):
            pcm = bytes(in_data)
        else:
            pcm = np.asarray(in_data, dtype=np.int16).tobytes()
        self._counter += 1
        packet = AudioPacket(pcm=pcm, counter=self._counter, timestamp=time.time())
        try:
            if self._recorder and self._recording_enabled:
                self._recorder.write(packet)
            if self._transmit_enabled:
                self._on_packet(packet)
        except Exception as exc:
            self._last_error = exc

    def close(self) -> None:
        self.stop()
        if self._stream is not None:
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
        self._recording_enabled = True
        self._gain = 1.0
        self._silence = self._silence_bytes(config.chunk_frames)
        self._last_chunk = self._silence
        self._counter = 0
        self._last_error: Exception | None = None

    def _silence_bytes(self, frames: int) -> bytes:
        array = np.zeros((frames, self.config.channels), dtype=np.int16)
        return cast(bytes, array.tobytes())

    @property
    def _expected_bytes(self) -> int:  # used by tests
        return self.config.chunk_frames * self.config.channels * 2

    def enable_playback(self, enabled: bool) -> None:
        self._playback_enabled = enabled

    @property
    def gain(self) -> float:
        return self._gain

    def set_gain(self, gain: float) -> None:
        if not math.isfinite(gain) or gain < 0:
            msg = f"gain must be a finite non-negative float, got {gain!r}"
            raise ValueError(msg)
        self._gain = float(gain)

    def enqueue(self, packet: AudioPacket) -> None:
        self._queue.put(packet)

    def enable_recording(self, enabled: bool) -> None:
        self._recording_enabled = enabled

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self._stream is None:
            self._stream = self._factory.open_output_stream(self.config, self._callback)
        self._running.set()
        self._thread = threading.Thread(
            target=self._loop, name="AudioOutputWorker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._stream is not None:
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
        if self._stream is None:
            return
        try:
            self._stream.start_stream()
            while self._running.is_set() and self._stream.is_active():
                time.sleep(0.05)
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                except Exception as exc:  # pragma: no cover
                    logging.debug(
                        "AudioOutputWorker loop stop_stream failed: %s",
                        exc,
                        exc_info=exc,
                    )

    def _callback(
        self,
        out_data: Any,
        frame_count: int,
        _time_info: Any,
        status_flags: int,
    ) -> None:
        if status_flags:
            logging.debug("AudioOutput status: %s", status_flags)
        try:
            packet = self._queue.get_nowait()
        except queue.Empty:
            packet = None

        if packet is not None:
            payload = self._normalize(packet.pcm, frame_count)
            timestamp = packet.timestamp
        else:
            payload = self._silence_bytes(frame_count)
            timestamp = time.time()

        self._last_chunk = payload
        self._counter += 1

        if self._recorder and self._recording_enabled:
            record_packet = AudioPacket(
                pcm=payload, counter=self._counter, timestamp=timestamp
            )
            try:
                self._recorder.write(record_packet)
            except Exception as exc:
                logging.debug("Recorder write failed: %s", exc, exc_info=exc)
                self._last_error = exc

        if not self._playback_enabled:
            playback = self._silence_bytes(frame_count)
        else:
            playback = payload

        array = np.frombuffer(playback, dtype=np.int16).reshape(
            frame_count, self.config.channels
        )

        gain = self._gain
        if gain != 1.0 and self._playback_enabled:
            scaled = array.astype(np.float32) * gain
            scaled = np.clip(
                scaled,
                np.iinfo(np.int16).min,
                np.iinfo(np.int16).max,
            ).astype(np.int16)
            out_data[:] = scaled
        else:
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
        if self._stream is not None:
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
