from __future__ import annotations

import socket
import threading

from neurotalk.config import SessionConfig, NetworkConfig
from neurotalk.network import SocketBundle
from neurotalk.session import ConversationSession
def test_run_debug_mode_handshake():
    inbound_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    outbound_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    control_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    inbound_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    outbound_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    control_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    inbound_a.bind(("127.0.0.1", 0))
    outbound_a.bind(("127.0.0.1", 0))
    control_a.bind(("127.0.0.1", 0))

    inbound_b.bind(("127.0.0.1", 0))
    outbound_b.bind(("127.0.0.1", 0))
    control_b.bind(("127.0.0.1", 0))

    control_a.settimeout(0.1)
    control_b.settimeout(0.1)

    bundle_a = SocketBundle(
        inbound=inbound_a,
        outbound=outbound_a,
        control=control_a,
        remote=(
            "127.0.0.1",
            inbound_b.getsockname()[1],
            outbound_b.getsockname()[1],
            control_b.getsockname()[1],
        ),
    )
    bundle_b = SocketBundle(
        inbound=inbound_b,
        outbound=outbound_b,
        control=control_b,
        remote=(
            "127.0.0.1",
            inbound_a.getsockname()[1],
            outbound_a.getsockname()[1],
            control_a.getsockname()[1],
        ),
    )

    session_a = ConversationSession(SessionConfig(participant_id="001", role="A", network=NetworkConfig()))
    session_b = ConversationSession(SessionConfig(participant_id="002", role="B", network=NetworkConfig()))

    session_a.state.sockets = bundle_a
    session_b.state.sockets = bundle_b

    session_a._start_control_loop()
    session_b._start_control_loop()

    errors = []

    def run_session(session: ConversationSession):
        try:
            session.run_debug_mode(ready_timeout=2.0, duration=0.5)
        except Exception as exc:  # pragma: no cover - should not happen
            errors.append(exc)

    thread_a = threading.Thread(target=run_session, args=(session_a,))
    thread_b = threading.Thread(target=run_session, args=(session_b,))

    thread_a.start()
    thread_b.start()

    thread_a.join()
    thread_b.join()

    session_a._stop_control_loop()
    session_b._stop_control_loop()

    bundle_a.inbound.close()
    bundle_a.outbound.close()
    bundle_a.control.close()
    bundle_b.inbound.close()
    bundle_b.outbound.close()
    bundle_b.control.close()

    assert not errors
