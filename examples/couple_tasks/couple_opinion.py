"""
Solo Opinion Task (no passing).
- Waits for TTL '=', brief blank, shows opinion prompt for OPINION_S seconds (or until submit key if set).
- Logs:
  * data/<base>_OPIN_min_<date>.csv (main wide CSV via ExperimentHandler)
  * data/<base>_OPIN_TimingsLog_<date>.csv (minimal timings log)
  * data/<base>_OPIN_TTLtimestamps_<date>.csv (verbose TTL pings)
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from psychopy import core, event, logging

if __package__:
    from .log import TaskLogger
    from .utils import (
        create_window,
        decode_pid,
        load_assignment_row,
        pick_first_speaker,
        slug,
        text_factory,
    )
else:  # pragma: no cover - script-mode support
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from couple_tasks.log import TaskLogger  # type: ignore[import-not-found]
    from couple_tasks.utils import (  # type: ignore[import-not-found]
        create_window,
        decode_pid,
        load_assignment_row,
        pick_first_speaker,
        slug,
        text_factory,
    )

# ---------- config ----------
SCANNER = None
WIN_SIZE = (1280, 800)
FULLSCR = True
DISPLAY = 0
LETTER_H = 0.07
WRAP_W = 2

INSTR_BLANK_S = 10.0
OPINION_S = 120.0
OPINION_PROMPT = "Please share your opinion on the problem area you just discussed:\n\n"

AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_DTYPE = "int16"

KEY_SUBMIT = None  # set to a key string if you want manual submit (e.g., 'return')
KEY_QUIT = "escape"
TTL_KEY = "5"
TTL_ACCEPT = {"equal", "=", TTL_KEY}

CSV_FILENAME = "participant_counterbalancing.csv"
SESSION_TYPE = "couple"


def main(
    pid: str,
    session: int,
    conflict: str,
    csv_path: str,
    *,
    display: int = DISPLAY,
):
    if session not in (1, 2):
        msg = "Session must be 1 or 2"
        raise ValueError(msg)
    if not (conflict and conflict.strip()):
        msg = "You must provide a non-empty --conflict string"
        raise ValueError(msg)
    if display < 0:
        msg = "--display must be >= 0"
        raise ValueError(msg)

    dyad, role = decode_pid(pid)

    assignment = load_assignment_row(csv_path, pid)
    starters = assignment.starters()
    exp_condition = assignment.condition
    first_speaker = pick_first_speaker(starters, session=session, session_type="couple")

    conflict_text = conflict.strip()

    logger = TaskLogger(
        pid=pid,
        session=session,
        session_type=SESSION_TYPE,
        exp_condition=exp_condition,
        first_speaker=first_speaker,
        conflict_text_slug=slug(conflict_text),
        task_code="OPIN",
        dyad=dyad,
        participant_role=role,
        conflict_text=conflict_text,
    )
    logging.console.setLevel(logging.WARNING)

    win = create_window(scanner=SCANNER, size=WIN_SIZE, fullscr=FULLSCR, screen=display)
    make_text = text_factory(win, letter_height=LETTER_H, wrap_width=WRAP_W)

    audio_data: np.ndarray | None = None
    audio_output_path = Path(f"{logger.filename}_audio.wav")

    def quit_task() -> None:
        if audio_data is not None:
            sd.stop()
        logger.close()
        win.close()
        core.quit()

    show_instructions = make_text(text="")
    show_opinion = make_text(text="", pos=(0, 0.25))
    show_timer = make_text(text="", pos=(0, -0.70))
    show_blank = make_text(text="+", pos=(0, 0.00))
    show_end = make_text(text="You are now done with this task.")

    instr = (
        f"In the last task, you discussed {conflict_text.upper()} with your partner.\n\n\n"
        f"Next, we would like you to report your own personal opinion on {conflict_text.upper()}.\n"
        "For N minutes, please speak aloud \n"
        "whatever you can about your thoughts and feelings on the topic.\n\n\n"
        "This recording is confidential.\n"
        "The experimenter has muted the audio, and what you say now will\n"
        "NOT be shared with your partner.\n\n\n"
        "You’ll first see a fixation cross for 10 seconds.\n"
        " After that, you will see instructions to begin speaking."
    )
    event.clearEvents(eventType="keyboard")
    show_instructions.setText(instr)
    trigger_source: str | None = None
    while trigger_source is None:
        show_instructions.draw()
        win.flip()
        keys = event.getKeys()
        if KEY_QUIT in keys:
            quit_task()
        if any(k in TTL_ACCEPT for k in keys):
            trigger_source = "ttl"
            break
        core.wait(0.01)

    run_clock = core.Clock()

    logger.log_timing(
        role_label=f"trigger_start_{trigger_source}",
        run_clock=run_clock,
        phase_clock=None,
    )
    logger.log_ttl(
        role_label="",
        segment=f"trigger_start_{trigger_source}",
        run_clock=run_clock,
        phase_clock=None,
    )

    show_blank.draw()
    win.flip()
    blank_clock = core.Clock()
    while blank_clock.getTime() < INSTR_BLANK_S:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if TTL_KEY in keys:
                logger.log_ttl(
                    role_label="",
                    segment="blank",
                    run_clock=run_clock,
                    phase_clock=None,
                )
                event.clearEvents(eventType="keyboard")
            if KEY_QUIT in keys:
                quit_task()
        core.wait(0.01)

    show_opinion.setText(f"{OPINION_PROMPT} {conflict_text.upper()}")
    show_opinion.setAutoDraw(True)
    show_timer.setAutoDraw(True)
    op_clock = core.Clock()

    logger.experiment.addData("dyad", dyad)
    logger.experiment.addData("session", session)
    logger.experiment.addData("exp_condition", exp_condition)
    logger.experiment.addData("event", "opinion_start")
    logger.experiment.addData("role", "")
    logger.experiment.addData("onset_run_s", run_clock.getTime())
    logger.experiment.addData("onset_phase_s", op_clock.getTime())
    logger.experiment.addData("conflict_text", conflict_text)
    logger.experiment.addData("first_speaker", first_speaker)
    logger.experiment.addData("participant_role", role)
    logger.experiment.nextEntry()

    logger.log_timing(
        role_label="opinion_start",
        run_clock=run_clock,
        phase_clock=op_clock,
    )

    frames = int(OPINION_S * AUDIO_SAMPLE_RATE)
    audio_data = sd.rec(
        frames,
        samplerate=AUDIO_SAMPLE_RATE,
        channels=AUDIO_CHANNELS,
        dtype=AUDIO_DTYPE,
    )

    submitted = False
    while op_clock.getTime() < OPINION_S and not submitted:
        remaining = round(OPINION_S - op_clock.getTime())
        show_timer.setText(f"{remaining} seconds")
        keys = event.getKeys([TTL_KEY, KEY_QUIT] + ([KEY_SUBMIT] if KEY_SUBMIT else []))
        if keys:
            if TTL_KEY in keys:
                logger.log_ttl(
                    role_label="",
                    segment="opinion",
                    run_clock=run_clock,
                    phase_clock=op_clock,
                )
                event.clearEvents(eventType="keyboard")
            if KEY_QUIT in keys:
                quit_task()
            if KEY_SUBMIT and (KEY_SUBMIT in keys):
                submitted = True
                event.clearEvents(eventType="keyboard")
        win.flip()

    logger.log_timing(
        role_label="opinion_end",
        run_clock=run_clock,
        phase_clock=op_clock,
    )

    logger.experiment.addData("dyad", dyad)
    logger.experiment.addData("session", session)
    logger.experiment.addData("exp_condition", exp_condition)
    logger.experiment.addData("event", "opinion_end")
    logger.experiment.addData("role", "")
    logger.experiment.addData("onset_run_s", run_clock.getTime())
    logger.experiment.addData("onset_phase_s", op_clock.getTime())
    logger.experiment.addData("conflict_text", conflict_text)
    logger.experiment.addData("first_speaker", first_speaker)
    logger.experiment.addData("participant_role", role)
    logger.experiment.nextEntry()

    show_opinion.setAutoDraw(False)
    show_timer.setAutoDraw(False)
    win.flip()

    show_end.draw()
    win.flip()
    core.wait(1.0)

    if audio_data is not None:
        sd.wait()
        with wave.open(str(audio_output_path), "wb") as wf:
            wf.setnchannels(AUDIO_CHANNELS)
            wf.setsampwidth(np.dtype(AUDIO_DTYPE).itemsize)
            wf.setframerate(AUDIO_SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())

    logger.save_and_close()
    win.close()
    core.quit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--pid",
        "-p",
        type=str,
        required=True,
        help="3-digit participant ID (e.g., 011, 402)",
    )
    ap.add_argument(
        "--session",
        "-s",
        type=int,
        choices=[1, 2],
        required=True,
        help="Session number (1 or 2)",
    )
    ap.add_argument(
        "--conflict",
        "-t",
        type=str,
        required=True,
        help="Human-readable conflict topic to display/log",
    )
    ap.add_argument(
        "--csv",
        "-c",
        type=str,
        default=CSV_FILENAME,
        help="Path to participant_counterbalancing.csv",
    )
    ap.add_argument(
        "--display",
        type=int,
        default=DISPLAY,
        help="Display index to use for PsychoPy window (0=primary monitor).",
    )
    args = ap.parse_args()
    main(
        pid=args.pid,
        session=args.session,
        conflict=args.conflict,
        csv_path=args.csv,
        display=args.display,
    )
