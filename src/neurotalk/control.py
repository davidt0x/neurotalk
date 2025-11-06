"""
Control-channel message definitions and helpers.

Messages are exchanged over the dedicated UDP control socket (`socketComm`
in the legacy scripts). This module formalizes the payload formats so other
components can produce/consume them without manipulating raw bytes directly.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum, auto


class ControlMessageType(Enum):
    """Enum covering every control packet currently supported."""

    HANDSHAKE_HELLO = auto()
    HANDSHAKE_HI_PARTNER = auto()
    HANDSHAKE_READY = auto()
    SYNC_REQUEST = auto()
    SYNC_TIMESTAMP = auto()
    TURN_PASS = auto()
    ESCAPE = auto()
    THANKS = auto()
    DEBUG_READY = auto()
    DEBUG_STOP = auto()


HANDSHAKE_HELLO = b"hello!"
HANDSHAKE_HI_PARTNER = b"hi partner"
HANDSHAKE_READY = b"please"
SYNC_REQUEST = b"syncTimeNow"
ESCAPE = b"esc"
THANKS = b"thanks"
DEBUG_READY = b"debugReady"
DEBUG_STOP = b"debugStop"

_DOUBLE = struct.Struct("<d")
_TRIPLE = struct.Struct("<ddd")


@dataclass(frozen=True)
class SyncTimestamp:
    """Single clock reading shared during the start-time negotiation."""

    value: float

    def pack(self) -> bytes:
        return _DOUBLE.pack(self.value)

    @staticmethod
    def unpack(payload: bytes) -> SyncTimestamp:
        (ts,) = _DOUBLE.unpack(payload)
        return SyncTimestamp(ts)


@dataclass(frozen=True)
class TurnPassPayload:
    """
    Metadata broadcast when a participant yields the turn.

    Attributes
    ----------
    wall_clock:
        Absolute `time.time()` reading when the handoff occurred.
    run_clock:
        Experiment run clock (seconds since run start).
    phase_clock:
        Phase/communication clock (seconds within current segment).
    """

    wall_clock: float
    run_clock: float
    phase_clock: float

    def pack(self) -> bytes:
        return _TRIPLE.pack(self.wall_clock, self.run_clock, self.phase_clock)

    @staticmethod
    def unpack(payload: bytes) -> TurnPassPayload:
        wall, run, phase = _TRIPLE.unpack(payload)
        return TurnPassPayload(wall, run, phase)


def classify_payload(data: bytes) -> tuple[ControlMessageType, object | None]:
    """
    Determine the message type for a raw UDP payload and return the parsed form.

    Returns
    -------
    (ControlMessageType, payload)
        Payload is `None` for token-only messages, a `SyncTimestamp` instance
        for `SYNC_TIMESTAMP`, and `TurnPassPayload` for `TURN_PASS`.
    """

    if data == HANDSHAKE_HELLO:
        return ControlMessageType.HANDSHAKE_HELLO, None
    if data == HANDSHAKE_HI_PARTNER:
        return ControlMessageType.HANDSHAKE_HI_PARTNER, None
    if data == HANDSHAKE_READY:
        return ControlMessageType.HANDSHAKE_READY, None
    if data == SYNC_REQUEST:
        return ControlMessageType.SYNC_REQUEST, None
    if data == ESCAPE:
        return ControlMessageType.ESCAPE, None
    if data == THANKS:
        return ControlMessageType.THANKS, None
    if data == DEBUG_READY:
        return ControlMessageType.DEBUG_READY, None
    if data == DEBUG_STOP:
        return ControlMessageType.DEBUG_STOP, None

    if len(data) == _DOUBLE.size:
        return ControlMessageType.SYNC_TIMESTAMP, SyncTimestamp.unpack(data)
    if len(data) == _TRIPLE.size:
        return ControlMessageType.TURN_PASS, TurnPassPayload.unpack(data)

    msg = f"Unknown control payload length={len(data)}"
    raise ValueError(msg)
