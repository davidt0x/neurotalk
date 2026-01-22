from __future__ import annotations

import pytest

from neurotalk.control import (
    DEBUG_READY,
    DEBUG_STOP,
    ESCAPE,
    HANDSHAKE_HELLO,
    HANDSHAKE_HI_PARTNER,
    HANDSHAKE_READY,
    SYNC_REQUEST,
    THANKS,
    TURN_TAKE_PREFIX,
    ControlMessageType,
    SyncTimestamp,
    TurnPassPayload,
    classify_payload,
)


def test_token_messages_classification():
    """Token-only payloads should round-trip through `classify_payload`."""
    for token, expected in [
        (HANDSHAKE_HELLO, ControlMessageType.HANDSHAKE_HELLO),
        (HANDSHAKE_HI_PARTNER, ControlMessageType.HANDSHAKE_HI_PARTNER),
        (HANDSHAKE_READY, ControlMessageType.HANDSHAKE_READY),
        (SYNC_REQUEST, ControlMessageType.SYNC_REQUEST),
        (ESCAPE, ControlMessageType.ESCAPE),
        (THANKS, ControlMessageType.THANKS),
        (DEBUG_READY, ControlMessageType.DEBUG_READY),
        (DEBUG_STOP, ControlMessageType.DEBUG_STOP),
    ]:
        msg_type, payload = classify_payload(token)
        assert msg_type is expected
        assert payload is None


def test_sync_timestamp_roundtrip():
    """`SyncTimestamp` should serialize/deserialize as an 8-byte payload."""
    ts = SyncTimestamp(123.456)
    msg_type, payload = classify_payload(ts.pack())
    assert msg_type is ControlMessageType.SYNC_TIMESTAMP
    assert isinstance(payload, SyncTimestamp)
    assert payload.value == pytest.approx(123.456)


def test_turn_pass_roundtrip():
    """`TurnPassPayload` should serialize/deserialize with or without turn_id."""
    payload = TurnPassPayload(1.0, 2.0, 3.0)
    msg_type, decoded = classify_payload(payload.pack())
    assert msg_type is ControlMessageType.TURN_PASS
    assert isinstance(decoded, TurnPassPayload)
    assert decoded.wall_clock == pytest.approx(1.0)
    assert decoded.run_clock == pytest.approx(2.0)
    assert decoded.phase_clock == pytest.approx(3.0)
    assert decoded.turn_id is None

    payload_with_id = TurnPassPayload(4.0, 5.0, 6.0, turn_id=9)
    msg_type, decoded_with_id = classify_payload(payload_with_id.pack())
    assert msg_type is ControlMessageType.TURN_PASS
    assert isinstance(decoded_with_id, TurnPassPayload)
    assert decoded_with_id.wall_clock == pytest.approx(4.0)
    assert decoded_with_id.turn_id == 9


def test_turn_take_roundtrip():
    """TURN_TAKE payloads should round-trip with the take prefix."""
    payload = TurnPassPayload(1.1, 2.2, 3.3, turn_id=5)
    packed = payload.pack(prefix=TURN_TAKE_PREFIX)
    msg_type, decoded = classify_payload(packed)
    assert msg_type is ControlMessageType.TURN_TAKE
    assert isinstance(decoded, TurnPassPayload)
    assert decoded.turn_id == 5


def test_unknown_payload_raises():
    """Unexpected payload sizes must trigger a `ValueError`."""
    with pytest.raises(ValueError, match="Unknown control payload length=5"):
        classify_payload(b"\x00" * 5)
