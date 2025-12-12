#!/usr/bin/env python

import argparse
import sys
from pathlib import Path
from importlib import resources
from psychopy import prefs
from psychtoolbox import audio
from psychopy import core, event, logging, sound, visual


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PsychoPy/PTB soundcheck utility.")
    parser.add_argument(
        "--audio",
        dest="audio_fn",
        help="Optional path to audio file to play. If omitted, a bundled soundcheck tone is used.",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=0.3,
        help="Starting volume (0-1 or higher for a boost; values >2 are clamped). Default: 0.3",
    )
    parser.add_argument(
        "--device",
        help="Preferred device name (substring match, PTB list). Default: auto-select output device.",
    )
    parser.add_argument(
        "--host",
        help="Preferred host API name (substring match, e.g., 'MME', 'Windows WASAPI'). Default: any.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List PTB/PortAudio devices and exit.",
    )
    return parser.parse_args()


args = parse_args()
audio_fn = args.audio_fn
volume = args.volume
# Allow louder-than-1.0 gains; cap at 2.0 to limit clipping.
MAX_VOLUME = 2.0
volume = max(0.0, min(MAX_VOLUME, volume))
LOOP_REPETITIONS = 1_000_000  # effectively "infinite" for our purposes

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

def choose_device(ptb_devices, name_hint, host_hint):
    """Select a 2-channel output device matching hints, fall back to first output-capable."""

    def match(dev):
        return (
            dev["NrOutputChannels"] == 2
            and (name_hint is None or name_hint.lower() in dev["DeviceName"].lower())
            and (host_hint is None or host_hint.lower() in dev["HostAudioAPIName"].lower())
        )

    for dev in ptb_devices:
        if match(dev):
            return dev
    for dev in ptb_devices:
        if dev["NrOutputChannels"] == 2:
            if host_hint is None or host_hint.lower() in dev["HostAudioAPIName"].lower():
                return dev
    for dev in ptb_devices:
        if dev["NrOutputChannels"] > 0:
            return dev
    return None


# Get the list of devices from PTB
ptb_devices = audio.get_devices()

if args.list_devices:

    print("\nPTB/PortAudio devices:")
    for dev in ptb_devices:
        print(
            f"Device {dev['DeviceIndex']}: name='{dev['DeviceName']}', "
            f"host='{dev['HostAudioAPIName']}', outputs={dev['NrOutputChannels']}, "
            f"inputs={dev['NrInputChannels']}"
        )
    sys.exit(0)

target_device = choose_device(ptb_devices, args.device, args.host)

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
    print("\nWARNING: No output device found; default device will be used.")

# Reduce verbosity of PsychoPy logging
logging.console.setLevel(logging.DATA)


def _default_audio_path() -> str:
    """Locate the bundled soundcheck WAV inside the neurotalk package."""
    try:
        return str(resources.files("neurotalk.data") / "soundcheck.ogg")
    except Exception:
        root = Path(__file__).resolve().parents[1]
        local = root / "src" / "neurotalk" / "data" / "soundcheck.ogg"
        return str(local)


# Load audio story stimulus at mid-volume
speaker_index = target_device["DeviceIndex"] if target_device else None
audio_source = audio_fn or _default_audio_path()
stimulus = sound.Sound(
    audio_source,
    stereo=True,  # bundled WAV is stereo; allow 2-ch devices
    volume=volume,
    name=str(audio_source),
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

stimulus.play(loops=LOOP_REPETITIONS)
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
