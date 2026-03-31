"""
Standalone conversation soundcheck for couple tasks.

Run this before conversation tasks to negotiate audio levels with the partner and
persist the chosen volume to the NeuroTalk YAML config.
"""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path

import yaml
from psychopy import core  # type: ignore[import-not-found]

if __package__:
    from .utils import (
        close_window_and_restore_display,
        configure_runtime_logging,
        create_window,
    )
else:  # pragma: no cover - script-mode support
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from couple_tasks.utils import (  # type: ignore[import-not-found]
        close_window_and_restore_display,
        configure_runtime_logging,
        create_window,
    )

from neurotalk.config import SessionConfig
from neurotalk.config_cli import add_config_arguments, load_config_from_args
from neurotalk.session import ConversationSession, SessionFaultError
from neurotalk.soundcheck import run_conversation_soundcheck

SCANNER = None
WIN_SIZE = (1280, 800)
FULLSCR = True
DISPLAY = 0


def main(
    *,
    session_cfg: SessionConfig,
    config_path: Path,
    ui: str,
    scanner: str | None,
    fullscr: bool,
    display: int,
    log_level: str,
) -> None:
    if display < 0:
        msg = "--display must be >= 0"
        raise ValueError(msg)
    configure_runtime_logging(log_level)

    conv_session: ConversationSession | None = None
    win = None
    try:
        conv_session = ConversationSession(
            session_cfg, recording_enabled=False, recording_label="soundcheck"
        )
        conv_session.connect()

        if ui in {"auto", "psychopy"}:
            win = create_window(
                scanner=scanner, size=WIN_SIZE, fullscr=fullscr, screen=display
            )

        result = run_conversation_soundcheck(conv_session, ui=ui, win=win)
        logging.info(
            "Soundcheck result playback_gain=%.3f (%s%%); saving to %s",
            result.playback_gain,
            result.volume_percent,
            config_path,
        )
        session_cfg.audio.playback_gain = result.playback_gain
        session_cfg.metadata["playback_volume_percent"] = result.volume_percent
        _write_playback_gain_only(
            config_path,
            playback_gain=result.playback_gain,
            volume_percent=result.volume_percent,
        )
    except SessionFaultError as exc:
        logging.error(
            "NeuroTalk session fault detected (%s). Stop and restart the run.",
            exc,
        )
    except KeyboardInterrupt:
        logging.info("Soundcheck aborted.")
    finally:
        if conv_session is not None:
            conv_session.close()
        if win is not None:
            close_window_and_restore_display(win)
        core.quit()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Conversation soundcheck")
    parser.add_argument(
        "--ui",
        choices=["auto", "psychopy", "console"],
        default="auto",
        help="UI to use for soundcheck (default: auto).",
    )
    parser.add_argument(
        "--fullscreen",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Run in fullscreen mode for PsychoPy UI (use --no-fullscreen for windowed).",
    )
    parser.add_argument(
        "--scanner",
        choices=["skyra", "prisma"],
        default=None,
        help="Monitor profile to use for the scanner display (default: laptop).",
    )
    parser.add_argument(
        "--display",
        type=int,
        default=DISPLAY,
        help="Display index to use for PsychoPy window (0=primary monitor).",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Console log level (DEBUG, INFO, WARNING, etc.)",
    )
    add_config_arguments(parser)
    return parser


def _update_block_value(
    text: str, *, key: str, value: str, block: str | None = None
) -> tuple[str, bool]:
    pattern = rf"(?m)^(?P<indent>\s*){re.escape(key)}:\s*[^#\n]*(?P<comment>\s*#.*)?$"
    match = re.search(pattern, text)
    if match:
        indent = match.group("indent") or ""
        comment = match.group("comment") or ""
        if comment and not comment.startswith(" "):
            comment = f" {comment}"
        new_line = f"{indent}{key}: {value}{comment}"
        updated = re.sub(pattern, new_line, text, count=1)
        return updated, True

    if block is None:
        return text, False

    block_match = re.search(rf"(?m)^{re.escape(block)}:\s*$", text)
    if not block_match:
        addition = f"\n{block}:\n  {key}: {value}\n"
        return text + addition, True

    after_block = re.search(r"(?m)^[A-Za-z0-9_-]+:\s*$", text[block_match.end() :])
    insert_pos = block_match.end() + (after_block.start() if after_block else 0)
    prefix = text[:insert_pos]
    suffix = text[insert_pos:]
    if not prefix.endswith("\n"):
        prefix += "\n"
    new_line = f"  {key}: {value}\n"
    return prefix + new_line + suffix, True


def _write_playback_gain_only(
    config_path: Path, *, playback_gain: float, volume_percent: int
) -> None:
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "audio": {"playback_gain": playback_gain},
            "metadata": {"playback_volume_percent": volume_percent},
        }
        config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        logging.info(
            "Config file not found; created new file with playback_gain at %s",
            config_path,
        )
        return

    updated = text
    updated, gain_changed = _update_block_value(
        updated,
        key="playback_gain",
        value=f"{playback_gain:.3f}",
        block="audio",
    )
    updated, meta_changed = _update_block_value(
        updated,
        key="playback_volume_percent",
        value=str(volume_percent),
        block="metadata",
    )

    if (gain_changed or meta_changed) and updated != text:
        config_path.write_text(updated, encoding="utf-8")
        logging.info(
            "Persisted playback_gain=%.3f (%s%%) to %s (metadata updated=%s)",
            playback_gain,
            volume_percent,
            config_path,
            meta_changed,
        )
    else:
        logging.info(
            "No changes written; playback_gain already up to date in %s", config_path
        )


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    cfg_path = Path(args.config) if args.config else Path("neurotalk.yaml")
    try:
        session_cfg = load_config_from_args(args)
    except FileNotFoundError:
        session_cfg = SessionConfig()
    main(
        session_cfg=session_cfg,
        config_path=cfg_path,
        ui=args.ui,
        scanner=args.scanner,
        fullscr=args.fullscreen,
        display=args.display,
        log_level=args.log_level,
    )
