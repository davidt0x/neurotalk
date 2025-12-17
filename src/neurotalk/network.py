"""
UDP networking primitives for NeuroTalk.

This module encapsulates socket creation, STUN diagnostics, and the
hole-punching handshake that mirrors the behaviour in the CONV/DIAD scripts.
"""

from __future__ import annotations

import contextlib
import logging
import socket
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass

from .config import NetworkConfig
from .control import HANDSHAKE_HELLO, HANDSHAKE_HI_PARTNER, HANDSHAKE_READY


class NetworkError(RuntimeError):
    """Raised when sockets fail to initialize or handshake cannot complete."""


logger = logging.getLogger(__name__)


@dataclass
class SocketBundle:
    """Container for the three UDP sockets used during a session."""

    inbound: socket.socket
    outbound: socket.socket
    control: socket.socket
    remote: tuple[str, int, int, int]

    def close(self) -> None:
        for sock in (self.inbound, self.outbound, self.control):
            with contextlib.suppress(OSError):
                sock.close()


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
        logger.info("[network] Probing STUN server %s", host)
        try:
            output = subprocess.check_output(
                ["stunclient", "--mode", "full", host],
                universal_newlines=True,
            )
            if "Behavior test: success" in output:
                success = True
                logger.info("[network] STUN success via %s", host)
                break
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    if servers and not success:
        msg = "STUN diagnostics failed for all provided servers"
        raise NetworkError(msg)


def _create_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", port))
    _disable_udp_connreset(sock)
    return sock


def _disable_udp_connreset(sock: socket.socket) -> None:
    """
    On Windows, disable ICMP 'connection reset' exceptions for UDP sockets.
    """

    if hasattr(socket, "SIO_UDP_CONNRESET"):
        try:
            sock.ioctl(socket.SIO_UDP_CONNRESET, 0)  # type: ignore[attr-defined]
        except OSError as exc:  # pragma: no cover - Windows-only behaviour
            logger.debug("Failed to disable UDP_CONNRESET: %s", exc, exc_info=exc)


def open_sockets(config: NetworkConfig) -> SocketBundle:
    """Create and bind the three UDP sockets according to the config."""

    in_port, out_port, ctrl_port = config.local_ports
    logger.info(
        "[network] Binding UDP sockets local_ports=(%s,%s,%s)", in_port, out_port, ctrl_port
    )
    try:
        inbound = _create_socket(in_port)
        outbound = _create_socket(out_port)
        control = _create_socket(ctrl_port)
    except OSError as exc:
        msg = f"Failed to bind UDP sockets: {exc}"
        raise NetworkError(msg) from exc

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
    if isinstance(role, str):
        role = role.lower()
    timeout = config.punch_timeout_s
    remote_ip, port_in, port_out, port_comm = bundle.remote
    logger.info(
        "[network] Starting NAT handshake role=%s remote_hint=%s timeout=%.1fs",
        role,
        bundle.remote,
        timeout,
    )

    if role == "auto":
        # Symmetric probing so either side can start first.
        handshake_token = HANDSHAKE_HI_PARTNER
        start = time.monotonic()
        seen: dict[str, tuple[bytes, tuple[str, int]]] = {}
        recv_counts = {"inbound": 0, "outbound": 0, "control": 0}
        send_count = 0
        while time.monotonic() - start < timeout:
            bundle.inbound.sendto(handshake_token, (remote_ip, port_out))
            bundle.outbound.sendto(handshake_token, (remote_ip, port_in))
            bundle.control.sendto(handshake_token, (remote_ip, port_comm))
            send_count += 1

            for name, sock in (
                ("inbound", bundle.inbound),
                ("outbound", bundle.outbound),
                ("control", bundle.control),
            ):
                try:
                    data, addr = sock.recvfrom(1024)
                except (TimeoutError, ConnectionResetError, OSError):
                    continue
                if data not in (
                    HANDSHAKE_HELLO,
                    HANDSHAKE_HI_PARTNER,
                    HANDSHAKE_READY,
                ):
                    continue
                seen[name] = (data, addr)
                recv_counts[name] += 1
                if name == "inbound":
                    remote_ip, port_out = addr[0], addr[1]
                elif name == "outbound":
                    remote_ip, port_in = addr[0], addr[1]
                elif name == "control":
                    remote_ip, port_comm = addr[0], addr[1]
                logger.debug(
                    "[network] Auto handshake received %r on %s from %s:%s",
                    data,
                    name,
                    addr[0],
                    addr[1],
                )

            if set(seen.keys()) == {"inbound", "outbound", "control"}:
                tokens = {payload for payload, _ in seen.values()}
                if len(tokens) == 1 or (
                    HANDSHAKE_READY in tokens and len(tokens) <= 2
                ):
                    incoming_in, addr_in = seen["inbound"]
                    _, addr_out = seen["outbound"]
                    _, addr_ctrl = seen["control"]
                    logger.info(
                        "[network] Auto handshake saw partner ip=%s in=%s out=%s control=%s token=%r",
                        addr_in[0],
                        addr_out[1],
                        addr_in[1],
                        addr_ctrl[1],
                        incoming_in,
                    )
                    remote_ip = addr_in[0]
                    port_out = addr_in[1]
                    port_in = addr_out[1]
                    port_comm = addr_ctrl[1]
                    handshake_token = (
                        HANDSHAKE_READY if HANDSHAKE_READY in tokens else incoming_in
                    )
                    break
        else:
            logger.warning(
                "[network] Auto handshake timed out after %s sends; received counts=%s",
                send_count,
                recv_counts,
            )
            if seen:
                tokens = {payload for payload, _ in seen.values()}
                logger.warning(
                    "[network] Proceeding with best-effort remote derived from partial handshake: %s",
                    seen,
                )
                any_payload, any_addr = next(iter(seen.values()))
                remote_ip = any_addr[0]
                if "inbound" in seen:
                    _, addr = seen["inbound"]
                    port_out = addr[1]
                if "outbound" in seen:
                    _, addr = seen["outbound"]
                    port_in = addr[1]
                if "control" in seen:
                    _, addr = seen["control"]
                    port_comm = addr[1]
                handshake_token = HANDSHAKE_READY if HANDSHAKE_READY in tokens else any_payload
            else:
                msg = "Timed out waiting for partner during auto handshake"
                raise NetworkError(msg)

        for _ in range(10):
            bundle.inbound.sendto(handshake_token, (remote_ip, port_out))
            bundle.outbound.sendto(handshake_token, (remote_ip, port_in))
            bundle.control.sendto(handshake_token, (remote_ip, port_comm))
        for _ in range(20):
            bundle.inbound.sendto(HANDSHAKE_READY, (remote_ip, port_out))
            bundle.outbound.sendto(HANDSHAKE_READY, (remote_ip, port_in))
            bundle.control.sendto(HANDSHAKE_READY, (remote_ip, port_comm))

    elif role == 0:
        # Passive role waits for incoming hello packets.
        start = time.monotonic()
        handshake_token = None
        while time.monotonic() - start < timeout:
            try:
                incoming_in, addr_in = bundle.inbound.recvfrom(1024)
                incoming_out, addr_out = bundle.outbound.recvfrom(1024)
                incoming_ctrl, addr_ctrl = bundle.control.recvfrom(1024)
            except (TimeoutError, ConnectionResetError, OSError):
                continue
            if incoming_in in (HANDSHAKE_HELLO, HANDSHAKE_HI_PARTNER) and (
                incoming_out == incoming_in and incoming_ctrl == incoming_in
            ):
                logger.info(
                    "[network] Passive handshake saw partner ip=%s in=%s out=%s control=%s token=%r",
                    addr_in[0],
                    addr_out[1],
                    addr_in[1],
                    addr_ctrl[1],
                    incoming_in,
                )
                remote_ip = addr_in[0]
                port_out = addr_in[1]
                port_in = addr_out[1]
                port_comm = addr_ctrl[1]
                handshake_token = incoming_in
                break
        else:
            msg = "Timed out waiting for handshake initiation"
            raise NetworkError(msg)

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
            msg = "Handshake confirmation never arrived"
            raise NetworkError(msg)

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
            except (TimeoutError, ConnectionResetError, OSError):
                continue
            if incoming_in in (HANDSHAKE_HI_PARTNER, HANDSHAKE_HELLO) and (
                incoming_out == incoming_in and incoming_ctrl == incoming_in
            ):
                logger.info(
                    "[network] Active handshake got response ip=%s in=%s out=%s control=%s token=%r",
                    addr_in[0],
                    addr_out[1],
                    addr_in[1],
                    addr_ctrl[1],
                    incoming_in,
                )
                remote_ip = addr_ctrl[0]
                port_out = addr_in[1]
                port_in = addr_out[1]
                port_comm = addr_ctrl[1]
                break
        else:
            msg = "Handshake probes were not acknowledged"
            raise NetworkError(msg)

        # Final ready message.
        for _ in range(5):
            bundle.inbound.sendto(HANDSHAKE_READY, (remote_ip, port_out))
            bundle.outbound.sendto(HANDSHAKE_READY, (remote_ip, port_in))
            bundle.control.sendto(HANDSHAKE_READY, (remote_ip, port_comm))

    bundle.remote = (remote_ip, port_in, port_out, port_comm)
    logger.info("[network] Handshake complete remote=%s", bundle.remote)
    return bundle.remote


def configure_nonblocking(bundle: SocketBundle, *, recv_timeout: float = 0.1) -> None:
    """
    Apply the non-blocking settings used post-handshake.
    """

    logger.debug(
        "[network] Configuring non-blocking sockets recv_timeout=%.2fs", recv_timeout
    )
    bundle.outbound.settimeout(0.0)  # non-blocking send
    bundle.inbound.settimeout(recv_timeout)
    bundle.control.settimeout(recv_timeout)


def flush_pending(bundle: SocketBundle, duration: float = 1.0) -> None:
    """
    Drain any datagrams left in the buffers after the handshake phase.
    """

    deadline = time.monotonic() + duration
    sockets = (bundle.inbound, bundle.outbound, bundle.control)
    logger.debug("[network] Flushing pending datagrams for %.1fs", duration)
    while time.monotonic() < deadline:
        for sock in sockets:
            try:
                sock.recv(1024)
            except TimeoutError:
                continue
            except BlockingIOError:
                continue
