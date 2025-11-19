"""
Copyright (c) 2025 David Turner. All rights reserved.

neurotalk: NeuroTalk is a WebRTC-powered Python toolkit that delivers synchronized, low-latency audio and control channels for hyperscanning experiments.
"""

from __future__ import annotations

from ._version import version as __version__
from .config import AudioConfig, NetworkConfig, RecordingConfig, SessionConfig
from .control import (
    ControlMessageType,
    TurnPassPayload,
    classify_payload,
)
from .network import NetworkError, flush_pending
from .records import (
    RecorderTarget,
    RecordingManifest,
    SegmentMarker,
    TelemetryReporter,
    WavRecorder,
)
from .session import ConversationSession
from .turns import TurnEvent, TurnEventSource, TurnManager, TurnRole

__all__ = [
    "AudioConfig",
    "ControlMessageType",
    "ConversationSession",
    "NetworkConfig",
    "NetworkError",
    "RecorderTarget",
    "RecordingConfig",
    "RecordingManifest",
    "SegmentMarker",
    "SessionConfig",
    "TelemetryReporter",
    "TurnEvent",
    "TurnEventSource",
    "TurnManager",
    "TurnRole",
    "TurnPassPayload",
    "WavRecorder",
    "__version__",
    "classify_payload",
    "flush_pending",
]
