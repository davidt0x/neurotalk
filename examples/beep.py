"""Play a simple sine wave through PyAudio.

Usage:
    uv run python examples/beep.py --frequency 880 --duration 5 --device "Speakers (Realtek(R) Audio)"
"""

from __future__ import annotations

import argparse

import numpy as np
import pyaudio


def _resolve_output_device(p: pyaudio.PyAudio, device_spec: str | int | None) -> int | None:
    if device_spec is None:
        return None
    if isinstance(device_spec, int):
        return device_spec
    name_fragment = device_spec.strip('"')
    device_count = p.get_device_count()
    for index in range(device_count):
        info = p.get_device_info_by_index(index)
        if info.get("maxOutputChannels", 0) > 0 and name_fragment.lower() in info.get("name", "").lower():
            return index
    raise ValueError(f"Output device containing '{name_fragment}' not found")


def play_tone(
    frequency: float,
    duration: float,
    sample_rate: int,
    volume: float,
    output_device: str | int | None,
) -> None:
    p = pyaudio.PyAudio()
    try:
        device_index = _resolve_output_device(p, output_device)
        stream = p.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=sample_rate,
            output=True,
            output_device_index=device_index,
        )
        samples = (
            np.sin(2 * np.pi * np.arange(sample_rate * duration) * frequency / sample_rate)
        ).astype(np.float32)
        stream.write(volume * samples)
        stream.stop_stream()
        stream.close()
    finally:
        p.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description="Play a sine wave through PyAudio.")
    parser.add_argument("--frequency", type=float, default=440.0, help="Sine frequency in Hz (default: 440)")
    parser.add_argument("--duration", type=float, default=5.0, help="Duration in seconds (default: 5)")
    parser.add_argument("--sample-rate", type=int, default=44100, help="Sample rate in Hz (default: 44100)")
    parser.add_argument("--volume", type=float, default=0.5, help="Volume multiplier (0.0-1.0, default: 0.5)")
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Optional output device name fragment or index (default: PyAudio default)",
    )
    args = parser.parse_args()

    play_tone(
        frequency=args.frequency,
        duration=args.duration,
        sample_rate=args.sample_rate,
        volume=args.volume,
        output_device=args.device,
    )


if __name__ == "__main__":
    main()
