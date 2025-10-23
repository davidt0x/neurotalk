"""Custom exceptions for NeuroTalk."""

from __future__ import annotations


class NeuroTalkError(Exception):
    """Base class for NeuroTalk specific exceptions."""


class ConfigurationError(NeuroTalkError):
    """Raised when configuration data is invalid."""


class SessionStateError(NeuroTalkError):
    """Raised when session methods are used in an illegal state."""
