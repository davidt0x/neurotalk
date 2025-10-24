"""Responder side of the minimal NeuroTalk audio call.

Run simple_call_host.py first, then start this script with the same --room value.
Both participants should be able to hear one another once READY is printed.
"""

from __future__ import annotations

import logging
import argparse
import asyncio

from neurotalk import EventHandlers, Session, SessionConfig, SessionState, SignalingConfig


logging.basicConfig(level=logging.INFO)


def _print_state(state: SessionState) -> None:
    print(f"[peer] session state -> {state.value}")


async def _async_main(args: argparse.Namespace) -> None:
    config = SessionConfig(
        peer_id=args.peer_id,
        signaling=SignalingConfig(url=args.signaling_url, room=args.room),
        initiator=False,
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
    parser = argparse.ArgumentParser(description="NeuroTalk audio example (peer).")
    parser.add_argument("--peer-id", default="peer", help="Identifier for this participant.")
    parser.add_argument("--room", default="demo-room", help="Shared room name for the call.")
    parser.add_argument(
        "--signaling-url",
        default="ws://127.0.0.1:8765",
        help="URL of the signaling server (default: ws://127.0.0.1:8765).",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\nExiting peer session.")


if __name__ == "__main__":
    main()
