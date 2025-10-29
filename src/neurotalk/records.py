"""
Recording and telemetry helpers for NeuroTalk.

The legacy scripts wrote CSV logs for timing events and dumped raw PCM into a
single binary file. This module will eventually provide richer logging,
segment markers, and WAV management. For now we define the public hooks so the
session layer can depend on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class SegmentMarker:
    """
    Represents a logical span within the recording (e.g., conversation turn).
    """

    label: str
    start_time: float
    end_time: Optional[float] = None
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
