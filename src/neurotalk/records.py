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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Protocol, Sequence, List, TYPE_CHECKING

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

    def write(self, packet: "AudioPacket") -> None:
        ...

    def close(self) -> None:
        ...

@dataclass
class SegmentMarker:
    """
    Represents a logical span within the recording (e.g., conversation turn).
    """

    label: str
    start_time: float
    end_time: Optional[float] = None
    start_frame: Optional[int] = None
    end_frame: Optional[int] = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class RecordingManifest:
    """
    Collects all artifacts produced for a session (tracks, logs, segments).
    """

    base_dir: Path
    local_track: Optional[Path] = None
    remote_track: Optional[Path] = None
    mix_track: Optional[Path] = None
    segments: list[SegmentMarker] = field(default_factory=list)

    def add_segment(self, marker: SegmentMarker) -> None:
        self.segments.append(marker)


class TelemetryReporter:
    """
    Lightweight observer for real-time stats.

    Concrete implementation might print to console or stream structured logs.
    """

    def report(self, *, packets_sent: int, packets_received: int, buffer_fill: int) -> None:
        pass  # Placeholder for future monitoring hooks


class WavRecorder(Recorder):
    """
    Simple WAV recorder that appends PCM frames and tracks labeled segments.
    """

    def __init__(self, target: RecorderTarget):
        self._target = target
        self._target.path.parent.mkdir(parents=True, exist_ok=True)
        self._wave = wave.open(str(self._target.path), "wb")
        self._wave.setnchannels(target.channels)
        self._wave.setsampwidth(target.sample_width_bytes)
        self._wave.setframerate(target.sample_rate_hz)
        self._frames_written = 0
        self._segments: list[SegmentMarker] = []
        self._active_segment: Optional[int] = None
        self._closed = False

    @property
    def segments(self) -> Sequence[SegmentMarker]:
        return tuple(self._segments)

    def write(self, packet: "AudioPacket") -> None:
        if self._closed:
            raise RuntimeError("Recorder already closed")
        data = packet.pcm
        self._wave.writeframes(data)
        frames = len(data) // (self._target.channels * self._target.sample_width_bytes)
        self._frames_written += frames

    def start_segment(self, label: str, *, metadata: Optional[dict[str, object]] = None) -> None:
        if self._active_segment is not None:
            raise RuntimeError("Segment already in progress")
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

    def split_segments(self, destination: Path, pattern: str = "{index:02d}_{label}.wav") -> List[Path]:
        """
        Write per-segment WAV files to the destination directory.

        Returns a list of generated file paths.
        """

        if not self._closed:
            self.close()

        destination.mkdir(parents=True, exist_ok=True)
        outputs: List[Path] = []
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
