"""Initiator side of a minimal NeuroTalk audio call.

Steps:
1. Launch the signaling server (see run_signaling_server.py) on a host reachable by both peers.
2. Start this script on participant A with the desired room id.
3. Start simple_call_peer.py on participant B with the same room id.
Both sides should hear each other once the transport reports READY.
"""

from __future__ import annotations


import logging
import argparse
import asyncio

from neurotalk import (
    AudioDeviceConfig,
    EventHandlers,
    Session,
    SessionConfig,
    SessionState,
    SignalingConfig,
)


logging.basicConfig(level=logging.DEBUG)
logging.getLogger("aioice").setLevel(logging.WARNING)
logging.getLogger("aiortc").setLevel(logging.WARNING)


def _print_state(state: SessionState) -> None:
    print(f"[host] session state -> {state.value}")


async def _async_main(args: argparse.Namespace) -> None:
    config = SessionConfig(
        peer_id=args.peer_id,
        signaling=SignalingConfig(url=args.signaling_url, room=args.room),
        audio=AudioDeviceConfig(sample_rate=args.sample_rate),
        initiator=True,
    )
    handlers = EventHandlers(on_state_change=_print_state)
    session = Session(config, handlers)
    try:
        async with session:
            await session.wait_transport_ready()
            print("Audio link active. Speak freely. Press Ctrl+C to exit.")
            await asyncio.Future()
    except asyncio.CancelledError:  # pragma: no cover
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="NeuroTalk audio example (initiator).")
    parser.add_argument("--peer-id", default="host", help="Identifier for this participant.")
    parser.add_argument("--room", default="demo-room", help="Shared room name for the call.")
    parser.add_argument(
        "--signaling-url",
        default="ws://127.0.0.1:8765",
        help="URL of the signaling server (default: ws://127.0.0.1:8765).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=16000,
        help="Sample rate in Hz for both capture and playback (default: 16000).",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\nExiting host session.")


if __name__ == "__main__":
    main()
