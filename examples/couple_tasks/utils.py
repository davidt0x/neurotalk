from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from psychopy import core, logging, monitors, visual  # type: ignore[import-not-found]


@dataclass(frozen=True)
class AssignmentRow:
    participant_id: str
    condition: str
    neutral_session_1: str
    couple_session_1: str
    neutral_session_2: str
    couple_session_2: str
    first_topic: str | None = None
    second_topic: str | None = None

    def starters(self) -> dict[str, str]:
        return {
            "Neutral_session_1": self.neutral_session_1,
            "Couple_session_1": self.couple_session_1,
            "Neutral_session_2": self.neutral_session_2,
            "Couple_session_2": self.couple_session_2,
        }


def finalize_and_quit(
    conv_session,
    recording_dir: Path,
    logger,
    mixdown: bool,
    win,
) -> None:
    """
    Ensure audio artifacts and logs are flushed before exiting early.
    """

    try:
        if conv_session is not None:
            conv_session.close()
            try:
                export_dir = recording_dir / "segments"
                conv_session.export_segments(export_dir)
            except Exception as exc:  # pragma: no cover - best-effort shutdown
                logging.error("Failed to export segments: %s", exc)
            if mixdown:
                try:
                    mix_path = conv_session.export_mix_track()
                    if mix_path:
                        logging.info("Mixed audio written to %s", mix_path)
                except Exception as exc:  # pragma: no cover - best-effort shutdown
                    logging.error("Failed to generate mix track: %s", exc)
    finally:
        logger.save_and_close()
        win.close()
        core.quit()


def decode_pid(pid_str: str) -> tuple[int, str]:
    if not (pid_str.isdigit() and len(pid_str) == 3):
        msg = "PID must be a 3-digit code like 011 or 402"
        raise ValueError(msg)
    pid_num = int(pid_str)
    dyad = pid_num // 10
    person = pid_num % 10
    if person not in (1, 2) or dyad < 1:
        msg = f"Bad participant id: {pid_str}"
        raise ValueError(msg)
    role = "A" if person == 1 else "B"
    return dyad, role


def slug(text: str) -> str:
    cleaned = (text or "").strip().lower()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"[^a-z0-9_\-]", "", cleaned)
    return cleaned[:60] or "topic"


def _sniff_delimiter(csv_file: Path) -> str:
    with csv_file.open(encoding="utf-8", newline="") as fpeek:
        sample = fpeek.read(4096)
        fpeek.seek(0)
        try:
            sniff = csv.Sniffer().sniff(sample)
            return sniff.delimiter
        except Exception:
            return ","


def load_assignment_row(csv_path: str, pid: str) -> AssignmentRow:
    """
    Load a participant row from the assignment CSV.

    Supports files that either include topic columns ("first_topic" & "second_topic")
    or omit them. Always normalizes starter columns to uppercase.
    """
    csv_file = Path(csv_path)
    abs_path = csv_file.resolve()
    if not csv_file.exists():
        msg = (
            f"Cannot find CSV at '{csv_file}' (abs: '{abs_path}'). "
            f"Current working directory: '{Path.cwd()}'."
        )
        raise FileNotFoundError(msg)

    delimiter = _sniff_delimiter(csv_file)
    with csv_file.open(encoding="utf-8", newline="") as f:
        rdr = csv.DictReader(f, delimiter=delimiter)
        cols = set(rdr.fieldnames or [])
        required = {
            "participant_id",
            "condition",
            "Neutral_session_1",
            "Couple_session_1",
            "Neutral_session_2",
            "Couple_session_2",
        }
        missing = required - cols
        if missing:
            suggestions: dict[str, str] = {}
            lowmap = {c.lower().strip(): c for c in cols}
            for want in required:
                lw = want.lower().strip()
                if lw in lowmap and want not in cols:
                    suggestions[want] = lowmap[lw]
            hint = f" (Possible header variants: {suggestions})" if suggestions else ""
            msg = (
                f"CSV is missing required columns: {sorted(missing)}. "
                f"Found columns: {sorted(cols)}.{hint}"
            )
            raise ValueError(msg)

        for row in rdr:
            row_data = {**row}
            rid = (row_data.get("participant_id") or "").strip()
            if rid != pid:
                continue

            return AssignmentRow(
                participant_id=pid,
                condition=(row_data.get("condition") or "").strip(),
                neutral_session_1=(row_data.get("Neutral_session_1") or "")
                .strip()
                .upper(),
                couple_session_1=(row_data.get("Couple_session_1") or "")
                .strip()
                .upper(),
                neutral_session_2=(row_data.get("Neutral_session_2") or "")
                .strip()
                .upper(),
                couple_session_2=(row_data.get("Couple_session_2") or "")
                .strip()
                .upper(),
                first_topic=(
                    (row_data.get("first_topic") or "").strip() or None
                    if "first_topic" in cols
                    else None
                ),
                second_topic=(
                    (row_data.get("second_topic") or "").strip() or None
                    if "second_topic" in cols
                    else None
                ),
            )

    msg = (
        f"participant_id '{pid}' not found in {abs_path}. "
        f"Note: your script expects zero-padded IDs like '011'."
    )
    raise KeyError(msg)


def pick_first_speaker(
    starters: dict[str, str], *, session: int, session_type: str
) -> str:
    if session not in (1, 2):
        msg = "Session must be 1 or 2"
        raise ValueError(msg)
    key = f"{session_type.capitalize()}_session_{session}"
    value = starters.get(key, "").upper()
    if value not in ("A", "B"):
        msg = f"Starter value for {key} must be 'A' or 'B', got: {value!r}"
        raise ValueError(msg)
    return value


def make_monitor(scanner: str | None):
    if scanner == "skyra":
        mon = monitors.Monitor("skyra")
        mon.setSizePix((1920, 1080))
        mon.setWidth(64)
        mon.setDistance(89)
        mon.save()
    elif scanner == "prisma":
        mon = monitors.Monitor("prisma")
        mon.setSizePix((1920, 1080))
        mon.setWidth(56)
        mon.setDistance(107.5)
        mon.save()
    else:
        mon = monitors.Monitor("defaultLaptop")
    return mon


def create_window(
    *,
    scanner: str | None,
    size: tuple[int, int],
    fullscr: bool,
    color: str = "black",
    units: str = "norm",
):
    mon = make_monitor(scanner)
    win = visual.Window(
        size=size, color=color, fullscr=fullscr, units=units, monitor=mon
    )
    win.mouseVisible = False
    return win


def text_factory(win, *, letter_height: float, wrap_width: float):
    def builder(**kwargs):
        return visual.TextStim(
            win,
            height=letter_height,
            wrapWidth=wrap_width,
            color="white",
            **kwargs,
        )

    return builder
