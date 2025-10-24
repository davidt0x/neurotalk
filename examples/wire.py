"""Loop microphone input back to speakers using PyAudio.

Usage:
    uv run python examples/wire.py --duration 10 --input "USB Mic" --output "Speakers (Realtek(R) Audio)"
"""

from __future__ import annotations

import argparse
import sys
import time

import pyaudio


def _resolve_device(p: pyaudio.PyAudio, device_spec: str | int | None, is_input: bool) -> int | None:
    if device_spec is None:
        return None
    if isinstance(device_spec, int):
        return device_spec
    name_fragment = device_spec.strip('"')
    device_count = p.get_device_count()
    for index in range(device_count):
        info = p.get_device_info_by_index(index)
        name = info.get("name", "")
        if name_fragment.lower() in name.lower():
            if is_input and info.get("maxInputChannels", 0) > 0:
                return index
            if not is_input and info.get("maxOutputChannels", 0) > 0:
                return index
    raise ValueError(f"Device containing '{name_fragment}' not found")


def run_loopback(
    duration: float,
    sample_rate: int,
    input_device: str | int | None,
    output_device: str | int | None,
) -> None:
    p = pyaudio.PyAudio()
    try:
        input_index = _resolve_device(p, input_device, True)
        output_index = _resolve_device(p, output_device, False)

        if input_index is None:
            default_in = p.get_default_input_device_info()
            print(f"Using default input device: {default_in.get('name', 'Unknown')}")
        else:
            print(f"Using input device index {input_index}")

        if output_index is None:
            default_out = p.get_default_output_device_info()
            print(f"Using default output device: {default_out.get('name', 'Unknown')}")
        else:
            print(f"Using output device index {output_index}")

        format_ = p.get_format_from_width(2)
        channels = 1 if sys.platform == "darwin" else 2

        stream = p.open(
            format=format_,
            channels=channels,
            rate=sample_rate,
            input=True,
            output=True,
            input_device_index=input_index,
            output_device_index=output_index,
            stream_callback=lambda in_data, frame_count, time_info, status: (in_data, pyaudio.paContinue),
        )

        start = time.time()
        while stream.is_active() and (time.time() - start) < duration:
            time.sleep(0.1)

        stream.stop_stream()
        stream.close()
    finally:
        p.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description="Loop microphone input back to speakers.")
    parser.add_argument("--duration", type=float, default=5.0, help="Duration in seconds (default: 5)")
    parser.add_argument("--sample-rate", type=int, default=44100, help="Sample rate for input/output (default: 44100)")
    parser.add_argument("--input", type=str, default=None, help="Input device name fragment or index")
    parser.add_argument("--output", type=str, default=None, help="Output device name fragment or index")
    args = parser.parse_args()

    run_loopback(args.duration, args.sample_rate, args.input, args.output)


if __name__ == "__main__":
    main()
