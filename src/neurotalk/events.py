from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Optional, cast

ControlPayload = Mapping[str, Any]
ControlHandler = Callable[[ControlPayload], Awaitable[None] | None]
StateHandler = Callable[["SessionState"], Awaitable[None] | None]
ErrorHandler = Callable[[Exception], Awaitable[None] | None]


class SessionState(Enum):
    """Lifecycle stages for a NeuroTalk session."""

    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    READY = "ready"
    CLOSED = "closed"
    ERROR = "error"


async def _run_callback(callback: Callable[..., Awaitable[None] | None], *args: Any) -> None:
    """Execute a callback, awaiting it when needed."""
    result = callback(*args)
    if inspect.isawaitable(result):
        await cast(Awaitable[Any], result)


@dataclass(slots=True)
class EventHandlers:
    """Container for optional session event callbacks."""

    on_control: Optional[ControlHandler] = None
    on_state_change: Optional[StateHandler] = None
    on_error: Optional[ErrorHandler] = None

    async def emit_control(self, payload: ControlPayload) -> None:
        if self.on_control:
            await _run_callback(self.on_control, payload)

    async def emit_state(self, state: SessionState) -> None:
        if self.on_state_change:
            await _run_callback(self.on_state_change, state)

    async def emit_error(self, error: Exception) -> None:
        if self.on_error:
            await _run_callback(self.on_error, error)
