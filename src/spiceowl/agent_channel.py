"""Explicit standalone/embedding ownership. Importing starts no process or IO."""

from spiceowl.domain.common import DomainError


CIRCUITGIT_URL = "https://circuitgit-dev.github.io/CircuitGit/"


def normalize_agent_mode(mode):
    if mode not in ('standalone', 'embedded_in_circuitgit'):
        raise DomainError('Choose standalone or embedded_in_circuitgit explicitly.', 'invalid_agent_mode')
    return mode


def agent_channel_info(mode='embedded_in_circuitgit'):
    """Return a fresh descriptor, not a claim that CircuitGit is connected."""
    standalone = normalize_agent_mode(mode) == 'standalone'
    return {"mode": mode, "owner": "spiceowl" if standalone else "circuitgit", "transport": "codex_app_server",
            "entry_url": None if standalone else CIRCUITGIT_URL, "connection_status": "not_checked",
            "local_inference_enabled": standalone, "legacy_history_read_only": True}


def export_simulation_management_tools(*, mode='embedded_in_circuitgit', dispatcher=None):
    """Export data only; an embedding host injects these into its existing thread."""
    from spiceowl.simulation_tools import SimulationToolDispatcher
    normalize_agent_mode(mode)
    dispatcher = dispatcher or SimulationToolDispatcher(mode=mode)
    if not isinstance(dispatcher, SimulationToolDispatcher) or dispatcher.mode != mode:
        raise DomainError('Tool dispatcher mode must match the embedding mode.', 'invalid_agent_mode')
    return dispatcher.definitions()


def agent_channel_error():
    return DomainError(
        "This legacy launcher is retired. Use 'spiceowl serve' for the explicit "
        "standalone Codex window, or CircuitGit's existing conversation in "
        "embedded mode. Use 'spiceowl read' for stored SDK views. No Agent or "
        "MCP service was started.", "agent_channel_owned_by_circuitgit")
