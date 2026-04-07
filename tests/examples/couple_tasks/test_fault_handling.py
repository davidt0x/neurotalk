from __future__ import annotations

import importlib
import logging as py_logging
import sys
import types
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(SRC) not in sys.path:
    sys.path.append(str(SRC))

config_module = cast(Any, importlib.import_module("neurotalk.config"))
session_module = cast(Any, importlib.import_module("neurotalk.session"))
RecordingConfig = config_module.RecordingConfig
SessionConfig = config_module.SessionConfig
SessionFault = session_module.SessionFault
SessionFaultError = session_module.SessionFaultError
SessionFaultSource = session_module.SessionFaultSource


def install_fake_psychopy(
    monkeypatch: pytest.MonkeyPatch, *, quit_calls: list[str]
) -> None:
    psychopy = cast(Any, types.ModuleType("psychopy"))
    core = cast(Any, types.ModuleType("psychopy.core"))
    data = cast(Any, types.ModuleType("psychopy.data"))
    event = cast(Any, types.ModuleType("psychopy.event"))
    logging_mod = cast(Any, types.ModuleType("psychopy.logging"))
    monitors = cast(Any, types.ModuleType("psychopy.monitors"))
    visual = cast(Any, types.ModuleType("psychopy.visual"))

    class DummyClock:
        def getTime(self) -> float:
            return 0.0

    class DummyConsole:
        def setLevel(self, _level: int) -> None:
            return None

    class DummyExperimentHandler:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def addData(self, *args, **kwargs) -> None:
            return None

        def nextEntry(self) -> None:
            return None

        def saveAsWideText(self, *args, **kwargs) -> None:
            return None

        def saveAsPickle(self, *args, **kwargs) -> None:
            return None

        def abort(self) -> None:
            return None

    def _noop(*args, **kwargs) -> None:
        return None

    core.Clock = DummyClock
    core.wait = _noop
    core.quit = lambda: quit_calls.append("quit")
    data.ExperimentHandler = DummyExperimentHandler
    data.getDateStr = lambda: "20260101"
    event.Mouse = lambda *args, **kwargs: object()
    event.getKeys = lambda *args, **kwargs: []
    event.clearEvents = _noop
    logging_mod.console = DummyConsole()
    logging_mod.EXP = 0
    logging_mod.LogFile = _noop
    logging_mod.flush = _noop
    logging_mod.info = _noop
    logging_mod.error = _noop
    logging_mod.warning = _noop
    monitors.Monitor = lambda *args, **kwargs: object()
    visual.Window = object
    visual.TextStim = object

    psychopy.core = core
    psychopy.data = data
    psychopy.event = event
    psychopy.logging = logging_mod
    psychopy.monitors = monitors
    psychopy.visual = visual

    for name, module in {
        "psychopy": psychopy,
        "psychopy.core": core,
        "psychopy.data": data,
        "psychopy.event": event,
        "psychopy.logging": logging_mod,
        "psychopy.monitors": monitors,
        "psychopy.visual": visual,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def import_couple_task_module(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    *,
    quit_calls: list[str],
):
    install_fake_psychopy(monkeypatch, quit_calls=quit_calls)
    for name in (
        module_name,
        "examples.couple_tasks.log",
        "examples.couple_tasks.utils",
    ):
        sys.modules.pop(name, None)
    return importlib.import_module(module_name)


def import_couple_task_utils(
    monkeypatch: pytest.MonkeyPatch, *, quit_calls: list[str]
):
    install_fake_psychopy(monkeypatch, quit_calls=quit_calls)
    sys.modules.pop("examples.couple_tasks.utils", None)
    return importlib.import_module("examples.couple_tasks.utils")


class DummyAssignment:
    condition = "persuade"
    first_topic = "air"
    second_topic = "tuition"

    def starters(self) -> dict[str, str]:
        return {
            "Neutral_session_1": "A",
            "Couple_session_1": "A",
            "Neutral_session_2": "B",
            "Couple_session_2": "B",
        }


class DummyLogger:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.saved = False
        self.filename = Path("data/test_task_log")

    def save_and_close(self) -> None:
        self.saved = True


def assert_conversation_task_fault_handling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    extra_kwargs: dict[str, object],
) -> None:
    module = import_couple_task_module(monkeypatch, module_name, quit_calls=[])
    errors: list[str] = []
    finalize_calls: list[
        tuple[object | None, Path, DummyLogger | None, bool, object | None]
    ] = []

    class FailingSession:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def connect(self) -> None:
            raise SessionFaultError(
                SessionFault(
                    source=SessionFaultSource.PEER_TIMEOUT,
                    message="peer timed out",
                    timestamp=0.0,
                )
            )

    def fake_finalize(conv_session, recording_dir, logger, mixdown, win) -> None:
        finalize_calls.append((conv_session, recording_dir, logger, mixdown, win))

    monkeypatch.setattr(
        module,
        "configure_runtime_logging",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(module, "ConversationSession", FailingSession)
    monkeypatch.setattr(module, "TaskLogger", DummyLogger)
    monkeypatch.setattr(module, "load_assignment_row", lambda *_args: DummyAssignment())
    monkeypatch.setattr(module, "pick_first_speaker", lambda *_args, **_kwargs: "A")
    monkeypatch.setattr(module, "finalize_and_quit", fake_finalize)
    monkeypatch.setattr(
        module.py_logging,
        "error",
        lambda message, *args: errors.append(message % args if args else str(message)),
    )

    cfg = SessionConfig(
        participant_id="011",
        recording=RecordingConfig(directory=tmp_path),
    )

    module.main(
        session_cfg=cfg,
        fullscr=False,
        display=0,
        mixdown=False,
        log_level="INFO",
        **extra_kwargs,
    )

    assert len(errors) == 1
    assert "NeuroTalk session fault detected" in errors[0]
    assert len(finalize_calls) == 1
    _, recording_dir, logger, mixdown, win = finalize_calls[0]
    assert recording_dir == tmp_path
    assert isinstance(logger, DummyLogger)
    assert mixdown is False
    assert win is None


def test_neutral_conversation_logs_and_cleans_up_on_session_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert_conversation_task_fault_handling(
        tmp_path,
        monkeypatch,
        "examples.couple_tasks.neutral_conversation",
        {"session": 1, "csv_path": Path("assign.csv")},
    )


def test_couple_conversation_logs_and_cleans_up_on_session_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert_conversation_task_fault_handling(
        tmp_path,
        monkeypatch,
        "examples.couple_tasks.couple_conversation",
        {"session": 1, "conflict": "tuition", "csv_path": Path("assign.csv")},
    )


def test_soundcheck_logs_and_exits_on_session_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quit_calls: list[str] = []
    module = import_couple_task_module(
        monkeypatch,
        "examples.couple_tasks.soundcheck",
        quit_calls=quit_calls,
    )
    errors: list[str] = []
    events: list[str] = []

    class DummyWindow:
        pass

    class DummySession:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def connect(self) -> None:
            return None

        def close(self) -> None:
            events.append("close")

    def raise_fault(*args, **kwargs):
        raise SessionFaultError(
            SessionFault(
                source=SessionFaultSource.AUDIO_RECEIVE,
                message="audio receive loop failed",
                timestamp=0.0,
            )
        )

    monkeypatch.setattr(
        module,
        "configure_runtime_logging",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(module, "ConversationSession", DummySession)
    monkeypatch.setattr(module, "create_window", lambda **_kwargs: DummyWindow())
    monkeypatch.setattr(module, "run_conversation_soundcheck", raise_fault)
    monkeypatch.setattr(
        module,
        "close_window_and_restore_display",
        lambda _win: events.append("close_window"),
    )
    monkeypatch.setattr(
        module.logging,
        "error",
        lambda message, *args: errors.append(message % args if args else str(message)),
    )

    cfg = SessionConfig(
        participant_id="011",
        recording=RecordingConfig(directory=tmp_path),
    )

    module.main(
        session_cfg=cfg,
        config_path=tmp_path / "neurotalk.yaml",
        ui="psychopy",
        scanner=None,
        fullscr=False,
        display=0,
        log_level="INFO",
    )

    assert len(errors) == 1
    assert "NeuroTalk session fault detected" in errors[0]
    assert events == ["close", "close_window"]
    assert quit_calls == ["quit"]


def test_configure_runtime_logging_writes_debug_logs_to_runtime_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    utils = import_couple_task_utils(monkeypatch, quit_calls=[])
    runtime_log_path = utils.build_runtime_log_path(
        directory=tmp_path,
        stem="runtime_test",
    )
    root = py_logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level

    try:
        for handler in old_handlers:
            root.removeHandler(handler)
        utils.configure_runtime_logging("INFO", log_path=runtime_log_path)
        logger = py_logging.getLogger("tests.runtime")
        logger.debug("debug line")
        logger.info("info line")
        for handler in root.handlers:
            handler.flush()
        contents = runtime_log_path.read_text(encoding="utf-8")
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            if isinstance(handler, py_logging.FileHandler):
                handler.close()
        for handler in old_handlers:
            root.addHandler(handler)
        root.setLevel(old_level)
        utils._RUNTIME_LOGGING_STATE.file_handler = None
        utils._RUNTIME_LOGGING_STATE.file_path = None

    assert "debug line" in contents
    assert "info line" in contents
