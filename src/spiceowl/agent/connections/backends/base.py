"""Static protocols retain agent/model identity without inventing live support.

These are intentionally not runtime_checkable: a runtime structural check would
ignore Literal types and incorrectly treat the two boundaries as equivalent.
The fake implementations also enforce kind at each method entry.
"""

from collections.abc import Iterator
from typing import Literal, Protocol


class AgentBackend(Protocol):
    """An existing agent runtime boundary; P0.1 only simulates it."""

    @property
    def kind(self) -> Literal["agent"]: ...

    def probe_connection(self, connection: dict, level: str) -> dict: ...

    def generate_events(
        self, connection: dict, evidence: dict, question: str
    ) -> Iterator[dict]: ...


class ModelBackend(Protocol):
    """An inference boundary whose surrounding session belongs to SpiceOwl."""

    @property
    def kind(self) -> Literal["model"]: ...

    def probe_connection(self, connection: dict, level: str) -> dict: ...

    def generate_events(
        self, connection: dict, evidence: dict, question: str
    ) -> Iterator[dict]: ...
