#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from neurotalk.soundcheck import list_output_devices, run_volume_check


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a sounddevice-based volume check for NeuroTalk experiments."
    )
    parser.add_argument(
        "--audio",
        type=Path,
        help="Optional path to an audio clip; defaults to a generated tone.",
    )
    parser.add_argument(
        "--device",
        type=int,
        help="Sounddevice output device index (see --list-devices).",
    )
    parser.add_argument(
        "--start-volume",
        type=float,
        default=0.5,
        help="Starting gain multiplier (default: 0.5).",
    )
    parser.add_argument(
        "--max-volume",
        type=float,
        default=2.0,
        help="Maximum gain multiplier (default: 2.0).",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=0.05,
        help="Volume increment per keypress (default: 0.05).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=48_000,
        help="Playback sample rate (default: 48000).",
    )
    parser.add_argument(
        "--channels",
        type=int,
        default=2,
        help="Output channel count (default: 2).",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List output-capable devices and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.list_devices:
        for dev in list_output_devices():
            name = dev["name"]
            api = dev.get("hostapi", "n/a")
            channels = dev["max_output_channels"]
            print(  # noqa: T201
                f"{dev['index']}: {name} (hostapi={api}, outputs={channels})"
            )
        return 0

    result = run_volume_check(
        audio_path=args.audio,
        device_index=args.device,
        sample_rate_hz=args.sample_rate,
        channels=args.channels,
        start_volume=args.start_volume,
        max_volume=args.max_volume,
        step=args.step,
    )
    print(  # noqa: T201
        f"Selected volume: {result.volume:.2f} (device={result.device_index}, "
        f"rate={result.sample_rate_hz} Hz, channels={result.channels})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
