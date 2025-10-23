from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress
import os
import platform
from fractions import Fraction
from typing import Optional

from av import AudioFrame
from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError

from .config import AudioDeviceConfig, RecordingConfig

_PY_AUDIO_AVAILABLE = False

try:
    import pyaudio  # type: ignore

    _PY_AUDIO_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    pyaudio = None

_disable_env = os.getenv("NEUROTALK_DISABLE_AUDIO")
if _disable_env is None:
    _disable_real_audio = "microsoft" in platform.release().lower()
else:
    _disable_real_audio = _disable_env.lower() not in {"0", "false", "off"}

HAS_PYAUDIO = _PY_AUDIO_AVAILABLE and not _disable_real_audio

LOGGER = logging.getLogger(__name__)


class AudioBackendError(RuntimeError):
    """Raised when audio hardware initialisation fails."""


def _layout_for_channels(channels: int) -> str:
    return {1: "mono", 2: "stereo"}.get(channels, "stereo")


class SilenceAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, sample_rate: int, channels: int, frame_duration_ms: int = 20) -> None:
        super().__init__()  # type: ignore[call-arg]
        self._sample_rate = sample_rate
        self._channels = channels
        self._samples_per_frame = max(int(sample_rate * frame_duration_ms / 1000), 1)
        self._sleep = frame_duration_ms / 1000.0
        self._layout = _layout_for_channels(channels)
        self._silence = b"\x00" * (self._samples_per_frame * channels * 2)
        self._timestamp = 0

    async def recv(self) -> AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        await asyncio.sleep(self._sleep)
        frame = AudioFrame(format="s16", layout=self._layout, samples=self._samples_per_frame)
        frame.planes[0].update(self._silence)
        frame.sample_rate = self._sample_rate
        frame.time_base = Fraction(1, self._sample_rate)
        frame.pts = self._timestamp
        self._timestamp += self._samples_per_frame
        return frame

    def stop(self) -> None:
        super().stop()


class PyAudioMicrophoneTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, config: AudioDeviceConfig, frame_duration_ms: int = 20) -> None:
        if not HAS_PYAUDIO:
            raise AudioBackendError("PyAudio is not available.")
        super().__init__()  # type: ignore[call-arg]
        self._config = config
        self._rate = config.sample_rate
        self._channels = config.channels
        self._layout = _layout_for_channels(self._channels)
        self._samples_per_frame = max(int(self._rate * frame_duration_ms / 1000), 1)
        self._chunk = self._samples_per_frame
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=8)
        self._timestamp = 0
        self._closed = asyncio.Event()

        self._pa = pyaudio.PyAudio()
        try:
            device_index = _resolve_device_index(self._pa, True, config.input_device)
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self._channels,
                rate=self._rate,
                input=True,
                frames_per_buffer=self._chunk,
                input_device_index=device_index,
            )
        except Exception as exc:  # pragma: no cover - hardware failure
            self._pa.terminate()
            raise AudioBackendError(f"Failed to open input device: {exc}") from exc
        self._running = True
        self._reader = threading.Thread(target=self._reader_loop, name="neurotalk-mic", daemon=True)
        self._reader.start()

    def _reader_loop(self) -> None:
        try:
            while self._running:
                try:
                    data = self._stream.read(self._chunk, exception_on_overflow=False)
                except Exception:
                    continue
                self._loop.call_soon_threadsafe(self._queue_put, data)
        finally:
            self._loop.call_soon_threadsafe(self._queue_put, None)

    def _queue_put(self, data: Optional[bytes]) -> None:
        if not self._queue.full():
            self._queue.put_nowait(data)
        else:
            # drop oldest to keep latency low
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(data)

    async def recv(self) -> AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        data = await self._queue.get()
        if data is None:
            raise MediaStreamError
        samples = len(data) // (self._channels * 2)
        frame = AudioFrame(format="s16", layout=self._layout, samples=samples)
        frame.planes[0].update(data)
        frame.sample_rate = self._rate
        frame.time_base = Fraction(1, self._rate)
        frame.pts = self._timestamp
        self._timestamp += samples
        return frame

    def stop(self) -> None:
        if self.readyState == "ended":
            return
        self._running = False
        if self._stream.is_active():
            with suppress(Exception):
                self._stream.stop_stream()
        with suppress(Exception):
            self._stream.close()
        with suppress(Exception):
            self._pa.terminate()
        super().stop()


class BaseAudioPlayer:
    async def consume(self, track: MediaStreamTrack) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class NullAudioPlayer(BaseAudioPlayer):
    async def consume(self, track: MediaStreamTrack) -> None:
        try:
            while True:
                await track.recv()
        except MediaStreamError:
            return

    async def close(self) -> None:
        return


class PyAudioPlayer(BaseAudioPlayer):
    def __init__(self, config: AudioDeviceConfig) -> None:
        if not HAS_PYAUDIO:
            raise AudioBackendError("PyAudio is not available.")
        self._config = config
        self._loop = asyncio.get_running_loop()
        self._pa = pyaudio.PyAudio()
        try:
            device_index = _resolve_device_index(self._pa, False, config.output_device)
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=config.channels,
                rate=config.sample_rate,
                output=True,
                output_device_index=device_index,
            )
        except Exception as exc:  # pragma: no cover - hardware failure
            self._pa.terminate()
            raise AudioBackendError(f"Failed to open output device: {exc}") from exc

    async def consume(self, track: MediaStreamTrack) -> None:
        try:
            while True:
                frame = await track.recv()
                data = frame.planes[0].to_bytes()
                await asyncio.to_thread(self._stream.write, data)
        except MediaStreamError:
            return

    async def close(self) -> None:
        with suppress(Exception):
            self._stream.stop_stream()
        with suppress(Exception):
            self._stream.close()
        with suppress(Exception):
            self._pa.terminate()


def _resolve_device_index(pa: "pyaudio.PyAudio", is_input: bool, device_spec: str | int | None) -> Optional[int]:
    if device_spec is None:
        return None
    if isinstance(device_spec, int):
        return device_spec
    try:
        device_count = pa.get_device_count()
        for index in range(device_count):
            info = pa.get_device_info_by_index(index)
            name = info.get("name", "")
            if device_spec.lower() in name.lower():
                if is_input and info.get("maxInputChannels", 0) > 0:
                    return index
                if not is_input and info.get("maxOutputChannels", 0) > 0:
                    return index
    except Exception:  # pragma: no cover - best effort
        pass
    return None


class AudioPipeline:
    """Manage local capture and remote playback."""

    def __init__(self, audio_config: AudioDeviceConfig, recording_config: RecordingConfig) -> None:
        self._audio_config = audio_config
        self._recording_config = recording_config
        self._capture: Optional[MediaStreamTrack] = None
        self._player: BaseAudioPlayer = NullAudioPlayer()
        self._play_task: Optional[asyncio.Task[None]] = None

        self._capture = self._create_capture()
        self._player = self._create_player()

    def _create_capture(self) -> MediaStreamTrack:
        if HAS_PYAUDIO:
            try:
                LOGGER.debug("Initialising PyAudio microphone track.")
                return PyAudioMicrophoneTrack(self._audio_config)
            except AudioBackendError as exc:  # pragma: no cover - best effort
                LOGGER.warning("Microphone initialisation failed: %s. Falling back to silence.", exc)
        return SilenceAudioTrack(self._audio_config.sample_rate, self._audio_config.channels)

    def _create_player(self) -> BaseAudioPlayer:
        if HAS_PYAUDIO:
            try:
                LOGGER.debug("Initialising PyAudio speaker.")
                return PyAudioPlayer(self._audio_config)
            except AudioBackendError as exc:  # pragma: no cover - best effort
                LOGGER.warning("Speaker initialisation failed: %s. Disabling playback.", exc)
        return NullAudioPlayer()

    @property
    def local_track(self) -> MediaStreamTrack:
        assert self._capture is not None
        return self._capture

    async def handle_remote_track(self, track: MediaStreamTrack) -> None:
        if track.kind != "audio":
            return
        if self._play_task:
            self._play_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._play_task
        self._play_task = asyncio.create_task(self._player.consume(track))

    async def close(self) -> None:
        if self._play_task:
            self._play_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._play_task
            self._play_task = None
        if self._capture:
            with suppress(Exception):
                self._capture.stop()
            self._capture = None
        await self._player.close()
