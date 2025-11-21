from __future__ import annotations

import argparse

from psychopy import core, event, logging

from examples.couple_tasks.log import TaskLogger
from examples.couple_tasks.utils import (
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
FULLSCR = False
LETTER_H = 0.07
WRAP_W = 2

INTRO_S = 2.0  # intro dwell before communication
COMM_S = 30.0  # communication phase duration (s)

KEY_PASS = "1"
KEY_QUIT = "escape"
TTL_KEY = "equal"  # for TTL pings during phases
TTL_ACCEPT = {"equal", "="}

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


def main(pid: str, session: int, csv_path: str):
    if session not in (1, 2):
        msg = "Session must be 1 or 2"
        raise ValueError(msg)

    dyad, role = decode_pid(pid)
    assignment = load_assignment_row(csv_path, pid)
    starters = assignment.starters()
    exp_condition = assignment.condition

    discussion_topic = assignment.first_topic if session == 1 else assignment.second_topic
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
    logging.console.setLevel(logging.WARNING)

    win = create_window(scanner=SCANNER, size=WIN_SIZE, fullscr=FULLSCR)
    make_text = text_factory(win, letter_height=LETTER_H, wrap_width=WRAP_W)

    show_instructions = make_text(text="")
    show_role_txt = make_text(text="", pos=(0, 0.65))
    show_pass = make_text(text="", pos=(0, 0.05))
    show_timer = make_text(text="", pos=(0, -0.70))
    show_blank = make_text(text="+", pos=(0, 0.00))
    show_topic = make_text(text="", pos=(0, 0.35))
    show_end = make_text(text="You are now done with this task.")

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

    current_role = "speaker" if (role == first_speaker) else "listener"
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
        current_role_label = (
            "speaker" if show_role_txt.text == "YOUR TURN TO SPEAK" else "listener"
        )

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

        keys = event.getKeys(keyList=[KEY_PASS, KEY_QUIT], timeStamped=comm_clock)
        if keys:
            key, _rt = keys[-1]
            if key == KEY_QUIT:
                logger.close()
                win.close()
                core.quit()
            elif key == KEY_PASS:
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

                logger.log_timing(
                    role_label=toggled_role,
                    run_clock=run_clock,
                    phase_clock=comm_clock,
                )

                logger.log_event(
                    event_name="pass_press",
                    role_label=toggled_role,
                    run_clock=run_clock,
                    phase_clock=comm_clock,
                )

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
    args = ap.parse_args()
    main(pid=args.pid, session=args.session, csv_path=args.csv)
