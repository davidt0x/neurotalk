from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Iterable

from aiortc import (
    RTCConfiguration,
    RTCDataChannel,
    MediaStreamTrack,
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCIceServer,
)
from aiortc.contrib.signaling import candidate_from_sdp, candidate_to_sdp


SignalSender = Callable[[str, dict[str, Any]], Awaitable[None]]
TrackHandler = Callable[[MediaStreamTrack], Awaitable[None] | None]


def _build_rtc_configuration(ice_servers: Iterable[dict[str, Any]]) -> RTCConfiguration:
    servers = []
    for entry in ice_servers:
        urls = entry.get("urls")
        if not urls:
            continue
        servers.append(RTCIceServer(urls=urls))
    return RTCConfiguration(iceServers=servers)


def _candidate_payload(candidate: RTCIceCandidate) -> dict[str, Any]:
    return {
        "candidate": candidate_to_sdp(candidate),
        "sdpMid": candidate.sdpMid,
        "sdpMLineIndex": candidate.sdpMLineIndex,
    }


def _candidate_from_payload(payload: dict[str, Any]) -> RTCIceCandidate:
    candidate_sdp = payload["candidate"]
    candidate = candidate_from_sdp(candidate_sdp)
    candidate.sdpMid = payload.get("sdpMid")
    candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
    return candidate


class PeerConnectionController:
    """Wrapper around aiortc's RTCPeerConnection handling offer/answer and ICE propagation."""

    def __init__(
        self,
        ice_servers: Iterable[dict[str, Any]],
        send_signal: SignalSender,
        initiator: bool = False,
        on_track: TrackHandler | None = None,
    ) -> None:
        self._pc = RTCPeerConnection(configuration=_build_rtc_configuration(ice_servers))
        self._send_signal = send_signal
        self._initiator = initiator
        self._connected = asyncio.Event()
        self._remote_description_set = asyncio.Event()
        self._data_channel = None
        self._on_track = on_track

        if self._initiator:
            self._data_channel = self._pc.createDataChannel("neurotalk-control")

            @self._data_channel.on("open")
            def _on_open() -> None:
                self._connected.set()

        @self._pc.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self._data_channel = channel
            @channel.on("open")
            def _on_open() -> None:
                self._connected.set()

        @self._pc.on("icecandidate")
        async def on_icecandidate(event) -> None:
            candidate = event
            if candidate:
                await self._send_signal("webrtc-candidate", _candidate_payload(candidate))

        @self._pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if self._pc.connectionState in ("connected", "completed"):
                self._connected.set()

        @self._pc.on("iceconnectionstatechange")
        async def on_ice_connection_state_change() -> None:
            if self._pc.iceConnectionState in ("connected", "completed"):
                self._connected.set()

        @self._pc.on("track")
        def on_track_handler(track: MediaStreamTrack) -> None:
            if self._on_track:
                result = self._on_track(track)
                if inspect.isawaitable(result):
                    asyncio.ensure_future(result)

    @property
    def connection(self) -> RTCPeerConnection:
        return self._pc

    @property
    def initiator(self) -> bool:
        return self._initiator

    async def close(self) -> None:
        await self._pc.close()

    async def start(self) -> None:
        if self._initiator:
            await self._create_and_send_offer()

    def add_audio_track(self, track: MediaStreamTrack) -> None:
        self._pc.addTrack(track)

    async def _create_and_send_offer(self) -> None:
        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        description = self._pc.localDescription
        if description is None:
            return
        await self._send_signal(
            "webrtc-offer",
            {"sdp": description.sdp, "type": description.type},
        )

    async def handle_offer(self, payload: dict[str, Any]) -> None:
        description = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
        await self._pc.setRemoteDescription(description)
        self._remote_description_set.set()
        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)
        local = self._pc.localDescription
        if local is None:
            return
        await self._send_signal(
            "webrtc-answer",
            {"sdp": local.sdp, "type": local.type},
        )

    async def handle_answer(self, payload: dict[str, Any]) -> None:
        description = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
        await self._pc.setRemoteDescription(description)
        self._remote_description_set.set()

    async def handle_candidate(self, payload: dict[str, Any]) -> None:
        candidate = _candidate_from_payload(payload)
        if not self._remote_description_set.is_set():
            await self._remote_description_set.wait()
        await self._pc.addIceCandidate(candidate)

    async def wait_connected(self) -> None:
        await self._connected.wait()
