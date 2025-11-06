#!/usr/bin/env python3
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

INTRO_S = 2.0  # intro dwell before communication
COMM_S = 30.0  # communication phase duration (s)

KEY_PASS = "1"
KEY_QUIT = "escape"
TTL_KEY = "equal"  # for TTL pings during phases
# Accept both 'equal' and '=' for the scanner trigger
TTL_ACCEPT = {"equal", "="}

RUN_NUM = 1
CSV_FILENAME = "participant_counterbalancing.csv"
SESSION_TYPE = "neutral"  # fixed for this task


# ----------------- helpers -----------------
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


def canonical_topic(t: str) -> str:
    m = {
        "air": "Air pollution",
        "tuition": "Cost of tuition",
    }
    return m.get((t or "").strip().lower(), (t or "").strip())


def slug(x: str):
    x = (x or "").strip().lower()
    x = re.sub(r"\s+", "_", x)
    x = re.sub(r"[^a-z0-9_\-]", "", x)
    return x[:60] or "topic"


def load_assignment_row(csv_path: str, pid: str):
    """
    Required columns (exact, case-sensitive):
      participant_id, condition,
      Neutral_session_1, Couple_session_1, Neutral_session_2, Couple_session_2,
      first_topic, second_topic
    """
    csv_file = Path(csv_path)
    abs_path = csv_file.resolve()
    if not csv_file.exists():
        msg = (
            f"Cannot find CSV at '{csv_file}' (abs: '{abs_path}'). "
            f"Current working directory: '{Path.cwd()}'."
        )
        raise FileNotFoundError(
            msg
        )

    # Detect delimiter to guard against ';' exports
    with csv_file.open(encoding="utf-8", newline="") as fpeek:
        sample = fpeek.read(4096)
        fpeek.seek(0)
        try:
            sniff = csv.Sniffer().sniff(sample)
            delimiter = sniff.delimiter
        except Exception:
            delimiter = ","  # fallback

    with csv_file.open(encoding="utf-8", newline="") as f:
        rdr = csv.DictReader(f, delimiter=delimiter)
        need = {
            "participant_id",
            "condition",
            "Neutral_session_1",
            "Couple_session_1",
            "Neutral_session_2",
            "Couple_session_2",
            "first_topic",
            "second_topic",
        }
        cols = set(rdr.fieldnames or [])

        missing = need - cols
        if missing:
            suggestions = {}
            lowmap = {c.lower().strip(): c for c in cols}
            for want in need:
                lw = want.lower().strip()
                if lw in lowmap and want not in cols:
                    suggestions[want] = lowmap[lw]
            hint = f" (Possible header variants: {suggestions})" if suggestions else ""
            msg = (
                f"CSV is missing required columns: {sorted(missing)}. "
                f"Found columns: {sorted(cols)}.{hint}"
            )
            raise ValueError(
                msg
            )

        found_row = None
        for row in rdr:
            rid = (row.get("participant_id") or "").strip()
            if rid == pid:
                found_row = {
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
                    "first_topic": (row.get("first_topic") or "").strip(),
                    "second_topic": (row.get("second_topic") or "").strip(),
                }
                break

        if not found_row:
            msg = (
                f"participant_id '{pid}' not found in {abs_path}. "
                f"Note: your script expects zero-padded IDs like '011'."
            )
            raise KeyError(
                msg
            )

        return found_row


def pick_first_speaker(starters: dict, session: int):
    key = f"Neutral_session_{session}"  # session type fixed to 'neutral'
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
    comm_clock,
    conflict_text,
    first_speaker,
    role,
    session,
    dyad,
    SESSION_TYPE,
):
    # TTL file remains verbose
    fTTL.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},{role_label},{segment},"
        f"{time.time()},{run_clock.getTime()},{'' if comm_clock is None else comm_clock.getTime()},"
        f"{conflict_text},{first_speaker},{role}\n"
    )
    fTTL.flush()


def log_comm_press(
    thisExp,
    *,
    event_name,
    role_label,
    run_clock,
    comm_clock,
    dyad,
    session,
    exp_condition,
    conflict_text,
    first_speaker,
    participant_role,
):
    # One row in the main CSV per press or phase onset (lean schema)
    thisExp.addData("dyad", dyad)
    thisExp.addData("session", session)
    thisExp.addData("exp_condition", exp_condition)
    thisExp.addData(
        "event", event_name
    )  # 'trigger_start_ttl', 'communication_start', 'pass_press', ...
    thisExp.addData("role", role_label or "")  # 'speaker'/'listener' or ''
    thisExp.addData("onset_run_s", run_clock.getTime())  # secs since trigger
    thisExp.addData("conflict_text", conflict_text)
    thisExp.addData("first_speaker", first_speaker)
    thisExp.addData("participant_role", participant_role)  # A/B
    thisExp.nextEntry()


# ----------------------------------------------------
def main(pid: str, session: int, csv_path: str):
    if session not in (1, 2):
        msg = "Session must be 1 or 2"
        raise ValueError(msg)

    # Decode ID and role
    dyad, role = decode_pid(pid)

    # Lookup from CSV (authoritative)
    row = load_assignment_row(csv_path, pid)
    exp_condition = row["condition"]

    # Topic by session
    discussion_topic = (
        row.get("first_topic") if session == 1 else row.get("second_topic")
    ) or ""
    discussion_topic = discussion_topic.strip()
    if not discussion_topic:
        which = "first_topic" if session == 1 else "second_topic"
        msg = (
            f"No discussion topic found in CSV for participant {pid} session {session} "
            f"(expected column '{which}' to be non-empty)."
        )
        raise ValueError(
            msg
        )
    conflict_text = discussion_topic
    display_topic = canonical_topic(conflict_text)

    # Instruction text by condition (neutral charity framing)
    persuade_instr_text = (
        "Next, you and your partner will discuss how the charity funds should be allocated.\n"
        f"You'll focus on how to address: {display_topic}.\n\n\n"
        "IMPORTANT: During this conversation, try to PERSUADE the other person of your opinion.\n"
        "We are studying how persuasion works in the brain, so please try to convince the other \n"
        "person of your opinion as much as possible and get them to understand your perspective.\n"
        "These instructions are only for you. So, please don't share them with your partner.\n\n\n"
        "You will have 10 minutes for this conversation. \n"
        "A timer will show you how many seconds are left.\n\n\n"
        "Tell the experimenter when you are ready to begin.\n"
        "You’ll first see a fixation cross for 10 seconds.\n"
        "After that, you will see instructions to begin the conversation."
    )
    compromise_instr_text = (
        "Next, you and your partner will discuss how the charity funds should be allocated.\n"
        f"You'll focus on how to address: {display_topic}.\n\n\n"
        "IMPORTANT: During this conversation, try to find a JOINT SOLUTION that you both agree on.\n"
        "We are studying how collaboration works in the brain, so please try to reconcile any \n"
        "differences of opinion as much as possible and look for a shared perspective.\n"
        "These instructions are only for you. So, please don't share them with your partner.\n\n\n"
        "You will have 10 minutes for this conversation. \n"
        "A timer will show you how many seconds are left.\n\n\n"
        "Tell the experimenter when you are ready to begin.\n"
        "You’ll first see a fixation cross for 10 seconds.\n"
        "After that, you will see instructions to begin the conversation."
    )

    cond_lower = (exp_condition or "").strip().lower()
    if cond_lower.startswith("persu"):
        conv_instr_text = persuade_instr_text
    else:
        conv_instr_text = compromise_instr_text

    starters = {
        "Neutral_session_1": row["Neutral_session_1"],
        "Couple_session_1": row["Couple_session_1"],
        "Neutral_session_2": row["Neutral_session_2"],
        "Couple_session_2": row["Couple_session_2"],
    }
    first_speaker = pick_first_speaker(starters, session)  # 'A' or 'B'

    # --- data files ---
    date_str = data.getDateStr()
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    base = f"{pid}_sess{session}_{SESSION_TYPE}_{exp_condition}_{first_speaker}_{slug(conflict_text)}"
    filename = data_dir / f"{base}_CONV_min_{date_str}"

    thisExp = data.ExperimentHandler(
        name="CONV_min",
        extraInfo={"session_type": SESSION_TYPE},
        savePickle=True,
        saveWideText=True,
        dataFileName=str(filename),
    )
    logging.LogFile(str(filename.with_suffix(".log")), level=logging.EXP)
    logging.console.setLevel(logging.WARNING)

    # --- TimingsLog (minimal) ---
    timings_path = data_dir / f"{base}_CONV_TimingsLog_{date_str}.csv"
    fLog = timings_path.open("w", newline="", encoding="utf-8")
    fLog.write(
        "dyad,session,session_type,exp_condition,role,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"
    )
    fLog.flush()

    # --- TTL timestamps file (verbose) ---
    ttl_path = data_dir / f"{base}_CONV_TTLtimestamps_{date_str}.csv"
    fTTL = ttl_path.open("w", newline="", encoding="utf-8")
    fTTL.write(
        "dyad,session,session_type,exp_condition,role,segment,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"
    )
    fTTL.flush()

    # --- window & text objects ---
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
    show_role_txt = txt(text="", pos=(0, 0.65))
    show_pass = txt(text="", pos=(0, 0.05))
    show_timer = txt(text="", pos=(0, -0.70))
    show_blank = txt(text="+", pos=(0, 0.00))
    show_topic = txt(text="", pos=(0, 0.35))
    show_end = txt(text="You are now done with this task.")

    # --- INSTRUCTIONS + TTL wait ---
    combined_instr = f"{conv_instr_text}"
    show_role_txt.setText("")
    show_pass.setText("")
    event.clearEvents(eventType="keyboard")

    show_instructions.setText(combined_instr)
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

    # Start run clock at TTL trigger
    run_clock = core.Clock()

    # TimingsLog + TTL + main CSV: trigger start
    fLog.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},trigger_start_{trigger_source},{time.time()},{run_clock.getTime()},,"
        f"{conflict_text},{first_speaker},{role}\n"
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
    log_comm_press(
        thisExp,
        event_name=f"trigger_start_{trigger_source}",
        role_label="",
        run_clock=run_clock,
        comm_clock=None,
        dyad=dyad,
        session=session,
        exp_condition=exp_condition,
        conflict_text=conflict_text,
        first_speaker=first_speaker,
        participant_role=role,
    )

    # --- brief blank BEFORE intro ---
    show_blank.draw()
    win.flip()
    blank_clock = core.Clock()
    while blank_clock.getTime() < 1.0:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if KEY_QUIT in keys:
                win.close()
                core.quit()
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
        core.wait(0.01)

    # --- Intro dwell (optional fixation) ---
    if INTRO_S > 0:
        show_blank.draw()
        win.flip()
        intro_clock = core.Clock()
        while intro_clock.getTime() < INTRO_S:
            keys = event.getKeys([TTL_KEY, KEY_QUIT])
            if keys:
                if KEY_QUIT in keys:
                    win.close()
                    core.quit()
                if TTL_KEY in keys:
                    log_ttl(
                        fTTL,
                        exp_condition,
                        "",
                        "intro_fixation",
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
            core.wait(0.01)

    # ---------------------------
    # Communication phase ONLY
    # ---------------------------
    fLog.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},Communication_start,"
        f"{time.time()},{run_clock.getTime()},,"
        f"{conflict_text},{first_speaker},{role}\n"
    )
    fLog.flush()

    role_text = (
        "YOUR TURN TO SPEAK" if (role == first_speaker) else "YOUR TURN TO LISTEN"
    )
    pass_text = "Press '1' to pass the mic." if (role == first_speaker) else ""
    show_topic.setText(f"Discussion topic: {display_topic}")

    show_role_txt.setText(role_text)
    show_pass.setText(pass_text)
    show_role_txt.setAutoDraw(True)
    show_timer.setAutoDraw(True)
    show_pass.setAutoDraw(True)
    show_topic.setAutoDraw(True)

    comm_clock = core.Clock()

    # main CSV: communication_start
    current_role = "speaker" if (role == first_speaker) else "listener"
    log_comm_press(
        thisExp,
        event_name="communication_start",
        role_label=current_role,
        run_clock=run_clock,
        comm_clock=comm_clock,
        dyad=dyad,
        session=session,
        exp_condition=exp_condition,
        conflict_text=conflict_text,
        first_speaker=first_speaker,
        participant_role=role,
    )

    # TimingsLog: communication_start + initial role
    fLog.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},{current_role},{time.time()},{run_clock.getTime()},{comm_clock.getTime()},"
        f"{conflict_text},{first_speaker},{role}\n"
    )
    fLog.flush()

    while comm_clock.getTime() < COMM_S:
        current_role_label = (
            "speaker" if show_role_txt.text == "YOUR TURN TO SPEAK" else "listener"
        )

        # TTL pings during communication
        keys_ttl = event.getKeys([TTL_KEY])
        if keys_ttl and (TTL_KEY in keys_ttl):
            log_ttl(
                fTTL,
                exp_condition,
                current_role_label,
                "communication",
                run_clock,
                comm_clock,
                conflict_text,
                first_speaker,
                role,
                session,
                dyad,
                SESSION_TYPE,
            )
            event.clearEvents(eventType="keyboard")

        # countdown
        remaining = round(COMM_S - comm_clock.getTime())
        show_timer.setText(f"{remaining} seconds")
        win.flip()

        # keys: pass / quit
        keys = event.getKeys(keyList=[KEY_PASS, KEY_QUIT], timeStamped=comm_clock)
        if keys:
            key, _rt = keys[-1]
            if key == KEY_QUIT:
                win.close()
                core.quit()
            elif key == KEY_PASS:
                # toggle label & pass hint
                current = show_role_txt.text
                new_txt = (
                    "YOUR TURN TO LISTEN"
                    if current == "YOUR TURN TO SPEAK"
                    else "YOUR TURN TO SPEAK"
                )
                show_role_txt.setText(new_txt)
                show_pass.setText(
                    "Press '1' to pass the mic."
                    if new_txt == "YOUR TURN TO SPEAK"
                    else ""
                )
                toggled_role = (
                    "speaker" if new_txt == "YOUR TURN TO SPEAK" else "listener"
                )

                # TimingsLog: role toggle
                fLog.write(
                    f"{dyad},{session},{SESSION_TYPE},{exp_condition},{toggled_role},{time.time()},{run_clock.getTime()},{comm_clock.getTime()},"
                    f"{conflict_text},{first_speaker},{role}\n"
                )
                fLog.flush()

                # Main CSV: button press
                log_comm_press(
                    thisExp,
                    event_name="pass_press",
                    role_label=toggled_role,
                    run_clock=run_clock,
                    comm_clock=comm_clock,
                    dyad=dyad,
                    session=session,
                    exp_condition=exp_condition,
                    conflict_text=conflict_text,
                    first_speaker=first_speaker,
                    participant_role=role,
                )

    # Stop showing comm UI
    for stim in (show_role_txt, show_timer, show_pass, show_topic):
        stim.setAutoDraw(False)

    # TimingsLog: communication_end
    fLog.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},communication_end,{time.time()},{run_clock.getTime()},{comm_clock.getTime()},"
        f"{conflict_text},{first_speaker},{role}\n"
    )
    fLog.flush()

    # Main CSV: communication_end (explicit phase boundary)
    end_role_label = (
        "speaker" if show_role_txt.text == "YOUR TURN TO SPEAK" else "listener"
    )
    thisExp.addData("dyad", dyad)
    thisExp.addData("session", session)
    thisExp.addData("exp_condition", exp_condition)
    thisExp.addData("event", "communication_end")
    thisExp.addData("role", end_role_label)
    thisExp.addData("onset_run_s", run_clock.getTime())
    thisExp.addData("onset_phase_s", comm_clock.getTime())
    thisExp.addData("conflict_text", conflict_text)
    thisExp.addData("first_speaker", first_speaker)
    thisExp.addData("participant_role", role)
    thisExp.nextEntry()

    # --- End screen ---
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
        "--csv",
        "-c",
        type=str,
        default=CSV_FILENAME,
        help="Path to participant_counterbalancing.csv",
    )
    args = ap.parse_args()
    main(pid=args.pid, session=args.session, csv_path=args.csv)
