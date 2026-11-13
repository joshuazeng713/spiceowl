"""Reduce measurement checks without treating target misses as invalid data."""

from spiceowl.domain.common import DomainError, require_id


MAX_MEASUREMENTS = 256
MAX_CHECKS = 64
_CHECK_VALIDITY = {
    "fail": "invalid",
    "indeterminate": "indeterminate",
    "pending": "pending",
    "pass": "valid",
}
_PRIORITY = {"invalid": 0, "indeterminate": 1, "pending": 2, "valid": 3}
_SPECIFICATIONS = {"pass", "fail", "not_evaluated"}


def _invalid(message):
    raise DomainError(message, "invalid_measurements")


def aggregate_acceptance(measurements):
    """Return aggregate validity/specification and independent measurement results.

    Each returned measurement has measurement_id, copied checks, validity and
    specification. Execution and observation are not inputs to this reducer;
    build_evidence preserves those independent run dimensions.
    """
    if type(measurements) is not list or not 1 <= len(measurements) <= MAX_MEASUREMENTS:
        _invalid("Measurements must be a nonempty bounded list.")

    results = []
    seen = set()
    for measurement in measurements:
        if type(measurement) is not dict or set(measurement) != {
            "measurement_id", "checks", "specification"
        }:
            _invalid("Each measurement must have only the declared fields.")
        try:
            measurement_id = require_id(measurement["measurement_id"], "measurement ID")
        except DomainError:
            _invalid("Invalid measurement ID.")
        if measurement_id in seen:
            _invalid("Duplicate measurement ID.")
        seen.add(measurement_id)

        checks = measurement["checks"]
        if type(checks) is not list or not 1 <= len(checks) <= MAX_CHECKS:
            _invalid("Measurement checks must be a nonempty bounded list.")
        if any(type(check) is not str or check not in _CHECK_VALIDITY for check in checks):
            _invalid("Invalid measurement check verdict.")
        specification = measurement["specification"]
        if type(specification) is not str or specification not in _SPECIFICATIONS:
            _invalid("Invalid specification verdict.")

        validity = min((_CHECK_VALIDITY[check] for check in checks), key=_PRIORITY.get)
        results.append({
            "measurement_id": measurement_id,
            "checks": list(checks),
            "validity": validity,
            "specification": specification if validity == "valid" else "not_evaluated",
        })

    validity = min((item["validity"] for item in results), key=_PRIORITY.get)
    specification = "not_evaluated"
    if validity == "valid" and all(item["specification"] != "not_evaluated" for item in results):
        specification = "fail" if any(item["specification"] == "fail" for item in results) else "pass"
    return {
        "measurement_validity": validity,
        "specification_result": specification,
        "measurements": results,
    }
