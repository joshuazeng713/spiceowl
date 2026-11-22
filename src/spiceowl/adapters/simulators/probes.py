"""Allowlisted version flags and narrow version-output recognition.

Cadence flag and banner references checked 2026-11-13:
https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/52327/how-to-add-variable-to-dynamic-parameter-file
https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/63456/unpin-float-menu-from-virtuoso
https://ngspice.sourceforge.io/docs/ngspice-manual.pdf

Recognizing a response is not binary attestation, license validation or a
simulation test. Unsupported banner formats remain unknown. OCEAN has no
verified version-only flag in this slice and is intentionally excluded.
"""

from pathlib import PurePosixPath
import re

from spiceowl.domain.common import DomainError


_FLAGS = {"spectre": ("-W",), "virtuoso": ("-W",), "ngspice": ("--version",)}
_PATH = re.compile(r"/[A-Za-z0-9_./+-]+", re.ASCII)
_PATTERNS = {
    "spectre": re.compile(
        r"[ \t]*sub-version[ \t]+([0-9]+(?:\.[0-9]+){2,4}(?:\.isr[0-9]+)?)[ \t]*",
        re.IGNORECASE | re.ASCII,
    ),
    "virtuoso": re.compile(
        r"[ \t]*sub-version[ \t]+(IC(?:ADVM)?[0-9]+(?:\.[0-9]+){1,2}"
        r"-(?:32|64)b(?:\.(?:[0-9]+|ISR[0-9]+)){1,4})[ \t]*",
        re.IGNORECASE | re.ASCII,
    ),
    "ngspice": re.compile(
        r"[ \t*]*ngspice(?:-|[ \t]+)([0-9]+(?:\.[0-9]+)*(?:\+|-dev)?)"
        r"(?:[ \t]*:[^\r\n]*|[ \t*]*)",
        re.IGNORECASE | re.ASCII,
    ),
}


def version_arguments(kind) -> tuple[str, ...]:
    """Return fixed flags; callers cannot supply their own arguments."""
    if not isinstance(kind, str) or kind not in _FLAGS:
        raise DomainError("Unsupported version probe tool.", "invalid_host_profile")
    return _FLAGS[kind]


def validate_tool(data) -> dict:
    if not isinstance(data, dict) or data.keys() != {"kind", "executable"}:
        raise DomainError("Invalid host tool fields.", "invalid_host_profile")
    kind = data["kind"]
    version_arguments(kind)
    executable = data["executable"]
    if not isinstance(executable, str) or len(executable) > 1024 or _PATH.fullmatch(executable) is None:
        raise DomainError("Tool executable must be a bounded absolute POSIX path.", "invalid_host_profile")
    parts = executable.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(executable).name != kind:
        raise DomainError("Tool path must end in its declared executable name.", "invalid_host_profile")
    return {"kind": kind, "executable": executable}


def parse_version(kind, output) -> str:
    """Return only a bounded version token, discarding all raw output."""
    version_arguments(kind)
    if not isinstance(output, str) or len(output) > 8192:
        raise DomainError("Unrecognized tool version response.", "unrecognized_version")
    matches = []
    for line in output.splitlines():
        for tool, pattern in _PATTERNS.items():
            match = pattern.fullmatch(line)
            if match:
                matches.append((tool, match.group(1)))
    if len(matches) != 1 or matches[0][0] != kind or len(matches[0][1]) > 80:
        raise DomainError("Unrecognized or ambiguous tool version response.", "unrecognized_version")
    return matches[0][1]
