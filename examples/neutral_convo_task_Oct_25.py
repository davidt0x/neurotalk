#!/usr/bin/env python3
import os, time, argparse, re, csv
from psychopy import visual, core, data, event, logging, monitors

# ---------- config ----------
SCANNER       = None
WIN_SIZE      = (1280, 800)
FULLSCR       = True
LETTER_H      = 0.05
WRAP_W        = 1.5

BETWEEN_TRIAL_S = 10.0   # break between segments (used for pre- and post-opinion blanks)
INTRO_S         = 2.0   # intro dwell before communication
COMM_S          = 30.0   # communication phase duration (s)
OPINION_S       = 15.0   # opinion phase duration (s)

OPINION_PROMPT = "Please share your opinion on the problem area you just discussed:"
KEY_SUBMIT     = None  # set to a key string if you later want opinion submit

KEY_PASS    = '1'
KEY_QUIT    = 'escape'
TTL_KEY     = 'equal'    # for TTL pings during phases
# Accept both 'equal' and '=' for the scanner trigger
TTL_ACCEPT = {'equal', '='}

RUN_NUM        = 1
CSV_FILENAME   = "participant_counterbalancing.csv"
SESSION_TYPE   = "neutral"   # fixed for this task

# ----------------- helpers -----------------
def decode_pid(pid_str: str):
    if not (isinstance(pid_str, str) and pid_str.isdigit() and len(pid_str) == 3):
        raise ValueError("PID must be a 3-digit code like 011 or 402")
    pid_num = int(pid_str)
    dyad = pid_num // 10
    person = pid_num % 10
    if person not in (1, 2) or dyad < 1:
        raise ValueError(f"Bad participant id: {pid_str}")
    role = 'A' if person == 1 else 'B'
    return dyad, role

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
    abs_path = os.path.abspath(csv_path)
    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"Cannot find CSV at '{csv_path}' (abs: '{abs_path}'). "
            f"Current working directory: '{os.getcwd()}'."
        )

    # Detect delimiter to guard against ';' exports
    with open(csv_path, 'r', encoding='utf-8', newline='') as fpeek:
        sample = fpeek.read(4096)
        fpeek.seek(0)
        try:
            sniff = csv.Sniffer().sniff(sample)
            delimiter = sniff.delimiter
        except Exception:
            delimiter = ','  # fallback

    with open(csv_path, 'r', encoding='utf-8', newline='') as f:
        rdr = csv.DictReader(f, delimiter=delimiter)
        need = {
            "participant_id","condition",
            "Neutral_session_1","Couple_session_1","Neutral_session_2","Couple_session_2",
            "first_topic","second_topic"
        }
        cols = set(rdr.fieldnames or [])
        # Quick debug print so you can see what’s actually in the file
        print(f"[CSV DEBUG] Reading: {abs_path}")
        print(f"[CSV DEBUG] Detected delimiter: {repr(delimiter)}")
        print(f"[CSV DEBUG] Columns found: {sorted(cols)}")

        missing = need - cols
        if missing:
            # Try to spot “almost matches” (e.g., spacing/case variants) to help debugging
            suggestions = {}
            lowmap = {c.lower().strip(): c for c in cols}
            for want in need:
                lw = want.lower().strip()
                if lw in lowmap and want not in cols:
                    suggestions[want] = lowmap[lw]
            hint = f" (Possible header variants: {suggestions})" if suggestions else ""
            raise ValueError(
                f"CSV is missing required columns: {sorted(missing)}. "
                f"Found columns: {sorted(cols)}.{hint}"
            )

        found_row = None
        for row in rdr:
            rid = (row.get("participant_id") or "").strip()
            # Debug each row’s participant_id to catch padding issues
            # print(f"[CSV DEBUG] Saw participant_id value: {repr(rid)}")
            if rid == pid:
                found_row = {
                    "participant_id": pid,
                    "condition":           (row.get("condition") or "").strip(),
                    "Neutral_session_1":   (row.get("Neutral_session_1") or "").strip().upper(),
                    "Couple_session_1":    (row.get("Couple_session_1")  or "").strip().upper(),
                    "Neutral_session_2":   (row.get("Neutral_session_2") or "").strip().upper(),
                    "Couple_session_2":    (row.get("Couple_session_2")  or "").strip().upper(),
                    "first_topic":         (row.get("first_topic") or "").strip(),
                    "second_topic":        (row.get("second_topic") or "").strip(),
                }
                break

        if not found_row:
            raise KeyError(
                f"participant_id '{pid}' not found in {abs_path}. "
                f"Note: your script expects zero-padded IDs like '011'."
            )

        return found_row


def pick_first_speaker(starters: dict, session: int):
    key = f"Neutral_session_{session}"      # session type fixed to 'couple'
    val = starters.get(key, "")
    if val not in ("A","B"):
        raise ValueError(f"Starter value for {key} must be 'A' or 'B', got: {val!r}")
    return val

def make_monitor(scanner):
    if scanner == 'skyra':
        mon = monitors.Monitor('skyra'); mon.setSizePix((1920,1080)); mon.setWidth(64); mon.setDistance(89); mon.save()
    elif scanner == 'prisma':
        mon = monitors.Monitor('prisma'); mon.setSizePix((1920,1080)); mon.setWidth(56); mon.setDistance(107.5); mon.save()
    else:
        mon = monitors.Monitor('defaultLaptop')
    return mon

def log_ttl(fTTL, exp_condition, role_label, segment, run_clock, comm_clock,
            conflict_text, first_speaker, role, session, dyad):
    # TTL file remains verbose
    fTTL.write(
        f"{dyad},{session},{exp_condition},{role_label},{segment},"
        f"{time.time()},{run_clock.getTime()},{'' if comm_clock is None else comm_clock.getTime()},"
        f"{conflict_text},{first_speaker},{role}\n"
    ); fTTL.flush()

def log_comm_press(thisExp, *, event_name, role_label, run_clock, comm_clock,
                   dyad, session, exp_condition, conflict_text, first_speaker, participant_role):
    # One row in the main CSV per press or phase onset (lean schema)
    thisExp.addData('dyad', dyad)
    thisExp.addData('session', session)
    thisExp.addData('exp_condition', exp_condition)
    thisExp.addData('event', event_name)                 # e.g., 'trigger_start_ttl', 'intro_start', 'communication_start', 'pass_press', ...
    thisExp.addData('role', role_label or '')            # 'speaker'/'listener' (presses) or '' (phase events)
    thisExp.addData('onset_run_s', run_clock.getTime())  # secs since trigger
    thisExp.addData('conflict_text', conflict_text)
    thisExp.addData('first_speaker', first_speaker)
    thisExp.addData('participant_role', participant_role)  # A/B
    thisExp.nextEntry()

# ----------------------------------------------------
def main(pid: str, session: int, csv_path: str):
    if session not in (1, 2):
        raise ValueError("Session must be 1 or 2")

    # Decode ID and role
    dyad, role = decode_pid(pid)

    # Lookup from CSV (authoritative)
    row = load_assignment_row(csv_path, pid)
    exp_condition = row["condition"]
    
    if session == 1:
        discussion_topic = row.get("first_topic", "")
    else:
        discussion_topic = row.get("second_topic", "")

    discussion_topic = (discussion_topic or "").strip()
    if not discussion_topic:
        which = "first_topic" if session == 1 else "second_topic"
        raise ValueError(
            f"No discussion topic found in CSV for participant {pid} session {session} "
            f"(expected column '{which}' to be non-empty)."
        )

    conflict_text = discussion_topic

    # --- neutral/controversial instructions (verbatim from prior study) ---
    persuade_instr_text = (
        "Next, you will discuss with the other participant how the charity money "
        "should be allocated."
        "\n\nIMPORTANT: During this conversation, try to PERSUADE "
        "the other person of your opinion. "
        "\n\nWe are studying how persuasion works in the brain," 
        "\n\n so please try to convince the other person of your opinion as much as possible"
        "\n\n and get them to understand your perspective."
        "\n\nThese instructions are only for you."
        "\n\n So, please don't share them with the other participant."
        "\n\nYou will have 10 minutes for this conversation. "
        "\n\n A timer will show you how many seconds are left. "
        "\n\nTell the experimenter when you are ready to begin."
    )

    compromise_instr_text = (
        "Next, you will discuss with the other participant how the charity money "
        "should be allocated."
        "\n\nIMPORTANT: During this conversation, try to find a "
        "JOINT SOLUTION that you both agree on. "
        "\n\nWe are studying how collaboration works in the brain,"
        "\n\nso please try to reconcile any differences of opinion as much as possible"
        "\n\nand look for a shared perspective."
        "\n\nThese instructions are only for you."
        "\n\n So, please don't share them with the other participant."
        "\n\nYou will have 10 minutes for this conversation. "
        "\n\nA timer will show you how many seconds are left. "
        "\n\nTell the experimenter when you are ready to begin."
    )

    # Pick which instruction to show, based on CSV condition
    cond_lower = (exp_condition or "").strip().lower()
    if cond_lower.startswith("persu"):   # e.g., 'persuade'/'persuasion'
        conv_instr_text = persuade_instr_text
    elif cond_lower.startswith("compr"):  # e.g., 'compromise'/'collaboration'
        conv_instr_text = compromise_instr_text
    else:
        # Fallback: default to compromise if condition is unknown
        conv_instr_text = compromise_instr_text

    
    starters = {
        "Neutral_session_1": row["Neutral_session_1"],
        "Couple_session_1":  row["Couple_session_1"],
        "Neutral_session_2": row["Neutral_session_2"],
        "Couple_session_2":  row["Couple_session_2"],
    }

    first_speaker = pick_first_speaker(starters, session)  # 'A' or 'B'

    # --- data files ---
    date_str = data.getDateStr()
    os.makedirs("data", exist_ok=True)
    base = f"{pid}_sess{session}_{SESSION_TYPE}_{exp_condition}_{first_speaker}_{slug(conflict_text)}"
    filename = os.path.join("data", f"{base}_CONV_min_{date_str}")

    thisExp = data.ExperimentHandler(
        name="CONV_min",
        extraInfo={'session_type': SESSION_TYPE},
        savePickle=True, saveWideText=True, dataFileName=filename
    )
    logging.LogFile(filename + '.log', level=logging.EXP)
    logging.console.setLevel(logging.WARNING)

    # --- TimingsLog (minimal) ---
    timings_path = os.path.join("data", f"{base}_CONV_TimingsLog_{date_str}.csv")
    fLog = open(timings_path, "w", newline="", encoding="utf-8")
    fLog.write("dyad,session,exp_condition,role,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"); fLog.flush()

    # --- TTL timestamps file (verbose) ---
    ttl_path = os.path.join("data", f"{base}_CONV_TTLtimestamps_{date_str}.csv")
    fTTL = open(ttl_path, "w", newline="", encoding="utf-8")
    fTTL.write("dyad,session,exp_condition,role,segment,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"); fTTL.flush()

    # --- window & text objects ---
    mon = make_monitor(SCANNER)
    win = visual.Window(size=WIN_SIZE, color='black', fullscr=FULLSCR, units='norm', monitor=mon)
    win.mouseVisible = False
    txt = lambda **kw: visual.TextStim(win, height=LETTER_H, wrapWidth=WRAP_W, color='white', **kw)

    show_instructions = txt(text="")
    show_prompt       = txt(text="", pos=(0, 0.25))
    show_role_txt     = txt(text="", pos=(0, 0.65))
    show_pass         = txt(text="", pos=(0, 0.05))
    show_timer        = txt(text="", pos=(0, -0.70))
    show_blank        = txt(text="+", pos=(0, 0.00))
    show_opinion      = txt(text="", pos=(0, 0.25))
    show_topic        = txt(text="", pos=(0, 0.35))
    show_end          = txt(text="You are now done with this task.")

    # --- Single combined INSTRUCTIONS + wait-for-TTL screen (start only on '=') ---
    combined_instr = (
        f"{conv_instr_text}\n\n"
        "Waiting for scanner trigger (=) to start...\n"
    )
    
    # Ensure convo UI is hidden here
    show_role_txt.setText("")
    show_pass.setText("")
    event.clearEvents(eventType='keyboard')

    show_instructions.setText(combined_instr)
    trigger_source = None
    while trigger_source is None:
        show_instructions.draw(); win.flip()
        keys = event.getKeys()
        if KEY_QUIT in keys:
            win.close(); core.quit()
        if any(k in TTL_ACCEPT for k in keys):
            trigger_source = 'ttl'
            break
        core.wait(0.01)

    # Start run clock at TTL trigger
    run_clock = core.Clock()

    # TimingsLog + TTL + main CSV: trigger start
    fLog.write(f"{dyad},{session},{exp_condition},trigger_start_{trigger_source},{time.time()},{run_clock.getTime()},,"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()
    log_ttl(fTTL, exp_condition, '', f"trigger_start_{trigger_source}", run_clock, None,
            conflict_text, first_speaker, role, session, dyad)
    log_comm_press(thisExp, event_name=f"trigger_start_{trigger_source}", role_label='',
                   run_clock=run_clock, comm_clock=None,
                   dyad=dyad, session=session, exp_condition=exp_condition,
                   conflict_text=conflict_text, first_speaker=first_speaker, participant_role=role)

    # --- brief blank BEFORE intro (legacy structure) ---
    show_blank.draw(); win.flip()
    blank_clock = core.Clock()
    while blank_clock.getTime() < 1.0:  # short settle; keep 1s so INTRO_S is the main dwell
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if KEY_QUIT in keys: win.close(); core.quit()
            if TTL_KEY in keys:
                log_ttl(fTTL, exp_condition, '', 'blank', run_clock, None,
                        conflict_text, first_speaker, role, session, dyad)
                event.clearEvents(eventType='keyboard')
        core.wait(0.01)

    # ---------------------------
    # Intro dwell (like legacy)
    # ---------------------------
    # --- Optional: intro fixation dwell (no second instruction screen) ---
    if INTRO_S > 0:
        show_blank.draw(); win.flip()
        intro_clock = core.Clock()



        while intro_clock.getTime() < INTRO_S:
            keys = event.getKeys([TTL_KEY, KEY_QUIT])
            if keys:
                if KEY_QUIT in keys:
                    win.close(); core.quit()
                if TTL_KEY in keys:
                    log_ttl(fTTL, exp_condition, '', 'intro_fixation', run_clock, None,
                            conflict_text, first_speaker, role, session, dyad)
                    event.clearEvents(eventType='keyboard')
            core.wait(0.01)


    # ---------------------------
    # Communication phase
    # ---------------------------
    
    # Add this line to mirror the couple script's marker in the TimingsLog:
    fLog.write(
        f"{dyad},{session},{exp_condition},Communication_start,"
        f"{time.time()},{run_clock.getTime()},,"
        f"{conflict_text},{first_speaker},{role}\n"
    )
    fLog.flush()
    
    role_text = "YOUR TURN TO SPEAK" if (role == first_speaker) else "YOUR TURN TO LISTEN"
    pass_text = "Press '1' to pass the mic." if (role == first_speaker) else ""

    show_topic.setText(f"Discussion topic: {conflict_text}")

    show_role_txt.setText(role_text)
    show_pass.setText(pass_text)

    show_role_txt.setAutoDraw(True)
    show_timer.setAutoDraw(True)
    show_pass.setAutoDraw(True)
    show_topic.setAutoDraw(True)

    comm_clock = core.Clock()

    # main CSV: communication_start
    current_role = 'speaker' if (role == first_speaker) else 'listener'
    log_comm_press(thisExp, event_name='communication_start', role_label=current_role,
                   run_clock=run_clock, comm_clock=comm_clock,
                   dyad=dyad, session=session, exp_condition=exp_condition,
                   conflict_text=conflict_text, first_speaker=first_speaker, participant_role=role)

    # TimingsLog: communication_start + initial role label
    fLog.write(f"{dyad},{session},{exp_condition},{current_role},{time.time()},{run_clock.getTime()},{comm_clock.getTime()},"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    while comm_clock.getTime() < COMM_S:
        current_role_label = 'speaker' if show_role_txt.text == "YOUR TURN TO SPEAK" else 'listener'

        # TTL pings during communication
        keys_ttl = event.getKeys([TTL_KEY])
        if keys_ttl and (TTL_KEY in keys_ttl):
            log_ttl(fTTL, exp_condition, current_role_label, 'communication', run_clock, comm_clock,
                    conflict_text, first_speaker, role, session, dyad)
            event.clearEvents(eventType='keyboard')

        # countdown
        remaining = int(round(COMM_S - comm_clock.getTime()))
        show_timer.setText(f"{remaining} seconds"); win.flip()

        # keys: pass / quit
        keys = event.getKeys(keyList=[KEY_PASS, KEY_QUIT], timeStamped=comm_clock)
        if keys:
            key, _rt = keys[-1]
            if key == KEY_QUIT:
                win.close(); core.quit()
            elif key == KEY_PASS:
                # toggle label & pass hint
                current = show_role_txt.text
                new_txt = "YOUR TURN TO LISTEN" if current == "YOUR TURN TO SPEAK" else "YOUR TURN TO SPEAK"
                show_role_txt.setText(new_txt)
                show_pass.setText("Press '1' to pass the mic." if new_txt == "YOUR TURN TO SPEAK" else "")
                toggled_role = 'speaker' if new_txt == "YOUR TURN TO SPEAK" else 'listener'

                # TimingsLog: role toggle moment
                fLog.write(f"{dyad},{session},{exp_condition},{toggled_role},{time.time()},{run_clock.getTime()},{comm_clock.getTime()},"
                           f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

                # Main CSV: button press
                log_comm_press(thisExp, event_name='pass_press', role_label=toggled_role,
                               run_clock=run_clock, comm_clock=comm_clock,
                               dyad=dyad, session=session, exp_condition=exp_condition,
                               conflict_text=conflict_text, first_speaker=first_speaker, participant_role=role)

    # stop showing comm UI
    for stim in (show_role_txt, show_timer, show_pass, show_topic):
        stim.setAutoDraw(False)

    # TimingsLog: communication_end
    fLog.write(f"{dyad},{session},{exp_condition},communication_end,{time.time()},{run_clock.getTime()},{comm_clock.getTime()},"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    # --- PRE-OPINION BLANK ---
    fLog.write(f"{dyad},{session},{exp_condition},pre_opinion_blank_start,{time.time()},{run_clock.getTime()},,"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    show_blank.draw(); win.flip()
    preop_clock = core.Clock()
    while preop_clock.getTime() < BETWEEN_TRIAL_S:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if KEY_QUIT in keys: win.close(); core.quit()
            if TTL_KEY in keys:
                log_ttl(fTTL, exp_condition, '', 'pre_opinion_blank', run_clock, None,
                        conflict_text, first_speaker, role, session, dyad)
                event.clearEvents(eventType='keyboard')
        core.wait(0.01)

    # ===========================
    # SOLO OPINION PHASE
    # ===========================
    show_opinion.setText(f"{OPINION_PROMPT} {conflict_text}")
    show_opinion.setAutoDraw(True)
    show_timer.setAutoDraw(True)

    op_clock = core.Clock()

    # main CSV: opinion_start
    log_comm_press(thisExp, event_name='opinion_start', role_label='',
                   run_clock=run_clock, comm_clock=op_clock,
                   dyad=dyad, session=session, exp_condition=exp_condition,
                   conflict_text=conflict_text, first_speaker=first_speaker, participant_role=role)

    # TimingsLog: Opinion start
    fLog.write(f"{dyad},{session},{exp_condition},opinion_start,{time.time()},{run_clock.getTime()},{op_clock.getTime()},"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    submitted = False
    while op_clock.getTime() < OPINION_S and not submitted:
        remaining = int(round(OPINION_S - op_clock.getTime()))
        show_timer.setText(f"{remaining} seconds")
        keys = event.getKeys([TTL_KEY, KEY_QUIT] + ([KEY_SUBMIT] if KEY_SUBMIT else []))
        if keys:
            if KEY_QUIT in keys: win.close(); core.quit()
            if TTL_KEY in keys:
                log_ttl(fTTL, exp_condition, '', 'opinion', run_clock, op_clock,
                        conflict_text, first_speaker, role, session, dyad)
                event.clearEvents(eventType='keyboard')
            if KEY_SUBMIT and (KEY_SUBMIT in keys):
                submitted = True; event.clearEvents(eventType='keyboard')
        win.flip()

    # TimingsLog: Opinion end
    fLog.write(f"{dyad},{session},{exp_condition},opinion_end,{time.time()},{run_clock.getTime()},{op_clock.getTime()},"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    # Stop drawing opinion UI
    show_opinion.setAutoDraw(False)
    show_timer.setAutoDraw(False)
    win.flip()  # clear

    # --- POST-OPINION BLANK ---
    fLog.write(f"{dyad},{session},{exp_condition},post_opinion_blank_start,{time.time()},{run_clock.getTime()},,"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    show_blank.draw(); win.flip()
    blank_clock2 = core.Clock()
    while blank_clock2.getTime() < BETWEEN_TRIAL_S:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if KEY_QUIT in keys: win.close(); core.quit()
            if TTL_KEY in keys:
                log_ttl(fTTL, exp_condition, '', 'blank', run_clock, None,
                        conflict_text, first_speaker, role, session, dyad)
                event.clearEvents(eventType='keyboard')
        core.wait(0.01)

    # --- End screen ---
    show_end.draw(); win.flip(); core.wait(1.0)

    # save & close
    thisExp.saveAsWideText(filename + '.csv')
    thisExp.saveAsPickle(filename)
    thisExp.abort()
    logging.flush()
    try:
        fLog.close(); fTTL.close()
    except Exception:
        pass
    win.close(); core.quit()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", "-p", type=str, required=True, help="3-digit participant ID (e.g., 011, 402)")
    ap.add_argument("--session", "-s", type=int, choices=[1,2], required=True, help="Session number (1 or 2)")
    ap.add_argument("--csv", "-c", type=str, default=CSV_FILENAME, help="Path to participant_counterbalancing.csv")
    args = ap.parse_args()
    main(pid=args.pid, session=args.session, csv_path=args.csv)
