from __future__ import annotations

from typing import Any

from neurotalk.audio import SoundDeviceStreamFactory
from neurotalk.config import AudioConfig


def test_sounddevice_stream_factory_passes_device_selectors(monkeypatch: Any) -> None:
    captured: dict[str, dict[str, Any]] = {}

    class DummyStream:
        active = False

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    def fake_input_stream(**kwargs: Any) -> DummyStream:
        captured["input"] = kwargs
        return DummyStream()

    def fake_output_stream(**kwargs: Any) -> DummyStream:
        captured["output"] = kwargs
        return DummyStream()

    monkeypatch.setattr("neurotalk.audio.sd.InputStream", fake_input_stream)
    monkeypatch.setattr("neurotalk.audio.sd.OutputStream", fake_output_stream)

    config = AudioConfig(input_device=5, output_device="USB Audio Device")
    factory = SoundDeviceStreamFactory()

    factory.open_input_stream(config, lambda *_args: None)
    factory.open_output_stream(config, lambda *_args: None)

    assert captured["input"]["device"] == 5
    assert captured["output"]["device"] == "USB Audio Device"
