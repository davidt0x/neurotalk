"""Turn-taking helpers built on top of :mod:`neurotalk.session`."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from neurotalk.control import ControlMessageType, TurnPassPayload

logger = logging.getLogger(__name__)


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
        turn_id: int | None = None,
    ) -> None: ...

    def take_turn(
        self,
        *,
        run_time: float,
        phase_time: float,
        wall_time: float | None = None,
        turn_id: int | None = None,
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
    LOCAL_TAKE = "local_take"
    REMOTE_PASS = "remote_pass"
    REMOTE_TAKE = "remote_take"


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
        self._turn_id_counter = 0
        self._last_turn_id: int | None = None

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
            logger.debug("TurnManager.start initial_role=%s", initial_role.value)
            self._turn_id_counter = 0
            self._last_turn_id = None
            return self._transition_to(initial_role, TurnEventSource.INITIAL)

    def stop(self) -> None:
        """Cease all segments and mute input/output."""

        with self._lock:
            logger.debug(
                "TurnManager.stop current_role=%s local_active=%s remote_active=%s",
                None if self._current_role is None else self._current_role.value,
                self._local_segment_active,
                self._remote_segment_active,
            )
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
        turn_id: int | None = None,
    ) -> TurnEvent:
        """Yield control to the partner and enter the listener role."""

        with self._lock:
            if self._current_role is not TurnRole.SPEAKER:
                msg = "Cannot pass turn when not the speaker"
                logger.debug(
                    "TurnManager.pass_turn rejected current_role=%s",
                    None if self._current_role is None else self._current_role.value,
                )
                raise RuntimeError(msg)
            if turn_id is None:
                resolved_turn_id = self._next_turn_id()
            else:
                resolved_turn_id = turn_id
                self._turn_id_counter = max(self._turn_id_counter, resolved_turn_id)
                self._last_turn_id = resolved_turn_id
            wall_here = wall_time if wall_time is not None else time.time()
            logger.debug(
                "TurnManager.pass_turn current_role=%s turn_id=%s run=%.3f phase=%.3f wall=%.3f",
                self._current_role.value,
                resolved_turn_id,
                run_time,
                phase_time,
                wall_here,
            )
            self._session.pass_turn(
                run_time=run_time,
                phase_time=phase_time,
                wall_time=wall_here,
                turn_id=resolved_turn_id,
            )
            payload = TurnPassPayload(
                wall_here,
                run_time,
                phase_time,
                turn_id=resolved_turn_id,
            )
            return self._transition_to(
                TurnRole.LISTENER, TurnEventSource.LOCAL_PASS, payload=payload
            )

    def take_turn(
        self,
        *,
        run_time: float,
        phase_time: float,
        wall_time: float | None = None,
        turn_id: int | None = None,
    ) -> TurnEvent:
        """Seize control from the partner and become the speaker."""

        with self._lock:
            if self._current_role is not TurnRole.LISTENER:
                msg = "Cannot take turn when not the listener"
                logger.debug(
                    "TurnManager.take_turn rejected current_role=%s",
                    None if self._current_role is None else self._current_role.value,
                )
                raise RuntimeError(msg)
            if turn_id is None:
                resolved_turn_id = self._next_turn_id()
            else:
                resolved_turn_id = turn_id
                self._turn_id_counter = max(self._turn_id_counter, resolved_turn_id)
                self._last_turn_id = resolved_turn_id
            wall_here = wall_time if wall_time is not None else time.time()
            logger.debug(
                "TurnManager.take_turn current_role=%s turn_id=%s run=%.3f phase=%.3f wall=%.3f",
                self._current_role.value,
                resolved_turn_id,
                run_time,
                phase_time,
                wall_here,
            )
            self._session.take_turn(
                run_time=run_time,
                phase_time=phase_time,
                wall_time=wall_here,
                turn_id=resolved_turn_id,
            )
            payload = TurnPassPayload(
                wall_here,
                run_time,
                phase_time,
                turn_id=resolved_turn_id,
            )
            return self._transition_to(
                TurnRole.SPEAKER, TurnEventSource.LOCAL_TAKE, payload=payload
            )

    def handle_control_event(
        self, msg_type: ControlMessageType, payload: object | None
    ) -> TurnEvent | None:
        """React to remote TURN_PASS messages (ignores other event types)."""

        if msg_type not in (
            ControlMessageType.TURN_PASS,
            ControlMessageType.TURN_TAKE,
        ):
            return None

        tp_payload = payload if isinstance(payload, TurnPassPayload) else None
        logger.debug(
            "TurnManager.handle_control_event msg_type=%s current_role=%s payload_turn_id=%s",
            msg_type.name,
            None if self._current_role is None else self._current_role.value,
            None if tp_payload is None else tp_payload.turn_id,
        )
        target_role = (
            TurnRole.SPEAKER
            if msg_type is ControlMessageType.TURN_PASS
            else TurnRole.LISTENER
        )
        source = (
            TurnEventSource.REMOTE_PASS
            if msg_type is ControlMessageType.TURN_PASS
            else TurnEventSource.REMOTE_TAKE
        )
        turn_id = tp_payload.turn_id if tp_payload else None

        with self._lock:
            if self._should_ignore_turn(turn_id):
                logger.debug(
                    "TurnManager.handle_control_event ignored stale msg_type=%s turn_id=%s last_turn_id=%s",
                    msg_type.name,
                    turn_id,
                    self._last_turn_id,
                )
                return None

            applied_id = self._record_remote_turn_id(turn_id)
            if tp_payload and tp_payload.turn_id != applied_id:
                tp_payload = replace(tp_payload, turn_id=applied_id)
            logger.debug(
                "TurnManager.handle_control_event applying msg_type=%s applied_turn_id=%s target_role=%s",
                msg_type.name,
                applied_id,
                target_role.value,
            )

            return self._transition_to(
                target_role,
                source,
                payload=tp_payload,
            )

    # ---- internal helpers ------------------------------------------
    def _transition_to(
        self,
        role: TurnRole,
        source: TurnEventSource,
        *,
        payload: TurnPassPayload | None = None,
    ) -> TurnEvent:
        previous_role = self._current_role
        if self._current_role is role and source is not TurnEventSource.INITIAL:
            logger.debug(
                "TurnManager._transition_to no-op previous_role=%s target_role=%s source=%s turn_id=%s",
                None if previous_role is None else previous_role.value,
                role.value,
                source.value,
                None if payload is None else payload.turn_id,
            )
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
        logger.debug(
            "TurnManager._transition_to previous_role=%s new_role=%s source=%s turn_id=%s local_active=%s remote_active=%s",
            None if previous_role is None else previous_role.value,
            role.value,
            source.value,
            None if payload is None else payload.turn_id,
            self._local_segment_active,
            self._remote_segment_active,
        )
        event = TurnEvent(role=role, source=source, payload=payload)
        if self._on_event:
            self._on_event(event)
        return event

    def _start_local_segment(self) -> None:
        if self._local_segment_active:
            logger.debug("TurnManager._start_local_segment skipped existing segment")
            return
        self._local_counter += 1
        label = self._format_label(self._local_label_template, self._local_counter)
        logger.debug("TurnManager._start_local_segment label=%s", label)
        self._session.start_segment(label, target="local")
        self._local_segment_active = True

    def _start_remote_segment(self) -> None:
        if self._remote_segment_active:
            logger.debug("TurnManager._start_remote_segment skipped existing segment")
            return
        self._remote_counter += 1
        label = self._format_label(self._remote_label_template, self._remote_counter)
        logger.debug("TurnManager._start_remote_segment label=%s", label)
        self._session.start_segment(label, target="remote")
        self._remote_segment_active = True

    def _end_local_segment(self) -> None:
        if not self._local_segment_active:
            logger.debug("TurnManager._end_local_segment skipped no active segment")
            return
        logger.debug("TurnManager._end_local_segment")
        self._session.stop_segment(target="local")
        self._local_segment_active = False

    def _end_remote_segment(self) -> None:
        if not self._remote_segment_active:
            logger.debug("TurnManager._end_remote_segment skipped no active segment")
            return
        logger.debug("TurnManager._end_remote_segment")
        self._session.stop_segment(target="remote")
        self._remote_segment_active = False

    def _should_ignore_turn(self, turn_id: int | None) -> bool:
        """Drop stale/duplicate transitions based on turn_id when provided."""

        return (
            turn_id is not None
            and self._last_turn_id is not None
            and turn_id <= self._last_turn_id
        )

    def _record_remote_turn_id(self, turn_id: int | None) -> int:
        """
        Update Lamport-style turn counter using a remote event id (or synthesize one).
        """

        if turn_id is None:
            self._turn_id_counter += 1
            applied = self._turn_id_counter
        else:
            self._turn_id_counter = max(self._turn_id_counter, turn_id)
            applied = self._turn_id_counter
        self._last_turn_id = applied
        return applied

    def _next_turn_id(self) -> int:
        """Allocate the next turn_id for locally initiated events."""

        self._turn_id_counter += 1
        self._last_turn_id = self._turn_id_counter
        return self._turn_id_counter

    @staticmethod
    def _format_label(template: str, index: int) -> str:
        try:
            return template.format(index=index, counter=index)
        except Exception:  # pragma: no cover - formatting fall back
            return template
