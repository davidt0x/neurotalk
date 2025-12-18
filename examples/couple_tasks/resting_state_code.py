"""
Resting-state (eyes-open) task
- Waits for TTL trigger ('=' or 'equal') using event.getKeys()
- Then shows a black fixation cross on white background for 11 minutes (660 s)
- ESC to quit
"""

from __future__ import annotations

import time

from psychopy import core, event, logging, visual

# ----------------- config -----------------
FULLSCR = True  # fullscreen recommended in scanner
BG_COLOR = "white"
FIX_COLOR = "black"
FIX_CHAR = "+"
FIX_HEIGHT_PX = 80  # fixation size (units='pix')
INSTR_HEIGHT = 32
INSTR_WRAP_PX = 1200

REST_DURATION_S = 11 * 60  # 11 minutes

KEY_QUIT = "escape"
TTL_KEY = "5"  # what you'll see in logs when pressing '='
TTL_ACCEPT = {"equal", "=", TTL_KEY}  # accept either label

# ----------------- window & logging -----------------
logging.LogFile("resting_state.log", level=logging.EXP)
logging.console.setLevel(logging.INFO)

# Important: don't pass size when FULLSCR=True to avoid the size/None issue
win = visual.Window(fullscr=FULLSCR, color=BG_COLOR, units="pix")
win.mouseVisible = False

# ----------------- stimuli -----------------
instr = visual.TextStim(
    win,
    text=(
        "For the next 10 minutes, please relax and let your mind wander freely.\n\n"
        "Keep your eyes open and look at the fixation cross.\n\n"
        "Stay awake and allow your thoughts to flow naturally."
    ),
    color="black",
    height=INSTR_HEIGHT,
    wrapWidth=INSTR_WRAP_PX,
    alignText="center",
)

fix = visual.TextStim(win, text=FIX_CHAR, color=FIX_COLOR, height=FIX_HEIGHT_PX)


# ----------------- wait for trigger (adapted to your working pattern) -----------------
def wait_for_ttl():
    """Block until '=' (or 'equal') is detected using event.getKeys()."""
    event.clearEvents(eventType="keyboard")
    trigger_source = None
    while trigger_source is None:
        instr.draw()
        win.flip()
        keys = event.getKeys()  # no keyList, exactly like your working task
        if keys:
            # quit first so ESC is immediate
            if KEY_QUIT in keys:
                raise KeyboardInterrupt
            # TTL acceptance exactly as in your code
            if any(k in TTL_ACCEPT for k in keys):
                trigger_source = "ttl"
                logging.exp(f"TTL received: {keys} (source={trigger_source})")
                return True
        core.wait(0.01)

    return False


# ----------------- run -----------------
try:
    logging.info("Displaying instructions and waiting for TTL trigger ('='/'equal').")
    wait_for_ttl()

    # Optional brief confirmation flash (comment out if you want zero visual change)
    # confirm = visual.TextStim(win, text="Trigger received — starting...", color='black', height=36)
    # confirm.draw(); win.flip(); core.wait(0.3)

    # Start fixation for full duration
    countdown = core.CountdownTimer(REST_DURATION_S)
    fix.autoDraw = True
    start_wall = time.time()
    logging.exp(
        f"Resting-state started (target {REST_DURATION_S}s) at wall {start_wall:.3f}"
    )

    while countdown.getTime() > 0:
        # keep ESC responsive
        if KEY_QUIT in event.getKeys([KEY_QUIT]):
            raise KeyboardInterrupt
        win.flip()

    fix.autoDraw = False
    win.flip()
    logging.exp(f"Resting-state complete (wall {time.time():.3f}).")

    # brief end screen
    done = visual.TextStim(win, text="Done. Thank you!", color="black", height=36)
    done.draw()
    win.flip()
    core.wait(1.0)

except KeyboardInterrupt:
    logging.warning("Session aborted by user (ESC).")

finally:
    win.close()
    core.quit()
