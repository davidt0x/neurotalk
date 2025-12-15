"""
This script plays the audio file stimulus 'I Knew You Were Black'.

Author: LT (November 1, 2019)
Edited: ZZ October 27, 2025
"""

from __future__ import annotations

import argparse

from psychopy import core, event, logging, sound, visual

# Prefer the pygame backend
if "pygame" in sound.Sound.getBackends():
    sound.Sound.backend = "pygame"
else:
    msg = "pygame backend is unavailable. Install pygame in the PsychoPy env to avoid PTB errors."
    raise RuntimeError(msg)

# # if modified, these should be changed _before_ other imports
# from psychopy import prefs
# prefs.general['audioLib'] = ['sounddevice']
# prefs.general['audioDriver'] = ['coreaudio']
# prefs.general['audioDevice'] = ['Built-in Line Output']

INSTRUCTIONS_TEXT = """
In the next task, you will listen to a 13-minute story live on stage.
\nAfter the scan session, you will be asked questions about the story.
\nRemember to try to minimize head movements throughout this task.
\nA fixation cross will appear when the scanner starts, then the story will begin.
"""

POLL_INTERVAL = 0.1


def _shutdown(win: visual.Window) -> None:
    """Close PsychoPy cleanly after an escape key press."""
    logging.info("Escape pressed. Exiting listening task.")
    win.close()
    core.quit()
    raise SystemExit(0)


def _check_escape(win: visual.Window) -> None:
    if "escape" in event.getKeys(["escape"]):
        _shutdown(win)


def _wait_with_escape(seconds: float, win: visual.Window) -> None:
    """Like core.wait but polls for escape presses."""
    timer = core.CountdownTimer(seconds)
    while timer.getTime() > 0:
        _check_escape(win)
        core.wait(min(POLL_INTERVAL, max(0.0, timer.getTime())))


def main(sub_id: str, windowed: bool, fixation_duration: int = 10):
    filename = f"sub-{sub_id}_task-listening_psycophy.log"
    logging.LogFile(filename, level=logging.INFO, filemode="w")

    run_clock = core.Clock()
    logging.setDefaultClock(run_clock)

    win = visual.Window(color="black", fullscr=not windowed)
    win.mouseVisible = False

    # config
    letter_h = 0.06
    wrap_w = 1.6

    # set up PsychoPy components
    aud_clip = sound.Sound("black_audio.ogg")
    logging.info(f"Audio duration: {aud_clip.getDuration()} seconds")
    logging.flush()

    instr = visual.TextStim(
        win,
        name="intro",
        text=INSTRUCTIONS_TEXT,
        color="white",
        height=letter_h,
        wrapWidth=wrap_w,
    )
    cross = visual.TextStim(win, name="cross", text="+")

    # -- display instructions screen --
    instr.draw()
    win.flip()

    # -- wait for trigger --
    triggered = False
    while not triggered:
        if keys := event.getKeys(["equal", "escape", "5"]):
            logging.data(f"Keys pressed {keys!s}")
            if "escape" in keys:
                _shutdown(win)
            if "equal" in keys or "5" in keys:
                triggered = True

    logging.info("Got first scanner trigger! Resetting clocks")
    run_clock.reset()

    # -- display ready screen --
    cross.draw()
    win.flip()

    # -- wait for scanner --
    _wait_with_escape(fixation_duration, win)

    # -- present audio --
    cross.draw()
    win.flip()
    aud_clip.play()
    _wait_with_escape(aud_clip.getDuration(), win)

    win.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("sub_id", help="Specify participant #")
    parser.add_argument("-w", "--windowed", action="store_true", default=False)
    args = parser.parse_args()

    main(args.sub_id, args.windowed)
    core.quit()
