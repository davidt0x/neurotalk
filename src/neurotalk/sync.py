from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class SyncAction(str, Enum):
    ANNOUNCE = "announce"
    ACK = "ack"


SYNC_MESSAGE_TYPE = "sync"


@dataclass(frozen=True, slots=True)
class SyncInstruction:
    action: SyncAction
    start_time: float


def encode_sync_instruction(instruction: SyncInstruction) -> Mapping[str, Any]:
    return {
        "action": instruction.action.value,
        "start_time": instruction.start_time,
    }


def decode_sync_instruction(payload: Mapping[str, Any]) -> SyncInstruction:
    try:
        action_raw = payload["action"]
        start_time_raw = payload["start_time"]
    except KeyError as exc:  # pragma: no cover - validation safety
        raise ValueError("Missing sync payload field.") from exc

    try:
        action = SyncAction(str(action_raw))
    except ValueError as exc:
        raise ValueError(f"Unsupported sync action: {action_raw!r}") from exc

    try:
        start_time = float(start_time_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid start_time value: {start_time_raw!r}") from exc

    return SyncInstruction(action=action, start_time=start_time)
