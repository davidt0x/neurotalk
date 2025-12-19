from __future__ import annotations

import time

from neurotalk.audio import MockStreamFactory
from neurotalk.config import SessionConfig
from neurotalk.session import ConversationSession


class _DummySocket:
    def __init__(self) -> None:
        self.closed = False

    def recv(self, _bufsize: int) -> bytes:
        time.sleep(0.01)
        raise TimeoutError

    def sendto(self, data: bytes, addr: object) -> None:
        # no-op sink for outbound packets
        self.last_sent = (data, addr)

    def close(self) -> None:
        self.closed = True


class _DummyBundle:
    def __init__(self) -> None:
        self.inbound = _DummySocket()
        self.outbound = _DummySocket()
        self.control = _DummySocket()
        self.remote = ("127.0.0.1", 0, 0, 0)

    def close(self) -> None:
        self.inbound.close()
        self.outbound.close()
        self.control.close()


def test_recording_label_used_in_output_names(tmp_path):
    cfg = SessionConfig(participant_id="123", role="A")
    cfg.recording.directory = tmp_path

    session = ConversationSession(cfg, recording_label="My Task Label")
    local_recorder, remote_recorder = session._create_recorders()
    assert local_recorder is not None
    assert remote_recorder is not None
    try:
        assert "123_A_My_Task_Label_" in local_recorder.path.name
        assert local_recorder.path.name.endswith("_local.wav")
        assert "123_A_My_Task_Label_" in remote_recorder.path.name
        assert remote_recorder.path.name.endswith("_remote.wav")
    finally:
        local_recorder.close()
        remote_recorder.close()


def test_recording_toggle_propagates_to_workers(tmp_path):
    cfg = SessionConfig(participant_id="999", role="B")
    cfg.recording.directory = tmp_path

    session = ConversationSession(
        cfg, recording_enabled=False, stream_factory=MockStreamFactory()
    )
    session.state.sockets = _DummyBundle()  # type: ignore[assignment]
    session._initialize_audio()
    try:
        input_worker = session.state.input_worker
        output_worker = session.state.output_worker
        assert input_worker is not None
        assert output_worker is not None
        assert not input_worker._recording_enabled
        assert not output_worker._recording_enabled

        session.enable_recording(True)
        refreshed_output = session.state.output_worker
        refreshed_input = session.state.input_worker
        assert refreshed_input is not None
        assert refreshed_output is not None
        assert refreshed_input._recording_enabled
        assert refreshed_output._recording_enabled
    finally:
        session._shutdown_audio()
