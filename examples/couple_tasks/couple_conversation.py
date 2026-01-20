"""
Couple Conversation Task (speaker/listener with pass key).
- Waits for TTL '=' (aka 'equal'), brief blank, then runs COMM_S with pass toggles.
- Logs:
  * data/<base>_CONV_min_<date>.csv (main wide CSV via ExperimentHandler)
  * data/<base>_CONV_TimingsLog_<date>.csv (minimal timings log)
  * data/<base>_CONV_TTLtimestamps_<date>.csv (verbose TTL pings)
"""

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
        finalize_and_quit,
        load_assignment_row,
        pick_first_speaker,
        slug,
        text_factory,
    )
else:  # pragma: no cover - script-mode support
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from couple_tasks.log import TaskLogger  # type: ignore[import-not-found]
    from couple_tasks.utils import (  # type: ignore[import-not-found]
        create_window,
        decode_pid,
        finalize_and_quit,
        load_assignment_row,
        pick_first_speaker,
        slug,
        text_factory,
    )
from neurotalk.config import SessionConfig
from neurotalk.config_cli import add_config_arguments, load_config_from_args
from neurotalk.session import ConversationSession
from neurotalk.turns import TurnEventSource, TurnManager, TurnRole

# ---------- config ----------
SCANNER = None  # default monitor profile (override via --scanner)
WIN_SIZE = (1280, 800)
FULLSCR = True
LETTER_H = 0.07
WRAP_W = 2
RECORDING_LABEL = "couple_conversation"

INSTR_BLANK_S = 10.0  # blank after instruction/trigger, before conversation UI
COMM_S = 600.0
SYNC_START_LAG = 12.0  # lead-in before instructions to sync presentation timing

KEY_PASS = "1"
KEY_QUIT = "escape"
KEY_TRIGGER = "space"
TTL_KEY = "5"
TTL_ACCEPT = {"equal", "=", TTL_KEY}
TRIGGER_ACCEPT = {"space", KEY_TRIGGER}

CSV_FILENAME = "participant_counterbalancing.csv"
SESSION_TYPE = "couple"  # fixed for this task


# ----------------------------------------------------
def main(
    *,
    session_cfg: SessionConfig,
    scanner: str | None = SCANNER,
    fullscr: bool = True,
    session: int,
    conflict: str,
    csv_path: Path,
    mixdown: bool = True,
    log_level: str = "WARNING",
):
    if session not in (1, 2):
        msg = "Session must be 1 or 2"
        raise ValueError(msg)
    if not (conflict and conflict.strip()):
        msg = "You must provide a non-empty --conflict string"
        raise ValueError(msg)

    cfg = SessionConfig.from_dict(session_cfg.to_dict())
    pid = cfg.participant_id
    dyad, role = decode_pid(pid)
    cfg.participant_id = pid
    cfg.role = role

    assignment = load_assignment_row(csv_path, pid)
    starters = assignment.starters()
    exp_condition = assignment.condition

    first_speaker = pick_first_speaker(
        starters, session=session, session_type="couple"
    )  # 'A' or 'B'
    conflict_text = conflict.strip()
    conflict_slug = slug(conflict_text)

    recording_dir = cfg.recording.directory
    recording_dir.mkdir(parents=True, exist_ok=True)

    conv_session: ConversationSession | None = ConversationSession(
        cfg, recording_enabled=False, recording_label=RECORDING_LABEL
    )
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
        conflict_text_slug=conflict_slug,
        task_code="CONV",
        dyad=dyad,
        participant_role=role,
        conflict_text=conflict_text,
    )
    level_name = log_level.upper()
    level_value = getattr(logging, level_name, logging.WARNING)
    logging.console.setLevel(level_value)

    win = create_window(scanner=scanner, size=WIN_SIZE, fullscr=fullscr)
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
    show_topic = make_text(text="", pos=(0, 0.35))
    show_blank = make_text(text="+", pos=(0, 0.00))
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
            finalize_and_quit(conv_session, recording_dir, logger, mixdown, win)
            return
        win.flip()
        core.wait(0.01)
    show_sync.setAutoDraw(False)

    cond = (exp_condition or "").strip().lower()
    minutes = round(COMM_S / 60.0)

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
        persuade_instr_text_couple
        if cond == "persuade"
        else compromise_instr_text_couple
        if cond == "compromise"
        else default_instr_text
    )

    event.clearEvents(eventType="keyboard")
    show_instructions.setText(instr_text)

    trigger_source: str | None = None
    while trigger_source is None:
        show_instructions.draw()
        win.flip()
        keys = event.getKeys()
        if KEY_QUIT in keys:
            finalize_and_quit(conv_session, recording_dir, logger, mixdown, win)
            return
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

    show_blank.setAutoDraw(True)
    win.flip()
    logging.info("Starting pre-conversation blank for %.1fs", INSTR_BLANK_S)
    logger.log_timing(
        role_label="blank_start",
        run_clock=run_clock,
        phase_clock=None,
    )
    blank_clock = core.Clock()
    blank_clock.reset()
    while blank_clock.getTime() < INSTR_BLANK_S:
        keys = event.getKeys([TTL_KEY, KEY_QUIT])
        if keys:
            if TTL_KEY in keys:
                logger.log_ttl(
                    role_label="",
                    segment="blank",
                    run_clock=run_clock,
                    phase_clock=blank_clock,
                )
                event.clearEvents(eventType="keyboard")
            if KEY_QUIT in keys:
                finalize_and_quit(conv_session, recording_dir, logger, mixdown, win)
                return
        win.flip()
        core.wait(0.01)

    show_blank.setAutoDraw(False)

    comm_clock = core.Clock()
    if conv_session is not None:
        conv_session.enable_recording(True)

    logger.log_timing(
        role_label="Communication_start",
        run_clock=run_clock,
        phase_clock=comm_clock,
    )

    initial_role = TurnRole.SPEAKER if role == first_speaker else TurnRole.LISTENER
    role_text = (
        "YOUR TURN TO SPEAK" if initial_role.is_speaker else "YOUR TURN TO LISTEN"
    )
    pass_text = (
        "Press trackball button to pass the mic." if initial_role.is_speaker else ""
    )
    show_topic.setText(f"Problem topic: {conflict_text}")

    show_role_txt.setText(role_text)
    show_pass.setText(pass_text)
    show_role_txt.setAutoDraw(True)
    show_pass.setAutoDraw(True)
    show_timer.setAutoDraw(True)
    show_topic.setAutoDraw(True)

    turn_manager.start(initial_role)

    logger.experiment.addData("dyad", dyad)
    logger.experiment.addData("session", session)
    logger.experiment.addData("exp_condition", exp_condition)
    logger.experiment.addData("event", "communication_start")
    logger.experiment.addData(
        "role", "speaker" if (role == first_speaker) else "listener"
    )
    logger.experiment.addData("onset_run_s", run_clock.getTime())
    logger.experiment.addData("onset_phase_s", comm_clock.getTime())
    logger.experiment.addData("conflict_text", conflict_text)
    logger.experiment.addData("first_speaker", first_speaker)
    logger.experiment.addData("participant_role", role)
    logger.experiment.nextEntry()

    logger.log_timing(
        role_label="speaker" if (role == first_speaker) else "listener",
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
            key_from_kb, _rt_kb = keys[-1]

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
                finalize_and_quit(conv_session, recording_dir, logger, mixdown, win)
                return

            if key == KEY_PASS:
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

    logger.log_timing(
        role_label="communication_end",
        run_clock=run_clock,
        phase_clock=comm_clock,
    )

    for stim in (show_role_txt, show_timer, show_pass, show_topic):
        stim.setAutoDraw(False)

    show_end.draw()
    win.flip()
    core.wait(1.0)

    finalize_and_quit(conv_session, recording_dir, logger, mixdown, win)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Couple Conversation Task (trackball)")
    parser.add_argument(
        "--session",
        "-s",
        type=int,
        choices=[1, 2],
        required=True,
        help="Session number (1 or 2)",
    )
    parser.add_argument(
        "--conflict",
        "-t",
        type=str,
        required=True,
        help="Human-readable conflict topic to display/log",
    )
    parser.add_argument(
        "--csv",
        "-c",
        type=Path,
        default=Path(CSV_FILENAME),
        help="Path to participant_counterbalancing.csv",
    )
    parser.add_argument(
        "--mixdown",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Produce a mixed speaker/listener WAV file (use --no-mixdown to skip)",
    )
    parser.add_argument(
        "--fullscreen",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Run in fullscreen mode (use --no-fullscreen for windowed).",
    )
    parser.add_argument(
        "--scanner",
        choices=["skyra", "prisma"],
        default=None,
        help="Monitor profile to use for the scanner display (default: laptop)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        help="Console log level (DEBUG, INFO, WARNING, etc.)",
    )
    add_config_arguments(parser)
    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    session_cfg = load_config_from_args(args)
    main(
        session_cfg=session_cfg,
        scanner=args.scanner,
        fullscr=args.fullscreen,
        session=args.session,
        conflict=args.conflict,
        csv_path=args.csv,
        mixdown=args.mixdown,
        log_level=args.log_level,
    )
