"""One bounded native Q&A via the official, version-matched app-server.

Checked 2026-11-13 against Codex 0.167.0-alpha.4.1 and its generated schema.
Sources: https://learn.chatgpt.com/docs/app-server.md
https://learn.chatgpt.com/docs/config-file/config-reference.md
https://learn.chatgpt.com/docs/enterprise/managed-configuration.md

The native runtime alone owns credentials/config. We never read auth files,
edit config, relax managed requirements, attach another thread, or select a
fallback model/provider. Native-managed network retries retain their configured
policy within the bounded process lifetime; the adapter sends turn/start once.
Parent owns request deduplication and live smoke tests.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from spiceowl import __version__
from spiceowl.domain.common import DomainError, require_id
from .native_support.transport import NativeFailure, NativeProcess


SUPPORTED_VERSION = "0.167.0-alpha.4.1"
PROBE_TIMEOUT = 20
ASK_TIMEOUT = 120
MAX_ANSWER = 32768
_FIELDS = {"schema", "agent_id", "label", "executable", "model"}
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}")
_NATIVE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
_PROVIDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_SERVER_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,95}")
MAX_MCP_SERVERS = 64
_DISABLED_FEATURES = (
    "shell_tool", "shell_snapshot", "shell_snapshot_v2", "apps",
    "apps_early_resource_discovery", "plugins", "multi_agent", "multi_agent_v2",
    "goals", "hooks", "view_image", "image_generation", "browser_use",
    "browser_use_external", "computer_use", "code_mode", "code_mode_host",
    "code_mode_prewarm", "code_mode_tool_search", "skill_mcp_dependency_install",
)
_OVERRIDES = (
    'sandbox_mode="read-only"', 'approval_policy="never"', 'web_search="disabled"',
    "mcp_servers={}", "agents.enabled=false", "project_doc_max_bytes=0",
    'history.persistence="none"',
)
_ERRORS = {
    "native_start": "The selected native binary could not be started.",
    "native_version": "This native version has not passed the adapter's protocol checks.",
    "native_timeout": "The bounded native operation timed out; the adapter did not replay the question.",
    "native_disconnected": "The native connection closed; the adapter did not replay the question.",
    "native_protocol": "The native response did not match the supported protocol.",
    "native_output_limit": "The native operation exceeded its bounded output limit.",
    "native_rejected": "The native runtime rejected the request; inspect native setup or managed policy separately.",
    "native_constraints": "Required Q&A-only runtime restrictions could not be verified.",
    "native_auth_required": "The native runtime requires its own login; no login was started.",
    "native_route": "The native provider/model changed or could not be identified; no inference was sent.",
    "native_permission_denied": "A native tool or permission request was denied by the Q&A-only adapter.",
    "native_tool_activity": "Unexpected native tool activity interrupted the Q&A-only operation.",
    "native_turn_failed": "The native turn ended without successful completion.",
}


def _identifier(value):
    return (value if isinstance(value, str) and _IDENTIFIER.fullmatch(value)
            and "://" not in value else None)


def _native_id(value):
    return value if isinstance(value, str) and _NATIVE_ID.fullmatch(value) else None


def validate_agent_profile(data):
    if not isinstance(data, dict) or set(data) != _FIELDS:
        raise DomainError("Agent profile must contain only the five documented fields.", "invalid_agent_profile")
    if data["schema"] != "spiceowl.agent.v1":
        raise DomainError("Unsupported agent profile schema.", "invalid_agent_profile")
    require_id(data["agent_id"], "agent ID")
    label = data["label"]
    if not isinstance(label, str) or not label.strip() or len(label) > 80 or not label.isprintable():
        raise DomainError("Agent label must be 1 to 80 printable characters.", "invalid_agent_profile")
    executable = data["executable"]
    if (not isinstance(executable, str) or len(executable) > 4096 or not executable.isprintable()
            or not Path(executable).is_absolute() or Path(executable).name not in ("codex", "codex.exe")
            or "://" in executable):
        raise DomainError("Select an absolute path to the installed Codex binary.", "invalid_agent_profile")
    if data["model"] is not None and _identifier(data["model"]) is None:
        raise DomainError("Model must be a native model identifier or null.", "invalid_agent_profile")
    return dict(data)


@contextmanager
def _workspace(workspace):
    try:
        base = Path(workspace)
        if not base.is_absolute():
            raise ValueError("relative")
        base = base.resolve(strict=True)
        info = base.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("untrusted scratch")
    except (TypeError, ValueError, OSError, RuntimeError):
        raise DomainError("Select an existing operator-owned scratch directory without shared write access.", "invalid_workspace") from None
    with tempfile.TemporaryDirectory(prefix="spiceowl-native-", dir=base) as child:
        yield child


def _argv(profile, servers=()):
    argv = [profile["executable"], "app-server", "--listen", "stdio://"]
    for feature in _DISABLED_FEATURES:
        argv.extend(("--disable", feature))
    for setting in _OVERRIDES:
        argv.extend(("-c", setting))
    for name in servers:
        # A bare TOML key only; never accept dots, quoting, whitespace or
        # arbitrary configuration fragments from the discovered server names.
        if not isinstance(name, str) or not _SERVER_KEY.fullmatch(name):
            raise NativeFailure("native_constraints")
        argv.extend(("-c", f"mcp_servers.{name}.enabled=false"))
    return argv


def _error(code):
    return {"code": code, "message": _ERRORS[code]}


def _check_version(profile, cwd):
    with NativeProcess([profile["executable"], "--version"], cwd, 5) as process:
        text = process.version_output()
    if text not in (f"codex-cli {SUPPORTED_VERSION}", f"codex {SUPPORTED_VERSION}"):
        raise NativeFailure("native_version")
    return SUPPORTED_VERSION


def _runtime_safety(config):
    features = config.get("features")
    if not isinstance(features, dict):
        raise NativeFailure("native_constraints")
    for feature in _DISABLED_FEATURES:
        value = features.get(feature)
        if value is not False and not (feature == "code_mode" and isinstance(value, dict) and value.get("enabled") is False):
            raise NativeFailure("native_constraints")
    if config.get("sandbox_mode") != "read-only" or config.get("approval_policy") != "never" or config.get("web_search") != "disabled":
        raise NativeFailure("native_constraints")


def _server_names(config):
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict) or len(servers) > MAX_MCP_SERVERS:
        raise NativeFailure("native_constraints")
    if any(not isinstance(name, str) or not _SERVER_KEY.fullmatch(name)
           or not isinstance(value, dict) for name, value in servers.items()):
        raise NativeFailure("native_constraints")
    return tuple(sorted(servers))


def _safety(config):
    _runtime_safety(config)
    for name in _server_names(config):
        if config["mcp_servers"][name].get("enabled") is not False:
            raise NativeFailure("native_constraints")


class _State:
    def __init__(self, process):
        self.process = process
        self.thread_id = self.turn_id = None
        self.items = {}
        self.usage = None
        self.terminal = None
        self.permission_denied = False
        self.pending = []

    def bind_turn(self, identifier):
        if _native_id(identifier) is None or (self.turn_id is not None and identifier != self.turn_id):
            raise NativeFailure("native_protocol")
        self.turn_id = identifier
        pending, self.pending = self.pending, []
        for message in pending:
            self.handle(message)

    def consume_items(self, items, *, completed=False):
        if not isinstance(items, list) or len(items) > 128:
            raise NativeFailure("native_protocol")
        for item in items:
            self._item(item, completed=completed)

    def _deny(self, message):
        method, rid = message.get("method"), message.get("id")
        responses = {
            "item/commandExecution/requestApproval": {"decision": "cancel"},
            "item/fileChange/requestApproval": {"decision": "cancel"},
            "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
            "mcpServer/elicitation/request": {"action": "decline"},
            "item/tool/requestUserInput": {"answers": {}},
            "item/tool/call": {"contentItems": [], "success": False},
            "applyPatchApproval": {"decision": "abort"},
            "execCommandApproval": {"decision": "abort"},
        }
        if method in responses:
            self.process.send({"id": rid, "result": responses[method]})
        else:
            # Includes external auth refresh and attestation: never manufacture
            # credentials, proofs, approvals, or answers on behalf of the user.
            self.process.send({"id": rid, "error": {"code": -32601, "message": "Unsupported Q&A-only client request."}})
        self.permission_denied = True

    def _item(self, item, *, completed=False):
        if not isinstance(item, dict):
            raise NativeFailure("native_protocol")
        if item.get("type") not in ("userMessage", "agentMessage", "reasoning"):
            raise NativeFailure("native_tool_activity")
        if item.get("type") == "agentMessage" and completed:
            identifier = _native_id(item.get("id"))
            text = item.get("text")
            if identifier is None or not isinstance(text, str):
                raise NativeFailure("native_protocol")
            self.items[identifier] = text
            self.answer()

    def answer(self):
        answer = "\n".join(self.items.values())
        try:
            size = len(answer.encode("utf-8"))
        except UnicodeError:
            raise NativeFailure("native_protocol") from None
        if size > MAX_ANSWER:
            raise NativeFailure("native_output_limit")
        return answer

    def handle(self, message):
        if not isinstance(message.get("method"), str) or not isinstance(message.get("params", {}), dict):
            raise NativeFailure("native_protocol")
        if "id" in message:
            self._deny(message)
            return
        method, params = message["method"], message.get("params", {})
        if self.thread_id is None or params.get("threadId") != self.thread_id:
            return
        if method in ("turn/started", "turn/completed"):
            turn = params.get("turn")
            if not isinstance(turn, dict) or _native_id(turn.get("id")) is None:
                raise NativeFailure("native_protocol")
            self.bind_turn(turn["id"])
            self.consume_items(turn.get("items", []), completed=method == "turn/completed")
            if method == "turn/completed":
                if turn.get("status") not in ("completed", "failed", "interrupted"):
                    raise NativeFailure("native_protocol")
                self.terminal = turn["status"]
            return
        if self.turn_id is None and isinstance(params.get("turnId"), str):
            if len(self.pending) >= 128:
                raise NativeFailure("native_output_limit")
            self.pending.append(message)
            return
        if self.turn_id is None or params.get("turnId") != self.turn_id:
            return
        if method == "item/agentMessage/delta":
            identifier, delta = _native_id(params.get("itemId")), params.get("delta")
            if identifier is None or not isinstance(delta, str):
                raise NativeFailure("native_protocol")
            self.items[identifier] = self.items.get(identifier, "") + delta
            self.answer()
        elif method in ("item/started", "item/completed"):
            self._item(params.get("item"), completed=method == "item/completed")
        elif method == "thread/tokenUsage/updated":
            token_usage = params.get("tokenUsage")
            if not isinstance(token_usage, dict):
                raise NativeFailure("native_protocol")
            raw = token_usage.get("last")
            if isinstance(raw, dict):
                names = {"inputTokens": "input_tokens", "outputTokens": "output_tokens",
                         "cachedInputTokens": "cached_input_tokens", "cacheWriteInputTokens": "cache_write_input_tokens",
                         "reasoningOutputTokens": "reasoning_output_tokens", "totalTokens": "total_tokens"}
                self.usage = {target: value if type(value := raw.get(source)) is int and 0 <= value <= 10**12 else None
                              for source, target in names.items()}
                self.usage.update(cost=None, source="native_last_turn")


def _read_config(process, state, cwd):
    process.request("initialize", {"clientInfo": {"name": "spiceowl_native", "title": "SpiceOwl", "version": __version__},
                                   "capabilities": {"experimentalApi": False}}, state.handle)
    process.send({"method": "initialized", "params": {}})
    config = process.request("config/read", {"includeLayers": False, "cwd": cwd}, state.handle).get("config")
    if not isinstance(config, dict):
        raise NativeFailure("native_protocol")
    if state.permission_denied:
        raise NativeFailure("native_permission_denied")
    return config


@contextmanager
def _constrained_process(profile, cwd, timeout):
    deadline = time.monotonic() + timeout

    def remaining():
        duration = deadline - time.monotonic()
        if duration <= 0:
            raise NativeFailure("native_timeout")
        return duration

    # An empty MCP table merges with inherited configuration. Discover only
    # names through native metadata, then disable every literal key in the
    # final process. This preflight never creates a thread or reads accounts.
    with NativeProcess(_argv(profile), cwd, min(PROBE_TIMEOUT, remaining())) as discovery:
        config = _read_config(discovery, _State(discovery), cwd)
        _runtime_safety(config)
        servers = _server_names(config)
    del config
    with NativeProcess(_argv(profile, servers), cwd, remaining()) as process:
        yield process


def _metadata(process, state, profile, cwd, *, catalog):
    config = _read_config(process, state, cwd)
    _safety(config)
    # Project only nonsecret identifiers; never retain/return config layers,
    # headers, endpoints, credential references, email or workspace identity.
    model, provider = _identifier(config.get("model")), _identifier(config.get("model_provider"))
    account = process.request("account/read", {"refreshToken": False}, state.handle)
    requires_auth = account.get("requiresOpenaiAuth")
    account_value = account.get("account")
    account_type = account_value.get("type") if isinstance(account_value, dict) else None
    if account_type not in ("apiKey", "chatgpt", "amazonBedrock", "gcp"):
        account_type = None
    auth = ("configured" if requires_auth is False or account_type else
            "required" if requires_auth is True else "unknown")
    models = []
    if catalog:
        data = process.request("model/list", {"limit": 25, "includeHidden": False}, state.handle).get("data")
        if not isinstance(data, list):
            raise NativeFailure("native_protocol")
        for item in data[:25]:
            if isinstance(item, dict) and (name := _identifier(item.get("model"))) is not None:
                models.append({"model": name, "is_default": item.get("isDefault") is True})
    if state.permission_denied:
        raise NativeFailure("native_permission_denied")
    return {"configured_model": model, "configured_provider": provider,
            "model": profile["model"] or model, "provider": provider,
            "account_type": account_type, "requires_openai_auth": requires_auth if type(requires_auth) is bool else None,
            "authentication": auth, "models": models}


def probe_agent(profile, workspace):
    profile = validate_agent_profile(profile)
    result = {"agent_id": profile["agent_id"], "synthetic": False, "scope": "native_metadata",
              "transport": "unknown", "authentication": "unknown",
              "configured_model": None, "configured_provider": None, "model": None, "provider": None,
              "account_type": None, "requires_openai_auth": None, "models": [], "catalog": "unknown",
              "inference_access": "unverified", "version": None, "error": None}
    with _workspace(workspace) as cwd:
        try:
            result["version"] = _check_version(profile, cwd)
            with _constrained_process(profile, cwd, PROBE_TIMEOUT) as process:
                result.update(_metadata(process, _State(process), profile, cwd, catalog=True))
                result.update(transport="reachable", catalog="advertised")
        except NativeFailure as exc:
            result["error"] = _error(exc.code)
    return result


def ask_agent(profile, question, workspace):
    profile = validate_agent_profile(profile)
    if (not isinstance(question, str) or not question.strip() or len(question) > 2000
            or any((ord(char) < 32 and char not in "\n\t") or 0xD800 <= ord(char) <= 0xDFFF for char in question)):
        raise DomainError("Enter a question between 1 and 2000 characters.", "invalid_question")
    result = {"status": "failed", "answer": "", "model": None, "provider": None,
              "native_thread_id": None, "native_turn_id": None, "usage": None, "error": None, "synthetic": False}
    dispatched = False
    state = None
    with _workspace(workspace) as cwd:
        try:
            _check_version(profile, cwd)
            with _constrained_process(profile, cwd, ASK_TIMEOUT) as process:
                state = _State(process)
                metadata = _metadata(process, state, profile, cwd, catalog=False)
                result.update(model=metadata["model"], provider=metadata["provider"])
                if metadata["authentication"] == "required":
                    raise NativeFailure("native_auth_required")
                provider = metadata["provider"]
                if not isinstance(provider, str) or not _PROVIDER.fullmatch(provider):
                    raise NativeFailure("native_route")
                # Preserve the native provider and its transport policy. Only
                # this adapter's dispatch is once-only; native network recovery
                # remains subject to the same process deadline and cleanup.
                params = {"cwd": cwd, "ephemeral": True, "sandbox": "read-only", "approvalPolicy": "never",
                          "developerInstructions": "Answer the user's question briefly in plain text. Do not call tools, read files, browse, run commands, or start other agents."}
                if profile["model"] is not None:
                    params["model"] = profile["model"]
                thread = process.request("thread/start", params, state.handle)
                info = thread.get("thread")
                if not isinstance(info, dict) or _native_id(info.get("id")) is None:
                    raise NativeFailure("native_protocol")
                state.thread_id = info["id"]
                result.update(native_thread_id=state.thread_id, model=_identifier(thread.get("model")),
                              provider=_identifier(thread.get("modelProvider")))
                sandbox = thread.get("sandbox")
                if (not isinstance(sandbox, dict) or sandbox.get("type") != "readOnly"
                        or sandbox.get("networkAccess", False) is not False or thread.get("approvalPolicy") != "never"
                        or info.get("ephemeral") is not True or thread.get("cwd") != cwd):
                    raise NativeFailure("native_constraints")
                if result["provider"] != provider or result["model"] is None or (
                    metadata["model"] is not None and result["model"] != metadata["model"]
                ):
                    raise NativeFailure("native_route")
                if state.permission_denied:
                    raise NativeFailure("native_permission_denied")
                # Set before any bytes may be written: even a partial write is
                # an uncertain accepted operation, never an automatic retry.
                dispatched = True
                try:
                    accepted = process.request("turn/start", {"threadId": state.thread_id,
                        "input": [{"type": "text", "text": question}]}, state.handle)
                except NativeFailure as exc:
                    # A terminal notification for this fresh thread establishes
                    # the outcome even when its start acknowledgement is lost.
                    if state.terminal is None or exc.code not in ("native_disconnected", "native_timeout"):
                        raise
                else:
                    turn = accepted.get("turn")
                    if not isinstance(turn, dict) or _native_id(turn.get("id")) is None:
                        raise NativeFailure("native_protocol")
                    state.bind_turn(turn["id"])
                    state.consume_items(turn.get("items", []))
                while state.terminal is None:
                    state.handle(process.next_message())
                result.update(answer=state.answer(), native_turn_id=state.turn_id, usage=state.usage)
                if state.permission_denied:
                    result["error"] = _error("native_permission_denied")
                elif state.terminal == "completed" and result["answer"]:
                    result["status"] = "completed"
                else:
                    result["error"] = _error("native_turn_failed")
        except NativeFailure as exc:
            result["status"] = "outcome_unknown" if dispatched and not exc.definitive else "failed"
            result["error"] = _error(exc.code)
            if state is not None:
                result.update(native_thread_id=state.thread_id, native_turn_id=state.turn_id, usage=state.usage)
                try:
                    result["answer"] = state.answer()
                except NativeFailure:
                    result["answer"] = ""
    return result
