"""Ownership metadata only; no Agent process, connection or authentication."""

from spiceowl.domain.common import DomainError


CIRCUITGIT_URL = "https://circuitgit-dev.github.io/CircuitGit/"


def agent_channel_info():
    """Return a fresh descriptor, not a claim that CircuitGit is connected."""
    return {"owner": "circuitgit", "transport": "codex_app_server",
            "entry_url": CIRCUITGIT_URL, "connection_status": "not_checked",
            "local_inference_enabled": False, "legacy_history_read_only": True}


def agent_channel_error():
    return DomainError(
        "Agent connections are owned by CircuitGit's Codex conversation "
        "(app-server / ic-workbench). Use that existing channel; use "
        "'spiceowl read' for stored SDK views. No parallel Agent or MCP service "
        "was started.", "agent_channel_owned_by_circuitgit")
