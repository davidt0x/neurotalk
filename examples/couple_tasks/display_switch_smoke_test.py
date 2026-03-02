from __future__ import annotations

import argparse
import importlib
from pathlib import Path

from psychopy import core, logging  # type: ignore[import-not-found]

if __package__:
    task_utils = importlib.import_module(f"{__package__}.utils")
else:  # pragma: no cover - script-mode support
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    task_utils = importlib.import_module("couple_tasks.utils")


def main(*, hold_seconds: float) -> None:
    """Switch to clone mode briefly, then restore extend mode."""
    if hold_seconds < 0:
        msg = "--hold-seconds must be >= 0"
        raise ValueError(msg)

    logging.console.setLevel(logging.INFO)
    logging.info("Starting display switch smoke test.")

    if not task_utils.has_multiple_displays():
        logging.warning(
            "Skipping clone/extend test because fewer than two displays are available."
        )
        return

    cloned = task_utils.switch_display_mode("clone")
    if not cloned:
        logging.error("Clone switch failed; not attempting timed hold.")
        return

    try:
        logging.info(
            f"Display is in clone mode. Holding for {hold_seconds:.1f} seconds before restoring."
        )
        core.wait(hold_seconds)
    finally:
        if task_utils.switch_display_mode("extend"):
            logging.info("Extend switch completed.")
        else:
            logging.error("Extend switch failed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Smoke test for DisplaySwitch clone/extend behavior."
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=5.0,
        help="Seconds to remain in clone mode before restoring extend.",
    )
    args = parser.parse_args()
    main(hold_seconds=args.hold_seconds)
