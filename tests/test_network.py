from __future__ import annotations

import socket
import threading

import pytest

from neurotalk.config import NetworkConfig
from neurotalk.network import (
    configure_nonblocking,
    flush_pending,
    hole_punch,
    open_sockets,
)


def _make_config(local_ports, remote_ports, nat_role):
    return NetworkConfig(
        local_ports=local_ports,
        remote_hint=("127.0.0.1", *remote_ports),
        nat_role=nat_role,
        punch_timeout_s=5.0,
        stun_servers=(),
    )


def _ensure_free_ports(base: int, count: int) -> tuple[int, ...]:
    """
    Try binding to sequential ports to ensure they're available. Useful for tests.
    """

    sockets = []
    ports = []
    try:
        for offset in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", base + offset))
            ports.append(base + offset)
            sockets.append(sock)
    finally:
        for sock in sockets:
            sock.close()
    return tuple(ports)


def test_hole_punch_loopback():
    """Run the full hole-punch handshake between two loopback endpoints."""

    # Reserve distinct port ranges for each side to avoid collisions.
    ports_a = _ensure_free_ports(42000, 3)
    ports_b = _ensure_free_ports(42100, 3)

    cfg_a = _make_config(ports_a, ports_b, nat_role=1)
    cfg_b = _make_config(ports_b, ports_a, nat_role=0)

    bundle_a = open_sockets(cfg_a)
    bundle_b = open_sockets(cfg_b)

    results = {}

    def punch_a():
        results["a"] = hole_punch(bundle_a, cfg_a)

    def punch_b():
        results["b"] = hole_punch(bundle_b, cfg_b)

    t_a = threading.Thread(target=punch_a, daemon=True)
    t_b = threading.Thread(target=punch_b, daemon=True)
    t_a.start()
    t_b.start()
    t_a.join(timeout=2.0)
    t_b.join(timeout=2.0)

    if "a" not in results or "b" not in results:
        bundle_a.close()
        bundle_b.close()
        pytest.fail("Hole punch handshake did not complete")

    assert results["a"][0] == "127.0.0.1"
    assert results["b"][0] == "127.0.0.1"

    # The remote ports observed by each bundle should match the other's locals (possibly swapped).
    assert results["a"][1:] == ports_b
    assert results["b"][1:] == ports_a

    configure_nonblocking(bundle_a)
    configure_nonblocking(bundle_b)
    flush_pending(bundle_a)
    flush_pending(bundle_b)

    bundle_a.close()
    bundle_b.close()
