from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("psychopy", reason="couple conversation task requires PsychoPy")

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "couple_conversation_only_task.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "examples.couple_conversation_only_task", MODULE_PATH
)
assert _SPEC is not None
assert _SPEC.loader is not None
task = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(task)


def test_decode_pid_assigns_dyad_and_role():
    dyad, role = task.decode_pid("122")
    assert dyad == 12
    assert role == "B"
    with pytest.raises(ValueError, match="3-digit"):
        task.decode_pid("12A")


def test_slug_normalizes_text():
    assert task.slug("Hello World!") == "hello_world"
    assert task.slug("  spaces   only  ") == "spaces_only"
    assert task.slug("") == "topic"


def test_pick_first_speaker_validates_values():
    starters = {"Couple_session_1": "A", "Couple_session_2": "B"}
    assert task.pick_first_speaker(starters, 1) == "A"
    starters["Couple_session_1"] = "X"
    with pytest.raises(ValueError, match="must be 'A' or 'B'"):
        task.pick_first_speaker(starters, 1)


def test_load_assignment_row_reads_csv(tmp_path):
    csv_path = tmp_path / "assign.csv"
    csv_path.write_text(
        "participant_id,condition,Neutral_session_1,Couple_session_1,Neutral_session_2,Couple_session_2\n"
        "123,collab,A,B,B,A\n",
        encoding="utf-8",
    )
    row = task.load_assignment_row(str(csv_path), "123")
    assert row["participant_id"] == "123"
    assert row["condition"] == "collab"
    assert row["Couple_session_1"] == "B"
    with pytest.raises(KeyError):
        task.load_assignment_row(str(csv_path), "999")
