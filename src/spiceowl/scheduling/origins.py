"""Reviewable origin routing without opening inputs or controlling native jobs."""

from spiceowl.domain.common import DomainError, require_id
from spiceowl.scheduling.placement import MAX_HOSTS, _timestamp, validate_requirements


_SIMULATORS = {"spectre", "ngspice"}
_PREREQUISITES = (
    "Freeze the experiment, effective netlist and transitive include/model identities.",
    "Validate licenses, environment, input references and current resources for that exact experiment.",
    "Obtain explicit launch authorization and establish remote execution ownership.",
    "Persist a stable submission operation and reconcile uncertainty before any retry.",
)


def _invalid(message):
    raise DomainError(message, "invalid_origin")


def _identifier(value, name):
    if type(value) is not str:
        _invalid(f"Invalid {name}.")
    try:
        return require_id(value, name)
    except DomainError:
        _invalid(f"Invalid {name}.")


def validate_origin(data):
    """Validate strict raw origin fields and add derived relocatability.

    input_ref is an opaque ID or local:ID, not a path, URL, script or netlist.
    native_run_ref is an opaque local mapping ID, never a command or PID guess.
    This validates syntax only; it does not attest that either mapping exists.
    """
    if type(data) is not dict:
        _invalid("Origin must be a dictionary.")
    kind = data.get("kind")
    if type(kind) is not str or kind not in {"standalone", "virtuoso_managed"}:
        _invalid("Unsupported simulation origin.")
    fields = ({"kind", "simulator", "input_ref"} if kind == "standalone"
              else {"kind", "simulator", "host_id", "native_run_ref"})
    if set(data) != fields:
        _invalid("Origin contains missing or unsupported fields.")
    simulator = data["simulator"]
    if type(simulator) is not str or simulator not in _SIMULATORS:
        _invalid("Unsupported simulator for this origin.")
    if kind == "standalone":
        reference = data["input_ref"]
        if type(reference) is not str:
            _invalid("Invalid local input reference.")
        identifier = reference[6:] if reference.startswith("local:") else reference
        _identifier(identifier, "local input reference")
        return {"kind": kind, "simulator": simulator, "input_ref": reference, "relocatable": True}
    if simulator != "spectre":
        _invalid("A Virtuoso-managed origin must use spectre in this slice.")
    return {"kind": kind, "simulator": simulator,
            "host_id": _identifier(data["host_id"], "host ID"),
            "native_run_ref": _identifier(data["native_run_ref"], "native run reference"),
            "relocatable": False}


def _origin_for_plan(origin):
    # Accept validate_origin's own output as well as a strict raw input. The
    # derived field is checked, never trusted as permission to relocate a job.
    if type(origin) is dict and "relocatable" in origin:
        result = validate_origin({key: value for key, value in origin.items() if key != "relocatable"})
        if type(origin["relocatable"]) is not bool or origin["relocatable"] != result["relocatable"]:
            _invalid("Origin relocatability contradicts its source kind.")
        return result
    return validate_origin(origin)


def _proposed_host(placement, simulator):
    if placement is None:
        return None, None
    if (type(placement) is not dict or placement.get("scope") != "placement_recommendation"
            or placement.get("reservation") is not False or placement.get("dispatch_enabled") is not False):
        _invalid("Placement must be a non-executable recommendation.")
    requirements = validate_requirements(placement.get("requirements"))
    if requirements["simulator"] != simulator:
        _invalid("Placement simulator does not match the standalone origin.")
    candidates = placement.get("candidates")
    if type(candidates) is not list or len(candidates) > MAX_HOSTS:
        _invalid("Placement candidates must be a bounded list.")
    seen = set()
    for rank, candidate in enumerate(candidates, 1):
        if type(candidate) is not dict or type(candidate.get("rank")) is not int or candidate["rank"] != rank:
            _invalid("Placement candidate ranks must be consecutive and ordered.")
        host_id = _identifier(candidate.get("host_id"), "candidate host ID")
        if host_id in seen or candidate.get("simulator") != simulator:
            _invalid("Placement candidates must be unique and match the requested simulator.")
        seen.add(host_id)
    host_id = candidates[0]["host_id"] if candidates else None
    if placement.get("recommended_host_id") != host_id:
        _invalid("Recommended host must match the highest-ranked candidate.")
    evaluated_at = placement.get("evaluated_at")
    if _timestamp(evaluated_at) is None:
        _invalid("Placement must identify its evaluation timestamp.")
    return host_id, evaluated_at


def plan_execution(origin, placement):
    """Select a review route, never submit, migrate, reserve or inspect files.

    Native origins deliberately ignore pool ranking and retain their binding.
    Relocatable on a standalone plan describes the new-input placement choice;
    it does not expose an implemented relocation capability for existing jobs.
    """
    normalized = _origin_for_plan(origin)
    native = normalized["kind"] == "virtuoso_managed"
    if native:
        if placement is not None and type(placement) is not dict:
            _invalid("Placement must be a dictionary or absent.")
        host_id, evaluated_at = normalized["host_id"], None
        status = "native_mapping_retained"
        reason = "Retain the selected Virtuoso host/native reference. Pool recommendations cannot move or resubmit this native job; native control requires a separate implementation."
    else:
        host_id, evaluated_at = _proposed_host(placement, normalized["simulator"])
        status = "proposed" if host_id else "no_candidate"
        reason = ("The highest-ranked host is proposed for a new exported-netlist experiment; this is not a reservation or launch."
                  if host_id else "No eligible placement is available. No host was selected and no network action was taken.")
    return {
        "scope": "execution_plan", "origin": normalized,
        "source_kind": normalized["kind"],
        "route": "virtuoso_native_manager" if native else "standalone_exported_netlist",
        "status": status, "host_id": host_id,
        "proposed_host_id": None if native else host_id,
        "native_run_ref": normalized.get("native_run_ref"),
        "relocatable": not native, "placement_evaluated_at": evaluated_at,
        "requires_review": True, "reason": reason,
        "prerequisites": list(_PREREQUISITES),
        "capabilities": {"submission": False, "relocation": False, "native_execution_control": False},
        "reservation": False, "dispatch_enabled": False,
    }
