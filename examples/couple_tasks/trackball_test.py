from psychopy import visual, event, core

win = visual.Window(size=(800, 600), fullscr=False)
mouse = event.Mouse(win=win, visible=False)

txt = visual.TextStim(win, text="Click the trackball button to log a PASS.\nPress ESC to quit.")
log = visual.TextStim(win, text="", pos=(0, -0.3))

n = 0
last_pressed = False

clock = core.Clock()

while True:
    txt.draw()
    log.draw()
    win.flip()

    keys = event.getKeys()
    if 'escape' in keys:
        break

    buttons = mouse.getPressed()  # [left, middle, right]
    pressed_now = bool(buttons[0])  # left button

    if pressed_now and not last_pressed:
        n += 1
        log.text = f"Pass count: {n} (t={clock.getTime():.2f}s)"
        print("PASS at", clock.getTime())

    last_pressed = pressed_now
    core.wait(0.01)

win.close()
core.quit()
