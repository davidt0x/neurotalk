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
    TURN_TAKE = auto()
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
_TURN_WITH_ID = struct.Struct("<dddq")
TURN_TAKE_PREFIX = b"T"


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
    Metadata broadcast when the turn state changes.

    Attributes
    ----------
    wall_clock:
        Absolute `time.time()` reading when the handoff occurred.
    run_clock:
        Experiment run clock (seconds since run start).
    phase_clock:
        Phase/communication clock (seconds within current segment).
    turn_id:
        Optional Lamport-style counter for ordering turn events. When omitted,
        receivers will accept the event but cannot use it to drop duplicates.
    """

    wall_clock: float
    run_clock: float
    phase_clock: float
    turn_id: int | None = None

    def pack(
        self, *, include_turn_id: bool | None = None, prefix: bytes | None = None
    ) -> bytes:
        """
        Serialize the payload.

        Parameters
        ----------
        include_turn_id:
            Force inclusion of the turn_id in the packed bytes. Defaults to
            including when ``turn_id`` is not ``None``.
        prefix:
            Optional one-byte prefix used to distinguish message kinds
            (e.g., TURN_TAKE vs TURN_PASS).
        """

        use_turn_id = (
            self.turn_id is not None if include_turn_id is None else include_turn_id
        )
        if use_turn_id:
            turn_id = -1 if self.turn_id is None else int(self.turn_id)
            body = _TURN_WITH_ID.pack(
                float(self.wall_clock),
                float(self.run_clock),
                float(self.phase_clock),
                turn_id,
            )
        else:
            body = _TRIPLE.pack(
                float(self.wall_clock), float(self.run_clock), float(self.phase_clock)
            )
        if prefix:
            return prefix + body
        return body

    @staticmethod
    def unpack(
        payload: bytes, *, expect_prefix: bytes | None = None
    ) -> TurnPassPayload:
        """
        Parse payload bytes back into a :class:`TurnPassPayload`.

        Parameters
        ----------
        expect_prefix:
            When provided, the payload must start with this prefix and it will
            be stripped before decoding the numeric fields.
        """

        data = payload
        if expect_prefix:
            if not data.startswith(expect_prefix):
                msg = "Payload missing expected prefix"
                raise ValueError(msg)
            data = data[len(expect_prefix) :]

        if len(data) == _TRIPLE.size:
            wall, run, phase = _TRIPLE.unpack(data)
            return TurnPassPayload(wall, run, phase, None)
        if len(data) == _TURN_WITH_ID.size:
            wall, run, phase, turn_id = _TURN_WITH_ID.unpack(data)
            normalized_id = None if turn_id < 0 else int(turn_id)
            return TurnPassPayload(wall, run, phase, normalized_id)

        msg = f"Unknown TURN payload length={len(data)}"
        raise ValueError(msg)


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

    if data.startswith(TURN_TAKE_PREFIX) and len(data) in (
        1 + _TRIPLE.size,
        1 + _TURN_WITH_ID.size,
    ):
        return ControlMessageType.TURN_TAKE, TurnPassPayload.unpack(
            data, expect_prefix=TURN_TAKE_PREFIX
        )

    if len(data) == _DOUBLE.size:
        return ControlMessageType.SYNC_TIMESTAMP, SyncTimestamp.unpack(data)
    if len(data) in (_TRIPLE.size, _TURN_WITH_ID.size):
        return ControlMessageType.TURN_PASS, TurnPassPayload.unpack(data)

    msg = f"Unknown control payload length={len(data)}"
    raise ValueError(msg)
