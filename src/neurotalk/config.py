from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, MutableMapping, Sequence, Tuple

from .exceptions import ConfigurationError

DEFAULT_STUN_SERVERS: Tuple[str, ...] = ("stun:stun.l.google.com:19302",)


@dataclass(slots=True, frozen=True)
class SignalingConfig:
    """Network configuration for the built-in signaling service."""

    url: str
    room: str
    token: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    verify_tls: bool = True

    def __post_init__(self) -> None:
        if not self.url:
            raise ConfigurationError("Signaling URL must be a non-empty string.")
        if not self.room:
            raise ConfigurationError("Room must be a non-empty string.")
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(slots=True, frozen=True)
class AudioDeviceConfig:
    """Configuration for local audio input/output devices."""

    input_device: str | int | None = None
    output_device: str | int | None = None
    sample_rate: int = 48_000
    channels: int = 1

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ConfigurationError("Sample rate must be a positive integer.")
        if self.channels not in (1, 2):
            raise ConfigurationError("Channels must be either 1 (mono) or 2 (stereo).")


@dataclass(slots=True, frozen=True)
class RecordingConfig:
    """Optional audio recording outputs for the session."""

    microphone_path: Path | None = None
    remote_path: Path | None = None
    mixed_path: Path | None = None

    def active_targets(self) -> tuple[Path, ...]:
        """Return the subset of recording targets that are configured."""
        targets = tuple(path for path in (self.microphone_path, self.remote_path, self.mixed_path) if path)
        return targets


@dataclass(slots=True, frozen=True)
class SessionConfig:
    """Aggregate configuration for a NeuroTalk session."""

    peer_id: str
    signaling: SignalingConfig
    audio: AudioDeviceConfig = field(default_factory=AudioDeviceConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    stun_servers: Sequence[str] = field(default_factory=lambda: DEFAULT_STUN_SERVERS)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    initiator: bool = False

    def __post_init__(self) -> None:
        if not self.peer_id:
            raise ConfigurationError("Peer ID must be provided.")
        # Freeze metadata and stun server list to avoid accidental runtime mutation.
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "stun_servers", tuple(self.stun_servers))

    @property
    def ice_servers(self) -> list[dict[str, Any]]:
        """Return ICE server entries consumable by aiortc."""
        servers: list[dict[str, Any]] = []
        if self.stun_servers:
            servers.append({"urls": list(self.stun_servers)})
        return servers

    def to_dict(self) -> dict[str, Any]:
        """Serialize a subset of configuration for diagnostics/logging."""
        return {
            "peer_id": self.peer_id,
            "signaling": {"url": self.signaling.url, "room": self.signaling.room},
            "audio": {
                "input_device": self.audio.input_device,
                "output_device": self.audio.output_device,
                "sample_rate": self.audio.sample_rate,
                "channels": self.audio.channels,
            },
            "recording": {
                "microphone": str(self.recording.microphone_path) if self.recording.microphone_path else None,
                "remote": str(self.recording.remote_path) if self.recording.remote_path else None,
                "mixed": str(self.recording.mixed_path) if self.recording.mixed_path else None,
            },
            "stun_servers": list(self.stun_servers),
            "metadata": dict(self.metadata),
            "initiator": self.initiator,
        }
