from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd

try:  # optional dependency; we fall back to a generated tone if absent.
    import soundfile as sf
except Exception:  # pragma: no cover - optional dependency
    sf = None  # type: ignore[assignment]


DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_DURATION_S = 2.0


@dataclass(slots=True)
class SoundcheckResult:
    """Outcome of a volume check."""

    volume: float
    device_index: int | None
    sample_rate_hz: int
    channels: int


def list_output_devices() -> list[dict[str, Any]]:
    """Return sounddevice output-capable devices with an added ``index`` key."""

    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] > 0:
            copy = dict(dev)
            copy["index"] = idx
            devices.append(copy)
    return devices


def _load_clip(
    audio_path: Path | None, sample_rate_hz: int, channels: int
) -> tuple[np.ndarray, int]:
    """Load an audio clip or fall back to a synthesized tone."""

    if audio_path is not None:
        if sf is None:
            msg = "soundfile is required to load audio clips"
            raise RuntimeError(msg)
        data, clip_rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
        sample_rate_hz = clip_rate
    else:
        data = _sine_tone(sample_rate_hz, channels)

    if data.shape[1] != channels:
        if data.shape[1] == 1 and channels == 2:
            data = np.repeat(data, 2, axis=1)
        else:
            data = data[:, :channels]

    return data.astype(np.float32, copy=False), sample_rate_hz


def _sine_tone(sample_rate_hz: int, channels: int) -> np.ndarray:
    """Generate a quiet test tone."""

    duration = DEFAULT_DURATION_S
    freq_hz = 440.0
    t = np.arange(int(duration * sample_rate_hz)) / float(sample_rate_hz)
    wave = 0.2 * np.sin(2 * np.pi * freq_hz * t, dtype=np.float32)
    return np.repeat(wave[:, np.newaxis], channels, axis=1)


class _LoopingPlayer(AbstractContextManager["_LoopingPlayer"]):
    """Loop an audio clip on a background sounddevice stream."""

    def __init__(
        self,
        clip: np.ndarray,
        sample_rate_hz: int,
        device_index: int | None,
        volume: float,
    ) -> None:
        self._clip = clip
        self._sample_rate_hz = sample_rate_hz
        self._device_index = device_index
        self._volume = volume
        self._pos = 0
        self._stream: sd.OutputStream | None = None

    def __enter__(self) -> _LoopingPlayer:
        self.start()
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        self.stop()

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, new_volume: float) -> None:
        self._volume = new_volume

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.OutputStream(
            samplerate=self._sample_rate_hz,
            channels=self._clip.shape[1],
            dtype="float32",
            callback=self._callback,
            device=self._device_index,
            blocksize=0,  # let sounddevice pick
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None

    def _callback(
        self,
        outdata: np.ndarray,
        frames: int,
        _time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logging.debug("sounddevice status: %s", status)

        end = self._pos + frames
        clip = self._clip
        if end <= clip.shape[0]:
            chunk = clip[self._pos : end]
        else:
            first = clip[self._pos :]
            remaining = end - clip.shape[0]
            second = clip[:remaining]
            chunk = np.vstack((first, second))
        self._pos = end % clip.shape[0]

        outdata[:] = chunk * self._volume


class _KeyPoller:
    """Cross-platform key polling without extra dependencies."""

    def __init__(self) -> None:
        self._is_windows = sys.platform.startswith("win")

    def poll(self) -> Iterable[str]:
        if self._is_windows:
            return self._poll_windows()
        return self._poll_posix()

    def _poll_windows(self) -> Iterable[str]:
        import msvcrt  # noqa: PLC0415

        keys: list[str] = []
        while msvcrt.kbhit():
            key = msvcrt.getwch()
            keys.append(key)
        return keys

    def _poll_posix(self) -> Iterable[str]:
        import select # noqa: PLC0415, I001

        keys: list[str] = []
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if ready:
            line = sys.stdin.readline().strip()
            if line:
                keys.append(line[0])
        return keys


def run_volume_check(
    audio_path: Path | None = None,
    *,
    device_index: int | None = None,
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE,
    channels: int = 2,
    start_volume: float = 0.5,
    max_volume: float = 2.0,
    step: float = 0.05,
) -> SoundcheckResult:
    """
    Play a looping clip and let the operator adjust volume before experiments.

    Parameters
    ----------
    audio_path:
        Optional path to an audio file to play. Falls back to a generated tone if
        omitted. Requires ``soundfile`` when provided.
    device_index:
        sounddevice output device index. Leave ``None`` to use the default.
    sample_rate_hz:
        Playback sample rate; will be replaced by the clip's native rate when
        loading from a file.
    channels:
        Number of output channels to request from sounddevice.
    start_volume:
        Initial gain multiplier.
    max_volume:
        Maximum allowed gain multiplier.
    step:
        Increment applied for each volume-up/volume-down keystroke.

    Returns
    -------
    SoundcheckResult
        The final volume selection and playback parameters.
    """

    volume = max(0.0, min(max_volume, start_volume))
    clip, sample_rate_hz = _load_clip(audio_path, sample_rate_hz, channels)
    logging.info(
        "Starting volume check (device=%s, sample_rate=%s Hz, channels=%s, max_volume=%.2f)",
        device_index,
        sample_rate_hz,
        clip.shape[1],
        max_volume,
    )
    logging.info("Controls: 1/+ louder, 2/- quieter, 4 or Enter to finish, q to abort.")

    poller = _KeyPoller()
    with _LoopingPlayer(clip, sample_rate_hz, device_index, volume) as player:
        finished = False
        while not finished:
            time.sleep(0.05)
            for _key in poller.poll():
                key = _key.lower()
                if key in ("q", "\x1b"):
                    msg = "Volume check aborted by user"
                    raise KeyboardInterrupt(msg)
                if key in ("1", "+"):
                    volume = min(max_volume, volume + step)
                    player.volume = volume
                    logging.info("Volume: %.2f", volume)
                if key in ("2", "-"):
                    volume = max(0.0, volume - step)
                    player.volume = volume
                    logging.info("Volume: %.2f", volume)
                if key in ("4", "\r", "\n"):
                    finished = True
                    break

    return SoundcheckResult(
        volume=volume,
        device_index=device_index,
        sample_rate_hz=sample_rate_hz,
        channels=clip.shape[1],
    )
