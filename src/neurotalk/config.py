"""
Configuration models for NeuroTalk sessions.

These dataclasses provide a structured way to supply experiment- and network
parameters without relying on module-level globals. Actual defaults mirror the
legacy CONV/DIAD scripts but callers are free to override anything.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

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
        the hole punch. Use ``"auto"`` to allow either side to start and punch.
    punch_timeout_s:
        Number of seconds to wait for handshake completion.
    """

    local_ports: tuple[int, int, int] = (30002, 30001, 30003)
    remote_hint: tuple[str, int, int, int] = ("127.0.0.1", 30002, 30001, 30003)
    stun_servers: Sequence[str] = ()
    nat_role: int | str = "auto"
    punch_timeout_s: float = 30.0

    def __post_init__(self) -> None:
        role = self.nat_role
        if isinstance(role, str):
            role = role.lower()
            object.__setattr__(self, "nat_role", role)
        if role not in (0, 1, "auto"):
            msg = "nat_role must be 0 (passive), 1 (active), or 'auto'"
            raise ValueError(msg)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> NetworkConfig:
        if data is None:
            return cls()
        d = dict(data)
        if "local_ports" in d:
            d["local_ports"] = tuple(d["local_ports"])
        if "remote_hint" in d:
            d["remote_hint"] = tuple(d["remote_hint"])
        if "stun_servers" in d:
            d["stun_servers"] = tuple(d["stun_servers"])
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _normalize(asdict(self)))


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
    mock_devices:
        When True, bypass real sound hardware and use the mock audio backend
        which produces synthetic silence for testing.
    """

    sample_rate_hz: int = 16_000
    channels: int = 1
    chunk_frames: int = 512
    buffer_chunks: int = 4
    format_tag: int = 8  # matches pyaudio.paInt16
    mock_devices: bool = False
    playback_gain: float = 1.0

    def __post_init__(self) -> None:
        chunk = self.chunk_frames
        if not (128 <= chunk <= 4096):
            msg = "chunk_frames must be between 128 and 4096"
            raise ValueError(msg)
        if chunk & (chunk - 1) != 0:
            msg = "chunk_frames must be a power of two"
            raise ValueError(msg)
        if not (1 <= self.buffer_chunks <= 25):
            msg = "buffer_chunks must be between 1 and 25"
            raise ValueError(msg)
        if self.playback_gain < 0.0:
            msg = "playback_gain must be non-negative"
            raise ValueError(msg)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> AudioConfig:
        if data is None:
            return cls()
        d = dict(data)
        if "playback_gain" in d:
            raw_gain = d["playback_gain"]
            if isinstance(raw_gain, str):
                stripped = raw_gain.split("#", 1)[0].strip()
                with contextlib.suppress(ValueError):
                    d["playback_gain"] = float(stripped)
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _normalize(asdict(self)))


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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> RecordingConfig:
        if data is None:
            return cls()
        d = dict(data)
        if "directory" in d:
            d["directory"] = Path(d["directory"])
        for key in ("local_track", "remote_track", "mix_track"):
            if key in d and d[key] is not None:
                d[key] = Path(d[key])
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _normalize(asdict(self)))


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

    participant_id: str = "participant"
    role: str = "role"
    network: NetworkConfig = field(default_factory=NetworkConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    debug: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.participant_id:
            self.participant_id = "participant"
        if not self.role:
            self.role = "role"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> SessionConfig:
        data_obj: object = data or {}
        if not isinstance(data_obj, Mapping):
            msg = "Config root must be a mapping"
            raise ValueError(msg)
        data = data_obj
        return cls(
            participant_id=str(data.get("participant_id", "")),
            role=str(data.get("role", "")),
            network=NetworkConfig.from_dict(data.get("network")),
            audio=AudioConfig.from_dict(data.get("audio")),
            recording=RecordingConfig.from_dict(data.get("recording")),
            debug=bool(data.get("debug", False)),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "role": self.role,
            "network": self.network.to_dict(),
            "audio": self.audio.to_dict(),
            "recording": self.recording.to_dict(),
            "debug": self.debug,
            "metadata": self.metadata,
        }

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> SessionConfig:
        cfg_path = Path(path) if path is not None else Path("neurotalk.yaml")
        if not cfg_path.exists():
            msg = f"Config file not found: {cfg_path}"
            raise FileNotFoundError(msg)
        with cfg_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return cls.from_dict(data)

    def to_yaml(self, path: str | Path) -> None:
        cfg_path = Path(path)
        cfg_path.write_text(yaml.safe_dump(self.to_dict()), encoding="utf-8")


def load_neurotalk_config(path: str | Path | None = None) -> SessionConfig:
    """
    Load a SessionConfig from YAML (default: neurotalk.yaml in CWD).
    """

    return SessionConfig.from_yaml(path)


def _normalize(obj: Any) -> Any:
    """Convert dataclass asdict output into YAML-friendly primitives."""

    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, tuple):
        return [_normalize(x) for x in obj]
    if isinstance(obj, list):
        return [_normalize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    return obj
