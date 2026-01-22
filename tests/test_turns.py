from __future__ import annotations

from neurotalk.control import ControlMessageType, TurnPassPayload
from neurotalk.turns import TurnEventSource, TurnManager, TurnRole


class StubSession:
    def __init__(self) -> None:
        self.transmit_enabled: bool | None = None
        self.receive_enabled: bool | None = None
        self.started_segments: list[tuple[str, str]] = []
        self.stopped_segments: list[str] = []
        self.pass_calls: list[tuple[float, float, float | None, int | None]] = []
        self.take_calls: list[tuple[float, float, float | None, int | None]] = []

    def enable_transmit(self, enabled: bool) -> None:
        self.transmit_enabled = enabled

    def enable_receive(self, enabled: bool) -> None:
        self.receive_enabled = enabled

    def start_segment(
        self,
        label: str,
        *,
        metadata: dict[str, object] | None = None,
        target: str | tuple[str, ...] = "both",
    ) -> None:
        self.started_segments.append((label, str(target)))

    def stop_segment(self, *, target: str | tuple[str, ...] = "both") -> None:
        self.stopped_segments.append(str(target))

    def pass_turn(
        self,
        *,
        run_time: float,
        phase_time: float,
        wall_time: float | None = None,
        turn_id: int | None = None,
    ) -> None:
        self.pass_calls.append((run_time, phase_time, wall_time, turn_id))

    def take_turn(
        self,
        *,
        run_time: float,
        phase_time: float,
        wall_time: float | None = None,
        turn_id: int | None = None,
    ) -> None:
        self.take_calls.append((run_time, phase_time, wall_time, turn_id))


def assert_flag(value: bool | None, expected: bool) -> None:
    assert value is expected


def test_turn_manager_pass_and_remote_handshake() -> None:
    session = StubSession()
    manager = TurnManager(session)

    manager.start(TurnRole.SPEAKER)
    assert session.started_segments == [("local_turn01", "local")]
    assert_flag(session.transmit_enabled, True)
    assert_flag(session.receive_enabled, False)

    manager.pass_turn(run_time=1.5, phase_time=0.5, wall_time=10.0)
    assert session.pass_calls == [(1.5, 0.5, 10.0, 1)]
    assert session.started_segments[-1] == ("remote_turn01", "remote")
    assert_flag(session.transmit_enabled, False)
    assert_flag(session.receive_enabled, True)

    payload = TurnPassPayload(11.0, 2.0, 1.0)
    event = manager.handle_control_event(ControlMessageType.TURN_PASS, payload)
    assert event is not None
    assert event.source is TurnEventSource.REMOTE_PASS
    assert isinstance(event.payload, TurnPassPayload)
    assert event.payload.turn_id == 2
    assert session.started_segments[-1] == ("local_turn02", "local")
    assert session.transmit_enabled is True
    assert session.receive_enabled is False


def test_turn_manager_stop_closes_segments() -> None:
    session = StubSession()
    manager = TurnManager(session)
    manager.start(TurnRole.LISTENER)
    assert session.started_segments == [("remote_turn01", "remote")]

    manager.stop()
    # stop() should close whichever segments are open and mute both directions
    assert "remote" in session.stopped_segments
    assert_flag(session.transmit_enabled, False)
    assert_flag(session.receive_enabled, False)


def test_turn_manager_local_take_and_segments() -> None:
    session = StubSession()
    manager = TurnManager(session)
    manager.start(TurnRole.LISTENER)
    assert session.started_segments == [("remote_turn01", "remote")]

    event = manager.take_turn(run_time=1.0, phase_time=0.2, wall_time=5.0)
    assert event.source is TurnEventSource.LOCAL_TAKE
    assert session.take_calls == [(1.0, 0.2, 5.0, 1)]
    assert session.started_segments[-1] == ("local_turn01", "local")
    assert_flag(session.transmit_enabled, True)
    assert_flag(session.receive_enabled, False)


def test_turn_manager_remote_take_deduplication() -> None:
    session = StubSession()
    manager = TurnManager(session)
    manager.start(TurnRole.SPEAKER)
    assert session.started_segments == [("local_turn01", "local")]

    payload_take = TurnPassPayload(1.0, 0.2, 0.2, turn_id=1)
    event_take = manager.handle_control_event(
        ControlMessageType.TURN_TAKE, payload_take
    )
    assert event_take is not None
    assert event_take.source is TurnEventSource.REMOTE_TAKE
    assert session.started_segments[-1] == ("remote_turn01", "remote")
    assert_flag(session.transmit_enabled, False)
    assert_flag(session.receive_enabled, True)

    duplicate = manager.handle_control_event(ControlMessageType.TURN_TAKE, payload_take)
    assert duplicate is None

    payload_pass = TurnPassPayload(2.0, 0.4, 0.4, turn_id=2)
    event_pass = manager.handle_control_event(
        ControlMessageType.TURN_PASS, payload_pass
    )
    assert event_pass is not None
    assert event_pass.source is TurnEventSource.REMOTE_PASS
    assert session.started_segments[-1] == ("local_turn02", "local")
    assert_flag(session.transmit_enabled, True)
    assert_flag(session.receive_enabled, False)
