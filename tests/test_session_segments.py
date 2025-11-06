from __future__ import annotations

import time
from pathlib import Path

import pytest

from neurotalk.audio import AudioPacket
from neurotalk.config import SessionConfig
from neurotalk.records import RecorderTarget, WavRecorder
from neurotalk.session import ConversationSession


def test_session_segment_export(tmp_path):
    session = ConversationSession(SessionConfig(participant_id="001", role="A"))

    local_target = RecorderTarget(
        path=tmp_path / "local.wav", channels=1, sample_rate_hz=16000
    )
    remote_target = RecorderTarget(
        path=tmp_path / "remote.wav", channels=1, sample_rate_hz=16000
    )

    local_recorder = WavRecorder(local_target)
    remote_recorder = WavRecorder(remote_target)

    session.state.local_recorder = local_recorder
    session.state.remote_recorder = remote_recorder

    session.start_segment("segment1")
    packet = AudioPacket(pcm=b"\x00\x01" * 32, counter=1, timestamp=time.time())
    session.state.local_recorder.write(packet)
    session.state.remote_recorder.write(packet)
    session.stop_segment()

    session.state.local_recorder.close()
    session.state.remote_recorder.close()

    output_dir = tmp_path / "segments"
    results = session.export_segments(output_dir)

    assert results.get("local")
    assert all(Path(path).exists() for path in results["local"])


def test_start_segment_twice_raises(tmp_path):
    session = ConversationSession(SessionConfig(participant_id="001", role="A"))

    target = RecorderTarget(
        path=tmp_path / "local.wav", channels=1, sample_rate_hz=16000
    )
    recorder = WavRecorder(target)
    session.state.local_recorder = recorder

    session.start_segment("segment1")
    with pytest.raises(RuntimeError):
        session.start_segment("segment2")
