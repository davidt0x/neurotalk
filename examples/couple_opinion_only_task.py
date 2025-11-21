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
import csv
import re
import time
from pathlib import Path

from psychopy import core, data, event, logging, monitors, visual

# ---------- config ----------
SCANNER = None
WIN_SIZE = (1280, 800)
FULLSCR = False
LETTER_H = 0.07
WRAP_W = 2

INSTR_BLANK_S = 10.0
OPINION_S = 15.0
OPINION_PROMPT = "Please share your opinion on the problem area you just discussed:\n\n"

KEY_SUBMIT = None  # set to a key string if you want manual submit (e.g., 'return')
KEY_QUIT = "escape"
KEY_TRIGGER = "space"
TTL_KEY = "equal"
TTL_ACCEPT = {"equal", "="}
TRIGGER_ACCEPT = {"space", KEY_TRIGGER}

CSV_FILENAME = "participant_counterbalancing.csv"
SESSION_TYPE = "couple"


# ----------------- helpers (duplicated for standalone use) -----------------
def decode_pid(pid_str: str):
    if not (isinstance(pid_str, str) and pid_str.isdigit() and len(pid_str) == 3):
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


def slug(x: str):
    x = (x or "").strip().lower()
    x = re.sub(r"\s+", "_", x)
    x = re.sub(r"[^a-z0-9_\-]", "", x)
    return x[:60] or "topic"


def load_assignment_row(csv_path: str, pid: str):
    """
    Required columns:
      participant_id, condition,
      Neutral_session_1, Couple_session_1, Neutral_session_2, Couple_session_2
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        msg = f"Cannot find CSV: {csv_file}"
        raise FileNotFoundError(msg)
    with csv_file.open(newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        need = {
            "participant_id",
            "condition",
            "Neutral_session_1",
            "Couple_session_1",
            "Neutral_session_2",
            "Couple_session_2",
        }
        if not need.issubset(set(rdr.fieldnames or [])):
            missing = need - set(rdr.fieldnames or [])
            msg = f"CSV is missing required columns: {sorted(missing)}"
            raise ValueError(msg)
        for row in rdr:
            if (row.get("participant_id") or "").strip() == pid:
                return {
                    "participant_id": pid,
                    "condition": (row.get("condition") or "").strip(),
                    "Neutral_session_1": (row.get("Neutral_session_1") or "")
                    .strip()
                    .upper(),
                    "Couple_session_1": (row.get("Couple_session_1") or "")
                    .strip()
                    .upper(),
                    "Neutral_session_2": (row.get("Neutral_session_2") or "")
                    .strip()
                    .upper(),
                    "Couple_session_2": (row.get("Couple_session_2") or "")
                    .strip()
                    .upper(),
                }
    msg = f"participant_id {pid} not found in {csv_file}"
    raise KeyError(msg)


def pick_first_speaker(starters: dict, session: int):
    key = f"Couple_session_{session}"
    val = starters.get(key, "")
    if val not in ("A", "B"):
        msg = f"Starter value for {key} must be 'A' or 'B', got: {val!r}"
        raise ValueError(msg)
    return val


def make_monitor(scanner):
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


def log_ttl(
    fTTL,
    exp_condition,
    role_label,
    segment,
    run_clock,
    phase_clock,
    conflict_text,
    first_speaker,
    role,
    session,
    dyad,
    SESSION_TYPE,
):
    fTTL.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},{role_label},{segment},"
        f"{time.time()},{run_clock.getTime()},{'' if phase_clock is None else phase_clock.getTime()},"
        f"{conflict_text},{first_speaker},{role}\n"
    )
    fTTL.flush()


# ----------------------------------------------------
def main(pid: str, session: int, conflict: str, csv_path: str):
    if session not in (1, 2):
        msg = "Session must be 1 or 2"
        raise ValueError(msg)
    if not (conflict and conflict.strip()):
        msg = "You must provide a non-empty --conflict string"
        raise ValueError(msg)

    dyad, role = decode_pid(pid)

    row = load_assignment_row(csv_path, pid)
    exp_condition = row["condition"]
    starters = {
        "Neutral_session_1": row["Neutral_session_1"],
        "Couple_session_1": row["Couple_session_1"],
        "Neutral_session_2": row["Neutral_session_2"],
        "Couple_session_2": row["Couple_session_2"],
    }
    first_speaker = pick_first_speaker(starters, session)
    conflict_text = conflict.strip()

    date_str = data.getDateStr()
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    base = f"{pid}_sess{session}_{SESSION_TYPE}_{exp_condition}_{first_speaker}_{slug(conflict_text)}"
    filename = data_dir / f"{base}_OPIN_min_{date_str}"

    thisExp = data.ExperimentHandler(
        name="OPIN_min",
        extraInfo={"session_type": SESSION_TYPE},
        savePickle=True,
        saveWideText=True,
        dataFileName=str(filename),
    )
    logging.LogFile(str(filename.with_suffix(".log")), level=logging.EXP)
    logging.console.setLevel(logging.WARNING)

    timings_path = data_dir / f"{base}_OPIN_TimingsLog_{date_str}.csv"
    fLog = timings_path.open("w", newline="", encoding="utf-8")
    fLog.write(
        "dyad,session,session_type,exp_condition,role,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"
    )
    fLog.flush()

    ttl_path = data_dir / f"{base}_OPIN_TTLtimestamps_{date_str}.csv"
    fTTL = ttl_path.open("w", newline="", encoding="utf-8")
    fTTL.write(
        "dyad,session,session_type,exp_condition,role,segment,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"
    )
    fTTL.flush()

    mon = make_monitor(SCANNER)
    win = visual.Window(
        size=WIN_SIZE, color="black", fullscr=FULLSCR, units="norm", monitor=mon
    )
    win.mouseVisible = False

    def txt(**kw):
        return visual.TextStim(
            win, height=LETTER_H, wrapWidth=WRAP_W, color="white", **kw
        )

    show_instructions = txt(text="")
    show_opinion = txt(text="", pos=(0, 0.25))
    show_timer = txt(text="", pos=(0, -0.70))
    show_blank = txt(text="+", pos=(0, 0.00))
    show_end = txt(text="You are now done with this task.")

    instr = (
        f"In the last task, you discussed {conflict_text.upper()} with your partner.\n\n\n"
        f"Next, we would like you to report your own personal opinion on {conflict_text.upper()}. For N minutes,\n"
        "please speak aloud whatever you can about your thoughts and feelings on the topic.\n\n\n"
        "This recording is confidential. The experimenter has muted the audio, and what you say now will\n"
        "NOT be shared with your partner.\n\n\n"
        "You’ll first see a fixation cross for 10 seconds.\n"
        " After that, you will see instructions to begin speaking."
    )
    event.clearEvents(eventType="keyboard")
    show_instructions.setText(instr)
    trigger_source = None
    while trigger_source is None:
        show_instructions.draw()
        win.flip()
        keys = event.getKeys()
        if KEY_QUIT in keys:
            win.close()
            core.quit()
        if any(k in TTL_ACCEPT for k in keys):
            trigger_source = "ttl"
            break
        core.wait(0.01)

    run_clock = core.Clock()

    # Trigger logs
    fLog.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},trigger_start_{trigger_source},{time.time()},{run_clock.getTime()},,,{first_speaker},{role}\n"
    )
    fLog.flush()
    log_ttl(
        fTTL,
        exp_condition,
        "",
        f"trigger_start_{trigger_source}",
        run_clock,
        None,
        conflict_text,
        first_speaker,
        role,
        session,
        dyad,
        SESSION_TYPE,
    )

    # brief blank
    show_blank.draw()
    win.flip()
    blank_clock = core.Clock()
    while blank_clock.getTime() < INSTR_BLANK_S:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if TTL_KEY in keys:
                log_ttl(
                    fTTL,
                    exp_condition,
                    "",
                    "blank",
                    run_clock,
                    None,
                    conflict_text,
                    first_speaker,
                    role,
                    session,
                    dyad,
                    SESSION_TYPE,
                )
                event.clearEvents(eventType="keyboard")
            if KEY_QUIT in keys:
                win.close()
                core.quit()
        core.wait(0.01)

    # ---------------------------
    # Opinion phase
    # ---------------------------
    show_opinion.setText(f"{OPINION_PROMPT} {conflict_text.upper()}")
    show_opinion.setAutoDraw(True)
    show_timer.setAutoDraw(True)
    op_clock = core.Clock()

    # Main CSV: opinion_start
    thisExp.addData("dyad", dyad)
    thisExp.addData("session", session)
    thisExp.addData("exp_condition", exp_condition)
    thisExp.addData("event", "opinion_start")
    thisExp.addData("role", "")
    thisExp.addData("onset_run_s", run_clock.getTime())
    thisExp.addData("onset_phase_s", op_clock.getTime())
    thisExp.addData("conflict_text", conflict_text)
    thisExp.addData("first_speaker", first_speaker)
    thisExp.addData("participant_role", role)
    thisExp.nextEntry()

    # TimingsLog: start
    fLog.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},opinion_start,{time.time()},{run_clock.getTime()},{op_clock.getTime()},{conflict_text},{first_speaker},{role}\n"
    )
    fLog.flush()

    submitted = False
    while op_clock.getTime() < OPINION_S and not submitted:
        remaining = round(OPINION_S - op_clock.getTime())
        show_timer.setText(f"{remaining} seconds")
        keys = event.getKeys([TTL_KEY, KEY_QUIT] + ([KEY_SUBMIT] if KEY_SUBMIT else []))
        if keys:
            if TTL_KEY in keys:
                log_ttl(
                    fTTL,
                    exp_condition,
                    "",
                    "opinion",
                    run_clock,
                    op_clock,
                    conflict_text,
                    first_speaker,
                    role,
                    session,
                    dyad,
                    SESSION_TYPE,
                )
                event.clearEvents(eventType="keyboard")
            if KEY_QUIT in keys:
                win.close()
                core.quit()
            if KEY_SUBMIT and (KEY_SUBMIT in keys):
                submitted = True
                event.clearEvents(eventType="keyboard")
        win.flip()

    # TimingsLog: end
    fLog.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},opinion_end,{time.time()},{run_clock.getTime()},{op_clock.getTime()},{conflict_text},{first_speaker},{role}\n"
    )
    fLog.flush()

    # --- MAIN CSV ROW: opinion_end (mirror of opinion_start) ---
    thisExp.addData("dyad", dyad)
    thisExp.addData("session", session)
    thisExp.addData("exp_condition", exp_condition)
    thisExp.addData("event", "opinion_end")  # explicit phase boundary
    thisExp.addData("role", "")  # no speaker/listener in solo opinion
    thisExp.addData("onset_run_s", run_clock.getTime())  # secs since trigger
    thisExp.addData(
        "onset_phase_s", op_clock.getTime()
    )  # secs since opinion phase start
    thisExp.addData("conflict_text", conflict_text)
    thisExp.addData("first_speaker", first_speaker)
    thisExp.addData("participant_role", role)
    thisExp.nextEntry()

    show_opinion.setAutoDraw(False)
    show_timer.setAutoDraw(False)
    win.flip()

    show_end.draw()
    win.flip()
    core.wait(1.0)

    # save & close
    thisExp.saveAsWideText(filename + ".csv")
    thisExp.saveAsPickle(filename)
    thisExp.abort()
    logging.flush()
    try:
        fLog.close()
        fTTL.close()
    except Exception:
        pass
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
    args = ap.parse_args()
    main(pid=args.pid, session=args.session, conflict=args.conflict, csv_path=args.csv)
