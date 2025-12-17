from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from .config import SessionConfig

_console = Console(stderr=True)


def add_config_arguments(parser: argparse.ArgumentParser) -> None:
    """Add common NeuroTalk configuration arguments to a parser."""

    parser.add_argument(
        "--config",
        type=Path,
        help="Path to neurotalk YAML config (default: neurotalk.yaml if present).",
    )
    parser.add_argument("--participant-id", help="Participant identifier.")
    parser.add_argument("--role", help="Role label (e.g., A/B or speaker/listener).")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose/diagnostic logging in the session.",
    )
    parser.add_argument(
        "--metadata",
        help="Free-form metadata as key=value pairs separated by commas (e.g., k1=v1,k2=v2).",
    )

    # Network overrides
    parser.add_argument(
        "--local-ports",
        help="Comma-separated UDP ports: inbound,outbound,control (e.g., 30002,30001,30003).",
    )
    parser.add_argument(
        "--remote-hint",
        help="Remote hint as ip,inbound,outbound,control (e.g., 127.0.0.1,30002,30001,30003).",
    )
    parser.add_argument(
        "--stun-server",
        action="append",
        dest="stun_servers",
        help="STUN server URI (may be given multiple times).",
    )
    parser.add_argument(
        "--nat-role",
        choices=["0", "1", "auto", "passive", "active"],
        help="0/passive = wait, 1/active = initiate, auto = probe and wait.",
    )
    parser.add_argument(
        "--punch-timeout",
        type=float,
        help="Seconds to wait for NAT punch to complete.",
    )

    # Audio overrides
    parser.add_argument("--sample-rate-hz", type=int, help="PCM sample rate.")
    parser.add_argument("--channels", type=int, help="Channel count.")
    parser.add_argument("--chunk-frames", type=int, help="Frames per audio packet.")
    parser.add_argument("--buffer-chunks", type=int, help="Client-side buffer chunks.")
    parser.add_argument("--format-tag", type=int, help="PyAudio format tag (int).")
    parser.add_argument(
        "--mock-devices",
        action="store_true",
        help="Use mock audio backend (no real I/O).",
    )

    # Recording overrides
    parser.add_argument("--recording-dir", type=Path, help="Recording base directory.")
    parser.add_argument("--local-track", type=Path, help="Override local mic filename.")
    parser.add_argument("--remote-track", type=Path, help="Override remote filename.")
    parser.add_argument("--mix-track", type=Path, help="Override mixed filename.")


def _print_loaded_config(path: Path) -> None:
    """
    Notify the user that a config file has been loaded, using colored output.
    """

    _console.print(f"[bold yellow]Loaded NeuroTalk config:[/bold yellow] {path}")


def load_config_from_args(args: argparse.Namespace) -> SessionConfig:
    """
    Load SessionConfig from --config (or neurotalk.yaml if present), applying CLI overrides.
    """

    cfg_path: Path | None = getattr(args, "config", None)
    if cfg_path:
        base_cfg = SessionConfig.from_yaml(cfg_path)
        _print_loaded_config(cfg_path)
    else:
        default_path = Path("neurotalk.yaml")
        if default_path.exists():
            base_cfg = SessionConfig.from_yaml(default_path)
            _print_loaded_config(default_path)
        else:
            base_cfg = SessionConfig()

    cfg = SessionConfig.from_dict(base_cfg.to_dict())  # clone to avoid mutating source

    # Top-level
    if getattr(args, "participant_id", None) is not None:
        cfg.participant_id = args.participant_id
    if getattr(args, "role", None) is not None:
        cfg.role = args.role
    if getattr(args, "debug", False):
        cfg.debug = True
    metadata_arg = getattr(args, "metadata", None)
    if metadata_arg:
        cfg.metadata.update(_parse_metadata(metadata_arg))

    # Network
    local_ports = getattr(args, "local_ports", None)
    if local_ports:
        cfg.network.local_ports = _parse_ports(local_ports, expected=3)
    remote_hint = getattr(args, "remote_hint", None)
    if remote_hint:
        cfg.network.remote_hint = _parse_remote_hint(remote_hint)
    stun_servers = getattr(args, "stun_servers", None)
    if stun_servers:
        cfg.network.stun_servers = tuple(stun_servers)
    if getattr(args, "nat_role", None) is not None:
        cfg.network.nat_role = _coerce_nat_role(args.nat_role)
    if getattr(args, "punch_timeout", None) is not None:
        cfg.network.punch_timeout_s = float(args.punch_timeout)

    # Audio
    for field in ("sample_rate_hz", "channels", "chunk_frames", "buffer_chunks", "format_tag"):
        value = getattr(args, field, None)
        if value is not None:
            setattr(cfg.audio, field, value)
    if getattr(args, "mock_devices", False):
        cfg.audio.mock_devices = True

    # Recording
    rec = cfg.recording
    if getattr(args, "recording_dir", None) is not None:
        rec.directory = args.recording_dir
    if getattr(args, "local_track", None) is not None:
        rec.local_track = args.local_track
    if getattr(args, "remote_track", None) is not None:
        rec.remote_track = args.remote_track
    if getattr(args, "mix_track", None) is not None:
        rec.mix_track = args.mix_track

    return cfg


def _parse_ports(text: str, expected: int) -> tuple[int, ...]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) != expected:
        msg = f"Expected {expected} comma-separated ports, got {len(parts)}"
        raise ValueError(msg)
    return tuple(int(p) for p in parts)


def _parse_remote_hint(text: str) -> tuple[str, int, int, int]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) != 4:
        msg = "Remote hint must be ip,inbound,outbound,control"
        raise ValueError(msg)
    ip = parts[0]
    ports = tuple(int(p) for p in parts[1:])
    return (ip, *ports)  # type: ignore[misc]


def _coerce_nat_role(value: str | int) -> int | str:
    text = str(value).strip().lower()
    if text in {"0", "passive"}:
        return 0
    if text in {"1", "active"}:
        return 1
    if text == "auto":
        return "auto"
    msg = "nat-role must be 0/passive, 1/active, or auto"
    raise ValueError(msg)


def _parse_metadata(text: str) -> dict[str, str]:
    """
    Parse k=v comma-separated pairs into a dict.
    """

    pairs = [p.strip() for p in text.split(",") if p.strip()]
    meta: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        meta[k.strip()] = v.strip()
    return meta
