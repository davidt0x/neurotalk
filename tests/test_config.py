from __future__ import annotations

from pathlib import Path

import pytest

from neurotalk import (
    AudioDeviceConfig,
    ConfigurationError,
    RecordingConfig,
    SessionConfig,
    SignalingConfig,
)


def test_signaling_config_freezes_headers() -> None:
    headers = {"Authorization": "Bearer token"}
    signaling = SignalingConfig(url="ws://localhost:8765", room="dyad01", headers=headers)
    headers["Authorization"] = "mutated"
    assert signaling.headers["Authorization"] == "Bearer token"


def test_audio_device_config_validation() -> None:
    with pytest.raises(ConfigurationError):
        AudioDeviceConfig(sample_rate=0)
    with pytest.raises(ConfigurationError):
        AudioDeviceConfig(channels=3)


def test_recording_config_targets() -> None:
    recording = RecordingConfig(
        microphone_path=Path("mic.wav"),
        remote_path=None,
        mixed_path=Path("mix.wav"),
    )
    assert recording.active_targets() == (Path("mic.wav"), Path("mix.wav"))


def test_session_config_defaults() -> None:
    config = SessionConfig(peer_id="dyad01-A", signaling=SignalingConfig(url="ws://localhost:8765", room="dyad01"))
    assert config.audio.sample_rate == 48_000
    assert config.stun_servers == ("stun:stun.l.google.com:19302",)
    assert config.initiator is False


def test_session_config_to_dict_roundtrip() -> None:
    config = SessionConfig(
        peer_id="dyad01-A",
        signaling=SignalingConfig(url="ws://localhost:8765", room="dyad01"),
        audio=AudioDeviceConfig(input_device="mic", output_device="spk"),
        recording=RecordingConfig(microphone_path=Path("mic.wav")),
        initiator=True,
    )
    as_dict = config.to_dict()
    assert as_dict["peer_id"] == "dyad01-A"
    assert as_dict["audio"]["input_device"] == "mic"
    assert as_dict["recording"]["microphone"] == "mic.wav"
    assert as_dict["initiator"] is True
