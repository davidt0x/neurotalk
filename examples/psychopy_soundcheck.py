#!/usr/bin/env python

# Run this from the command line from storyteller/scripts/ directory using, e.g.:
#   ./soundcheck_presentation.py stimuli/bronx_clean.wav .5

import sys
import time
from os.path import exists, join
from psychopy import prefs
from psychtoolbox import audio
from psychopy import core, event, logging, sound, visual

# Command line arguments for audio file and initial volume
audio_fn = sys.argv[1]
volume = float(sys.argv[2])
# Allow louder-than-1.0 gains; map common 0-10 inputs into a 0-2 range.
MAX_VOLUME = 1.0
volume = min(volume, MAX_VOLUME) 
volume = max(0.0, min(MAX_VOLUME, volume))

# Set up function to check keyboard inputs
def proceed_keys(keys, wait):
    if '1' in keys:
        wait = False
    return wait

def quit_keys(keys, stimulus=None, message=None):
    if 'q' in keys or 'escape' in keys:
        wait = False
        if stimulus:
            stimulus.stop()
        logging.flush()
        win.close()
        if message:
            print(message)
        core.quit()

def scanner_keys(keys, wait=False):
    if 'equal' in keys:
        logging.info('Trigger received')
        wait = False
    if '1' in keys:
        logging.data('Response 1')
    if '2' in keys:
        logging.data('Response 2')
    if '3' in keys:
        logging.data('Response 3')
    if '4' in keys:
        logging.data('Response 4')
    return wait

# Also show PTB/PortAudio devices with channel counts (these indices are used by
# the psychtoolbox backend).
ptb_devices = audio.get_devices()
print("\nPTB/PortAudio devices:")
for dev in ptb_devices:
    print(
        f"Device {dev['DeviceIndex']}: name='{dev['DeviceName']}', "
        f"host='{dev['HostAudioAPIName']}', outputs={dev['NrOutputChannels']}, "
        f"inputs={dev['NrInputChannels']}"
    )

# Pick the first two-channel output device matching the preferred name/host (or
# the first two-channel device as a fallback) so we avoid multi-channel
# defaults that trigger FillBuffer errors.
preferred_device_name = "Headphones (Realtek(R) Audio)"
preferred_host = "MME"
target_device = None
for dev in ptb_devices:
    if (
        dev["NrOutputChannels"] == 2
        and preferred_device_name in dev["DeviceName"]
        and dev["HostAudioAPIName"] == preferred_host
    ):
        target_device = dev
        break
if target_device is None:
    for dev in ptb_devices:
        if dev["NrOutputChannels"] == 2 and dev["HostAudioAPIName"] == preferred_host:
            target_device = dev
            break
if target_device is None:
    for dev in ptb_devices:
        if dev["NrOutputChannels"] == 2:
            target_device = dev
            break

if target_device:
    prefs.hardware["audioDevice"] = target_device["DeviceName"]
    prefs.hardware["audioLib"] = ["PTB"]
    prefs.hardware["audioWASAPIOnly"] = False  # allow MME/WDM devices
    print(
        f"\nUsing PTB device {target_device['DeviceIndex']}: "
        f"{target_device['DeviceName']} (outputs={target_device['NrOutputChannels']}, "
        f"host={target_device['HostAudioAPIName']})"
    )
else:
    print("\nWARNING: No 2-channel output device found; default device will be used.")

# Reduce verbosity of PsychoPy logging
logging.console.setLevel(logging.DATA)

# Load audio story stimulus at mid-volume
speaker_index = target_device["DeviceIndex"] if target_device else None
stimulus = sound.Sound(
    audio_fn,
    stereo=False,
    volume=volume,
    name=audio_fn,
    speaker=speaker_index,
)

# Open window and provide instructions
win = visual.Window([1280, 720], screen=0, fullscr=False, color=0, name='Window')

instructions = visual.TextStim(win, pos=[-.625, .575], wrapWidth=1.3,
                               anchorHoriz='left', anchorVert='top', name='Instructions',
                               text=("Use the buttons to adjust the volume until "
                                     "you can hear and understand clearly. "
                                     "Button 1 is closest to the cord and button "
                                     "4 is farthest from the cord."))

buttons = visual.TextStim(win, pos=[0, .075], wrapWidth=1.5,
                          anchorHoriz='center', anchorVert='top', name='Button list',
                          text=("button 1: volume +\n"
                                "button 2: volume -\n"
                                "button 4: finished"))

ready = visual.TextStim(win, pos=[0, -.4], wrapWidth=1,
                        anchorHoriz='center', anchorVert='top',
                        text="Ready?")
ready_button = visual.TextStim(win, pos=[0, -.55], anchorHoriz='center',
                               text="(press button 1 to continue)")

instructions.draw()
buttons.draw()
ready.draw()
ready_button.draw()
win.flip()

subject_wait = True
while subject_wait:
    keys = event.getKeys()
    subject_wait = proceed_keys(keys, wait=subject_wait)
    quit_keys(keys)
    
# Wait for scanner trigger (or keyboard)
waiting = visual.TextStim(win, pos=[0, -.5], text="Waiting for scanner...",
                          name="Waiting")
instructions.draw()
buttons.draw()
waiting.draw()
win.flip()

scanner_wait = True
while scanner_wait:
    keys = event.getKeys()
    scanner_wait = scanner_keys(keys, wait=scanner_wait)
    quit_keys(keys)

# Keyboard-controlled volume adjuster (replaces deprecated RatingScale)
volume_text = visual.TextStim(
    win,
    pos=[0, -0.35],
    wrapWidth=1,
    anchorHoriz='center',
    anchorVert='center',
    text=f"Volume: {volume:.2f} (max {MAX_VOLUME:.1f})",
)

stimulus.play()
adjusting = True
while adjusting:
    instructions.draw()
    buttons.draw()
    volume_text.text = f"Volume: {volume:.2f} (max {MAX_VOLUME:.1f})"
    volume_text.draw()
    win.flip()
    keys = event.getKeys()
    quit_keys(keys, stimulus=stimulus)
    if '1' in keys:
        volume = min(MAX_VOLUME, volume + 0.05)
        stimulus.volume = volume
    if '2' in keys:
        volume = max(0.0, volume - 0.05)
        stimulus.volume = volume
    if '4' in keys:
        adjusting = False
stimulus.stop()

soundcheck_finished = visual.TextStim(win, pos=[0, 0], wrapWidth=1,
                                      anchorHoriz='center', anchorVert='top',
                                      text="Sound check finished!")
soundcheck_finished.draw()
win.flip()

final_volume = """\n
================================
Subject's volume selection: {0:.2f}
================================\n
""".format(volume)

quit_wait = True
while quit_wait:
    keys = event.getKeys()
    quit_keys(keys, message=final_volume)
