"""
pip install psychopy sounddevice pygame
Run with: `python read_task {user_id}`
"""

import argparse
import wave

import numpy as np
import pandas as pd
import sounddevice as sd
from psychopy import core, event, logging, sound, visual

# Prefer the pygame backend to avoid PTB forcing the 8-channel card if we play any sounds.
if "pygame" in sound.Sound.getBackends():
    sound.Sound.backend = "pygame"
else:
    raise RuntimeError(
        "pygame backend is unavailable. Install pygame in the PsychoPy env to avoid PTB errors."
    )


FIXATION_DURATION = 10  # seconds
SAMPLE_RATE = 16000
CHANNELS = 1  # mono
DTYPE = "int16"
POLL_INTERVAL = 0.1


class EscapeHandler:
    """Stateful helper so we don't rely on globals."""

    def __init__(self) -> None:
        self.exit_requested = False

    def request_exit(self) -> None:
        if not self.exit_requested:
            logging.info(
                "Escape pressed. Finishing current reading block before closing."
            )
        self.exit_requested = True

    def check(self) -> None:
        if "escape" in event.getKeys(["escape"]):
            self.request_exit()

    def wait(self, seconds: float) -> None:
        if self.exit_requested:
            return
        timer = core.CountdownTimer(seconds)
        while timer.getTime() > 0 and not self.exit_requested:
            self.check()
            core.wait(min(POLL_INTERVAL, max(0.0, timer.getTime())))


def main(sub_id: str, take: int | None = None) -> None:
    file_name = f"sub-{sub_id}_task-reading_audio.wav"
    audio_data = np.zeros((0, CHANNELS), dtype=DTYPE)
    handler = EscapeHandler()

    df = pd.read_csv("transcript.csv")
    if take:
        df = df.iloc[:take]
        logging.warn(f"Taking first {take} sentences only.")

    win = visual.Window(color="black", monitor="testMonitor")
    win.mouseVisible = False
    message = visual.TextStim(win, "", autoDraw=True, name="message")

    run_clock = core.Clock()
    logging.setDefaultClock(run_clock)
    logging.console.setLevel(logging.WARN)
    logging.LogFile(
        f"sub-{sub_id}_task-reading_psychopy.log", level=logging.INFO, filemode="w"
    )

    logging.info("Starting")
    instructions = """
In the next task, you will read a 13-minute story aloud. The story was told live on stage to an audience.
\nYou will see one sentence on screen at a time. Read each sentence aloud as it appears. The sentences will advance automatically, and there will be pauses after some sentences. 
\nRead the sentences at a pace that feels comfortable for you. It is okay if you don't finish reading one sentence before the next begins. If you make a mistake, you do not need to repeat or correct yourself. 
\nWe included notes in [square brackets] from the live stage reading of this story. You do not need to read the text in square brackets. 
\nRemember to try to minimize head movements throughout this task. 
\nA fixation cross will appear when the scanner starts. The story will begin after the fixation cross.
"""

    inst_stim = visual.TextStim(win, instructions, name="instructions", alignText="left")
    inst_stim.height = 0.06
    inst_stim.wrapWidth = 1.6
    inst_stim.draw()
    win.flip()
    logging.info("Finished instructions")

    triggered = False
    while not triggered and not handler.exit_requested:
        if keys := event.getKeys(["equal", "escape"]):
            logging.data(f"Keys pressed {str(keys)}")
            if "escape" in keys:
                handler.request_exit()
            if "equal" in keys:
                triggered = True

    logging.info("Got first scanner trigger! Resetting clocks")
    run_clock.reset()

    if not handler.exit_requested:
        message.text = "+"
        win.flip()
        handler.wait(FIXATION_DURATION)
        logging.info("Fixation done")

        frames = int(df["duration"].sum() * SAMPLE_RATE)
        logging.info(f"Recording {frames // SAMPLE_RATE} frames of audio.")
        audio_data = sd.rec(
            frames, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE
        )

        for i, row in df.iterrows():
            if handler.exit_requested:
                logging.info("Exit requested; stopping sentence presentation.")
                break
            handler.check()
            logging.info(f"Sentence {i}")
            duration = row["duration"]
            message.text = row["text"].strip()

            win.flip()
            handler.wait(duration)
    else:
        logging.info("Exit requested before task start; skipping fixation and sentences.")

    with wave.open(file_name, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(np.dtype(DTYPE).itemsize)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_data.tobytes())

    win.close()
    core.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("sub_id")
    parser.add_argument("-t", "--take", type=int, default=None)
    args = parser.parse_args()
    main(args.sub_id, args.take)
