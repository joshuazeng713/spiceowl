"""Offline, synthetic connection validation and diagnostic streams."""

from .config import validate_connection
from .backends.base import AgentBackend, ModelBackend
from .backends.fake import (
    FakeAgentBackend,
    FakeModelBackend,
    generate_events,
    probe_connection,
)

__all__ = [
    "AgentBackend",
    "ModelBackend",
    "FakeAgentBackend",
    "FakeModelBackend",
    "validate_connection",
    "probe_connection",
    "generate_events",
]
