"""Explicit, local Codex app-server connection for standalone SpiceOwl."""

from .bridge import CodexBridge
from .transport import BridgeError

__all__ = ['CodexBridge', 'BridgeError']
