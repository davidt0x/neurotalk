#!/usr/bin/env python3
# /// script
# requires-python = "<=3.11"
# dependencies = [
#   "psychopy",
# ]
# ///

import os, time, argparse, re, csv
from psychopy import visual, core, data, event, logging, monitors

# ---------- config ----------
SCANNER       = None
WIN_SIZE      = (1280, 800)
FULLSCR       = False
LETTER_H      = 0.05
WRAP_W        = 1.2

INSTR_BLANK_S   = 10.0   # blank after instruction/trigger, before communication UI
BETWEEN_TRIAL_S = 10.0   # CHANGE HERE TO CHANGE THE BREAK BETWEEN CONVO AND OPINION
INTRO_S         = 10.0
COMM_S          = 30.0

OPINION_S      = 15.0
OPINION_PROMPT = "Please share your opinion on the problem area you just discussed:"
KEY_SUBMIT     = None

KEY_PASS    = '1'
KEY_QUIT    = 'escape'
KEY_TRIGGER = 'space'
TTL_KEY     = 'equal'
# Accept both 'equal' and '=' (and common numpad variants) for the TTL trigger
TTL_ACCEPT = {'equal', '='}  # PsychoPy may report '=' as 'equal'
# Accept space plus whatever you set for KEY_TRIGGER
TRIGGER_ACCEPT = {'space', KEY_TRIGGER}


RUN_NUM        = 1
CSV_FILENAME   = "participant_counterbalancing.csv"
SESSION_TYPE   = "couple"   # fixed for this task

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
    Required columns:
      participant_id, condition,
      Neutral_session_1, Couple_session_1, Neutral_session_2, Couple_session_2
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Cannot find CSV: {csv_path}")
    with open(csv_path, newline='', encoding='utf-8') as f:
        rdr = csv.DictReader(f)
        need = {"participant_id","condition",
                "Neutral_session_1","Couple_session_1","Neutral_session_2","Couple_session_2"}
        if not need.issubset(set(rdr.fieldnames or [])):
            missing = need - set(rdr.fieldnames or [])
            raise ValueError(f"CSV is missing required columns: {sorted(missing)}")
        for row in rdr:
            if (row.get("participant_id") or "").strip() == pid:
                return {
                    "participant_id": pid,
                    "condition": (row.get("condition") or "").strip(),
                    "Neutral_session_1": (row.get("Neutral_session_1") or "").strip().upper(),
                    "Couple_session_1":  (row.get("Couple_session_1")  or "").strip().upper(),
                    "Neutral_session_2": (row.get("Neutral_session_2") or "").strip().upper(),
                    "Couple_session_2":  (row.get("Couple_session_2")  or "").strip().upper(),
                }
    raise KeyError(f"participant_id {pid} not found in {csv_path}")

def pick_first_speaker(starters: dict, session: int):
    key = f"Couple_session_{session}"      # session type fixed to 'couple'
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
    # TTL file remains verbose (unchanged)
     fTTL.write(
        f"{dyad},{session},{exp_condition},{role_label},{segment},"
        f"{time.time()},{run_clock.getTime()},{'' if comm_clock is None else comm_clock.getTime()},"
        f"{conflict_text},{first_speaker},{role}\n"
    ); fTTL.flush()
    

    
def log_comm_press(thisExp, *, event_name, role_label, run_clock, comm_clock,
                   dyad, session, exp_condition, conflict_text, first_speaker, participant_role):
    # One row in the main CSV per press
    thisExp.addData('dyad', dyad)
    thisExp.addData('session', session)
    thisExp.addData('exp_condition', exp_condition)
    thisExp.addData('event', event_name)                 # 'pass_press' or 'quit_press'
    thisExp.addData('role', role_label or '')            # 'speaker'/'listener' AFTER toggle
    thisExp.addData('onset_run_s', run_clock.getTime())  # secs since trigger
    thisExp.addData('conflict_text', conflict_text)
    thisExp.addData('first_speaker', first_speaker)
    thisExp.addData('participant_role', participant_role)
    thisExp.nextEntry()    

# ----------------------------------------------------
def main(pid: str, session: int, conflict: str, csv_path: str):
    if session not in (1, 2):
        raise ValueError("Session must be 1 or 2")
    if not (conflict and conflict.strip()):
        raise ValueError("You must provide a non-empty --conflict string")

    # Decode ID and role
    dyad, role = decode_pid(pid)

    # Lookup from CSV (authoritative)
    row = load_assignment_row(csv_path, pid)
    exp_condition = row["condition"]
    starters = {
        "Neutral_session_1": row["Neutral_session_1"],
        "Couple_session_1":  row["Couple_session_1"],
        "Neutral_session_2": row["Neutral_session_2"],
        "Couple_session_2":  row["Couple_session_2"],
    }
    starters_summary = f"N1={starters['Neutral_session_1']},C1={starters['Couple_session_1']},N2={starters['Neutral_session_2']},C2={starters['Couple_session_2']}"

    first_speaker = pick_first_speaker(starters, session)  # 'A' or 'B'
    turn_role = 'speaker' if role == first_speaker else 'listener'
    conflict_text = conflict.strip()

    # --- data files ---
    date_str = data.getDateStr()
    os.makedirs("data", exist_ok=True)
    base = f"{pid}_sess{session}_{SESSION_TYPE}_{exp_condition}_{first_speaker}_{slug(conflict_text)}"
    filename = os.path.join("data", f"{base}_CONV_min_{date_str}")

    thisExp = data.ExperimentHandler(
        name="CONV_min",
        extraInfo={
            'session_type': SESSION_TYPE,  # fixed
        },
        savePickle=True, saveWideText=True, dataFileName=filename
    )
    logging.LogFile(filename + '.log', level=logging.EXP)
    logging.console.setLevel(logging.WARNING)

    # --- TimingsLog (trimmed as requested) ---
    timings_path = os.path.join("data", f"{base}_CONV_TimingsLog_{date_str}.csv")
    fLog = open(timings_path, "w", newline="", encoding="utf-8")
    # New minimal header
    fLog.write("dyad,session,exp_condition,role,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n")
    fLog.flush()

    # TTL timestamps file (unchanged/verbose)
    ttl_path = os.path.join("data", f"{base}_CONV_TTLtimestamps_{date_str}.csv")
    fTTL = open(ttl_path, "w", newline="", encoding="utf-8")
    fTTL.write("dyad,session,exp_condition,role,segment,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n")

    fTTL.flush()

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
    show_topic        = txt(text="", pos=(0, 0.35))  # adjust pos as you like
    show_wait = txt(text="", pos=(0, 0.0))
    show_end          = txt(text="You are now done with this task.")

    trial_speak_text  = "YOUR TURN TO SPEAK"
    trial_listen_text = "YOUR TURN TO LISTEN"

    # --- start screen ---
    # --- Single combined instruction screen ---
    combined_instr = (
        "In this next part of the experiment you will have a conversation with your partner.\n\n"
        "In this conversation, you will discuss a problematic area of the relationship.\n\n"
        f"Please discuss the following problem area: {conflict_text}.\n\n" 
         "When it’s your turn, speak; when it’s not, listen.\n\n"
        f"Press {KEY_PASS!r} to pass the mic to your partner.\n\n"
        "Waiting for scanner trigger (=) to start...\n"
    )

    # Make sure convo UI is hidden here
    show_role_txt.setText("")
    show_pass.setText("")

    # Clear stale keys and show the combined screen
    event.clearEvents(eventType='keyboard')
    show_instructions.setText(combined_instr)

    trigger_source = None
    while trigger_source is None:
        show_instructions.draw()
        win.flip()
        keys = event.getKeys()  # don't filter so we see the exact names
        if KEY_QUIT in keys:
            win.close(); core.quit()
        # Start ONLY on TTL '=' (aka 'equal')
        if any(k in TTL_ACCEPT for k in keys):
            trigger_source = 'ttl'
            break
        core.wait(0.01)

    # Start run clock on TTL trigger
    run_clock = core.Clock()

    # --- TimingsLog: trigger/start ---
    fLog.write(
        f"{dyad},{session},{exp_condition},trigger_start_{trigger_source},"
        f"{time.time()},{run_clock.getTime()},,"
        f"{conflict_text},{first_speaker},{role}\n"
    ); fLog.flush()

    # (Optional) TTL file symmetry
    log_ttl(
        fTTL, exp_condition, '', f'trigger_start_{trigger_source}',
        run_clock, None, conflict_text, first_speaker, role, session, dyad
    )

    # Main CSV: trigger row (same schema as button presses)
    log_comm_press(
        thisExp,
        event_name=f"trigger_start_{trigger_source}",
        role_label='',
        run_clock=run_clock,
        comm_clock=None,  # not in comm phase yet
        dyad=dyad,
        session=session,
        exp_condition=exp_condition,
        conflict_text=conflict_text,
        first_speaker=first_speaker,
        participant_role=role
    )

    # brief blank before communication UI
    show_blank.draw(); win.flip()
    blank_clock = core.Clock()
    while blank_clock.getTime() < INSTR_BLANK_S:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if TTL_KEY in keys:
                log_ttl(fTTL, exp_condition, '', 'blank', run_clock, None,
                        conflict_text, first_speaker, role, session, dyad)
                event.clearEvents(eventType='keyboard')
            if KEY_QUIT in keys:
                win.close(); core.quit()
        core.wait(0.01)
    # ---------------------------
    # Communication phase
    # ---------------------------
    # set texts now (first time they appear)

    fLog.write(f"{dyad},{session},{exp_condition},Communication_start,{time.time()},{run_clock.getTime()},,"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()


    role_text = "YOUR TURN TO SPEAK" if (role == first_speaker) else "YOUR TURN TO LISTEN"
    pass_text = "Press '1' to pass the mic." if (role == first_speaker) else ""
    
    # set and show the topic during the convo
    show_topic.setText(f"Problem topic: {conflict_text}")

    show_role_txt.setText(role_text)
    show_pass.setText(pass_text)
    show_role_txt.setAutoDraw(True)
    show_timer.setAutoDraw(True)
    show_pass.setAutoDraw(True)
    show_topic.setAutoDraw(True)   # ← you were missing this line


    comm_clock = core.Clock()
    
    # main csv file
    current_role = 'speaker' if (role == first_speaker) else 'listener'
    thisExp.addData('dyad', dyad)
    thisExp.addData('session', session)
    thisExp.addData('exp_condition', exp_condition)
    thisExp.addData('event', 'communication_start')   # NEW
    thisExp.addData('role', current_role)             # speaker/listener at start
    thisExp.addData('onset_run_s', run_clock.getTime())   # seconds since trigger
    thisExp.addData('onset_phase_s', comm_clock.getTime())# ~0.0 at phase start
    thisExp.addData('conflict_text', conflict_text)
    thisExp.addData('first_speaker', first_speaker)
    thisExp.addData('participant_role', role)         # participant’s A/B role
    thisExp.nextEntry()

    # TimingsLog: Comm phase starts with participant's current role label
    fLog.write(f"{dyad},{session},{exp_condition},{'speaker' if (role == first_speaker) else 'listener'},"
               f"{time.time()},{run_clock.getTime()},{comm_clock.getTime()},"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    while comm_clock.getTime() < COMM_S:
        current_role_label = 'speaker' if show_role_txt.text == "YOUR TURN TO SPEAK" else 'listener'
        keys_ttl = event.getKeys([TTL_KEY])
        if keys_ttl and (TTL_KEY in keys_ttl):
            log_ttl(fTTL, exp_condition, current_role_label, 'communication', run_clock, comm_clock,
                    conflict_text, first_speaker, role, session, dyad)
            event.clearEvents(eventType='keyboard')

        remaining = int(round(COMM_S - comm_clock.getTime()))
        show_timer.setText(f"{remaining} seconds")
        win.flip()

        keys = event.getKeys(keyList=[KEY_PASS, KEY_QUIT], timeStamped=comm_clock)
        if keys:
            key, _rt = keys[-1]
            if key == KEY_QUIT:
                win.close(); core.quit()
            elif key == KEY_PASS:
                # toggle label
                current = show_role_txt.text
                new_txt = "YOUR TURN TO LISTEN" if current == "YOUR TURN TO SPEAK" else "YOUR TURN TO SPEAK"
                show_role_txt.setText(new_txt)
                show_pass.setText("Press '1' to pass the mic." if new_txt == "YOUR TURN TO SPEAK" else "")

                # define role after the toggle
                toggled_role = 'speaker' if new_txt == "YOUR TURN TO SPEAK" else 'listener'

                # TimingsLog: role toggle moment (keep this)
                fLog.write(
                    f"{dyad},{session},{exp_condition},{toggled_role},"
                    f"{time.time()},{run_clock.getTime()},{comm_clock.getTime()},"
                    f"{conflict_text},{first_speaker},{role}\n"
                )
                fLog.flush()

                # Main CSV: button press log
                log_comm_press(
                    thisExp,
                    event_name='pass_press',
                    role_label=toggled_role,
                    run_clock=run_clock,
                    comm_clock=comm_clock,
                    dyad=dyad,
                    session=session,
                    exp_condition=exp_condition,
                    conflict_text=conflict_text,
                    first_speaker=first_speaker,
                    participant_role=role
                )

    for stim in (show_role_txt, show_timer, show_pass, show_topic):
        stim.setAutoDraw(False)

    # TimingsLog: Communication end
    fLog.write(f"{dyad},{session},{exp_condition},communication_end,{time.time()},{run_clock.getTime()},{comm_clock.getTime()},"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    # --- PRE-OPINION BLANK ---
    show_blank.draw(); win.flip()
    preop_clock = core.Clock()
    while preop_clock.getTime() < BETWEEN_TRIAL_S:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if TTL_KEY in keys:
                log_ttl(fTTL, exp_condition, '', 'pre_opinion_blank', run_clock, None,
                        conflict_text, first_speaker, role, session, dyad)
                event.clearEvents(eventType='keyboard')
            if KEY_QUIT in keys:
                win.close(); core.quit()
        core.wait(0.01)

    # ===========================
    # SOLO OPINION PHASE
    # ===========================
    show_opinion.setText(f"{OPINION_PROMPT} {conflict_text}")
    show_opinion.setAutoDraw(True)
    show_timer.setAutoDraw(True)

    op_clock = core.Clock()
    
    # --- MAIN CSV ROW: opinion_start (onset) ---
    thisExp.addData('dyad', dyad)
    thisExp.addData('session', session)
    thisExp.addData('exp_condition', exp_condition)
    thisExp.addData('event', 'opinion_start')         # NEW
    thisExp.addData('role', '')                       # (no speaker/listener here)
    thisExp.addData('onset_run_s', run_clock.getTime())
    thisExp.addData('onset_phase_s', op_clock.getTime())  # ~0.0 at phase start
    thisExp.addData('conflict_text', conflict_text)
    thisExp.addData('first_speaker', first_speaker)
    thisExp.addData('participant_role', role)
    thisExp.nextEntry()

    # TimingsLog: Opinion start
    fLog.write(f"{dyad},{session},{exp_condition},opinion_start,{time.time()},{run_clock.getTime()},{op_clock.getTime()},"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    submitted = False
    while op_clock.getTime() < OPINION_S and not submitted:
        remaining = int(round(OPINION_S - op_clock.getTime()))
        show_timer.setText(f"{remaining} seconds")
        keys = event.getKeys([TTL_KEY, KEY_QUIT] + ([KEY_SUBMIT] if KEY_SUBMIT else []))
        if keys:
            if TTL_KEY in keys:
                log_ttl(fTTL, exp_condition, '', 'opinion', run_clock, op_clock,
                        conflict_text, first_speaker, role, session, dyad)
                event.clearEvents(eventType='keyboard')
            if KEY_QUIT in keys:
                win.close(); core.quit()
            if KEY_SUBMIT and (KEY_SUBMIT in keys):
                submitted = True
                event.clearEvents(eventType='keyboard')
        win.flip()

    op_dur = op_clock.getTime()

    # TimingsLog: Opinion end
    fLog.write(f"{dyad},{session},{exp_condition},opinion_end,{time.time()},{run_clock.getTime()},{op_clock.getTime()},"
               f"{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    # Stop drawing opinion UI
    show_opinion.setAutoDraw(False)
    show_timer.setAutoDraw(False)
    win.flip()  # clears the screen before showing fixation

    # final mini blank
    show_blank.draw(); win.flip()
    blank_clock2 = core.Clock()
    while blank_clock2.getTime() < BETWEEN_TRIAL_S:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if TTL_KEY in keys:
                log_ttl(fTTL, exp_condition, '', 'blank', run_clock, None,
                        conflict_text, first_speaker, role, session, dyad)
                event.clearEvents(eventType='keyboard')
            if KEY_QUIT in keys:
                win.close(); core.quit()
        core.wait(0.01)

    # ---------------------------
    
    # end screen
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
    ap.add_argument("--conflict", "-t", type=str, required=True, help="Human-readable conflict topic to display/log")
    ap.add_argument("--csv", "-c", type=str, default=CSV_FILENAME, help="Path to participant_counterbalancing.csv")
    args = ap.parse_args()
    main(pid=args.pid, session=args.session, conflict=args.conflict, csv_path=args.csv)
