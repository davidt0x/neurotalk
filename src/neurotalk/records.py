"""
Recording and telemetry helpers for NeuroTalk.

The legacy scripts wrote CSV logs for timing events and dumped raw PCM into a
single binary file. This module will eventually provide richer logging,
segment markers, and WAV management. For now we define the public hooks so the
session layer can depend on them.
"""

from __future__ import annotations

import contextlib
import wave
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from .audio import AudioPacket


@dataclass
class RecorderTarget:
    """Description of a recording destination."""

    path: Path
    channels: int
    sample_rate_hz: int
    sample_width_bytes: int = 2


class Recorder(Protocol):
    """Protocol for classes that can persist audio packets."""

    def write(self, packet: AudioPacket) -> None: ...

    def close(self) -> None: ...


@dataclass
class SegmentMarker:
    """
    Represents a logical span within the recording (e.g., conversation turn).
    """

    label: str
    start_time: float
    end_time: float | None = None
    start_frame: int | None = None
    end_frame: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class RecordingManifest:
    """
    Collects all artifacts produced for a session (tracks, logs, segments).
    """

    base_dir: Path
    local_track: Path | None = None
    remote_track: Path | None = None
    mix_track: Path | None = None
    segments: list[SegmentMarker] = field(default_factory=list)

    def add_segment(self, marker: SegmentMarker) -> None:
        self.segments.append(marker)


class TelemetryReporter:
    """
    Lightweight observer for real-time stats.

    Concrete implementation might print to console or stream structured logs.
    """

    def report(
        self, *, packets_sent: int, packets_received: int, buffer_fill: int
    ) -> None:
        pass  # Placeholder for future monitoring hooks


class WavRecorder(Recorder):
    """
    Simple WAV recorder that appends PCM frames and tracks labeled segments.
    """

    def __init__(self, target: RecorderTarget):
        self._target = target
        self._target.path.parent.mkdir(parents=True, exist_ok=True)
        self._wave = wave.open(str(self._target.path), "wb")  # noqa: SIM115
        self._wave.setnchannels(target.channels)
        self._wave.setsampwidth(target.sample_width_bytes)
        self._wave.setframerate(target.sample_rate_hz)
        self._frames_written = 0
        self._segments: list[SegmentMarker] = []
        self._active_segment: int | None = None
        self._closed = False

    @property
    def segments(self) -> Sequence[SegmentMarker]:
        return tuple(self._segments)

    @property
    def path(self) -> Path:
        """Absolute path to the WAV file on disk."""

        return self._target.path

    def write(self, packet: AudioPacket) -> None:
        if self._closed:
            msg = "Recorder already closed"
            raise RuntimeError(msg)
        data = packet.pcm
        self._wave.writeframes(data)
        frames = len(data) // (self._target.channels * self._target.sample_width_bytes)
        self._frames_written += frames

    def start_segment(
        self, label: str, *, metadata: dict[str, object] | None = None
    ) -> None:
        if self._active_segment is not None:
            msg = "Segment already in progress"
            raise RuntimeError(msg)
        start_time = self._frames_written / self._target.sample_rate_hz
        marker = SegmentMarker(
            label=label,
            start_time=start_time,
            start_frame=self._frames_written,
            metadata=metadata or {},
        )
        self._segments.append(marker)
        self._active_segment = len(self._segments) - 1

    def stop_segment(self) -> None:
        if self._active_segment is None:
            return
        marker = self._segments[self._active_segment]
        marker.end_frame = self._frames_written
        marker.end_time = self._frames_written / self._target.sample_rate_hz
        self._active_segment = None

    def close(self) -> None:
        if self._closed:
            return
        if self._active_segment is not None:
            self.stop_segment()
        self._wave.close()
        self._closed = True

    def split_segments(
        self, destination: Path, pattern: str = "{index:02d}_{label}.wav"
    ) -> list[Path]:
        """
        Write per-segment WAV files to the destination directory.

        Returns a list of generated file paths.
        """

        if not self._closed:
            self.close()

        destination.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []
        with contextlib.closing(wave.open(str(self._target.path), "rb")) as source:
            for index, marker in enumerate(self._segments):
                start_frame = marker.start_frame or 0
                end_frame = marker.end_frame or self._frames_written
                frame_count = max(end_frame - start_frame, 0)
                source.setpos(start_frame)
                frames = source.readframes(frame_count)
                filename = pattern.format(index=index, label=marker.label)
                out_path = destination / filename
                with contextlib.closing(wave.open(str(out_path), "wb")) as out:
                    out.setnchannels(self._target.channels)
                    out.setsampwidth(self._target.sample_width_bytes)
                    out.setframerate(self._target.sample_rate_hz)
                    out.writeframes(frames)
                outputs.append(out_path)
        return outputs


def mix_turn_recordings(
    *,
    destination: Path,
    local_recorder: WavRecorder,
    remote_recorder: WavRecorder,
) -> Path:
    """
    Combine alternating local/remote segments into a single WAV file.

    The function assumes that local and remote segments are mutually exclusive
    (as enforced by :class:`neurotalk.turns.TurnManager`). Gaps between segments
    are filled with silence so that the mixed track matches the full recording
    duration.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)

    if not (local_recorder.segments or remote_recorder.segments):
        msg = "Cannot mix recordings without segment metadata"
        raise ValueError(msg)

    with contextlib.closing(wave.open(str(local_recorder.path), "rb")) as local_wav, contextlib.closing(
        wave.open(str(remote_recorder.path), "rb")
    ) as remote_wav, contextlib.closing(wave.open(str(destination), "wb")) as mix_wav:
        nchannels = local_wav.getnchannels()
        sampwidth = local_wav.getsampwidth()
        framerate = local_wav.getframerate()
        mix_wav.setnchannels(nchannels)
        mix_wav.setsampwidth(sampwidth)
        mix_wav.setframerate(framerate)
        frame_bytes = sampwidth * nchannels

        def iter_segments() -> Iterator[tuple[int, int, Literal["local", "remote"]]]:
            for seg in local_recorder.segments:
                if seg.start_frame is None or seg.end_frame is None:
                    continue
                yield (seg.start_frame, seg.end_frame, "local")
            for seg in remote_recorder.segments:
                if seg.start_frame is None or seg.end_frame is None:
                    continue
                yield (seg.start_frame, seg.end_frame, "remote")

        segments = sorted(iter_segments(), key=lambda item: item[0])
        total_frames = max(local_wav.getnframes(), remote_wav.getnframes())
        write_position = 0

        def write_silence(frames: int) -> None:
            remaining = frames
            while remaining > 0:
                step = min(4096, remaining)
                mix_wav.writeframes(b"\x00" * (step * frame_bytes))
                remaining -= step

        for start_frame, end_frame, source in segments:
            start = max(start_frame, write_position)
            end = max(end_frame, start)
            if start > write_position:
                write_silence(start - write_position)
                write_position = start
            frames = end - start
            if frames <= 0:
                continue
            reader = local_wav if source == "local" else remote_wav
            reader.setpos(start)
            remaining = frames
            while remaining > 0:
                step = min(4096, remaining)
                data = reader.readframes(step)
                expected = step * frame_bytes
                if len(data) < expected:
                    data = data + (b"\x00" * (expected - len(data)))
                mix_wav.writeframes(data)
                remaining -= step
            write_position += frames

        if total_frames > write_position:
            write_silence(total_frames - write_position)

    return destination
