from __future__ import annotations

import time
from pathlib import Path
from typing import TextIO

from psychopy import core, data, logging


class TaskLogger:
    """
    Manages PsychoPy ExperimentHandler logging plus the auxiliary timing/TTL CSVs.
    """

    def __init__(
        self,
        *,
        pid: str,
        session: int,
        session_type: str,
        exp_condition: str,
        first_speaker: str,
        conflict_text_slug: str,
        task_code: str,
        dyad: int,
        participant_role: str,
        conflict_text: str,
    ):
        self.session = session
        self.session_type = session_type
        self.exp_condition = exp_condition
        self.first_speaker = first_speaker
        self.participant_role = participant_role
        self.dyad = dyad
        self.conflict_text = conflict_text

        date_str = data.getDateStr()
        data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)
        base = f"{pid}_sess{session}_{session_type}_{exp_condition}_{first_speaker}_{conflict_text_slug}"
        self.filename = data_dir / f"{base}_{task_code}_min_{date_str}"
        self.task_code = task_code

        self.experiment = data.ExperimentHandler(
            name=f"{task_code}_min",
            extraInfo={"session_type": session_type},
            savePickle=True,
            saveWideText=True,
            dataFileName=str(self.filename),
        )
        logging.LogFile(str(self.filename.with_suffix(".log")), level=logging.EXP)

        timings_path = data_dir / f"{base}_{task_code}_TimingsLog_{date_str}.csv"
        self.timings_file: TextIO = timings_path.open("w", newline="", encoding="utf-8")
        self.timings_file.write(
            "dyad,session,session_type,exp_condition,role,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"
        )
        self.timings_file.flush()

        ttl_path = data_dir / f"{base}_{task_code}_TTLtimestamps_{date_str}.csv"
        self.ttl_file: TextIO = ttl_path.open("w", newline="", encoding="utf-8")
        self.ttl_file.write(
            "dyad,session,session_type,exp_condition,role,segment,time.time,run.time,comm.time,conflict_text,first_speaker,participant_role\n"
        )
        self.ttl_file.flush()

    def log_ttl(
        self,
        *,
        role_label: str,
        segment: str,
        run_clock: core.Clock,
        phase_clock: core.Clock | None,
    ):
        self.ttl_file.write(
            f"{self.dyad},{self.session},{self.session_type},{self.exp_condition},{role_label},{segment},"
            f"{time.time()},{run_clock.getTime()},{'' if phase_clock is None else phase_clock.getTime()},"
            f"{self.conflict_text},{self.first_speaker},{self.participant_role}\n"
        )
        self.ttl_file.flush()

    def log_event(
        self,
        *,
        event_name: str,
        role_label: str,
        run_clock: core.Clock,
        phase_clock: core.Clock | None,
    ):
        self.experiment.addData("dyad", self.dyad)
        self.experiment.addData("session", self.session)
        self.experiment.addData("exp_condition", self.exp_condition)
        self.experiment.addData("event", event_name)
        self.experiment.addData("role", role_label or "")
        self.experiment.addData("onset_run_s", run_clock.getTime())
        if phase_clock is not None:
            self.experiment.addData("onset_phase_s", phase_clock.getTime())
        self.experiment.addData("conflict_text", self.conflict_text)
        self.experiment.addData("first_speaker", self.first_speaker)
        self.experiment.addData("participant_role", self.participant_role)
        self.experiment.nextEntry()

    def log_timing(
        self,
        *,
        role_label: str,
        run_clock: core.Clock | None = None,
        phase_clock: core.Clock | None = None,
        wall_time: float | None = None,
        run_time: float | None = None,
        phase_time: float | None = None,
    ):
        if wall_time is None:
            wall_time = time.time()
        if run_time is None:
            if run_clock is None:
                msg = "log_timing requires run_clock or run_time"
                raise ValueError(msg)
            run_time = run_clock.getTime()
        if phase_time is not None:
            phase_value: float | str = phase_time
        elif phase_clock is not None:
            phase_value = phase_clock.getTime()
        else:
            phase_value = ""
        self.timings_file.write(
            f"{self.dyad},{self.session},{self.session_type},{self.exp_condition},{role_label},"
            f"{wall_time},{run_time},{phase_value},{self.conflict_text},{self.first_speaker},{self.participant_role}\n"
        )
        self.timings_file.flush()

    def save_and_close(self):
        filename = str(self.filename)
        self.experiment.saveAsWideText(filename + ".csv")
        self.experiment.saveAsPickle(filename)
        self.experiment.abort()
        logging.flush()
        self.close()

    def close(self):
        try:
            self.timings_file.close()
        except Exception:
            logging.warning("Failed to close timings file")

        try:
            self.ttl_file.close()
        except Exception:
            logging.warning("Failed to close TTL file")
