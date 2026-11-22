"""Distinct agent-runtime and model-inference boundaries, with fake adapters."""

from .base import AgentBackend, ModelBackend
from .fake import FakeAgentBackend, FakeModelBackend, generate_events, probe_connection

__all__ = [
    "AgentBackend", "ModelBackend", "FakeAgentBackend", "FakeModelBackend",
    "generate_events", "probe_connection",
]
