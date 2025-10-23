"""Minimal signaling server runner for NeuroTalk examples.

Usage:
    uv run python examples/run_signaling_server.py --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import argparse
import asyncio

from neurotalk import SignalingServer


async def _async_main(host: str, port: int) -> None:
    server = SignalingServer()
    ws_server = await server.serve(host, port)
    print(f"Signaling server listening on ws://{host}:{port}. Press Ctrl+C to exit.")
    try:
        await asyncio.Future()
    except asyncio.CancelledError:  # pragma: no cover
        raise
    except KeyboardInterrupt:
        print("\nStopping signaling server...")
    finally:
        ws_server.close()
        await ws_server.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NeuroTalk WebSocket signaling server.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765).")
    args = parser.parse_args()
    try:
        asyncio.run(_async_main(args.host, args.port))
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
