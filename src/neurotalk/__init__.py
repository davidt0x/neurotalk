"""
Copyright (c) 2025 David Turner. All rights reserved.

neurotalk: NeuroTalk is a WebRTC-powered Python toolkit that delivers synchronized, low-latency audio and control channels for hyperscanning experiments.
"""

from __future__ import annotations

from ._version import version as __version__
from .audio import AudioBackendError, AudioPipeline
from .config import AudioDeviceConfig, RecordingConfig, SessionConfig, SignalingConfig
from .events import ControlPayload, EventHandlers, SessionState
from .exceptions import ConfigurationError, NeuroTalkError, SessionStateError
from .session import Session
from .signaling import BaseSignaling, SignalingClient, SignalingMessage, SignalingServer
from .sync import SyncAction, SyncInstruction

__all__ = [
    "__version__",
    "AudioDeviceConfig",
    "AudioBackendError",
    "AudioPipeline",
    "ConfigurationError",
    "ControlPayload",
    "EventHandlers",
    "NeuroTalkError",
    "RecordingConfig",
    "Session",
    "SessionConfig",
    "SessionState",
    "SessionStateError",
    "SignalingConfig",
    "SignalingClient",
    "SignalingMessage",
    "SignalingServer",
    "BaseSignaling",
    "SyncAction",
    "SyncInstruction",
]
