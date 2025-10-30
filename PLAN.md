# NeuroTalk Library Extraction Plan

## Context
- `examples/CONV_scan.py` is the authoritative implementation of the CONV experiment, bundling networking, audio, sync, and logging logic in a single script.
- We want a reusable `neurotalk` library that exposes the same capabilities through a cleaner API while preserving the legacy script as an integration reference.
- Refactor must stay close to the existing UDP + PyAudio approach (no protocol rewrites, no changes to `CONV_scan.py` right now).

## Objectives
- Extract the functional pieces used by `CONV_scan.py` into organized modules under `src/neurotalk`.
- Provide a documented, experiment-friendly API that wraps those pieces (e.g., session lifecycle, audio transport, sync helpers, control channel messaging).
- Maintain feature parity: UDP hole punching, dual audio streams, timestamped packets, run/start synchronization, logging, and cleanup.
- Leave `CONV_scan.py` untouched; plan to add a separate example that consumes the new API later.

## Assumptions & Constraints
- Target Python 3.10+ (dropping Python 3.7/legacy shims); ensure legacy script keeps any compatibility tweaks it still needs.
- PyAudio and multiprocessing remain the audio/parallelism stack; no new third-party deps unless essential.
- Prefer thread-based workers over multiprocessing for audio/control loops to simplify state sharing across platforms (spawn vs. fork).
- CLI tooling via `uv` when running scripts/tests (per user instructions).
- Aim for incremental pull-up: introduce modules, copy/adapt code, then wire higher-level façade.
- Library must not depend on PsychoPy; integration points should be optional helpers or documented patterns.

## Milestones
1. **Audit & Blueprint**
   - Catalogue every function/table/constant in `CONV_scan.py` related to networking, audio, synchronization, trial logging, and cleanup.
   - Decide module boundaries (e.g., `network.py`, `audio.py`, `sync.py`, `control.py`, `session.py`).
   - Document required globals/state to minimize cross-module coupling.

2. **Create Module Skeleton**
   - Establish package directories/files under `src/neurotalk/` with placeholders for the identified modules.
   - Add `__all__` exports and type hints where feasible.
   - Stub a high-level `Session`/`ExperimentRuntime` API to target during extraction.

3. **Port Networking Layer**
   - Move STUN wrapper, socket setup, and hole-punch logic into `network.py`.
   - Encapsulate sockets and ports in a data class; expose methods for handshake, send/receive, and cleanup.
   - Ensure functions can operate without script-level globals (inject dependencies).

4. **Port Audio Handling**
   - Relocate PyAudio callback functions, stream setup, and process orchestration into `audio.py`.
   - Provide classes for input/output workers that accept queues and sockets.
   - Handle metadata packing/unpacking (`packetParser`) within the module.

5. **Port Synchronization & Control Messaging**
   - Move timestamp exchange utilities, turn-taking messaging, and TTL logging helpers into `sync.py` / `control.py`.
   - Normalize message formats (struct packing) and expose convenience methods.

6. **High-Level Session API**
   - Implement a coordinating class (e.g., `ConversationSession`) that wires networking, audio, and control components.
   - Offer start/stop/context-manager patterns, hooks for run phases, and integration points for PsychoPy loops.
   - Provide clear error handling and cleanup semantics.

7. **Testing & Validation**
   - Add unit tests for packet serialization, port setup, and sync math.
     - Basic coverage now in `tests/test_control.py`, `tests/test_network.py`, and `tests/test_session_core.py`.
   - Create an integration harness that mimics two peers exchanging audio locally (loopback) to validate the new API.
   - Unit-tests for audio workers (using mocked PyAudio streams) live in `tests/test_audio.py`.
   - Debug-mode loopback is covered by `tests/test_session_debug.py`.
   - End-to-end session behavior (transmit toggles, turn passing, segment tagging) is exercised by `tests/test_session_integration.py`.
   - WAV recording and segment splitting are verified in `tests/test_records.py` and `tests/test_session_segments.py`.
   - Document manual test steps for running alongside `CONV_scan.py`.

8. **Documentation & Examples**
   - Write module docstrings and API docs summarizing usage.
   - Add a new example script under `examples/` that demonstrates the library interface.
   - Update README/docs to explain the migration path and how the library maps to the legacy script.

## Open Questions
- Do we retain Python 2 compatibility shims inside the library or drop them for clarity?
- Should the library expose blocking and async variants, or is a blocking façade sufficient?
- What level of configuration (e.g., IPs, ports, buffers) should be user-settable versus defaulted?

## Audit Findings (Step 1)
### Networking
- `magicNumbers` establishes role-based addressing (local vs. remote IP, default UDP ports) and returns the timer/port configuration used elsewhere.
- `stunQuery` shells out to `stunclient` and parses textual output to diagnose NAT behaviour.
- `openSocket` creates UDP sockets with `SO_REUSEADDR`, binding each to a specified local port.
- `punchThrough` performs two-way UDP hole punching, handling NAT-present (active) and NAT-free (passive) roles and mutating globals `IP`, `PortIn`, `PortOut`, `PortComm`.
- `networkInit` sequences optional STUN lookup, socket creation, hole punching, timeout configuration, and stores `socketIn/socketOut/socketComm` globally.

### Audio
- `callbackInput` (PyAudio) tags outbound PCM chunks with incrementing packet counters and timestamps, writes them to the local recording file (`fOut`), and sends them over `socketOut`.
- `callbackOutput` consumes buffered audio frames, manages underflow recovery by replaying `lastData`, writes playback data into `fOut`, and updates counters.
- `micOpen` / `speakersOpen` construct PyAudio streams wired to the callbacks.
- `packetParser` separates audio payload, packet ID, and timestamp from received UDP datagrams.
- `cleanupInput` / `cleanupOutput` terminate PyAudio streams, close sockets, and send terminal control packets (`b"thanks"`).
- `inputProcess` runs in a separate `multiprocessing.Process`, reacting to queue commands (`start/stop/die`), and delegates cleanup.
- `outputProcess` runs in parallel, drains `socketIn`, populates the rolling `audioBuffer`, tracks timing stats, and triggers `messagesOutput` on run end.
- `messagesOutput` reports buffer under/overflow counts and writes timestamp CSV logs.

### Sync & Control Flow
- Within `goGo`, the `socketComm` control channel coordinates:
  - Start-time negotiation (`'syncTimeNow'` handshake, double-precision timestamps via `struct.pack('<d', ...)`).
  - Turn-taking: doubles/triples packed as `<ddd>` carrying event timestamps; special strings (`'esc'`, `'thanks'`) signal escape/termination.
  - TTL logging and mic pass events propagate through the same channel.
- Event loops continuously poll PsychoPy’s `event.getKeys` while also checking `socketComm` for incoming control packets.

### Runtime Orchestration & Data
- `goGo` loads experiment assets (PsychoPy, pandas CSVs), initializes randomization, spawns audio processes, manages queues, and logs audio positions/TTLs.
- CLI parsing at file end enforces constraints on buffer sizes, NAT flags, etc., and pushes values into globals (`BUFFER`, `CHUNK`, `PARTICIPANT`, `RUN`) before invoking `goGo`.
- Extensive use of module-level globals (`IP`, `PortIn/Out`, `PortComm`, `socket*`, `fOut`, `stream*`, `audioBuffer`, `startFlag`, `BUFFER`, `CHUNK`, etc.) couples components tightly.

## Shared State & External Dependencies (Step 2)
- **Globals to disentangle**: network identifiers (`IP`, `PortIn`, `PortOut`, `PortComm`), live sockets (`socketIn`, `socketOut`, `socketComm`), audio handles (`streamInput`, `streamOutput`, `pIn`, `pOut`), buffers and counters (`audioBuffer`, `silenceBuffer`, `lastData`, `chunkCounter`, `startFlag`, `underFlowFlag`, `overFlowFlag`), logging handles (`fOut`, `fLog`, `fTTL`), and run metadata (`BUFFER`, `CHUNK`, `PARTICIPANT`, `RUN`, `run_n`).
- **Inter-process coordination**: relies on `multiprocessing.Queue` commands (`start`, `stop`, `die`, `run_end`) and queue polling semantics.
- **Message vocabulary**: UDP control payloads include ASCII tokens (`'hello!'`, `'hi partner'`, `'please'`, `'thanks'`, `'syncTimeNow'`, `'esc'`) and packed doubles (`<d>` for single timestamps, `<ddd>` for turn handoff triplets). Audio datagrams append `<l><d>` metadata to raw PCM bytes.
- **External libraries**: `pyaudio` (PortAudio bindings), `socket`, `struct`, `multiprocessing`, `time`, `datetime`, `random`, `csv`, `pandas` (stimuli intake), `psychopy` (UI/input), `subprocess`/`stunclient` for diagnostics. Library extraction should only retain the networking/audio portions, leaving PsychoPy/pandas usage to caller code.

## Module Blueprint Draft (Step 3)
- `config.py`: dataclasses for participant role, network ports, buffer sizes, and file/log destinations; encapsulates the logic currently embedded in `magicNumbers`.
- `network.py`: STUN wrapper, socket factory, hole-punch routine, and a `UdpLink` class managing `socketIn/out/comm`, port negotiation, send/receive helpers, and teardown.
- `audio.py`: PyAudio stream wrappers providing callback wiring, background worker processes (or threads), packet packing/parsing, and recording support without relying on globals.
- `control.py`: serialization utilities for command messages (`sync`, `turn_pass`, `escape`, `thanks`), plus a small dispatcher for handling incoming control traffic.
- `session.py`: high-level orchestrator composing `network`, `audio`, and `control`, exposing start/stop, mute toggles, run lifecycle hooks, and context management.
- `logging.py` (or `records.py`): helpers for writing timing logs/CSV outputs and computing buffer statistics.
- `legacy/` or `examples/`: scripts that demonstrate how to drive the new API from PsychoPy-based experiments, keeping `CONV_scan.py` untouched for reference.

### Scaffolding Status
- `src/neurotalk/config.py` defines `NetworkConfig`, `AudioConfig`, `RecordingConfig`, and `SessionConfig`.
- `src/neurotalk/control.py` formalizes the control payload vocabulary with typed helpers.
- `src/neurotalk/network.py` wraps socket creation, STUN diagnostics, and hole punching.
- `src/neurotalk/audio.py` implements thread-based PyAudio-backed input/output workers with injectable stream factories.
- `src/neurotalk/session.py` now wires the audio transport, handles debug-mode handshakes, and exposes recording segment helpers.
- `src/neurotalk/records.py` now provides WAV recorders, segment tracking, and telemetry hooks.

### Control Message Vocabulary
- `HANDSHAKE_HELLO` (`b"hello!"`): passive-side response on all three sockets to capture the caller’s public IP/port tuple during hole punching.
- `HANDSHAKE_HI_PARTNER` (`b"hi partner"`): active-side probe looped until echoed back, proving bi-directional reachability through NAT.
- `HANDSHAKE_READY` (`b"please"`): sent right after a successful handshake to keep NAT mappings warm and mark readiness for media.
- `SYNC_REQUEST` (`b"syncTimeNow"`): control-channel ping exchanged until both sides hear it, confirming the sync channel is open.
- `SYNC_TIMESTAMP` (`struct.pack("<d", time.time())`): single float timestamp each side sends after `SYNC_REQUEST`; combined to compute the shared `start_time`.
- `TURN_PASS` (`struct.pack("<ddd", wall_clock, run_clock, phase_clock)`): triplet sent when the active speaker hands off; listener toggles roles and logs partner timing on receipt.
- `ESCAPE` (`b"esc"`): emergency stop broadcast when a participant hits Escape; receivers tear down audio/control immediately.
- `THANKS` (`b"thanks"`): audio-socket sentinel emitted during cleanup so the remote playback loop exits even if PCM stops arriving.

## Additional Feature Requirements
- **Debug Mode**: Pre-experiment phase that exercises the audio link and turn-passing logic without touching study data; should expose simple prompts to verify mic/speaker routing.
- **Real-Time Monitoring**: Console-level telemetry (e.g., packet counters, buffer fill, under/overflow counts, latency estimates) to assess link quality while the session runs.
- **Selective Transmission Control**: Ability to mute outbound audio while continuing to capture/record local input (and vice versa), so experimenters can halt live transmission without losing recordings.
- **WAV Recording**: Persist microphone and remote audio streams as proper WAV files (separate tracks and/or mixed), handling header management automatically.
- **Segmented Audio Output**: Provide APIs to cut recordings into meaningful chunks (turn boundaries, trials, tagged bookmarks) using control events or explicit markers.
- **Recording Architecture Review**: Current implementation writes raw PCM from both callbacks into a single binary file; refactor must introduce structured recorders that decouple local vs. remote capture and support the features above.
