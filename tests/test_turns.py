from __future__ import annotations

from neurotalk.control import ControlMessageType, TurnPassPayload
from neurotalk.turns import TurnEventSource, TurnManager, TurnRole


class StubSession:
    def __init__(self) -> None:
        self.transmit_enabled: bool | None = None
        self.receive_enabled: bool | None = None
        self.started_segments: list[tuple[str, str]] = []
        self.stopped_segments: list[str] = []
        self.pass_calls: list[tuple[float, float, float | None]] = []

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
    ) -> None:
        self.pass_calls.append((run_time, phase_time, wall_time))


def test_turn_manager_pass_and_remote_handshake() -> None:
    session = StubSession()
    manager = TurnManager(session)

    manager.start(TurnRole.SPEAKER)
    assert session.started_segments == [("local_turn01", "local")]
    assert session.transmit_enabled is True
    assert session.receive_enabled is False

    manager.pass_turn(run_time=1.5, phase_time=0.5, wall_time=10.0)
    assert session.pass_calls == [(1.5, 0.5, 10.0)]
    assert session.started_segments[-1] == ("remote_turn01", "remote")
    assert session.transmit_enabled is False
    assert session.receive_enabled is True

    payload = TurnPassPayload(11.0, 2.0, 1.0)
    event = manager.handle_control_event(ControlMessageType.TURN_PASS, payload)
    assert event is not None
    assert event.source is TurnEventSource.REMOTE_PASS
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
    assert session.transmit_enabled is False
    assert session.receive_enabled is False
