from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any, Iterable, Optional

from .audio import AudioPipeline
from .config import SessionConfig
from .events import ControlPayload, EventHandlers, SessionState
from .exceptions import NeuroTalkError, SessionStateError
from .signaling import SignalingClient, SignalingMessage
from .sync import (
    SYNC_MESSAGE_TYPE,
    SyncAction,
    SyncInstruction,
    decode_sync_instruction,
    encode_sync_instruction,
)
from .webrtc import PeerConnectionController


class Session:
    """Skeleton session implementation to be extended with WebRTC transport."""

    def __init__(self, config: SessionConfig, handlers: EventHandlers | None = None) -> None:
        self._config = config
        self._handlers = handlers or EventHandlers()
        self._state = SessionState.IDLE
        self._state_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        self._signaling: SignalingClient | None = None
        self._signaling_task: asyncio.Task[None] | None = None
        self._webrtc: PeerConnectionController | None = None
        self._audio: AudioPipeline | None = None
        self._sync_lock = asyncio.Lock()
        self._sync_ready_event = asyncio.Event()
        self._sync_ack_event = asyncio.Event()
        self._sync_initiated = False
        self._start_time: Optional[float] = None

    @property
    def config(self) -> SessionConfig:
        return self._config

    @property
    def handlers(self) -> EventHandlers:
        return self._handlers

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    async def __aenter__(self) -> "Session":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        await self.close()

    async def connect(self) -> None:
        """Prepare resources for the session."""
        await self._assert_state({SessionState.IDLE})
        await self._transition(SessionState.CONNECTING)
        try:
            await self._initialise_transport()
        except Exception as exc:  # pragma: no cover - future transport errors
            await self._transition(SessionState.ERROR)
            await self._handlers.emit_error(exc)
            raise
        await self._transition(SessionState.CONNECTED)

    async def close(self) -> None:
        """Close the session and release resources."""
        if self.closed:
            return
        if self._signaling_task:
            self._signaling_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._signaling_task
            self._signaling_task = None

        if self._signaling:
            await self._signaling.close()
            self._signaling = None
        if self._webrtc:
            await self._webrtc.close()
            self._webrtc = None
        if self._audio:
            await self._audio.close()
            self._audio = None

        # reset sync state
        self._sync_ready_event = asyncio.Event()
        self._sync_ack_event = asyncio.Event()
        self._sync_initiated = False
        self._start_time = None

        await self._transition(SessionState.CLOSED)
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def sync_start(self, delay_seconds: float) -> None:
        """Block until the agreed start time is reached."""
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative.")
        await self._assert_state({SessionState.CONNECTED, SessionState.READY})
        async with self._sync_lock:
            if not self._signaling:
                raise NeuroTalkError("Signaling connection is not available.")
            # reset sync events if we are starting a new negotiation
            if not self._sync_initiated and not self._sync_ready_event.is_set() and self._start_time is None:
                self._sync_ready_event = asyncio.Event()
                self._sync_ack_event = asyncio.Event()

            if self._start_time is None:
                self._start_time = time.time() + delay_seconds
                self._sync_initiated = True
                instruction = SyncInstruction(action=SyncAction.ANNOUNCE, start_time=self._start_time)
                await self._signaling.send(
                    SignalingMessage(
                        type=SYNC_MESSAGE_TYPE,
                        payload=encode_sync_instruction(instruction),
                        sender=self._config.peer_id,
                    ),
                )

        if self._sync_initiated:
            await self._sync_ack_event.wait()

        await self._sync_ready_event.wait()
        async with self._sync_lock:
            start_time = self._start_time
        if start_time is None:
            raise SessionStateError("Synchronization failed to establish a start time.")

        if self._webrtc:
            try:
                await asyncio.wait_for(self._webrtc.wait_connected(), timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover - best effort
                pass

        remaining = max(0.0, start_time - time.time())
        if remaining:
            await asyncio.sleep(remaining)
        await self._transition(SessionState.READY)

    async def send_control(self, payload: ControlPayload) -> None:
        """Send a control payload to the remote peer."""
        await self._assert_state({SessionState.CONNECTED, SessionState.READY})
        if not self._signaling:
            raise NeuroTalkError("Signaling connection is not available.")
        message = SignalingMessage(type="control", payload=payload, sender=self._config.peer_id)
        await self._signaling.send(message)

    async def handle_control(self, payload: ControlPayload) -> None:
        """Invoke the control callback for an incoming payload."""
        await self._handlers.emit_control(payload)

    async def _initialise_transport(self) -> None:
        """Placeholder for upcoming WebRTC initialisation logic."""
        self._signaling = SignalingClient(self._config)
        await self._signaling.connect()
        self._signaling_task = asyncio.create_task(self._signaling_loop())
        self._audio = AudioPipeline(self._config.audio, self._config.recording)
        self._webrtc = PeerConnectionController(
            ice_servers=self._config.ice_servers,
            send_signal=self._send_webrtc_signal,
            initiator=self._config.initiator,
            on_track=self._handle_peer_track,
        )
        if self._audio.local_track:
            self._webrtc.add_audio_track(self._audio.local_track)
        await self._webrtc.start()

    async def _transition(self, state: SessionState) -> None:
        async with self._state_lock:
            if state is self._state:
                return
            self._state = state
        await self._handlers.emit_state(state)

    async def _assert_state(self, allowed: Iterable[SessionState]) -> None:
        async with self._state_lock:
            if self._state not in allowed:
                allowed_states = ", ".join(state.value for state in allowed)
                raise SessionStateError(f"Invalid state {self._state.value!r}; expected one of {allowed_states}.")

    async def _signaling_loop(self) -> None:
        assert self._signaling is not None
        try:
            while True:
                message = await self._signaling.receive()
                if message.type == "control":
                    await self.handle_control(message.payload)
                elif message.type == SYNC_MESSAGE_TYPE:
                    await self._handle_sync_message(message.payload)
                elif message.type == "webrtc-offer":
                    await self._handle_webrtc_offer(message.payload)
                elif message.type == "webrtc-answer":
                    await self._handle_webrtc_answer(message.payload)
                elif message.type == "webrtc-candidate":
                    await self._handle_webrtc_candidate(message.payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handlers.emit_error(exc)
            await self._transition(SessionState.ERROR)

    async def _handle_sync_message(self, payload: ControlPayload) -> None:
        try:
            instruction = decode_sync_instruction(payload)
        except ValueError as exc:
            await self._handlers.emit_error(exc)
            return

        if instruction.action is SyncAction.ANNOUNCE:
            await self._process_sync_announce(instruction.start_time)
        elif instruction.action is SyncAction.ACK:
            await self._process_sync_ack(instruction.start_time)

    async def _process_sync_announce(self, start_time: float) -> None:
        async with self._sync_lock:
            if self._start_time is None:
                self._start_time = start_time
            else:
                self._start_time = max(self._start_time, start_time)
            final_start = self._start_time
            self._sync_initiated = False
            self._sync_ready_event.set()

        if self._signaling and final_start is not None:
            instruction = SyncInstruction(action=SyncAction.ACK, start_time=final_start)
            await self._signaling.send(
                SignalingMessage(
                    type=SYNC_MESSAGE_TYPE,
                    payload=encode_sync_instruction(instruction),
                    sender=self._config.peer_id,
                ),
            )

    async def _process_sync_ack(self, start_time: float) -> None:
        async with self._sync_lock:
            if self._start_time is None:
                self._start_time = start_time
            else:
                self._start_time = max(self._start_time, start_time)
            self._sync_initiated = False
            self._sync_ready_event.set()
        self._sync_ack_event.set()

    async def _send_webrtc_signal(self, message_type: str, payload: dict[str, Any]) -> None:
        if not self._signaling:
            raise NeuroTalkError("Signaling connection is not available.")
        await self._signaling.send(
            SignalingMessage(type=message_type, payload=payload, sender=self._config.peer_id),
        )

    async def _handle_webrtc_offer(self, payload: dict[str, Any]) -> None:
        if not self._webrtc:
            return
        await self._webrtc.handle_offer(payload)

    async def _handle_webrtc_answer(self, payload: dict[str, Any]) -> None:
        if not self._webrtc:
            return
        await self._webrtc.handle_answer(payload)

    async def _handle_webrtc_candidate(self, payload: dict[str, Any]) -> None:
        if not self._webrtc:
            return
        await self._webrtc.handle_candidate(payload)

    async def wait_transport_ready(self) -> None:
        if self._webrtc:
            try:
                await asyncio.wait_for(self._webrtc.wait_connected(), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover - best effort
                pass

    async def _handle_peer_track(self, track) -> None:
        if self._audio:
            await self._audio.handle_remote_track(track)
