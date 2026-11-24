"""Deprecated import compatibility for SpiceOwl's transport-neutral read SDK.

Use spiceowl.read_api for new integrations. CircuitGit owns the Codex
conversation channel; these aliases neither load MCP nor start a controller.
All projections and bounds remain implemented in the neutral module.
"""

from spiceowl.read_api import (
    ReadViews, MAX_EVENTS, MAX_RESOURCES, MAX_RECORDS,
    _TOOL_KINDS, _VERSION_PATTERNS, _DIMENSIONS, _FACT_BOOLEANS,
    _FACT_NUMBERS, _EVENT_KINDS, _READINESS, _IC_ARTIFACTS, _IC_ISSUES,
    _text, _id, _enum, _number, _boolean, _timestamp, _envelope,
    _run_summary, _facts, _probe, _probe_error, _ic_error, _ic_observation,
    _ic_hash, _ic_resource_timestamp, _ic_summary, _ic_detail,
)

__all__ = ["ReadViews", "MAX_EVENTS", "MAX_RESOURCES"]
