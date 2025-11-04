#!/usr/bin/env python3
"""Minimal NeuroTalk peer using real audio devices."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
import queue
import logging

from neurotalk.config import AudioConfig, NetworkConfig, RecordingConfig, SessionConfig
from neurotalk.control import ControlMessageType
from neurotalk.session import ConversationSession


LOGGER = logging.getLogger("neurotalk.simple_peer")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple NeuroTalk peer")
    parser.add_argument("remote_ip", help="Peer IP address")
    parser.add_argument("participant_id", help="Participant identifier")
    parser.add_argument("role", help="Role label (e.g., A or B)")
    parser.add_argument("record_dir", nargs="?", default="data", help="Directory for recordings")
    parser.add_argument("segment_label", nargs="?", default="segment", help="Base label for recording segments")
    parser.add_argument("--local-in", type=int, default=30002, help="Local inbound audio port")
    parser.add_argument("--local-out", type=int, default=30001, help="Local outbound audio port")
    parser.add_argument("--local-control", type=int, default=30003, help="Local control port")
    parser.add_argument("--remote-in", type=int, default=30002, help="Remote inbound audio port")
    parser.add_argument("--remote-out", type=int, default=30001, help="Remote outbound audio port")
    parser.add_argument("--remote-control", type=int, default=30003, help="Remote control port")
    parser.add_argument("--nat-role", type=int, choices=[0, 1], default=None, help="NAT role: 0=passive, 1=active")
    parser.add_argument("--chunk-frames", type=int, default=512, help="Audio buffer size (default: 512)")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Sample rate Hz (default: 16000)")
    parser.add_argument("--turn-duration", type=float, default=10.0, help="Seconds to speak before passing the turn")
    parser.add_argument("--debug-duration", type=float, default=10.0, help="Seconds to run debug loopback before task")
    parser.add_argument(
        "--speaker-order",
        choices=["first", "second"],
        default=None,
        help="Whether this peer speaks first or second",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (e.g., INFO, DEBUG) for instrumentation output",
    )
    return parser.parse_args()


def build_session(args: argparse.Namespace) -> ConversationSession:
    network = NetworkConfig(
        local_ports=(args.local_in, args.local_out, args.local_control),
        remote_hint=(args.remote_ip, args.remote_in, args.remote_out, args.remote_control),
        nat_role=args.nat_role,
        punch_timeout_s=10.0,
        stun_servers=(),
    )
    audio = AudioConfig(sample_rate_hz=args.sample_rate, chunk_frames=args.chunk_frames)
    recording = RecordingConfig(directory=Path(args.record_dir))
    session_config = SessionConfig(
        participant_id=args.participant_id,
        role=args.role,
        network=network,
        audio=audio,
        recording=recording,
    )
    return ConversationSession(session_config)


def wait_for_turn(session: ConversationSession) -> None:
    LOGGER.debug("Waiting for TURN_PASS events")
    while True:
        try:
            msg_type, _ = session.next_control_event(timeout=0.5)
        except queue.Empty:
            LOGGER.debug("No TURN_PASS yet; retrying")
            continue
        if msg_type == ControlMessageType.TURN_PASS:
            LOGGER.debug("TURN_PASS received")
            return


def set_mode(session: ConversationSession, *, speaking: bool) -> None:
    """Configure local transmit/receive based on current role."""
    LOGGER.debug("set_mode speaking=%s", speaking)
    if speaking:
        session.enable_transmit(True)
        session.enable_receive(False)
    else:
        session.enable_transmit(False)
        session.enable_receive(True)


def speak_segment(session: ConversationSession, label: str, duration: float, run_clock: float) -> None:
    LOGGER.debug("Starting segment %s duration=%.2f run_clock=%.2f", label, duration, run_clock)
    set_mode(session, speaking=True)
    session.start_segment(label)
    print(f"[{session.config.role}] Speak now ({duration:.1f}s). Press Enter to pass early.")
    start = time.monotonic()
    while True:
        remaining = duration - (time.monotonic() - start)
        if remaining <= 0:
            break
        if remaining <= 0.5:
            time.sleep(remaining)
            break
        time.sleep(0.5)
    session.stop_segment()
    phase_clock = time.monotonic() - start
    LOGGER.debug("Passing turn after segment %s phase_clock=%.2f", label, phase_clock)
    session.pass_turn(run_time=run_clock, phase_time=phase_clock)
    print(f"[{session.config.role}] Turn passed.")
    set_mode(session, speaking=False)


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.nat_role is None:
        args.nat_role = 1 if args.role.upper() == "A" else 0
    if args.speaker_order is None:
        args.speaker_order = "first" if args.role.upper() == "A" else "second"
    session = build_session(args)
    print(f"[{args.role}] connecting to {args.remote_ip}...")
    session.connect()
    try:
        run_start = time.monotonic()
        if args.speaker_order == "first":
            set_mode(session, speaking=True)
        else:
            set_mode(session, speaking=False)
        # Turn 1
        if args.speaker_order == "first":
            speak_segment(session, f"{args.segment_label}_turn1", args.turn_duration, time.monotonic() - run_start)
            print(f"[{args.role}] Waiting for partner...")
            set_mode(session, speaking=False)
            wait_for_turn(session)
        else:
            print(f"[{args.role}] Waiting for partner...")
            set_mode(session, speaking=False)
            wait_for_turn(session)
            speak_segment(session, f"{args.segment_label}_turn1", args.turn_duration, time.monotonic() - run_start)

        # Turn 2
        if args.speaker_order == "first":
            print(f"[{args.role}] Waiting for partner...")
            set_mode(session, speaking=False)
            wait_for_turn(session)
            speak_segment(session, f"{args.segment_label}_turn2", args.turn_duration, time.monotonic() - run_start)
        else:
            speak_segment(session, f"{args.segment_label}_turn2", args.turn_duration, time.monotonic() - run_start)
            print(f"[{args.role}] Waiting for partner...")
            set_mode(session, speaking=False)
            wait_for_turn(session)

        export_dir = Path(args.record_dir) / "segments"
        segments = session.export_segments(export_dir)
        print(f"[{args.role}] Segments exported to {export_dir} => {segments}")
    finally:
        session.close()


if __name__ == "__main__":  # pragma: no cover
    main()
