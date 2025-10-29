from __future__ import annotations

import time

import neurotalk.audio as audio_module
from neurotalk.audio import AudioConfig, AudioInputWorker, AudioOutputWorker, AudioPacket


class RecordingStub:
    def __init__(self):
        self.packets: list[AudioPacket] = []
        self.closed = False

    def write(self, packet: AudioPacket) -> None:
        self.packets.append(packet)

    def close(self) -> None:
        self.closed = True


class ErrorRecorder:
    def __init__(self):
        self.closed = False

    def write(self, packet: AudioPacket) -> None:
        raise RuntimeError("recorder failure")

    def close(self) -> None:
        self.closed = True


class FakeInputStream:
    def __init__(self, callback):
        self._callback = callback
        self.started = False
        self.stopped = False
        self.closed = False

    def start_stream(self) -> None:
        self.started = True

    def stop_stream(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def is_active(self) -> bool:
        return False

    def emit(self, data: bytes) -> None:
        self._callback(data, 0, None, None)


class FakeOutputStream:
    def __init__(self, callback, config: AudioConfig):
        self._callback = callback
        self._config = config
        self.started = False
        self.stopped = False
        self.closed = False

    def start_stream(self) -> None:
        self.started = True

    def stop_stream(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def is_active(self) -> bool:
        return False

    def emit(self):
        return self._callback(None, self._config.chunk_frames, None, None)


class FakeStreamFactory:
    def __init__(self):
        self.input_stream: FakeInputStream | None = None
        self.output_stream: FakeOutputStream | None = None
        self.terminated = False

    def open_input_stream(self, config: AudioConfig, callback):
        stream = FakeInputStream(callback)
        self.input_stream = stream
        return stream

    def open_output_stream(self, config: AudioConfig, callback):
        stream = FakeOutputStream(callback, config)
        self.output_stream = stream
        return stream

    def terminate(self) -> None:
        self.terminated = True


def make_packet_with_size(worker: AudioOutputWorker) -> AudioPacket:
    data = bytes(worker._expected_bytes)  # type: ignore[attr-defined]
    return AudioPacket(pcm=data, counter=1, timestamp=time.time())


def test_audio_input_worker_emits_packets():
    """Input worker should invoke callback and recorder for each captured chunk."""
    packets: list[AudioPacket] = []

    def on_packet(packet: AudioPacket) -> None:
        packets.append(packet)

    recorder = RecordingStub()
    factory = FakeStreamFactory()
    worker = AudioInputWorker(AudioConfig(), on_packet, recorder=recorder, stream_factory=factory)
    worker.start()

    assert factory.input_stream is not None
    factory.input_stream.emit(b"\x01\x02")
    time.sleep(0.01)

    worker.close()

    assert len(packets) == 1
    assert packets[0].pcm == b"\x01\x02"
    assert recorder.packets == packets
    assert recorder.closed
    assert factory.input_stream.closed


def test_audio_input_worker_respects_transmit_toggle():
    """Disabling transmit should prevent packets from reaching the callback."""
    packets: list[AudioPacket] = []

    def on_packet(packet: AudioPacket) -> None:
        packets.append(packet)

    worker = AudioInputWorker(AudioConfig(), on_packet, stream_factory=FakeStreamFactory())
    worker.enable_transmit(False)
    worker._callback(b"\x01", 0, None, None)
    assert packets == []


def test_audio_input_worker_records_errors():
    """Recorder failures bubble into `last_error` and trigger abort flags."""
    worker = AudioInputWorker(AudioConfig(), lambda packet: None, recorder=ErrorRecorder(), stream_factory=FakeStreamFactory())
    result = worker._callback(b"\x00", 0, None, None)
    assert isinstance(worker.last_error, RuntimeError)
    expected_flag = getattr(audio_module.pyaudio, "paAbort", 0) if audio_module.pyaudio else 0
    assert result[1] == expected_flag


def test_audio_output_worker_playback_and_recording():
    """Output worker should play queued audio and fan it out to the recorder."""
    recorder = RecordingStub()
    factory = FakeStreamFactory()
    worker = AudioOutputWorker(AudioConfig(), recorder=recorder, stream_factory=factory)
    worker.start()
    assert factory.output_stream is not None

    packet = make_packet_with_size(worker)
    worker.enqueue(packet)
    chunk, flag = factory.output_stream.emit()
    worker.close()

    assert chunk == packet.pcm
    assert recorder.packets == [packet]
    expected_flag = getattr(audio_module.pyaudio, "paContinue", 0) if audio_module.pyaudio else 0
    assert flag == expected_flag


def test_audio_output_worker_disable_playback_and_error_capture():
    """When playback is muted, silence is emitted but recorder errors get logged."""
    recorder = ErrorRecorder()
    factory = FakeStreamFactory()
    worker = AudioOutputWorker(AudioConfig(), recorder=recorder, stream_factory=factory)
    worker.start()
    assert factory.output_stream is not None

    worker.enable_playback(False)
    packet = make_packet_with_size(worker)
    worker.enqueue(packet)
    chunk, flag = factory.output_stream.emit()
    worker.close()

    assert worker.last_error is not None
    assert chunk == worker._silence  # type: ignore[attr-defined]
    expected_flag = getattr(audio_module.pyaudio, "paContinue", 0) if audio_module.pyaudio else 0
    assert flag == expected_flag
