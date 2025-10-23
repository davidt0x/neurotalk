# NeuroTalk documentation

NeuroTalk is under active development. This documentation site will expand as core features land.

## Current status

- Session configuration (`neurotalk.config`) provides dataclasses for signaling, audio devices, and recording outputs.
- Event handlers (`neurotalk.events`) define the callback API for control messages and session state updates.
- Signaling utilities (`neurotalk.signaling`) offer a WebSocket client and lightweight relay server for room-based message exchange.
- Synchronisation helpers (`neurotalk.sync`) define the announce/ack protocol used to align start times between peers.
- Audio helpers (`neurotalk.audio`) manage microphone capture and speaker playback, falling back to silent/blackhole transports when hardware is unavailable.
- WebRTC utilities (`neurotalk.webrtc`) wrap `aiortc` peer connections, offers, answers, and ICE propagation.
- The `neurotalk.session.Session` class exposes the upcoming WebRTC session lifecycle with placeholder transport logic.

Check the project `PLAN.md` for the implementation roadmap and open decisions.
