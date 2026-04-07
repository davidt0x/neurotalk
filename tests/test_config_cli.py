from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from neurotalk.config import SessionConfig
from neurotalk.config_cli import add_config_arguments, load_config_from_args


def _parse_with_args(args: list[str]) -> SessionConfig:
    parser = argparse.ArgumentParser()
    add_config_arguments(parser)
    ns = parser.parse_args(args)
    return load_config_from_args(ns)


def test_loads_default_when_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _parse_with_args([])
    assert cfg.participant_id == "participant"
    assert cfg.role == "role"
    assert cfg.audio.channels == 1
    assert cfg.audio.playback_gain == 1.0
    assert cfg.audio.input_device is None
    assert cfg.network.peer_warning_s == 3.0


def test_loads_from_config_and_overrides(tmp_path: Path) -> None:
    cfg_path = tmp_path / "neurotalk.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "participant_id": "base",
                "role": "base",
                "network": {"local_ports": [1, 2, 3]},
                "audio": {"channels": 1, "playback_gain": 0.8, "input_device": 5},
                "recording": {"directory": str(tmp_path / "data")},
            }
        )
    )
    args = [
        "--config",
        str(cfg_path),
        "--participant-id",
        "override",
        "--channels",
        "2",
        "--local-ports",
        "10,11,12",
        "--stun-server",
        "stun:example.org",
        "--playback-gain",
        "1.5",
        "--input-device",
        "7",
        "--output-device",
        "USB Audio Device",
        "--recording-dir",
        str(tmp_path / "rec"),
    ]
    cfg = _parse_with_args(args)
    assert cfg.participant_id == "override"
    assert cfg.role == "base"
    assert cfg.audio.channels == 2
    assert cfg.audio.playback_gain == 1.5
    assert cfg.audio.input_device == 7
    assert cfg.audio.output_device == "USB Audio Device"
    assert cfg.network.local_ports == (10, 11, 12)
    assert cfg.network.stun_servers == ("stun:example.org",)
    assert cfg.recording.directory == tmp_path / "rec"


def test_parses_remote_hint_and_metadata(tmp_path: Path) -> None:
    cfg_path = tmp_path / "neurotalk.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"network": {"remote_hint": ["1.2.3.4", 4, 5, 6]}})
    )
    args = [
        "--config",
        str(cfg_path),
        "--remote-hint",
        "2.2.2.2,20,21,22",
        "--metadata",
        "task=demo,session=1",
    ]
    cfg = _parse_with_args(args)
    assert cfg.network.remote_hint == ("2.2.2.2", 20, 21, 22)
    assert cfg.metadata["task"] == "demo"
    assert cfg.metadata["session"] == "1"


def test_missing_ports_raises() -> None:
    parser = argparse.ArgumentParser()
    add_config_arguments(parser)
    ns = parser.parse_args(["--local-ports", "1,2"])
    with pytest.raises(ValueError, match="Expected 3 comma-separated ports"):
        load_config_from_args(ns)
