from __future__ import annotations

import argparse
import sys
from typing import Any


def _list_sounddevice() -> list[dict[str, Any]]:
    import sounddevice as sd 

    hostapis = sd.query_hostapis()
    devices = sd.query_devices()
    results: list[dict[str, Any]] = []
    for index, dev in enumerate(devices):
        if dev["max_output_channels"] == 0 and dev["max_input_channels"] == 0:
            continue
        host_index = dev.get("hostapi")
        host_name = hostapis[host_index]["name"] if host_index is not None else "n/a"
        results.append(
            {
                "index": index,
                "name": dev["name"],
                "hostapi": host_name,
                "outputs": dev["max_output_channels"],
                "inputs": dev["max_input_channels"],
            }
        )
    return results


def _list_ptb() -> list[dict[str, Any]]:
    try:
        from psychtoolbox import audio
    except Exception as exc:  # pragma: no cover - optional dep
        msg = (
            "psychtoolbox/psychopy is required for --backend ptb; "
            "install the 'examples' extra or the psychopy package."
        )
        raise RuntimeError(msg) from exc

    devices = audio.get_devices()
    results: list[dict[str, Any]] = []
    for dev in devices:
        results.append(
            {
                "index": dev["DeviceIndex"],
                "name": dev["DeviceName"],
                "hostapi": dev.get("HostAudioAPIName", "n/a"),
                "outputs": dev["NrOutputChannels"],
                "inputs": dev["NrInputChannels"],
            }
        )
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NeuroTalk command-line utilities (device listing, etc.)."
    )
    parser.add_argument(
        "--backend",
        choices=["sounddevice", "ptb"],
        default="sounddevice",
        help="Backend to query (default: sounddevice).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    devices = _list_sounddevice() if args.backend == "sounddevice" else _list_ptb()

    for dev in devices:
        print(  # noqa: T201
            f"{dev['index']}: {dev['name']} (hostapi={dev['hostapi']}, "
            f"outputs={dev['outputs']}, inputs={dev['inputs']})"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
