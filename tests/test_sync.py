from __future__ import annotations

import pytest

from neurotalk.sync import (
    SYNC_MESSAGE_TYPE,
    SyncAction,
    SyncInstruction,
    decode_sync_instruction,
    encode_sync_instruction,
)


def test_encode_decode_sync_instruction() -> None:
    instruction = SyncInstruction(action=SyncAction.ANNOUNCE, start_time=123.456)
    payload = encode_sync_instruction(instruction)
    decoded = decode_sync_instruction(payload)
    assert decoded == instruction


def test_decode_invalid_action() -> None:
    with pytest.raises(ValueError):
        decode_sync_instruction({"action": "invalid", "start_time": 1.0})


def test_decode_missing_field() -> None:
    with pytest.raises(ValueError):
        decode_sync_instruction({"action": SyncAction.ACK.value})


def test_decode_bad_start_time() -> None:
    with pytest.raises(ValueError):
        decode_sync_instruction({"action": SyncAction.ANNOUNCE.value, "start_time": "not-a-float"})
