from __future__ import annotations

import argparse
import queue
import time
from pathlib import Path

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

from neurotalk.config import AudioConfig, NetworkConfig, RecordingConfig, SessionConfig
from neurotalk.session import ConversationSession
from neurotalk.turns import TurnEventSource, TurnManager, TurnRole

# ---------- config ----------
SCANNER = None
WIN_SIZE = (1280, 800)
FULLSCR = True
LETTER_H = 0.07
WRAP_W = 2

INTRO_S = 10.0  # intro dwell before communication
COMM_S = 600.0  # communication phase duration (s)
SYNC_START_LAG = 12.0  # lead-in before instructions to sync timing

KEY_PASS = "1"
KEY_QUIT = "escape"
TTL_KEY = "5"  # for TTL pings during phases
TTL_ACCEPT = {"equal", "=", TTL_KEY}

RUN_NUM = 1
CSV_FILENAME = "participant_counterbalancing.csv"
SESSION_TYPE = "neutral"  # fixed for this task


def canonical_topic(topic: str) -> str:
    mapping = {
        "air": "Air pollution",
        "tuition": "Cost of tuition",
    }
    cleaned = (topic or "").strip()
    return mapping.get(cleaned.lower(), cleaned)


def main(
    *,
    pid: str,
    session: int,
    csv_path: str,
    remote_ip: str,
    record_dir: str,
    local_in: int,
    local_out: int,
    local_control: int,
    remote_in: int,
    remote_out: int,
    remote_control: int,
    nat_role: int | None,
    chunk_frames: int,
    sample_rate: int,
    mixdown: bool,
    mix_track: str | None,
    mock_audio: bool,
    log_level: str,
):
    if session not in (1, 2):
        msg = "Session must be 1 or 2"
        raise ValueError(msg)

    dyad, role = decode_pid(pid)
    assignment = load_assignment_row(csv_path, pid)
    starters = assignment.starters()
    exp_condition = assignment.condition

    discussion_topic = (
        assignment.first_topic if session == 1 else assignment.second_topic
    )
    discussion_topic = (discussion_topic or "").strip()
    if not discussion_topic:
        which = "first_topic" if session == 1 else "second_topic"
        msg = (
            f"No discussion topic found in CSV for participant {pid} session {session} "
            f"(expected column '{which}' to be non-empty)."
        )
        raise ValueError(msg)
    conflict_text = discussion_topic
    display_topic = canonical_topic(conflict_text)

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
    conv_instr_text = (
        persuade_instr_text if cond_lower.startswith("persu") else compromise_instr_text
    )

    first_speaker = pick_first_speaker(
        starters, session=session, session_type="neutral"
    )

    recording_dir = Path(record_dir)
    recording_dir.mkdir(parents=True, exist_ok=True)

    nat_role_value = nat_role if nat_role is not None else (1 if role == "A" else 0)
    network = NetworkConfig(
        local_ports=(local_in, local_out, local_control),
        remote_hint=(remote_ip, remote_in, remote_out, remote_control),
        nat_role=nat_role_value,
        punch_timeout_s=10.0,
        stun_servers=(),
    )
    audio = AudioConfig(
        sample_rate_hz=sample_rate,
        chunk_frames=chunk_frames,
        mock_devices=mock_audio,
    )
    mix_path = Path(mix_track) if mix_track else None
    recording = RecordingConfig(directory=recording_dir, mix_track=mix_path)
    session_cfg = SessionConfig(
        participant_id=pid,
        role=role,
        network=network,
        audio=audio,
        recording=recording,
    )
    conv_session: ConversationSession | None = ConversationSession(session_cfg)
    turn_manager = TurnManager(conv_session)
    conv_session.connect()
    conv_session.enable_transmit(False)
    conv_session.enable_receive(False)

    logger = TaskLogger(
        pid=pid,
        session=session,
        session_type=SESSION_TYPE,
        exp_condition=exp_condition,
        first_speaker=first_speaker,
        conflict_text_slug=slug(conflict_text),
        task_code="CONV",
        dyad=dyad,
        participant_role=role,
        conflict_text=conflict_text,
    )
    level_name = log_level.upper()
    logging.console.setLevel(getattr(logging, level_name, logging.WARNING))

    win = create_window(scanner=SCANNER, size=WIN_SIZE, fullscr=FULLSCR)
    make_text = text_factory(win, letter_height=LETTER_H, wrap_width=WRAP_W)

    # Trackball: treated as a standard mouse
    # On the 932 box, make sure the device is in a HID mouse/trackball mode.
    trackball = event.Mouse(win=win, visible=False)

    # For debouncing the pass button (left button on the trackball)
    last_pass_pressed = False

    show_instructions = make_text(text="")
    show_sync = make_text(text="Syncing start time with your partner...")
    show_role_txt = make_text(text="", pos=(0, 0.65))
    show_pass = make_text(text="", pos=(0, 0.05))
    show_timer = make_text(text="", pos=(0, -0.70))
    show_blank = make_text(text="+", pos=(0, 0.00))
    show_topic = make_text(text="", pos=(0, 0.35))
    show_end = make_text(text="You are now done with this task.")

    show_sync.setAutoDraw(True)
    win.flip()
    instr_sync_time = conv_session.sync_start(SYNC_START_LAG)
    logging.info("Pre-instruction sync ready at %s", instr_sync_time)
    while True:
        now = time.time()
        if now >= instr_sync_time:
            break
        keys = event.getKeys([KEY_QUIT])
        if KEY_QUIT in keys:
            if conv_session is not None:
                conv_session.close()
                conv_session = None
            logger.close()
            win.close()
            core.quit()
        win.flip()
        core.wait(0.01)
    show_sync.setAutoDraw(False)

    show_instructions.setText(conv_instr_text)
    show_role_txt.setText("")
    show_pass.setText("")
    event.clearEvents(eventType="keyboard")

    trigger_source: str | None = None
    while trigger_source is None:
        show_instructions.draw()
        win.flip()
        keys = event.getKeys()
        if KEY_QUIT in keys:
            if conv_session is not None:
                conv_session.close()
                conv_session = None
            logger.close()
            win.close()
            core.quit()
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
    logger.log_event(
        event_name=f"trigger_start_{trigger_source}",
        role_label="",
        run_clock=run_clock,
        phase_clock=None,
    )

    show_blank.draw()
    win.flip()
    blank_clock = core.Clock()
    while blank_clock.getTime() < 1.0:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if KEY_QUIT in keys:
                if conv_session is not None:
                    conv_session.close()
                    conv_session = None
                logger.close()
                win.close()
                core.quit()
            if TTL_KEY in keys:
                logger.log_ttl(
                    role_label="",
                    segment="blank",
                    run_clock=run_clock,
                    phase_clock=None,
                )
                event.clearEvents(eventType="keyboard")
        core.wait(0.01)

    if INTRO_S > 0:
        show_blank.draw()
        win.flip()
        intro_clock = core.Clock()
        while intro_clock.getTime() < INTRO_S:
            keys = event.getKeys([TTL_KEY, KEY_QUIT])
            if keys:
                if KEY_QUIT in keys:
                    if conv_session is not None:
                        conv_session.close()
                        conv_session = None
                    logger.close()
                    win.close()
                    core.quit()
                if TTL_KEY in keys:
                    logger.log_ttl(
                        role_label="",
                        segment="intro_fixation",
                        run_clock=run_clock,
                        phase_clock=None,
                    )
                    event.clearEvents(eventType="keyboard")
            core.wait(0.01)

    logger.log_timing(
        role_label="Communication_start",
        run_clock=run_clock,
        phase_clock=None,
    )

    initial_role = TurnRole.SPEAKER if role == first_speaker else TurnRole.LISTENER
    role_text = (
        "YOUR TURN TO SPEAK" if initial_role.is_speaker else "YOUR TURN TO LISTEN"
    )
    pass_text = "Press trackball button to pass the mic." if initial_role.is_speaker else ""
    show_topic.setText(f"Discussion topic: {display_topic}")

    show_role_txt.setText(role_text)
    show_pass.setText(pass_text)
    show_role_txt.setAutoDraw(True)
    show_timer.setAutoDraw(True)
    show_pass.setAutoDraw(True)
    show_topic.setAutoDraw(True)

    comm_clock = core.Clock()
    turn_manager.start(initial_role)

    current_role = "speaker" if initial_role.is_speaker else "listener"
    logger.log_event(
        event_name="communication_start",
        role_label=current_role,
        run_clock=run_clock,
        phase_clock=comm_clock,
    )

    logger.log_timing(
        role_label=current_role,
        run_clock=run_clock,
        phase_clock=comm_clock,
    )

    while comm_clock.getTime() < COMM_S:
        while True:
            try:
                msg_type, payload = conv_session.next_control_event(timeout=0.0)
            except queue.Empty:
                break
            turn_event = turn_manager.handle_control_event(msg_type, payload)
            if not turn_event or turn_event.source is not TurnEventSource.REMOTE_PASS:
                continue
            show_role_txt.setText("YOUR TURN TO SPEAK")
            show_pass.setText("Press trackball button to pass the mic.")
            toggled_role = "speaker"
            logger.log_timing(
                role_label=toggled_role,
                run_clock=run_clock,
                phase_clock=comm_clock,
            )
            logger.log_event(
                event_name="partner_pass",
                role_label=toggled_role,
                run_clock=run_clock,
                phase_clock=comm_clock,
            )

        current_role_label = "speaker" if turn_manager.is_speaker else "listener"

        keys_ttl = event.getKeys([TTL_KEY])
        if keys_ttl and (TTL_KEY in keys_ttl):
            logger.log_ttl(
                role_label=current_role_label,
                segment="communication",
                run_clock=run_clock,
                phase_clock=comm_clock,
            )
            event.clearEvents(eventType="keyboard")

        remaining = round(COMM_S - comm_clock.getTime())
        show_timer.setText(f"{remaining} seconds")
        win.flip()

        # ------ 1) Keyboard pass / quit ------
        keys = event.getKeys(keyList=[KEY_PASS, KEY_QUIT], timeStamped=comm_clock)
        key_from_kb = None
        if keys:
            key_from_kb, rt_kb = keys[-1]

        # ------ 2) Trackball pass (left button) ------
        # trackball buttons: [left, middle, right]
        buttons = trackball.getPressed()
        pass_pressed_now = bool(buttons[0])

        # Edge detection: only trigger on 0 -> 1 transition
        key_from_tb = None
        if pass_pressed_now and not last_pass_pressed:
            key_from_tb = KEY_PASS  # pretend we saw a '1' press from keyboard

        # Update debounce state
        last_pass_pressed = pass_pressed_now

        # Decide which "key" to act on (trackball gets priority if both fire)
        key = key_from_tb or key_from_kb

        if key is not None:
            if key == KEY_QUIT:
                if conv_session is not None:
                    conv_session.close()
                    conv_session = None
                logger.close()
                win.close()
                core.quit()

            elif key == KEY_PASS:
                # Only speakers can pass
                if not turn_manager.is_speaker:
                    # ignore if listener presses the trackball
                    continue

                time_here = time.time()
                run_here = run_clock.getTime()
                comm_here = comm_clock.getTime()

                turn_manager.pass_turn(
                    run_time=run_here, phase_time=comm_here, wall_time=time_here
                )

                show_role_txt.setText("YOUR TURN TO LISTEN")
                show_pass.setText("")
                toggled_role = "listener"

                logger.log_timing(
                    role_label=toggled_role,
                    wall_time=time_here,
                    run_time=run_here,
                    phase_time=comm_here,
                )

                logger.log_event(
                    event_name="pass_press",
                    role_label=toggled_role,
                    run_clock=run_clock,
                    phase_clock=comm_clock,
                )

    turn_manager.stop()

    for stim in (show_role_txt, show_timer, show_pass, show_topic):
        stim.setAutoDraw(False)

    logger.log_timing(
        role_label="communication_end",
        run_clock=run_clock,
        phase_clock=comm_clock,
    )

    end_role_label = (
        "speaker" if show_role_txt.text == "YOUR TURN TO SPEAK" else "listener"
    )
    logger.experiment.addData("dyad", dyad)
    logger.experiment.addData("session", session)
    logger.experiment.addData("exp_condition", exp_condition)
    logger.experiment.addData("event", "communication_end")
    logger.experiment.addData("role", end_role_label)
    logger.experiment.addData("onset_run_s", run_clock.getTime())
    logger.experiment.addData("onset_phase_s", comm_clock.getTime())
    logger.experiment.addData("conflict_text", conflict_text)
    logger.experiment.addData("first_speaker", first_speaker)
    logger.experiment.addData("participant_role", role)
    logger.experiment.nextEntry()

    show_end.draw()
    win.flip()
    core.wait(1.0)

    if conv_session is not None:
        conv_session.close()
        try:
            export_dir = recording_dir / "segments"
            conv_session.export_segments(export_dir)
        except Exception as exc:
            logging.error("Failed to export segments: %s", exc)
        if mixdown:
            try:
                mix_track_path = conv_session.export_mix_track()
                if mix_track_path:
                    logging.info("Mixed audio written to %s", mix_track_path)
            except Exception as exc:
                logging.error("Failed to generate mix track: %s", exc)

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
        "--csv",
        "-c",
        type=str,
        default=CSV_FILENAME,
        help="Path to participant_counterbalancing.csv",
    )
    ap.add_argument("--remote-ip", required=True, help="Peer IP address")
    ap.add_argument(
        "--record-dir",
        default="data",
        help="Directory for NeuroTalk recordings",
    )
    ap.add_argument(
        "--local-in", type=int, default=30002, help="Local inbound audio port"
    )
    ap.add_argument(
        "--local-out", type=int, default=30001, help="Local outbound audio port"
    )
    ap.add_argument(
        "--local-control", type=int, default=30003, help="Local control port"
    )
    ap.add_argument(
        "--remote-in", type=int, default=30002, help="Remote inbound audio port"
    )
    ap.add_argument(
        "--remote-out", type=int, default=30001, help="Remote outbound audio port"
    )
    ap.add_argument(
        "--remote-control", type=int, default=30003, help="Remote control port"
    )
    ap.add_argument(
        "--nat-role",
        type=int,
        choices=[0, 1],
        default=None,
        help="NAT traversal role override (0=passive,1=active)",
    )
    ap.add_argument(
        "--chunk-frames", type=int, default=512, help="Audio chunk size (frames)"
    )
    ap.add_argument(
        "--sample-rate", type=int, default=16000, help="Audio sample rate (Hz)"
    )
    ap.add_argument(
        "--mixdown",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Produce a mixed speaker/listener WAV file (use --no-mixdown to skip)",
    )
    ap.add_argument(
        "--mix-track",
        type=str,
        default=None,
        help="Filename (relative to --record-dir) for the mixed audio track",
    )
    ap.add_argument(
        "--mock-audio",
        action="store_true",
        help="Use mock audio devices for local testing (still records streams)",
    )
    ap.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        help="Console log level (DEBUG, INFO, WARNING, etc.)",
    )
    args = ap.parse_args()
    main(
        pid=args.pid,
        session=args.session,
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
        mixdown=args.mixdown,
        mix_track=args.mix_track,
        mock_audio=args.mock_audio,
        log_level=args.log_level,
    )
