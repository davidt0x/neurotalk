from __future__ import annotations

from pathlib import Path

import pytest

from neurotalk.config import AudioConfig, NetworkConfig, SessionConfig


def test_from_dict_explicit(tmp_path: Path) -> None:
    data = {
        "participant_id": "001",
        "role": "A",
        "debug": True,
        "metadata": {"task": "demo"},
        "network": {
            "local_ports": [1, 2, 3],
            "remote_hint": ["10.0.0.1", 4, 5, 6],
            "stun_servers": ["stun:example.org"],
            "nat_role": 0,
            "punch_timeout_s": 10.0,
        },
        "audio": {
            "sample_rate_hz": 22050,
            "channels": 2,
            "chunk_frames": 256,
            "buffer_chunks": 2,
            "format_tag": 8,
            "mock_devices": True,
        },
        "recording": {
            "directory": str(tmp_path / "data"),
            "local_track": "mic.wav",
            "remote_track": "remote.wav",
            "mix_track": "mix.wav",
        },
    }
    cfg = SessionConfig.from_dict(data)
    assert cfg.participant_id == "001"
    assert cfg.role == "A"
    assert cfg.debug is True
    assert cfg.metadata == {"task": "demo"}
    assert cfg.audio.playback_gain == 1.0
    assert cfg.network == NetworkConfig(
        local_ports=(1, 2, 3),
        remote_hint=("10.0.0.1", 4, 5, 6),
        stun_servers=("stun:example.org",),
        nat_role=0,
        punch_timeout_s=10.0,
        peer_timeout_s=5.0,
    )
    assert cfg.audio == AudioConfig(
        sample_rate_hz=22_050,
        channels=2,
        chunk_frames=256,
        buffer_chunks=2,
        format_tag=8,
        mock_devices=True,
    )
    assert cfg.recording.directory == tmp_path / "data"
    assert cfg.recording.local_track == Path("mic.wav")
    assert cfg.recording.remote_track == Path("remote.wav")
    assert cfg.recording.mix_track == Path("mix.wav")


def test_defaults_and_yaml_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "neurotalk.yaml"
    cfg = SessionConfig()
    cfg.to_yaml(cfg_path)

    monkeypatch.chdir(tmp_path)
    loaded = SessionConfig.from_yaml()
    assert loaded.participant_id == "participant"
    assert loaded.role == "role"
    assert loaded.audio.sample_rate_hz == 16_000
    assert loaded.audio.playback_gain == 1.0

    roundtrip = SessionConfig.from_dict(loaded.to_dict())
    assert roundtrip == loaded


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SessionConfig.from_yaml(tmp_path / "missing.yaml")
