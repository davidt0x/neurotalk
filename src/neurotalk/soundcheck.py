from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, cast

import numpy as np
import sounddevice as sd

from neurotalk.control import DEBUG_READY, DEBUG_STOP, ControlMessageType
from neurotalk.session import ConversationSession

sf: Any | None
try:  # optional dependency; we fall back to a generated tone if absent.
    import soundfile as _soundfile
except Exception:  # pragma: no cover - optional dependency
    sf = None
else:
    sf = _soundfile


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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
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

        msvcrt_mod = cast(Any, msvcrt)

        keys: list[str] = []
        while msvcrt_mod.kbhit():
            key = msvcrt_mod.getwch()
            keys.append(key)
        return keys

    def _poll_posix(self) -> Iterable[str]:
        import select  # noqa: PLC0415

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


@dataclass(slots=True)
class ConversationSoundcheckResult:
    """Outcome of a conversation-based sound check."""

    volume_percent: int
    playback_gain: float
    ui: str


class _ConversationSoundcheckSync:
    def __init__(
        self,
        *,
        send_interval_s: float = 0.5,
    ) -> None:
        self.partner_joined = False
        self.partner_done = False
        self.local_done = False
        self._send_interval_s = send_interval_s
        self._last_ready_send = 0.0
        self._last_done_send = 0.0

    def poll(self, session: ConversationSession) -> None:
        while True:
            try:
                msg_type, _payload = session.next_control_event(timeout=0.0)
            except queue.Empty:
                return
            if msg_type is ControlMessageType.DEBUG_READY:
                self.partner_joined = True
            elif msg_type is ControlMessageType.DEBUG_STOP:
                self.partner_joined = True
                self.partner_done = True

    def mark_local_done(self) -> None:
        self.local_done = True

    def tick(self, session: ConversationSession) -> None:
        now = time.time()
        if (
            not self.partner_joined
            and (now - self._last_ready_send) >= self._send_interval_s
        ):
            _send_control_token(session, DEBUG_READY)
            self._last_ready_send = now
        if self.local_done and (now - self._last_done_send) >= self._send_interval_s:
            _send_control_token(session, DEBUG_STOP)
            self._last_done_send = now


def _send_control_token(session: ConversationSession, token: bytes) -> None:
    sockets = session.state.sockets
    if sockets is None:
        msg = "Session not connected"
        raise RuntimeError(msg)
    remote_ip, _, _, port_comm = sockets.remote
    sockets.control.sendto(token, (remote_ip, port_comm))


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def _gain_from_percent(volume_percent: int) -> float:
    return max(0.0, float(volume_percent) / 100.0)


class _ConsoleLineReader:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if line == "":
                return
            self._queue.put(line)

    def poll(self) -> str | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None


def run_conversation_soundcheck(
    session: ConversationSession,
    *,
    ui: str = "auto",
    win: Any | None = None,
    start_volume_percent: int | None = None,
    min_volume_percent: int = 0,
    max_volume_percent: int = 500,
    step_percent: int = 5,
    restore_audio_state: bool = True,
) -> ConversationSoundcheckResult:
    """
    Run a bi-directional audio sound check over an existing ConversationSession.

    The default UI prints instructions to the console. When ``ui='psychopy'`` (or
    ``ui='auto'`` with a PsychoPy window provided via ``win``), the sound check
    uses PsychoPy to render instructions and accept mouse button input.
    """

    resolved_ui = ui.lower()
    if resolved_ui == "auto":
        resolved_ui = "psychopy" if win is not None else "console"
    if resolved_ui not in ("console", "psychopy"):
        msg = (
            f"Unknown soundcheck ui={ui!r} (expected 'auto', 'console', or 'psychopy')"
        )
        raise ValueError(msg)

    previous_transmit = session.state.transmit_enabled
    previous_receive = session.state.receive_enabled

    session.enable_transmit(True)
    session.enable_receive(True)

    if start_volume_percent is None:
        start_volume_percent = round(float(session.get_playback_gain()) * 100.0)
    volume_percent = _clamp_int(
        start_volume_percent, min_volume_percent, max_volume_percent
    )
    session.set_playback_gain(_gain_from_percent(volume_percent))

    sync = _ConversationSoundcheckSync()

    try:
        if resolved_ui == "psychopy":
            _run_psychopy_conversation_soundcheck(
                session,
                win=win,
                sync=sync,
                volume_percent=volume_percent,
                min_volume_percent=min_volume_percent,
                max_volume_percent=max_volume_percent,
                step_percent=step_percent,
            )
            volume_percent = round(float(session.get_playback_gain()) * 100.0)
        else:
            volume_percent = _run_console_conversation_soundcheck(
                session,
                sync=sync,
                volume_percent=volume_percent,
                min_volume_percent=min_volume_percent,
                max_volume_percent=max_volume_percent,
                step_percent=step_percent,
            )
    finally:
        if restore_audio_state:
            session.enable_transmit(previous_transmit)
            session.enable_receive(previous_receive)

    gain = float(session.get_playback_gain())
    return ConversationSoundcheckResult(
        volume_percent=round(gain * 100.0),
        playback_gain=gain,
        ui=resolved_ui,
    )


def _run_console_conversation_soundcheck(
    session: ConversationSession,
    *,
    sync: _ConversationSoundcheckSync,
    volume_percent: int,
    min_volume_percent: int,
    max_volume_percent: int,
    step_percent: int,
) -> int:
    reader = _ConsoleLineReader()

    logging.info(
        "[soundcheck] Talk with your partner and adjust playback volume "
        "to a comfortable level."
    )
    logging.info(
        "[soundcheck] Controls: '+' louder, '-' quieter, Enter when ready (max 500%%)."
    )
    logging.info("[soundcheck] Ctrl+C to abort.")

    last_status: tuple[bool, bool, bool] | None = None
    last_volume = None

    while True:
        sync.poll(session)
        sync.tick(session)

        status = (sync.partner_joined, sync.partner_done, sync.local_done)
        if status != last_status:
            if not sync.partner_joined:
                logging.info("[soundcheck] Waiting for partner to join...")
            elif not sync.partner_done and sync.local_done:
                logging.info("[soundcheck] Waiting for partner to finish...")
            elif sync.partner_done and not sync.local_done:
                logging.info(
                    "[soundcheck] Partner is ready; press Enter when you are ready too."
                )
            last_status = status

        if volume_percent != last_volume:
            logging.info("[soundcheck] Volume: %s%%", volume_percent)
            last_volume = volume_percent

        if sync.local_done and sync.partner_done:
            return volume_percent

        line = reader.poll()
        if line is None:
            time.sleep(0.05)
            continue

        cmd = line.strip().lower()
        if cmd == "":
            sync.mark_local_done()
            _send_control_token(session, DEBUG_STOP)
            continue
        if cmd in ("q", "quit", "exit"):
            msg = "Soundcheck aborted by user"
            raise KeyboardInterrupt(msg)
        if cmd.startswith("+"):
            volume_percent = _clamp_int(
                volume_percent + step_percent, min_volume_percent, max_volume_percent
            )
            session.set_playback_gain(_gain_from_percent(volume_percent))
            continue
        if cmd.startswith("-"):
            volume_percent = _clamp_int(
                volume_percent - step_percent, min_volume_percent, max_volume_percent
            )
            session.set_playback_gain(_gain_from_percent(volume_percent))
            continue


def _run_psychopy_conversation_soundcheck(  # pragma: no cover - optional dependency
    session: ConversationSession,
    *,
    win: Any | None,
    sync: _ConversationSoundcheckSync,
    volume_percent: int,
    min_volume_percent: int,
    max_volume_percent: int,
    step_percent: int,
) -> None:
    if win is None:
        msg = "PsychoPy soundcheck requires a PsychoPy window via win=..."
        raise ValueError(msg)

    try:
        from psychopy import (  # type: ignore[import-not-found]  # noqa: PLC0415
            core,
            event,
            visual,
        )
    except Exception as exc:  # pragma: no cover - optional dependency
        msg = "PsychoPy soundcheck requested but PsychoPy is not available."
        raise RuntimeError(msg) from exc

    instructions = visual.TextStim(
        win,
        text=(
            "Sound check\n\n"
            "Talk with your partner and adjust the volume to a comfortable listening level.\n\n"
            "Left click: lower volume\n"
            "Right click: increase volume\n\n"
            "When you are done, click the button below."
        ),
        height=0.07,
        wrapWidth=2,
        color="white",
        pos=(0, 0.55),
    )
    volume_text = visual.TextStim(
        win,
        text="",
        height=0.07,
        wrapWidth=2,
        color="white",
        pos=(0, -0.10),
    )
    status_text = visual.TextStim(
        win,
        text="",
        height=0.06,
        wrapWidth=2,
        color="white",
        pos=(0, -0.35),
    )
    button_rect = visual.Rect(
        win,
        width=0.75,
        height=0.18,
        pos=(0, -0.65),
        fillColor="dimgray",
        lineColor="white",
    )
    button_text = visual.TextStim(
        win,
        text="I'M READY",
        height=0.07,
        color="white",
        pos=(0, -0.65),
    )

    mouse = event.Mouse(win=win)
    prev_pressed = (0, 0, 0)

    mouse_visible = getattr(win, "mouseVisible", False)
    try:
        win.mouseVisible = True

        while True:
            sync.poll(session)
            sync.tick(session)

            keys = event.getKeys()
            if "escape" in keys:
                msg = "Soundcheck aborted by user"
                raise KeyboardInterrupt(msg)

            if sync.local_done and sync.partner_done:
                return

            if sync.partner_done and not sync.local_done:
                status_text.text = (
                    "Your partner is ready.\n"
                    "Click the button below when you are ready too."
                )
            elif sync.local_done and not sync.partner_done:
                status_text.text = "Waiting for your partner to finish soundcheck..."
            elif not sync.partner_joined:
                status_text.text = "Waiting for your partner to join soundcheck..."
            else:
                status_text.text = ""

            if not sync.local_done:
                pressed = mouse.getPressed()
                click = any(pressed) and not any(prev_pressed)
                if click:
                    if button_rect.contains(mouse):
                        sync.mark_local_done()
                        _send_control_token(session, DEBUG_STOP)
                    else:
                        left = bool(pressed[0])
                        right = bool(pressed[2] if len(pressed) > 2 else pressed[1])
                        x_pos, _y_pos = mouse.getPos()
                        wants_increase = right or (left and x_pos >= 0)
                        if wants_increase:
                            volume_percent = _clamp_int(
                                volume_percent + step_percent,
                                min_volume_percent,
                                max_volume_percent,
                            )
                        else:
                            volume_percent = _clamp_int(
                                volume_percent - step_percent,
                                min_volume_percent,
                                max_volume_percent,
                            )
                        session.set_playback_gain(_gain_from_percent(volume_percent))
                prev_pressed = pressed

            gain = session.get_playback_gain()
            volume_text.text = f"Volume: {round(gain * 100.0)}%"

            instructions.draw()
            volume_text.draw()
            status_text.draw()
            button_rect.draw()
            button_text.draw()
            win.flip()
            core.wait(0.01)
    finally:
        win.mouseVisible = mouse_visible
