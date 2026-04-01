from __future__ import annotations

import pytest

pytest.importorskip("psychopy", reason="couple conversation task requires PsychoPy")

import importlib
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=ResourceWarning, message=".*messages.mo.*")
sys.path.append(str(Path(__file__).resolve().parents[3]))

utils = importlib.import_module("examples.couple_tasks.utils")


def test_decode_pid_assigns_dyad_and_role():
    dyad, role = utils.decode_pid("122")
    assert dyad == 12
    assert role == "B"
    with pytest.raises(ValueError, match="3-digit"):
        utils.decode_pid("12A")


def test_slug_normalizes_text():
    assert utils.slug("Hello World!") == "hello_world"
    assert utils.slug("  spaces   only  ") == "spaces_only"
    assert utils.slug("") == "topic"


def test_pick_first_speaker_validates_values():
    starters = {"Couple_session_1": "A", "Couple_session_2": "B"}
    assert utils.pick_first_speaker(starters, session=1, session_type="couple") == "A"
    starters["Couple_session_1"] = "X"
    with pytest.raises(ValueError, match="must be 'A' or 'B'"):
        utils.pick_first_speaker(starters, session=1, session_type="couple")


def test_load_assignment_row_reads_csv(tmp_path):
    csv_path = tmp_path / "assign.csv"
    csv_path.write_text(
        "participant_id,condition,Neutral_session_1,Couple_session_1,Neutral_session_2,Couple_session_2\n"
        "123,collab,A,B,B,A\n",
        encoding="utf-8",
    )
    row = utils.load_assignment_row(str(csv_path), "123")
    assert row.participant_id == "123"
    assert row.condition == "collab"
    assert row.couple_session_1 == "B"
    assert row.first_topic is None
    with pytest.raises(KeyError):
        utils.load_assignment_row(str(csv_path), "999")


def test_close_window_and_restore_display_waits_after_extend(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []

    class DummyWindow:
        def close(self) -> None:
            events.append("close")

    def fake_switch(mode: str) -> bool:
        events.append(f"switch:{mode}")
        return True

    def fake_wait(seconds: float) -> None:
        events.append(f"wait:{seconds}")

    monkeypatch.setattr(utils, "switch_display_mode", fake_switch)
    monkeypatch.setattr(utils.core, "wait", fake_wait)

    utils.close_window_and_restore_display(DummyWindow(), settle_seconds=1.5)

    assert events == ["close", "switch:extend", "wait:1.5"]


def test_finalize_and_quit_allows_missing_logger_and_window(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    events: list[str] = []

    class DummySession:
        def close(self) -> None:
            events.append("close")

        def export_segments(self, _path) -> None:
            events.append("export_segments")

    monkeypatch.setattr(utils.core, "quit", lambda: events.append("quit"))

    utils.finalize_and_quit(
        DummySession(),
        tmp_path,
        logger=None,
        mixdown=False,
        win=None,
    )

    assert events == ["close", "export_segments", "quit"]
