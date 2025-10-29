from __future__ import annotations

import time
import wave

from neurotalk.audio import AudioPacket
from neurotalk.records import RecorderTarget, WavRecorder


def make_packet(data: bytes) -> AudioPacket:
    return AudioPacket(pcm=data, counter=0, timestamp=time.time())


def frames_for(data: bytes, *, channels: int, sample_width: int) -> int:
    return len(data) // (channels * sample_width)


def test_wav_recorder_writes_file(tmp_path):
    target = RecorderTarget(path=tmp_path / "test.wav", channels=1, sample_rate_hz=16000)
    recorder = WavRecorder(target)
    packet = make_packet(b"\x01\x02" * 10)
    recorder.write(packet)
    recorder.close()

    with wave.open(str(target.path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == target.sample_width_bytes
        assert wav.getframerate() == target.sample_rate_hz
        assert wav.readframes(10) == packet.pcm


def test_wav_recorder_segments_and_times(tmp_path):
    target = RecorderTarget(path=tmp_path / "test.wav", channels=1, sample_rate_hz=16000)
    recorder = WavRecorder(target)

    before = b"\x00\x00" * 8
    seg_data = b"\x01\x01" * 16

    recorder.write(make_packet(before))
    recorder.start_segment("turn1")
    recorder.write(make_packet(seg_data))
    recorder.stop_segment()
    recorder.close()

    segment = recorder.segments[0]
    frames_before = frames_for(before, channels=target.channels, sample_width=target.sample_width_bytes)
    frames_segment = frames_for(seg_data, channels=target.channels, sample_width=target.sample_width_bytes)

    assert segment.label == "turn1"
    assert segment.start_frame == frames_before
    assert segment.end_frame == frames_before + frames_segment
    assert segment.start_time == frames_before / target.sample_rate_hz
    assert segment.end_time == (frames_before + frames_segment) / target.sample_rate_hz


def test_wav_recorder_split_segments(tmp_path):
    target = RecorderTarget(path=tmp_path / "full.wav", channels=1, sample_rate_hz=16000)
    recorder = WavRecorder(target)

    recorder.start_segment("seg0")
    recorder.write(make_packet(b"\x11\x11" * 8))
    recorder.stop_segment()
    recorder.start_segment("seg1")
    recorder.write(make_packet(b"\x22\x22" * 16))
    recorder.stop_segment()
    recorder.close()

    out_dir = tmp_path / "segments"
    outputs = recorder.split_segments(out_dir)
    assert len(outputs) == 2

    with wave.open(str(outputs[0]), "rb") as wav:
        assert wav.getnframes() == 8
    with wave.open(str(outputs[1]), "rb") as wav:
        assert wav.getnframes() == 16
