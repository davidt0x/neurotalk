#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Conversation Task (speaker/listener with pass key).
- Waits for TTL '=' (aka 'equal'), brief blank, then runs COMM_S with pass toggles.
- Logs:
  * data/<base>_CONV_min_<date>.csv (main wide CSV via ExperimentHandler)
  * data/<base>_CONV_TimingsLog_<date>.csv (minimal timings log)
  * data/<base>_CONV_TTLtimestamps_<date>.csv (verbose TTL pings)
"""

import os, time, argparse, re, csv, queue
from pathlib import Path
from typing import Optional

from psychopy import visual, core, data, event, logging, monitors

from neurotalk.config import AudioConfig, NetworkConfig, RecordingConfig, SessionConfig
from neurotalk.control import ControlMessageType
from neurotalk.session import ConversationSession

# ---------- config ----------
SCANNER       = None
WIN_SIZE      = (1280, 800)
FULLSCR       = False
LETTER_H      = 0.07
WRAP_W        = 2

INSTR_BLANK_S   = 10.0   # blank after instruction/trigger, before conversation UI
COMM_S          = 30.0

KEY_PASS    = '1'
KEY_QUIT    = 'escape'
KEY_TRIGGER = 'space'
TTL_KEY     = 'equal'
TTL_ACCEPT  = {'equal', '='}
TRIGGER_ACCEPT = {'space', KEY_TRIGGER}

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

def log_ttl(fTTL, exp_condition, role_label, segment, run_clock, phase_clock,
            conflict_text, first_speaker, role, session, dyad,SESSION_TYPE):
    fTTL.write(
        f"{dyad},{session},{SESSION_TYPE},{exp_condition},{role_label},{segment},"
        f"{time.time()},{run_clock.getTime()},{'' if phase_clock is None else phase_clock.getTime()},"
        f"{conflict_text},{first_speaker},{role}\n"
    ); fTTL.flush()

def log_comm_press(thisExp, *, event_name, role_label, run_clock, phase_clock,
                   dyad, session, exp_condition, conflict_text, first_speaker, participant_role):
    thisExp.addData('dyad', dyad)
    thisExp.addData('session', session)
    thisExp.addData('exp_condition', exp_condition)
    thisExp.addData('event', event_name)                 # 'pass_press' / 'quit_press' / 'trigger_start_ttl'
    thisExp.addData('role', role_label or '')            # 'speaker'/'listener' AFTER toggle
    thisExp.addData('onset_run_s', run_clock.getTime())  # secs since trigger
    if phase_clock is not None:
        thisExp.addData('onset_phase_s', phase_clock.getTime())
    thisExp.addData('conflict_text', conflict_text)
    thisExp.addData('first_speaker', first_speaker)
    thisExp.addData('participant_role', participant_role)
    thisExp.nextEntry()


def set_mode(session: ConversationSession, *, speaking: bool) -> None:
    session.enable_transmit(speaking)
    session.enable_receive(not speaking)

# ----------------------------------------------------
def main(
    *,
    pid: str,
    session: int,
    conflict: str,
    csv_path: str,
    remote_ip: str,
    record_dir: str,
    local_in: int,
    local_out: int,
    local_control: int,
    remote_in: int,
    remote_out: int,
    remote_control: int,
    nat_role: Optional[int],
    chunk_frames: int,
    sample_rate: int,
):
    if session not in (1, 2):
        raise ValueError("Session must be 1 or 2")
    if not (conflict and conflict.strip()):
        raise ValueError("You must provide a non-empty --conflict string")

    # Decode ID and role
    dyad, role = decode_pid(pid)

    # Lookup from CSV
    row = load_assignment_row(csv_path, pid)
    exp_condition = row["condition"]
    starters = {
        "Neutral_session_1": row["Neutral_session_1"],
        "Couple_session_1":  row["Couple_session_1"],
        "Neutral_session_2": row["Neutral_session_2"],
        "Couple_session_2":  row["Couple_session_2"],
    }

    first_speaker = pick_first_speaker(starters, session)  # 'A' or 'B'
    conflict_text = conflict.strip()

    recording_dir = Path(record_dir)
    recording_dir.mkdir(parents=True, exist_ok=True)

    nat_role_value = nat_role if nat_role is not None else (1 if role == 'A' else 0)
    network = NetworkConfig(
        local_ports=(local_in, local_out, local_control),
        remote_hint=(remote_ip, remote_in, remote_out, remote_control),
        nat_role=nat_role_value,
        punch_timeout_s=10.0,
        stun_servers=(),
    )
    audio = AudioConfig(sample_rate_hz=sample_rate, chunk_frames=chunk_frames)
    recording = RecordingConfig(directory=recording_dir)
    session_cfg = SessionConfig(
        participant_id=pid,
        role=role,
        network=network,
        audio=audio,
        recording=recording,
    )
    conv_session: Optional[ConversationSession] = None

    conv_session = ConversationSession(session_cfg)
    conv_session.connect()
    conv_session.enable_transmit(False)
    conv_session.enable_receive(False)

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
    fLog.write("dyad,session,session_type,exp_condition,role,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"); fLog.flush()

    # TTL timestamps (verbose)
    ttl_path = os.path.join("data", f"{base}_CONV_TTLtimestamps_{date_str}.csv")
    fTTL = open(ttl_path, "w", newline="", encoding="utf-8")
    fTTL.write("dyad,session,session_type,exp_condition,role,segment,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"); fTTL.flush()

    # --- window & text objects ---
    mon = make_monitor(SCANNER)
    win = visual.Window(size=WIN_SIZE, color='black', fullscr=FULLSCR, units='norm', monitor=mon)
    win.mouseVisible = False
    txt = lambda **kw: visual.TextStim(win, height=LETTER_H, wrapWidth=WRAP_W, color='white', **kw)

    show_instructions = txt(text="")
    show_role_txt     = txt(text="", pos=(0, 0.65))
    show_pass         = txt(text="", pos=(0, 0.05))
    show_timer        = txt(text="", pos=(0, -0.70))
    show_topic        = txt(text="", pos=(0, 0.35))
    show_blank        = txt(text="+", pos=(0, 0.00))
    show_end          = txt(text="You are now done with this task.")

    # --- instruction & trigger (condition-dependent) ---
    cond = (exp_condition or "").strip().lower()
    minutes = int(round(COMM_S / 60.0))

    persuade_instr_text_couple = (
        "Next, you will discuss a problem area in your relationship with your partner.\n"
        f"Please discuss the following problem area: {conflict_text.upper()}.\n\n\n"
        "IMPORTANT: During this conversation, try to PERSUADE the other person of your opinion.\n"
        "We are studying how persuasion works in the brain, so please try to convince the other\n"
        "person of your opinion as much as possible and get them to understand your perspective.\n"
        "These instructions are only for you. So, please don't share them with your partner.\n\n\n"
        f"You will have {minutes} minute{'s' if minutes != 1 else ''} for this conversation.\n"
        "A timer will show you how many seconds are left.\n\n\n"
        "Tell the experimenter when you are ready to begin.\n"
        "You’ll first see a fixation cross for 10 seconds.\n"
        "After that, you will see instructions to begin the conversation."
    )

    compromise_instr_text_couple = (
        "Next, you will discuss a problem area in your relationship with your partner.\n"
        f"Please discuss the following problem area: {conflict_text.upper()}.\n\n\n"
        "IMPORTANT: During this conversation, try to find a JOINT SOLUTION that you both agree on.\n"
        "We are studying how collaboration works in the brain, so please try to reconcile any\n"
        "differences of opinion as much as possible and look for a shared perspective.\n"
        "These instructions are only for you. So, please don't share them with your partner.\n\n\n"
        f"You will have {minutes} minute{'s' if minutes != 1 else ''} for this conversation.\n"
        "A timer will show you how many seconds are left.\n\n\n"
        "Tell the experimenter when you are ready to begin.\n"
        "You’ll first see a fixation cross for 10 seconds.\n"
        "After that, you will see instructions to begin the conversation."
    )

    default_instr_text = (
        "In this next part of the experiment you will have a conversation with your partner.\n\n"
        f"Please discuss the following problem area: {conflict_text.upper()}.\n\n"
        f"You will have {minutes} minute{'s' if minutes != 1 else ''} for this conversation.\n"
        "A timer will show you how many seconds are left.\n\n"
        "Tell the experimenter when you are ready to begin."
    )

    instr_text = (
        persuade_instr_text_couple if cond == "persuade"
        else compromise_instr_text_couple if cond == "compromise"
        else default_instr_text
    )

    event.clearEvents(eventType='keyboard')
    show_instructions.setText(instr_text)   # ← CLEAN. No scanner text injected here.

    trigger_source = None
    while trigger_source is None:
        show_instructions.draw(); win.flip()
        keys = event.getKeys()
        if KEY_QUIT in keys:
            if conv_session is not None:
                conv_session.close()
            win.close(); core.quit()
        if any(k in TTL_ACCEPT for k in keys):
            trigger_source = 'ttl'
            break
        core.wait(0.01)

    run_clock = core.Clock()


    # TimingsLog + TTL + main CSV: trigger
    fLog.write(f"{dyad},{session},{SESSION_TYPE},{exp_condition},trigger_start_{trigger_source},{time.time()},{run_clock.getTime()},,,{first_speaker},{role}\n"); fLog.flush()
    log_ttl(fTTL, exp_condition, '', f'trigger_start_{trigger_source}', run_clock, None, conflict_text, first_speaker, role, session, dyad,SESSION_TYPE)
    log_comm_press(thisExp, event_name=f"trigger_start_{trigger_source}", role_label='', run_clock=run_clock, phase_clock=None,
                   dyad=dyad, session=session, exp_condition=exp_condition, conflict_text=conflict_text, first_speaker=first_speaker, participant_role=role)

    # brief blank
    show_blank.draw(); win.flip()
    blank_clock = core.Clock()
    while blank_clock.getTime() < INSTR_BLANK_S:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if TTL_KEY in keys:
                log_ttl(fTTL, exp_condition, '', 'blank', run_clock, None, conflict_text, first_speaker, role, session, dyad,SESSION_TYPE)
                event.clearEvents(eventType='keyboard')
            if KEY_QUIT in keys:
                if conv_session is not None:
                    conv_session.close()
                win.close(); core.quit()
        core.wait(0.01)

    # ---------------------------
    # Conversation phase
    # ---------------------------
    fLog.write(f"{dyad},{session},{SESSION_TYPE},{exp_condition},Communication_start,{time.time()},{run_clock.getTime()},,{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    speaking = (role == first_speaker)
    local_segment_active = False
    remote_segment_active = False
    local_segment_counter = 0
    remote_segment_counter = 0

    def begin_local_segment() -> None:
        nonlocal local_segment_active, local_segment_counter
        if not local_segment_active:
            local_segment_counter += 1
            conv_session.start_segment(f"local_turn{local_segment_counter:02d}", target="local")
            local_segment_active = True

    def end_local_segment() -> None:
        nonlocal local_segment_active
        if local_segment_active:
            conv_session.stop_segment(target="local")
            local_segment_active = False

    def begin_remote_segment() -> None:
        nonlocal remote_segment_active, remote_segment_counter
        if not remote_segment_active:
            remote_segment_counter += 1
            conv_session.start_segment(f"remote_turn{remote_segment_counter:02d}", target="remote")
            remote_segment_active = True

    def end_remote_segment() -> None:
        nonlocal remote_segment_active
        if remote_segment_active:
            conv_session.stop_segment(target="remote")
            remote_segment_active = False

    role_text = "YOUR TURN TO SPEAK" if speaking else "YOUR TURN TO LISTEN"
    pass_text = "Press '1' to pass the mic." if speaking else ""
    show_topic.setText(f"Problem topic: {conflict_text}")

    show_role_txt.setText(role_text)
    show_pass.setText(pass_text)
    show_role_txt.setAutoDraw(True)
    show_pass.setAutoDraw(True)
    show_timer.setAutoDraw(True)
    show_topic.setAutoDraw(True)

    comm_clock = core.Clock()
    set_mode(conv_session, speaking=speaking)
    if speaking:
        begin_local_segment()
    else:
        begin_remote_segment()

    # Main CSV: conversation_start
    thisExp.addData('dyad', dyad)
    thisExp.addData('session', session)
    thisExp.addData('exp_condition', exp_condition)
    thisExp.addData('event', 'communication_start')
    thisExp.addData('role', 'speaker' if (role == first_speaker) else 'listener')
    thisExp.addData('onset_run_s', run_clock.getTime())
    thisExp.addData('onset_phase_s', comm_clock.getTime())
    thisExp.addData('conflict_text', conflict_text)
    thisExp.addData('first_speaker', first_speaker)
    thisExp.addData('participant_role', role)
    thisExp.nextEntry()

    # TimingsLog: current role at start
    fLog.write(f"{dyad},{session},{SESSION_TYPE},{exp_condition},{'speaker' if (role == first_speaker) else 'listener'},{time.time()},{run_clock.getTime()},{comm_clock.getTime()},{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    while comm_clock.getTime() < COMM_S:
        while True:
            try:
                msg_type, _ = conv_session.next_control_event(timeout=0.0)
            except queue.Empty:
                break
            if msg_type == ControlMessageType.TURN_PASS and not speaking:
                end_remote_segment()
                begin_local_segment()
                set_mode(conv_session, speaking=True)
                speaking = True
                show_role_txt.setText("YOUR TURN TO SPEAK")
                show_pass.setText("Press '1' to pass the mic.")
                toggled_role = 'speaker'
                fLog.write(f"{dyad},{session},{SESSION_TYPE},{exp_condition},{toggled_role},{time.time()},{run_clock.getTime()},{comm_clock.getTime()},{conflict_text},{first_speaker},{role}\n"); fLog.flush()
                log_comm_press(
                    thisExp,
                    event_name='partner_pass',
                    role_label=toggled_role,
                    run_clock=run_clock,
                    phase_clock=comm_clock,
                    dyad=dyad,
                    session=session,
                    exp_condition=exp_condition,
                    conflict_text=conflict_text,
                    first_speaker=first_speaker,
                    participant_role=role,
                )
            else:
                continue

        current_role_label = 'speaker' if show_role_txt.text == "YOUR TURN TO SPEAK" else 'listener'
        keys_ttl = event.getKeys([TTL_KEY])
        if keys_ttl and (TTL_KEY in keys_ttl):
            log_ttl(fTTL, exp_condition, current_role_label, 'communication', run_clock, comm_clock, conflict_text, first_speaker, role, session, dyad,SESSION_TYPE)
            event.clearEvents(eventType='keyboard')

        remaining = int(round(COMM_S - comm_clock.getTime()))
        show_timer.setText(f"{remaining} seconds")
        win.flip()

        keys = event.getKeys(keyList=[KEY_PASS, KEY_QUIT], timeStamped=comm_clock)
        if keys:
            key, _rt = keys[-1]
            if key == KEY_QUIT:
                if conv_session is not None:
                    conv_session.close()
                win.close(); core.quit()
            elif key == KEY_PASS:
                if not speaking:
                    continue
                time_here = time.time()
                run_here = run_clock.getTime()
                comm_here = comm_clock.getTime()
                end_local_segment()
                conv_session.pass_turn(run_time=run_here, phase_time=comm_here)
                set_mode(conv_session, speaking=False)
                begin_remote_segment()
                speaking = False
                show_role_txt.setText("YOUR TURN TO LISTEN")
                show_pass.setText("")
                toggled_role = 'listener'
                fLog.write(f"{dyad},{session},{SESSION_TYPE},{exp_condition},{toggled_role},{time_here},{run_here},{comm_here},{conflict_text},{first_speaker},{role}\n"); fLog.flush()
                log_comm_press(
                    thisExp,
                    event_name='pass_press',
                    role_label=toggled_role,
                    run_clock=run_clock,
                    phase_clock=comm_clock,
                    dyad=dyad,
                    session=session,
                    exp_condition=exp_condition,
                    conflict_text=conflict_text,
                    first_speaker=first_speaker,
                    participant_role=role,
                )

    end_local_segment()
    end_remote_segment()
    set_mode(conv_session, speaking=False)

    # stop drawing
    
    # --- MAIN CSV ROW: communication_end ---
    end_role_label = 'speaker' if show_role_txt.text == "YOUR TURN TO SPEAK" else 'listener'
    thisExp.addData('dyad', dyad)
    thisExp.addData('session', session)
    thisExp.addData('exp_condition', exp_condition)
    thisExp.addData('event', 'communication_end')          # explicit phase boundary
    thisExp.addData('role', end_role_label)                # role at end of comm
    thisExp.addData('onset_run_s', run_clock.getTime())
    thisExp.addData('onset_phase_s', comm_clock.getTime())
    thisExp.addData('conflict_text', conflict_text)
    thisExp.addData('first_speaker', first_speaker)
    thisExp.addData('participant_role', role)
    thisExp.nextEntry()
    
    fLog.write(f"{dyad},{session},{SESSION_TYPE},{exp_condition},communication_end,{time.time()},{run_clock.getTime()},{comm_clock.getTime()},{conflict_text},{first_speaker},{role}\n"); fLog.flush()

    for stim in (show_role_txt, show_timer, show_pass, show_topic):
        stim.setAutoDraw(False)

    # end logs

    show_end.draw(); win.flip(); core.wait(1.0)

    if conv_session is not None:
        conv_session.close()
        try:
            export_dir = recording_dir / "segments"
            segments = conv_session.export_segments(export_dir)
            print(f"Segments exported to {export_dir}: {segments}")
        except Exception as exc:
            print(f"Segment export failed: {exc}")

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
    ap.add_argument("--remote-ip", required=True, help="Peer IP address")
    ap.add_argument("--record-dir", default="data", help="Directory for NeuroTalk recordings")
    ap.add_argument("--local-in", type=int, default=30002, help="Local inbound audio port")
    ap.add_argument("--local-out", type=int, default=30001, help="Local outbound audio port")
    ap.add_argument("--local-control", type=int, default=30003, help="Local control port")
    ap.add_argument("--remote-in", type=int, default=30002, help="Remote inbound audio port")
    ap.add_argument("--remote-out", type=int, default=30001, help="Remote outbound audio port")
    ap.add_argument("--remote-control", type=int, default=30003, help="Remote control port")
    ap.add_argument("--nat-role", type=int, choices=[0,1], default=None, help="NAT traversal role override (0=passive,1=active)")
    ap.add_argument("--chunk-frames", type=int, default=512, help="Audio chunk size (frames)")
    ap.add_argument("--sample-rate", type=int, default=16000, help="Audio sample rate (Hz)")
    args = ap.parse_args()
    main(
        pid=args.pid,
        session=args.session,
        conflict=args.conflict,
        csv_path=args.csv,
        remote_ip=args.remote_ip,
        record_dir=args.record_dir,
        local_in=args.local_in,
        local_out=args.local_out,
        local_control=args.local_control,
        remote_in=args.remote_in,
        remote_out=args.remote_out,
        remote_control=args.remote_control,
        nat_role=args.nat_role,
        chunk_frames=args.chunk_frames,
        sample_rate=args.sample_rate,
    )
