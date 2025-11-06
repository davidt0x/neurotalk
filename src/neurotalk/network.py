"""
UDP networking primitives for NeuroTalk.

This module encapsulates socket creation, STUN diagnostics, and the
hole-punching handshake that mirrors the behaviour in the CONV/DIAD scripts.
"""

from __future__ import annotations

import socket
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass

from .config import NetworkConfig
from .control import HANDSHAKE_HELLO, HANDSHAKE_HI_PARTNER, HANDSHAKE_READY


class NetworkError(RuntimeError):
    """Raised when sockets fail to initialize or handshake cannot complete."""


@dataclass
class SocketBundle:
    """Container for the three UDP sockets used during a session."""

    inbound: socket.socket
    outbound: socket.socket
    control: socket.socket
    remote: tuple[str, int, int, int]

    def close(self) -> None:
        for sock in (self.inbound, self.outbound, self.control):
            try:
                sock.close()
            except OSError:
                pass


def run_stun_diagnostics(servers: Iterable[str]) -> None:
    """
    Fire the `stunclient` CLI against the provided endpoints.

    Raises
    ------
    NetworkError
        If all probes fail.
    """

    success = False
    for host in servers:
        try:
            output = subprocess.check_output(
                ["stunclient", "--mode", "full", host],
                universal_newlines=True,
            )
            if "Behavior test: success" in output:
                success = True
                break
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    if servers and not success:
        raise NetworkError("STUN diagnostics failed for all provided servers")


def _create_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", port))
    return sock


def open_sockets(config: NetworkConfig) -> SocketBundle:
    """Create and bind the three UDP sockets according to the config."""

    in_port, out_port, ctrl_port = config.local_ports
    try:
        inbound = _create_socket(in_port)
        outbound = _create_socket(out_port)
        control = _create_socket(ctrl_port)
    except OSError as exc:
        raise NetworkError(f"Failed to bind UDP sockets: {exc}") from exc

    for sock in (inbound, outbound, control):
        sock.settimeout(1.0)

    return SocketBundle(
        inbound=inbound,
        outbound=outbound,
        control=control,
        remote=config.remote_hint,
    )


def hole_punch(
    bundle: SocketBundle, config: NetworkConfig
) -> tuple[str, int, int, int]:
    """
    Perform UDP hole punching to learn the partner's reachable ports.

    Returns
    -------
    (ip, port_in, port_out, port_comm) tuple describing the remote endpoint.
    """

    role = config.nat_role
    timeout = config.punch_timeout_s
    remote_ip, port_in, port_out, port_comm = bundle.remote

    if role == 0:
        # Passive role waits for incoming hello packets.
        start = time.monotonic()
        handshake_token = None
        while time.monotonic() - start < timeout:
            try:
                incoming_in, addr_in = bundle.inbound.recvfrom(1024)
                incoming_out, addr_out = bundle.outbound.recvfrom(1024)
                incoming_ctrl, addr_ctrl = bundle.control.recvfrom(1024)
            except TimeoutError:
                continue
            if incoming_in in (HANDSHAKE_HELLO, HANDSHAKE_HI_PARTNER) and (
                incoming_out == incoming_in and incoming_ctrl == incoming_in
            ):
                remote_ip = addr_in[0]
                port_out = addr_in[1]
                port_in = addr_out[1]
                port_comm = addr_ctrl[1]
                handshake_token = incoming_in
                break
        else:
            raise NetworkError("Timed out waiting for handshake initiation")

        # Confirm by echoing back.
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            bundle.inbound.sendto(handshake_token, (remote_ip, port_out))
            bundle.outbound.sendto(handshake_token, (remote_ip, port_in))
            bundle.control.sendto(handshake_token, (remote_ip, port_comm))
            try:
                incoming_ctrl, addr_ctrl = bundle.control.recvfrom(1024)
                if addr_ctrl[0] == remote_ip and incoming_ctrl in (
                    handshake_token,
                    HANDSHAKE_READY,
                ):
                    break
            except TimeoutError:
                continue
        else:
            raise NetworkError("Handshake confirmation never arrived")

    else:
        # Active role repeatedly probes the remote endpoint.
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            bundle.inbound.sendto(HANDSHAKE_HI_PARTNER, (remote_ip, port_out))
            bundle.outbound.sendto(HANDSHAKE_HI_PARTNER, (remote_ip, port_in))
            bundle.control.sendto(HANDSHAKE_HI_PARTNER, (remote_ip, port_comm))
            try:
                incoming_in, addr_in = bundle.inbound.recvfrom(1024)
                incoming_out, addr_out = bundle.outbound.recvfrom(1024)
                incoming_ctrl, addr_ctrl = bundle.control.recvfrom(1024)
            except TimeoutError:
                continue
            if incoming_in in (HANDSHAKE_HI_PARTNER, HANDSHAKE_HELLO) and (
                incoming_out == incoming_in and incoming_ctrl == incoming_in
            ):
                remote_ip = addr_ctrl[0]
                port_out = addr_in[1]
                port_in = addr_out[1]
                port_comm = addr_ctrl[1]
                break
        else:
            raise NetworkError("Handshake probes were not acknowledged")

        # Final ready message.
        for _ in range(5):
            bundle.inbound.sendto(HANDSHAKE_READY, (remote_ip, port_out))
            bundle.outbound.sendto(HANDSHAKE_READY, (remote_ip, port_in))
            bundle.control.sendto(HANDSHAKE_READY, (remote_ip, port_comm))

    bundle.remote = (remote_ip, port_in, port_out, port_comm)
    return bundle.remote


def configure_nonblocking(bundle: SocketBundle, *, recv_timeout: float = 0.1) -> None:
    """
    Apply the non-blocking settings used post-handshake.
    """

    bundle.outbound.settimeout(0.0)  # non-blocking send
    bundle.inbound.settimeout(recv_timeout)
    bundle.control.settimeout(recv_timeout)


def flush_pending(bundle: SocketBundle, duration: float = 1.0) -> None:
    """
    Drain any datagrams left in the buffers after the handshake phase.
    """

    deadline = time.monotonic() + duration
    sockets = (bundle.inbound, bundle.outbound, bundle.control)
    while time.monotonic() < deadline:
        for sock in sockets:
            try:
                sock.recv(1024)
            except TimeoutError:
                continue
            except BlockingIOError:
                continue
