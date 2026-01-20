"""
Standalone conversation soundcheck for couple tasks.

Run this before conversation tasks to negotiate audio levels with the partner and
persist the chosen volume to the NeuroTalk YAML config.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from psychopy import core, logging as pylogging  # type: ignore[import-not-found]

if __package__:
    from .utils import create_window
else:  # pragma: no cover - script-mode support
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from couple_tasks.utils import create_window  # type: ignore[import-not-found]

from neurotalk.config import SessionConfig
from neurotalk.config_cli import add_config_arguments, load_config_from_args
from neurotalk.session import ConversationSession
from neurotalk.soundcheck import run_conversation_soundcheck

SCANNER = None
WIN_SIZE = (1280, 800)
FULLSCR = True


def main(
    *,
    session_cfg: SessionConfig,
    config_path: Path,
    ui: str,
    scanner: str | None,
    fullscr: bool,
    log_level: str,
) -> None:
    level_name = log_level.upper()
    level_value = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level_value, format="%(message)s")
    pylogging.console.setLevel(level_value)

    conv_session: ConversationSession | None = None
    win = None
    try:
        conv_session = ConversationSession(
            session_cfg, recording_enabled=False, recording_label="soundcheck"
        )
        conv_session.connect()

        if ui in {"auto", "psychopy"}:
            win = create_window(scanner=scanner, size=WIN_SIZE, fullscr=fullscr)

        result = run_conversation_soundcheck(conv_session, ui=ui, win=win)
        logging.info(
            "Soundcheck result playback_gain=%.3f (%s%%); saving to %s",
            result.playback_gain,
            result.volume_percent,
            config_path,
        )
        session_cfg.audio.playback_gain = result.playback_gain
        session_cfg.metadata["playback_volume_percent"] = result.volume_percent
        config_path.parent.mkdir(parents=True, exist_ok=True)
        session_cfg.to_yaml(config_path)
        logging.info(
            "Persisted playback_gain=%.3f (%s%%) from soundcheck to config file at %s",
            result.playback_gain,
            result.volume_percent,
            config_path,
        )
    except KeyboardInterrupt:
        logging.info("Soundcheck aborted.")
    finally:
        if conv_session is not None:
            conv_session.close()
        if win is not None:
            win.close()
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
        "--log-level",
        type=str,
        default="INFO",
        help="Console log level (DEBUG, INFO, WARNING, etc.)",
    )
    add_config_arguments(parser)
    return parser


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
        log_level=args.log_level,
    )
