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
from .records import RecordingManifest, RecorderTarget, SegmentMarker, TelemetryReporter, WavRecorder
from .session import ConversationSession

__all__ = [
    "__version__",
    "AudioConfig",
    "NetworkConfig",
    "RecordingConfig",
    "SessionConfig",
    "ControlMessageType",
    "TurnPassPayload",
    "classify_payload",
    "NetworkError",
    "RecorderTarget",
    "RecordingManifest",
    "SegmentMarker",
    "TelemetryReporter",
    "WavRecorder",
    "ConversationSession",
    "flush_pending",
]
