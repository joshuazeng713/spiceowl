"""Read-only, explicitly registered IC Workbench gateway integration.

IC Workbench owns execution and native run IDs. This module never performs SSH,
submits work, collects artifacts, follows redirects, or retries a failed read.
"""

from contextlib import contextmanager
import copy
import fcntl
import http.client
import json
import math
import re
import socket
import threading
import time
from urllib.parse import quote, urlencode, urlsplit

from spiceowl.domain.common import DomainError, require_id, utcnow
from spiceowl.storage.sqlite import Store


BACKEND = "ic-workbench"
CONFIG_FIELDS = frozenset({"id", "label", "base_url", "project_id"})
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 5.0
MAX_CACHED_RUNS = 100
ARTIFACT_NAMES = frozenset({"full-results.tar.gz", "result-summary.json", "archive-files.json",
                            "latest-status.log", "waveforms.csv", "waveforms.json", "waveforms.png"})
IDENTITY_FIELDS = frozenset({"run_id", "project_id", "original_project_id", "source_commit",
                             "configuration", "compiled_inputs_sha256", "library_id", "cell_id"})
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def validate_gateway_config(data):
    """Validate a local registration without connecting to the gateway."""
    if not isinstance(data, dict) or set(data) != CONFIG_FIELDS:
        raise DomainError("An IC gateway requires id, label, base_url and project_id.")
    require_id(data["id"], "gateway ID")
    label = data["label"]
    if (not isinstance(label, str) or not 1 <= len(label.strip()) <= 80
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in label)):
        raise DomainError("Enter a gateway label between 1 and 80 characters.")
    project = data["project_id"]
    if not isinstance(project, str) or not _PROJECT.fullmatch(project):
        raise DomainError("Invalid IC project ID.")
    base_url = data["base_url"]
    # A literal IPv4 loopback address avoids DNS, proxy and rebinding behavior.
    match = (re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/?", base_url)
             if isinstance(base_url, str) else None)
    if not match or int(match[1]) > 65535:
        raise DomainError("Use an explicit IC gateway at http://127.0.0.1:PORT.")
    return dict(data, label=label.strip(), base_url=base_url.rstrip("/"))


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate key")
        obj[key] = value
    return obj


def _invalid_constant(_):
    raise ValueError("nonfinite number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite number")
    return number


def decode_envelope(raw):
    """Decode bounded strict JSON; remote error text is never exposed."""
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise DomainError("The IC gateway response exceeded its size limit.", "ic_gateway_response_limit")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant, parse_float=_finite_float)
    except (UnicodeError, ValueError, RecursionError):
        raise DomainError("The IC gateway returned invalid JSON.", "ic_gateway_protocol") from None
    if (not isinstance(result, dict) or set(result) != {"ok", "data"}
            or result["ok"] is not True or not isinstance(result["data"], dict)):
        raise DomainError("The IC gateway returned an unsupported response.", "ic_gateway_protocol")
    return result["data"]


def _check(condition, *, identity=False):
    if not condition:
        raise DomainError("The IC gateway returned inconsistent run identity." if identity else
                          "The IC gateway returned an unsupported supervision record.",
                          "ic_gateway_identity" if identity else "ic_gateway_protocol")


def _fields(value, names, optional=""):
    required = set(names.split())
    _check(isinstance(value, dict) and required <= set(value) <= required | set(optional.split()))


def _enum(value, choices):
    _check(isinstance(value, str) and value in choices)


def _text(value, limit=240, nullable=True):
    _check(value is None and nullable or isinstance(value, str) and len(value) <= limit
           and all(ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _hash(value, length=64):
    _check(value is None or isinstance(value, str) and re.fullmatch(r"[a-f0-9]{%d}" % length, value))


def _run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", value):
        raise DomainError("Invalid IC run ID.")
    return value


def _submission_key(value):
    if (not isinstance(value, str) or not 8 <= len(value) <= 160
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise DomainError("IC submission keys contain 8–160 printable characters.")
    return value


def validate_run(data, project_id, expected=None):
    """Validate IC's frozen v1 projection without inventing missing evidence."""
    _fields(data, "run_id project_id original_project_id association_basis source_commit configuration "
                  "compiled_inputs_sha256 state execution observation data specification latest_observed_at "
                  "record_updated_at issues waveform_run_id artifacts", optional="library_id cell_id")
    for name in ("library_id", "cell_id"):
        value = data.get(name)
        _check(value is None or isinstance(value, str) and _PROJECT.fullmatch(value), identity=True)
    _check(isinstance(data["run_id"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,180}", data["run_id"]), identity=True)
    for name in ("project_id", "original_project_id", "configuration", "state", "latest_observed_at", "record_updated_at", "waveform_run_id"):
        _text(data[name])
    _check(data["project_id"] == project_id, identity=True)
    _check(isinstance(data["association_basis"], str) and data["association_basis"] in {"recorded_project_id", "recorded_plan_design_id"}, identity=True)
    if data["association_basis"] == "recorded_project_id":
        _check(data["original_project_id"] == project_id, identity=True)
    _hash(data["source_commit"], 40)
    _hash(data["compiled_inputs_sha256"])
    execution = data["execution"]
    _fields(execution, "state raw_state exit_code observed_at")
    _enum(execution["state"], {"queued", "running", "succeeded", "failed", "unknown"})
    _check(execution["exit_code"] is None or type(execution["exit_code"]) is int)
    _text(execution["raw_state"])
    _text(execution["observed_at"])
    raw_state, exit_code = execution["raw_state"], execution["exit_code"]
    if exit_code == 0 and raw_state == "simulator_complete_pending_measurements":
        evidenced_state = "succeeded"
    elif exit_code is not None and exit_code != 0 or raw_state in {
            "simulator_failed", "input_hash_failure", "job_setup_failed", "preflight_failed"}:
        evidenced_state = "failed"
    elif raw_state in {"running", "running_observed"}:
        evidenced_state = "running"
    elif raw_state in {"queued", "preflight"}:
        evidenced_state = "queued"
    else:
        evidenced_state = "unknown"
    _check(execution["state"] == evidenced_state)
    observation = data["observation"]
    _fields(observation, "state observed_at")
    _enum(observation["state"], {"recorded", "missing"})
    _text(observation["observed_at"])
    _check(observation["observed_at"] == execution["observed_at"] == data["latest_observed_at"])
    _check((observation["state"] == "recorded") == bool(observation["observed_at"]))
    collection = data["data"]
    _fields(collection, "state raw_retained")
    _enum(collection["state"], {"not_collected", "collecting", "collected", "failed", "unknown"})
    _check(collection["raw_retained"] is None or type(collection["raw_retained"]) is bool)
    specification = data["specification"]
    _fields(specification, "state metrics")
    _enum(specification["state"], {"reported", "not_evaluated"})
    _check(isinstance(specification["metrics"], list) and len(specification["metrics"]) <= 16)
    _check((specification["state"] == "reported") == bool(specification["metrics"]))
    for metric in specification["metrics"]:
        _fields(metric, "name value unit passed scope")
        _text(metric["name"], 160, False)
        _text(metric["unit"])
        _text(metric["scope"])
        _check(metric["passed"] is None or type(metric["passed"]) is bool)
        _check(metric["value"] is None or type(metric["value"]) in (int, float)
               and (type(metric["value"]) is int or math.isfinite(metric["value"])))
    _check(isinstance(data["issues"], list) and len(data["issues"]) <= 8)
    for issue in data["issues"]:
        _fields(issue, "code message")
        _text(issue["code"], 80, False)
        _text(issue["message"], 240, False)
    _check(data["waveform_run_id"] is None or data["waveform_run_id"] == data["run_id"]
           and data["source_commit"] is not None, identity=True)
    _check(isinstance(data["artifacts"], list) and len(data["artifacts"]) <= 7)
    names = set()
    for artifact in data["artifacts"]:
        _fields(artifact, "name bytes sha256 url digest_source")
        _check(isinstance(artifact["name"], str) and artifact["name"] in ARTIFACT_NAMES and artifact["name"] not in names)
        names.add(artifact["name"])
        _check(type(artifact["bytes"]) is int and artifact["bytes"] >= 0)
        _hash(artifact["sha256"])
        _check(artifact["digest_source"] == ("stored_record" if artifact["sha256"] else "unavailable"))
        _check(artifact["url"] == "/api/jobs/" + data["run_id"] + "/artifacts/" + artifact["name"], identity=True)
    if expected is not None:
        _check(isinstance(expected, dict) and set(expected) <= IDENTITY_FIELDS, identity=True)
        for key, value in expected.items():
            _check(data.get(key) == value, identity=True)
    return copy.deepcopy(data)


def validate_supervision(data, project_id):
    _fields(data, "schema project_id read_at source runs scanned_records unavailable_records truncated single_site")
    _check(data["schema"] == "ic.supervision.snapshot.v1" and data["source"] == "stored_observations")
    _check(data["project_id"] == project_id, identity=True)
    _text(data["read_at"], 240, False)
    _check(data["single_site"] is True and type(data["truncated"]) is bool)
    _check(type(data["scanned_records"]) is int and 0 <= data["scanned_records"] <= 200)
    _check(type(data["unavailable_records"]) is int and 0 <= data["unavailable_records"] <= data["scanned_records"])
    _check(isinstance(data["runs"], list) and len(data["runs"]) <= 25)
    runs = [validate_run(run, project_id) for run in data["runs"]]
    _check(len({run["run_id"] for run in runs}) == len(runs), identity=True)
    _check(len(runs) <= data["scanned_records"])
    return copy.deepcopy(data)


def validate_submission(data, key, project_id, expected=None):
    _fields(data, "schema submission_key read_at found run_id run")
    _check(data["schema"] == "ic.submission.lookup.v1")
    _check(data["submission_key"] == key, identity=True)
    _text(data["read_at"], 240, False)
    _check(type(data["found"]) is bool)
    if data["found"]:
        validate_run(data["run"], project_id, expected)
        _check(data["run_id"] == data["run"]["run_id"], identity=True)
    else:
        _check(data["run_id"] is None and data["run"] is None, identity=True)
    return copy.deepcopy(data)


class LoopbackGatewayClient:
    """Finite GET-only client with a byte budget and a whole-request deadline."""

    def __init__(self, config):
        self.config = validate_gateway_config(config)

    def _get(self, path):
        if not isinstance(path, str) or len(path.encode("utf-8")) > 4096 or any(ord(c) < 32 for c in path):
            raise DomainError("The IC gateway request exceeded its URL limit.")
        parsed = urlsplit(self.config["base_url"])
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=REQUEST_TIMEOUT)
        expired = threading.Event()
        connected_socket = []
        deadline = time.monotonic() + REQUEST_TIMEOUT

        def abort():
            expired.set()
            stream = connected_socket[0] if connected_socket else connection.sock
            if stream is not None:
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()

        timer = threading.Timer(REQUEST_TIMEOUT, abort)
        timer.daemon = True
        timer.start()
        try:
            connection.connect()
            connected_socket.append(connection.sock)
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError()
            connection.request("GET", path, headers={"Accept": "application/json", "Connection": "close"})
            response = connection.getresponse()
            if response.status != 200:
                raise DomainError("The IC gateway read was not accepted.", "ic_gateway_http")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise DomainError("The IC gateway returned unsupported encoding.", "ic_gateway_protocol")
            media_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise DomainError("The IC gateway response is not JSON.", "ic_gateway_protocol")
            length = response.getheader("Content-Length")
            if length is not None and response.getheader("Transfer-Encoding") is not None:
                raise DomainError("The IC gateway returned ambiguous framing.", "ic_gateway_protocol")
            if length is not None:
                if not re.fullmatch(r"[0-9]+", length):
                    raise DomainError("The IC gateway returned invalid framing.", "ic_gateway_protocol")
                if len(length) > 10 or int(length) > MAX_RESPONSE_BYTES:
                    raise DomainError("The IC gateway response exceeded its size limit.", "ic_gateway_response_limit")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if expired.is_set() or remaining <= 0:
                    raise TimeoutError()
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise DomainError("The IC gateway response exceeded its size limit.", "ic_gateway_response_limit")
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError()
            if length is not None and size != int(length):
                raise DomainError("The IC gateway response was incomplete.", "ic_gateway_protocol")
            return decode_envelope(b"".join(chunks))
        except (OSError, http.client.HTTPException):
            raise DomainError("The IC gateway could not be observed. Previous run evidence is preserved.",
                              "ic_gateway_transport") from None
        finally:
            timer.cancel()
            connection.close()

    def supervision(self):
        return self._get("/api/supervision?" + urlencode({"project_id": self.config["project_id"]}))

    def submission(self, key):
        return self._get("/api/submissions/" + quote(_submission_key(key), safe=""))


def gateway_summary(record):
    """Only selected metadata and safe observation status reach the console."""
    return {key: record[key] for key in ("id", "label", "project_id", "backend", "created_at", "observation")}


class ICGatewayBackend:
    """Persist registration and read projections, never lifecycle authority."""

    def __init__(self, directory):
        self.store = Store(directory)

    def register(self, data):
        config = validate_gateway_config(data)
        with self.store.transaction() as db:
            existing = db.execute("SELECT body FROM records WHERE kind='ic_gateway' AND id=?", (config["id"],)).fetchone()
            if existing:
                record = json.loads(existing[0])
                if {key: record[key] for key in CONFIG_FIELDS} != config:
                    raise DomainError("This gateway ID already has different settings. Register a new ID.", "conflict")
                return gateway_summary(record), False
            record = config | {"backend": BACKEND, "created_at": utcnow(),
                               "observation": {"state": "unobserved", "checked_at": None,
                                               "last_success_at": None, "error": None}}
            self.store._put(db, "ic_gateway", config["id"], record)
            return gateway_summary(record), True

    def list(self):
        return [gateway_summary(record) for record in self.store.list("ic_gateway")]

    def inspect(self, gateway_id):
        return self.store.get("ic_gateway", require_id(gateway_id, "gateway ID"))

    @staticmethod
    def _config(record):
        return {key: record[key] for key in CONFIG_FIELDS}

    @staticmethod
    def _run_kind(gateway_id):
        return "ic_run:" + require_id(gateway_id, "gateway ID")

    def runs(self, gateway_id):
        """Return at most 100 saved projections, without contacting IC."""
        self.inspect(gateway_id)
        with self.store.transaction() as db:
            rows = db.execute("SELECT body FROM records WHERE kind=? ORDER BY rowid DESC LIMIT ?",
                              (self._run_kind(gateway_id), MAX_CACHED_RUNS)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def run(self, gateway_id, run_id):
        """Read one exact native run ID within its registered gateway."""
        self.inspect(gateway_id)
        return self.store.get(self._run_kind(gateway_id), _run_id(run_id))

    @contextmanager
    def _read_lock(self, gateway_id):
        path = self.store.directory / (".ic-gateway-" + require_id(gateway_id) + ".lock")
        with path.open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DomainError("A read of this IC gateway is already in progress.", "ic_gateway_busy") from None
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _save_run(self, db, gateway_id, run, source_read_at, checked_at):
        kind = self._run_kind(gateway_id)
        existing = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, run["run_id"])).fetchone()
        prior = json.loads(existing[0]) if existing else None
        if prior:
            # New explicit evidence can fill a historical null; known identity
            # cannot change or disappear on a later gateway read.
            for field in IDENTITY_FIELDS:
                if prior.get(field) is not None:
                    _check(prior[field] == run.get(field), identity=True)
        observation = run["observation"] | {"source_state": run["observation"]["state"],
            "checked_at": checked_at, "last_success_at": checked_at, "error": None}
        projection = run | {"schema": "spiceowl.ic-run.v1", "gateway_id": gateway_id,
            "backend": BACKEND, "source": "stored_observations", "source_read_at": source_read_at,
            "submission_key": None, "imported": True,
            "synthetic": any(issue["code"] == "synthetic_fixture" for issue in run["issues"]),
            "measurement": {"state": "indeterminate", "reason": "measurement_validity_not_attested"},
            "observation": observation, "first_seen_at": prior["first_seen_at"] if prior else checked_at}
        self.store._put(db, kind, run["run_id"], projection)
        return projection

    def refresh(self, gateway_id):
        """Explicitly read one stored snapshot; persist the complete batch or none."""
        gateway = self.inspect(gateway_id)
        with self._read_lock(gateway_id):
            checked_at = utcnow()
            try:
                snapshot = validate_supervision(LoopbackGatewayClient(self._config(gateway)).supervision(), gateway["project_id"])
                with self.store.transaction() as db:
                    present = {run["run_id"] for run in snapshot["runs"]}
                    for run in snapshot["runs"]:
                        self._save_run(db, gateway_id, run, snapshot["read_at"], checked_at)
                    kind = self._run_kind(gateway_id)
                    for row in db.execute("SELECT id,body FROM records WHERE kind=?", (kind,)).fetchall():
                        if row["id"] not in present:
                            previous = json.loads(row["body"])
                            previous["observation"] = previous["observation"] | {
                                "state": "unobserved", "checked_at": checked_at,
                                "error": {"code": "ic_run_not_in_snapshot", "message": "This run was absent from the bounded stored snapshot."}}
                            self.store._put(db, kind, row["id"], previous)
                    current = self.store._get(db, "ic_gateway", gateway_id)
                    current["observation"] = {"state": "readable", "checked_at": checked_at,
                                               "last_success_at": checked_at, "error": None}
                    current["snapshot"] = {key: value for key, value in snapshot.items() if key != "runs"}
                    self.store._put(db, "ic_gateway", gateway_id, current)
                return {"gateway": gateway_summary(current), "snapshot": current["snapshot"],
                        "runs": self.runs(gateway_id), "updated": True}
            except DomainError as error:
                return self._record_failure(gateway_id, error, checked_at)

    def lookup_submission(self, gateway_id, key, expected_identity=None):
        """Read an existing key mapping; a miss never permits resubmission."""
        gateway = self.inspect(gateway_id)
        _submission_key(key)
        if expected_identity is not None and (not isinstance(expected_identity, dict)
                                               or not set(expected_identity) <= IDENTITY_FIELDS):
            raise DomainError("Expected identity must contain only IC run identity fields.")
        kind = "ic_submission:" + gateway_id
        with self._read_lock(gateway_id):
            checked_at = utcnow()
            try:
                lookup = validate_submission(LoopbackGatewayClient(self._config(gateway)).submission(key), key,
                                             gateway["project_id"], expected_identity)
                with self.store.transaction() as db:
                    row = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
                    prior = json.loads(row[0]) if row else None
                    previous_run = prior.get("last_known_run_id") if prior else None
                    if previous_run and lookup["found"]:
                        _check(previous_run == lookup["run_id"], identity=True)
                    if lookup["found"]:
                        self._save_run(db, gateway_id, lookup["run"], lookup["read_at"], checked_at)
                    result = lookup | {"gateway_id": gateway_id, "backend": BACKEND,
                        "resubmit_authorized": False, "last_known_run_id": lookup["run_id"] or previous_run,
                        "observation": {"state": "readable", "checked_at": checked_at, "last_success_at": checked_at, "error": None}}
                    self.store._put(db, kind, key, result)
                return result
            except DomainError as error:
                with self.store.transaction() as db:
                    row = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
                    previous = json.loads(row[0]) if row else {
                        "schema": "ic.submission.lookup.v1", "submission_key": key, "read_at": None,
                        "found": None, "run_id": None, "run": None, "last_known_run_id": None,
                        "gateway_id": gateway_id, "backend": BACKEND, "resubmit_authorized": False,
                        "observation": {"last_success_at": None}}
                    previous["observation"] = previous["observation"] | {"state": "unobserved", "checked_at": checked_at,
                                                                            "error": {"code": error.code, "message": str(error)}}
                    self.store._put(db, kind, key, previous)
                return previous

    def _record_failure(self, gateway_id, error, checked_at):
        """Atomically lose observability without replacing prior evidence."""
        safe = {"code": error.code, "message": str(error)}
        with self.store.transaction() as db:
            gateway = self.store._get(db, "ic_gateway", gateway_id)
            observation = gateway["observation"] | {"state": "unobserved", "checked_at": checked_at, "error": safe}
            gateway = gateway | {"observation": observation}
            self.store._put(db, "ic_gateway", gateway_id, gateway)
            kind = self._run_kind(gateway_id)
            records = db.execute("SELECT id,body FROM records WHERE kind=?", (kind,)).fetchall()
            for row in records:
                run = json.loads(row["body"])
                run["observation"] = run["observation"] | {"state": "unobserved", "checked_at": checked_at, "error": safe}
                self.store._put(db, kind, row["id"], run)
        return {"gateway": gateway_summary(gateway), "runs": self.runs(gateway_id), "updated": False}
