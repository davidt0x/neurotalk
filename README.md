# neurotalk

[![Actions Status][actions-badge]][actions-link]
[![Documentation Status][rtd-badge]][rtd-link]

[![PyPI version][pypi-version]][pypi-link]
[![PyPI platforms][pypi-platforms]][pypi-link]

[![GitHub Discussion][github-discussions-badge]][github-discussions-link]

<!-- SPHINX-START -->

NeuroTalk is a Python toolkit for running hyperscanning experiments that need
reliable, low-latency audio plus an out-of-band control link. It grew out of the
CONV/DIAD paradigms used at Princeton's Hyperscanning Lab; you can still inspect
those legacy scripts under `examples/legacy/`. The library is being rewritten
from the ground up so researchers can script their own protocols without copying
monolithic experiments.

## Features

- High-level `ConversationSession` that coordinates UDP sockets, audio
  ingestion/playback, control traffic, synchronization, and segment logging.
- Structured configuration objects (`SessionConfig`, `NetworkConfig`,
  `AudioConfig`, etc.) so experiments can describe desired parameters instead of
  editing globals.
- Datagram control channel with typed messages for NAT hole punching, start-time
  negotiation, turn passing, and debug commands.
- Built-in WAV recorders that capture local/remote tracks, track labeled
  segments, and export per-segment clips or mixed audio for QA.
- Mock audio backends and deterministic queues that make unit tests practical
  without real microphones or loudspeakers.

## Installation

NeuroTalk targets Python 3.10+. Pre-built wheels are published to PyPI:

```bash
pip install neurotalk
```

The legacy experiment scripts under `examples/` need windowed UI packages such
as PsychoPy/Pygame. Install them with:

```bash
pip install "neurotalk[examples]"
```

See the [ReadTheDocs site][rtd-link] for API documentation and change notes.

## Quick start

```python
from neurotalk.config import SessionConfig
from neurotalk.session import ConversationSession

config = SessionConfig(participant_id="011", role="A")

with ConversationSession(config) as session:
    session.connect()  # Opens sockets, performs hole punch
    session.sync_start(delay_seconds=12)  # Agree on a shared start timestamp
    session.enable_transmit(True)
    session.start_segment("practice", target="local")
    # ... conduct your task ...
    session.stop_segment(target="local")
```

This snippet shows the ergonomic surface area: create the config, connect once,
and then drive the session through helper methods (turn passing, debug helpers,
segment recording, etc.).

### Examples

- `examples/simple_peer.py` – CLI program that runs a single peer with real
  audio devices and demonstrates the turn-based conversation scaffold.
- `examples/story_tasks/` and `examples/couple_tasks/` – Work in progress ports
  of the bespoke paradigms that originally motivated NeuroTalk.

Run any example via `python examples/simple_peer.py --help` to inspect the
available options.

## Development

The repository uses [uv](https://github.com/astral-sh/uv) for dependency and
tool management:

```bash
uv sync
uv run pytest              # or: uv run pytest tests/test_session_core.py
uvx ruff check src tests
uvx mypy src tests
```

`uvx pre-commit run --all-files` executes Ruff plus MyPy in the configuration
used by CI.

## Status and roadmap

NeuroTalk is still in the "Planning" phase. The current codebase focuses on the
core transport primitives; browser-grade WebRTC audio, GUIs, and telemetry
dashboards will land in future milestones. Please file GitHub issues if you rely
on a missing feature so we can incorporate it into the roadmap.

## License

This project is distributed under the BSD 3-Clause license. See `LICENSE` for
the full text.

<!-- prettier-ignore-start -->
[actions-badge]:            https://github.com/davidt0x/neurotalk/workflows/CI/badge.svg
[actions-link]:             https://github.com/davidt0x/neurotalk/actions
[conda-badge]:              https://img.shields.io/conda/vn/conda-forge/neurotalk
[conda-link]:               https://github.com/conda-forge/neurotalk-feedstock
[github-discussions-badge]: https://img.shields.io/static/v1?label=Discussions&message=Ask&color=blue&logo=github
[github-discussions-link]:  https://github.com/davidt0x/neurotalk/discussions
[pypi-link]:                https://pypi.org/project/neurotalk/
[pypi-platforms]:           https://img.shields.io/pypi/pyversions/neurotalk
[pypi-version]:             https://img.shields.io/pypi/v/neurotalk
[rtd-badge]:                https://readthedocs.org/projects/neurotalk/badge/?version=latest
[rtd-link]:                 https://neurotalk.readthedocs.io/en/latest/?badge=latest

<!-- prettier-ignore-end -->
