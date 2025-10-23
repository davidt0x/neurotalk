# NeuroTalk – Work Plan

## 1. Goals
- Deliver `neurotalk`, a reusable Python library that reproduces the CONV/DIAD audio-link workflow using WebRTC via `aiortc`.
- Provide a minimal, experiment-friendly API for PsychoPy scripts (low ceremony, a handful of method calls).
- Preserve essential capabilities: low-latency, bi-directional audio, selective muting, synchronized start, control signalling, and automatic NAT traversal.

## 2. Scope & Requirements
- **Audio transport**: full-duplex, near real-time voice; allow toggling transmit/receive per endpoint.
- **Control channel**: data channel for turn-taking, TTL events, sync handshakes, and arbitrary experiment markers.
- **Session synchronization**: utility that agrees on a future `start_time` before unmuting streams.
- **Recording**: optional per-endpoint WAV capture of local mic, remote stream, or combined mix.
- **Networking**: ICE/STUN/TURN support via `aiortc`; package should make server configuration optional but pluggable.
- **Ease-of-use**: declarative configuration object + context-managed session with callbacks (Python 3.10+).
- **Reliability**: graceful error propagation, structured logging, and deterministic cleanup.

Out of scope (for v1): video, TURN server provisioning, GUI tooling, and legacy UDP-backend support.

## 3. High-Level Architecture
```
neurotalk/
  __init__.py
  config.py            # User-facing dataclasses (peer role, media devices, STUN servers)
  session.py           # Session lifecycle; wraps aiortc PeerConnection, media/data channels
  audio.py             # MediaPlayer/MediaRecorder integration, mute control, recording helpers
  signaling.py         # Handshake helpers (e.g., simple WebSocket/HTTP signaling or manual tokens)
  sync.py              # Clock exchange & start-time agreement utilities
  events.py            # Typed event/callback definitions for experiment hooks
  logging.py           # Structured logging utilities (JSON/CSV)
  examples/            # Sample PsychoPy scripts showing integration
  docs/                # API and setup guides
```

## 4. Current Project Skeleton
- `pyproject.toml` is Hatch-based with dynamic versioning (`hatch-vcs`), Ruff, Mypy, and pytest already wired in.
- `noxfile.py` is present for task automation; expand it with lint/test/type/doc sessions once the workflow stabilises.
- Core modules (`config`, `events`, `session`, `signaling`, `sync`, `audio`, `webrtc`) now provide configuration dataclasses, event hooks, microphone/speaker management, a session shell wired to the signaling client, announce/ack start synchronisation, WebRTC offer/answer + ICE helpers, and a bundled WebSocket relay service.
- `tests/` includes coverage for configuration helpers, signaling broadcast, WebRTC connection bring-up, session lifecycle/control messaging, and the sync handshake; `docs/index.md` summarises the implemented surface.
- Publishing metadata (README badges, BSD-3 license) is already configured; align new features with this structure.

## 5. Development Phases
1. **Foundations**
   - Define configuration dataclasses (`SessionConfig`, `MediaConfig`, `SignalingConfig`).
   - Implement minimal signaling transport (e.g., WebSocket server/client or manual JSON exchange).
   - Spin up `aiortc.RTCPeerConnection` with audio send/receive + data channel.
2. **Core Features**
   - Add mute/unmute controls (track-enabled flags).
   - Expose control channel send/receive with typed events and callbacks.
   - Introduce start-time sync utility (announce/ack on signaling channel; later migrate to data channel if needed).
3. **Recording & Logging**
   - Integrate `MediaRecorder` for WAV capture (mic, remote, mix).
   - Provide logging hooks for connection state, stream stats, control events.
4. **API Polish**
   - Finalize high-level session interface (`with Session(config) as sess:`; `sess.wait_for_start()`; callbacks).
   - Add synchronous wrappers for ease of use in PsychoPy (which is often non-async).
   - Document error handling patterns.
5. **Testing & Validation**
   - Unit tests for helpers (timestamp sync, config parsing).
   - Integration tests using loopback peers.
   - Dry-run scripts mimicking CONV/DIAD workflows; verify latency, muting, recordings, control events.
6. **Documentation & Examples**
   - Write tutorials for experimenters.
   - Provide drop-in replacements for existing scripts demonstrating new API.

## 6. API Sketch
```python
from neurotalk import Session, SessionConfig, EventHandlers

config = SessionConfig(
    peer_id="dyad01-A",
    signaling_url="wss://signal.server/room/dyad01",
    audio_devices={"input": "Built-in Mic", "output": "Headphones"},
    recording={"microphone": "local_mic.wav", "remote": "partner.wav"},
    stun_servers=["stun:stun.l.google.com:19302"],
)

handlers = EventHandlers(
    on_control=lambda msg: handle_control(msg),
    on_state_change=lambda state: log_state(state),
)

with Session(config, handlers) as session:
    session.connect()
    session.wait_for_remote()
    session.sync_start(delay_seconds=12)
    session.unmute(send=True, receive=True)
    # ... experiment loop ...
    session.send_control({"type": "turn", "payload": {...}})
```

## 7. Dependencies & Tooling
- Maintain support for Python 3.10–3.11 to match PsychoPy constraints.
- `aiortc` for WebRTC (requires `pyee`, `av`, etc.).
- `websockets` powers the bundled signaling client/server; `uvicorn` remains optional if we later expose an ASGI variant.
- PyAudio is optional for real microphone/speaker capture; when unavailable, NeuroTalk falls back to silent capture and blackhole playback.
- Environment management and installs via `uv`.
- Testing: `pytest`, `pytest-asyncio`, and `pytest-timeout`.
- Code quality: `ruff` (lint + formatting) and `mypy`.
- Packaging/build flow uses Hatch (`hatchling` + `hatch-vcs`); extend `nox` sessions to call `uv`-managed environments.

## 8. Testing Strategy
- **Unit**: config parsing, sync math, control message serialization.
- **Integration**: pair of peers started in CI using loopback; assert audio frames transmitted, data channel events delivered, recordings created.
- **Performance**: manual latency checks comparing end-to-end delay vs. current scripts.
- **Resilience**: simulate mute toggles, drop/rejoin, failed signaling.

## 9. Documentation Deliverables
- README with quick start.
- Step-by-step PsychoPy integration guide.
- Signaling server setup instructions.
- Migration notes from legacy UDP scripts.

## 10. Decisions & Constraints
- Assume initial deployments are on the lab LAN; STUN is still configured, but TURN provisioning can wait for later releases.
- Target Windows, macOS, and Linux. Development happens in WSL, with PowerShell fallbacks when direct Windows device access is required.
- Record raw microphone and remote tracks separately to start; mixdown/post-processing can be layered on when needed.
- Ship a lightweight, pre-built signaling service (WebSocket-based) alongside client hooks so experimenters can run it on a stimulus machine or another reachable host.
