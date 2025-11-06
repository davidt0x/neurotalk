"""
Configuration models for NeuroTalk sessions.

These dataclasses provide a structured way to supply experiment- and network
parameters without relying on module-level globals. Actual defaults mirror the
legacy CONV/DIAD scripts but callers are free to override anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

PortRange = tuple[int, int]


@dataclass(slots=True)
class NetworkConfig:
    """
    Static network parameters for establishing the UDP link.

    Attributes
    ----------
    local_ports:
        Local UDP port triplet (inbound audio, outbound audio, control).
    remote_hint:
        Initial guess for the partner's IP/ports before hole punching.
        The handshake will update these once incoming packets reveal the
        correct mapping.
    stun_servers:
        Optional iterable of STUN endpoints for diagnostics.
    nat_role:
        0 when this machine is reachable without NAT; 1 when it must initiate
        the hole punch.
    punch_timeout_s:
        Number of seconds to wait for handshake completion.
    """

    local_ports: tuple[int, int, int] = (30002, 30001, 30003)
    remote_hint: tuple[str, int, int, int] = ("127.0.0.1", 30002, 30001, 30003)
    stun_servers: Sequence[str] = ()
    nat_role: int = 1
    punch_timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if self.nat_role not in (0, 1):
            raise ValueError("nat_role must be 0 (passive) or 1 (active)")


@dataclass(slots=True)
class AudioConfig:
    """
    Parameters for the audio transport layer.

    Attributes
    ----------
    sample_rate_hz:
        PCM sampling rate; legacy scripts use 16 kHz mono.
    channels:
        Number of channels per stream (1=mono). APIs currently assume mono.
    chunk_frames:
        Frames per audio packet; must be a power of two between 128 and 4096.
    buffer_chunks:
        Number of chunks to buffer client-side before playback starts.
    format_tag:
        PyAudio format constant (e.g., `pyaudio.paInt16`). Stored as int to
        avoid importing PyAudio in pure-config contexts.
    """

    sample_rate_hz: int = 16_000
    channels: int = 1
    chunk_frames: int = 512
    buffer_chunks: int = 4
    format_tag: int = 8  # matches pyaudio.paInt16

    def __post_init__(self) -> None:
        chunk = self.chunk_frames
        if not (128 <= chunk <= 4096):
            raise ValueError("chunk_frames must be between 128 and 4096")
        if chunk & (chunk - 1) != 0:
            raise ValueError("chunk_frames must be a power of two")
        if not (1 <= self.buffer_chunks <= 25):
            raise ValueError("buffer_chunks must be between 1 and 25")


@dataclass(slots=True)
class RecordingConfig:
    """
    Controls how audio is captured to disk.

    Attributes
    ----------
    directory:
        Base directory where recording artifacts should be stored.
    local_track:
        Optional filename for the local microphone track (relative to directory).
    remote_track:
        Optional filename for the remote playback track.
    mix_track:
        Optional filename for a combined/mixdown track.
    """

    directory: Path = Path("data")
    local_track: Path | None = None
    remote_track: Path | None = None
    mix_track: Path | None = None


@dataclass(slots=True)
class SessionConfig:
    """
    Aggregate configuration consumed by the high-level session interface.

    Attributes
    ----------
    participant_id:
        String identifier (e.g., `011`), used for logging/filenames.
    role:
        Logical role label such as `'A'` / `'B'` or `'speaker'`.
    network:
        NetworkConfig instance governing socket creation.
    audio:
        AudioConfig instance controlling PyAudio stream parameters.
    recording:
        RecordingConfig describing how audio should be persisted.
    debug:
        Enable additional diagnostics (e.g., debug handshake phase).
    metadata:
        Free-form dict for experimenters to stash extra context.
    """

    participant_id: str
    role: str
    network: NetworkConfig = field(default_factory=NetworkConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    debug: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.participant_id:
            raise ValueError("participant_id must be non-empty")
        if not self.role:
            raise ValueError("role must be non-empty")
