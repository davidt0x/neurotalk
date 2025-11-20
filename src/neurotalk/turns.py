"""Turn-taking helpers built on top of :mod:`neurotalk.session`."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from neurotalk.control import ControlMessageType, TurnPassPayload


class ConversationSessionLike(Protocol):
    """Protocol capturing the TurnManager interactions with a session."""

    def enable_transmit(self, enabled: bool) -> None: ...

    def enable_receive(self, enabled: bool) -> None: ...

    def start_segment(
        self,
        label: str,
        *,
        metadata: dict[str, object] | None = None,
        target: str | tuple[str, ...] = "both",
    ) -> None: ...

    def stop_segment(self, *, target: str | tuple[str, ...] = "both") -> None: ...

    def pass_turn(
        self,
        *,
        run_time: float,
        phase_time: float,
        wall_time: float | None = None,
    ) -> None: ...


class TurnRole(Enum):
    """Enumerates the two conversation roles for a dyad."""

    SPEAKER = "speaker"
    LISTENER = "listener"

    @property
    def is_speaker(self) -> bool:
        return self is TurnRole.SPEAKER


class TurnEventSource(Enum):
    """Explains why a turn transition occurred."""

    INITIAL = "initial"
    LOCAL_PASS = "local_pass"
    REMOTE_PASS = "remote_pass"


@dataclass(frozen=True)
class TurnEvent:
    """Metadata surfaced whenever the local role changes."""

    role: TurnRole
    source: TurnEventSource
    payload: TurnPassPayload | None = None


class TurnManager:
    """Manages local/remote role transitions for a :class:`ConversationSession`."""

    def __init__(
        self,
        session: ConversationSessionLike,
        *,
        on_event: Callable[[TurnEvent], None] | None = None,
        local_label_template: str = "local_turn{index:02d}",
        remote_label_template: str = "remote_turn{index:02d}",
    ) -> None:
        self._session = session
        self._on_event = on_event
        self._local_label_template = local_label_template
        self._remote_label_template = remote_label_template
        self._lock = threading.Lock()
        self._current_role: TurnRole | None = None
        self._local_segment_active = False
        self._remote_segment_active = False
        self._local_counter = 0
        self._remote_counter = 0

    # ---- public API -------------------------------------------------
    @property
    def role(self) -> TurnRole | None:
        return self._current_role

    @property
    def is_speaker(self) -> bool:
        return self._current_role is TurnRole.SPEAKER

    def start(self, initial_role: TurnRole) -> TurnEvent:
        """Begin turn management in the requested role."""

        with self._lock:
            return self._transition_to(initial_role, TurnEventSource.INITIAL)

    def stop(self) -> None:
        """Cease all segments and mute input/output."""

        with self._lock:
            self._end_local_segment()
            self._end_remote_segment()
            self._session.enable_transmit(False)
            self._session.enable_receive(False)
            self._current_role = None

    def pass_turn(
        self,
        *,
        run_time: float,
        phase_time: float,
        wall_time: float | None = None,
    ) -> TurnEvent:
        """Yield control to the partner and enter the listener role."""

        with self._lock:
            if self._current_role is not TurnRole.SPEAKER:
                msg = "Cannot pass turn when not the speaker"
                raise RuntimeError(msg)
            self._session.pass_turn(
                run_time=run_time, phase_time=phase_time, wall_time=wall_time
            )
            return self._transition_to(TurnRole.LISTENER, TurnEventSource.LOCAL_PASS)

    def handle_control_event(
        self, msg_type: ControlMessageType, payload: object | None
    ) -> TurnEvent | None:
        """React to remote TURN_PASS messages (ignores other event types)."""

        if msg_type is not ControlMessageType.TURN_PASS:
            return None
        tp_payload = payload if isinstance(payload, TurnPassPayload) else None
        with self._lock:
            return self._transition_to(
                TurnRole.SPEAKER, TurnEventSource.REMOTE_PASS, payload=tp_payload
            )

    # ---- internal helpers ------------------------------------------
    def _transition_to(
        self,
        role: TurnRole,
        source: TurnEventSource,
        *,
        payload: TurnPassPayload | None = None,
    ) -> TurnEvent:
        if self._current_role is role and source is not TurnEventSource.INITIAL:
            return TurnEvent(role=role, source=source, payload=payload)

        if role is TurnRole.SPEAKER:
            self._end_remote_segment()
            self._start_local_segment()
            self._session.enable_transmit(True)
            self._session.enable_receive(False)
        else:
            self._end_local_segment()
            self._start_remote_segment()
            self._session.enable_transmit(False)
            self._session.enable_receive(True)

        self._current_role = role
        event = TurnEvent(role=role, source=source, payload=payload)
        if self._on_event:
            self._on_event(event)
        return event

    def _start_local_segment(self) -> None:
        if self._local_segment_active:
            return
        self._local_counter += 1
        label = self._format_label(self._local_label_template, self._local_counter)
        self._session.start_segment(label, target="local")
        self._local_segment_active = True

    def _start_remote_segment(self) -> None:
        if self._remote_segment_active:
            return
        self._remote_counter += 1
        label = self._format_label(self._remote_label_template, self._remote_counter)
        self._session.start_segment(label, target="remote")
        self._remote_segment_active = True

    def _end_local_segment(self) -> None:
        if not self._local_segment_active:
            return
        self._session.stop_segment(target="local")
        self._local_segment_active = False

    def _end_remote_segment(self) -> None:
        if not self._remote_segment_active:
            return
        self._session.stop_segment(target="remote")
        self._remote_segment_active = False

    @staticmethod
    def _format_label(template: str, index: int) -> str:
        try:
            return template.format(index=index, counter=index)
        except Exception:  # pragma: no cover - formatting fall back
            return template
