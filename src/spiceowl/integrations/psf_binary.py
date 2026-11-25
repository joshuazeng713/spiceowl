"""Streaming PSF Binary, PSFXL, and PSF ASCII waveform reader, envelope downsampler, and cycle slicer.

Designed for large analog/mixed-signal simulations (e.g., thousands of cycles
and multi-GB raw files) without loading full waveform files into memory or
transferring gigabytes over SSH:

- Pure Python standard library only (macOS, Linux, Windows compatible).
- Supports Cadence PSF Binary (``psfbin``, big-endian TOC with non-windowed or
  windowed VALUE blocks), PSFXL metadata + binary ``.dat`` trace bundles, and
  PSF ASCII (``psfascii``) through one uniform streaming interface.
- Strictly separates lossy visual previews (``representation="downsampled_preview"``,
  Min-Max envelope buckets that never drop glitches/overshoot) from lossless
  exact slices (``representation="exact_window"`` / ``"exact_cycle"``) suitable
  for scientific measurements and ``adctools``.
- Provides Cycle Slicer / Cycle-by-Cycle Metrics / Worst-Case Cycle detection /
  Cycle Overlay (eye-style alignment) so multi-thousand-cycle runs expose both
  macroscopic drift and 100% exact intra-cycle waveform details in <200 KB.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct

try:
    from spiceowl.domain.common import DomainError
    from spiceowl.integrations.spectre_results import PsfError, parse_psf_ascii
except ImportError:  # pragma: no cover - standalone remote Linux execution fallback
    class DomainError(Exception):
        def __init__(self, message, code="invalid_input"):
            super().__init__(message)
            self.code = code

    class PsfError(DomainError):
        def __init__(self, message="Unsupported or invalid PSF.", code="psf_parse"):
            super().__init__(message, code)

    def parse_psf_ascii(_text):
        raise PsfError("Remote standalone fallback supports PSF Binary and PSFXL directly; collect PSF ASCII locally.", "psf_unsupported")

# ---------------------------------------------------------------------------
# Constants & safety bounds
# ---------------------------------------------------------------------------

SCHEMA_INDEX = "spiceowl.waveform-index.v1"
SCHEMA_QUERY = "spiceowl.waveform-query.v1"
SCHEMA_CYCLES = "spiceowl.waveform-cycles.v1"

PSF_BIN_MAGIC = 0x00000400  # 1024, big-endian uint32 at EOF
_SEC_HEADER = 0
_SEC_TYPE = 1
_SEC_SWEEP = 2
_SEC_TRACE = 3
_SEC_VALUE = 4

_REC_TYPE_DEF = 16
_REC_GROUP = 17
_REC_WINDOW = 20
_REC_SECTION = 21
_REC_INDEX = 22
_REC_PROP_STR = 33
_REC_PROP_INT = 34
_REC_PROP_FLT = 35

_DTYPE_INT8 = 1
_DTYPE_STR = 2
_DTYPE_INT32 = 5
_DTYPE_FLOAT64 = 11
_DTYPE_COMPLEX128 = 12
_DTYPE_STRUCT = 16

MAX_INDEX_FILES = 64
MAX_SIGNALS_PER_QUERY = 16
MAX_PREVIEW_POINTS = 16_384
MAX_EXACT_SAMPLES = 262_144
MAX_CYCLES_REPORTED = 4096
MAX_OVERLAY_CYCLES = 32
MAX_FULL_HASH_BYTES = 16 * 1024 * 1024  # Hash header+tail fingerprint above 16 MiB


def _be_u32(buf: bytes, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def _be_i32(buf: bytes, off: int) -> int:
    return struct.unpack_from(">i", buf, off)[0]


def _be_f64(buf: bytes, off: int) -> float:
    return struct.unpack_from(">d", buf, off)[0]


def _read_psf_str(buf: bytes, off: int) -> tuple[str, int]:
    if off + 4 > len(buf):
        raise PsfError("Truncated PSF binary string length.", "psf_parse")
    length = _be_u32(buf, off)
    off += 4
    if length > 65536 or off + length > len(buf):
        raise PsfError("Invalid or truncated PSF binary string.", "psf_parse")
    text = buf[off:off + length].decode("utf-8", errors="replace")
    # Strings are 4-byte aligned in PSF binary.
    padded = (length + 3) & ~3
    return text, off + padded


def _pack_psf_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    pad = (-len(raw)) & 3
    return struct.pack(">I", len(raw)) + raw + (b"\x00" * pad)


def _file_fingerprint(path: Path) -> tuple[int, str, str]:
    """Return (size_bytes, digest_hex, digest_scope) in O(1) time for huge files."""
    size = path.stat().st_size
    h = hashlib.sha256()
    if size <= MAX_FULL_HASH_BYTES:
        with path.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return size, h.hexdigest(), "full_file_sha256"
    # Bounded head + tail + size fingerprint so indexing multi-GB files takes milliseconds.
    h.update(struct.pack(">Q", size))
    with path.open("rb") as f:
        h.update(f.read(65536))
        f.seek(max(0, size - 65536))
        h.update(f.read(65536))
    return size, h.hexdigest(), "head_tail_size_sha256"


def detect_waveform_format(path: str | Path) -> str:
    """Detect waveform format: 'psfbin', 'psfxl', 'psfascii', or 'unknown'."""
    p = Path(path)
    if not p.is_file():
        return "unknown"
    size = p.stat().st_size
    if size < 8:
        return "unknown"
    name_lower = p.name.lower()
    if name_lower.endswith(".psfxl") or (p.parent / (p.name + ".dat")).is_file():
        with p.open("rb") as f:
            head = f.read(256)
        if head.startswith(b"PSFXL") or b"PSFXL" in head[:128] or b"spiceowl.psfxl-bundle.v1" in head:
            return "psfxl"
    with p.open("rb") as f:
        f.seek(size - 4)
        tail = f.read(4)
        if len(tail) == 4 and _be_u32(tail, 0) == PSF_BIN_MAGIC:
            return "psfbin"
        f.seek(0)
        head = f.read(4096)
    if b"\x00" not in head:
        text = head.decode("utf-8", errors="ignore")
        if "HEADER" in text and ("TYPE" in text or "SWEEP" in text or "VALUE" in text):
            return "psfascii"
    return "unknown"


# ---------------------------------------------------------------------------
# Cadence PSF Binary writer (used by tests and synthetic multi-cycle demos)
# ---------------------------------------------------------------------------

def write_psf_binary(
    path: str | Path,
    *,
    sweep_name: str,
    sweep_values: list[float],
    signals: dict[str, list[float] | list[complex]],
    sweep_unit: str = "s",
    signal_units: dict[str, str] | None = None,
    header_props: dict[str, str | int | float] | None = None,
    windowed: bool = False,
    window_samples: int = 256,
) -> Path:
    """Write a valid big-endian Cadence PSF Binary file (scalar real or complex).

    Produces standard TOC trailer with magic ``0x00000400`` and supports both
    non-windowed point-interleaved VALUE frames and windowed block-interleaved
    VALUE frames for random-access ``seek()`` benchmarking and unit tests.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    n_samples = len(sweep_values)
    sig_names = list(signals.keys())
    units = signal_units or {}
    for name, vals in signals.items():
        if len(vals) != n_samples:
            raise ValueError(f"Signal {name!r} length {len(vals)} != sweep length {n_samples}")

    def _pack_props(props: dict) -> bytes:
        out = bytearray()
        for k, v in props.items():
            if isinstance(v, str):
                out += struct.pack(">I", _REC_PROP_STR) + _pack_psf_str(k) + _pack_psf_str(v)
            elif isinstance(v, bool) or isinstance(v, int):
                out += struct.pack(">I", _REC_PROP_INT) + _pack_psf_str(k) + struct.pack(">i", int(v))
            elif isinstance(v, float):
                out += struct.pack(">I", _REC_PROP_FLT) + _pack_psf_str(k) + struct.pack(">d", v)
        return bytes(out)

    def _wrap_section(body: bytes, start_offset: int) -> bytes:
        end_offset = start_offset + 8 + len(body)
        return struct.pack(">II", _REC_SECTION, end_offset) + body

    is_complex = {
        name: any(isinstance(v, complex) for v in signals[name][:4])
        for name in sig_names
    }

    blob = bytearray()
    toc: list[tuple[int, int]] = []

    # Section 0: HEADER
    h_props = {"PSFversion": "1.00", "simulator": "spectre"}
    if windowed:
        h_props["PSF window size"] = window_samples * 8
    if header_props:
        h_props.update(header_props)
    toc.append((_SEC_HEADER, len(blob)))
    blob += _wrap_section(_pack_props(h_props), len(blob))

    # Section 1: TYPE (type 1 = real float64, type 2 = complex128)
    type_body = bytearray()
    type_body += struct.pack(">II", _REC_TYPE_DEF, 1) + _pack_psf_str("real") + struct.pack(">II", 0, _DTYPE_FLOAT64)
    type_body += struct.pack(">II", _REC_TYPE_DEF, 2) + _pack_psf_str("complex") + struct.pack(">II", 0, _DTYPE_COMPLEX128)
    toc.append((_SEC_TYPE, len(blob)))
    blob += _wrap_section(bytes(type_body), len(blob))

    # Section 2: SWEEP (id 100)
    sweep_id = 100
    sweep_body = (
        struct.pack(">II", _REC_TYPE_DEF, sweep_id)
        + _pack_psf_str(sweep_name)
        + struct.pack(">I", 1)
        + _pack_props({"units": sweep_unit})
    )
    toc.append((_SEC_SWEEP, len(blob)))
    blob += _wrap_section(sweep_body, len(blob))

    # Section 3: TRACE (ids 101 .. 101+M-1)
    trace_body = bytearray()
    trace_ids: list[int] = []
    for idx, name in enumerate(sig_names):
        tid = 101 + idx
        trace_ids.append(tid)
        type_ref = 2 if is_complex[name] else 1
        t_props = {"units": units[name]} if name in units else {}
        trace_body += (
            struct.pack(">II", _REC_TYPE_DEF, tid)
            + _pack_psf_str(name)
            + struct.pack(">I", type_ref)
            + _pack_props(t_props)
        )
    toc.append((_SEC_TRACE, len(blob)))
    blob += _wrap_section(bytes(trace_body), len(blob))

    # Section 4: VALUE
    toc.append((_SEC_VALUE, len(blob)))
    val_body = bytearray()
    if not windowed:
        # Point-interleaved standard swept VALUE frames:
        # [16, sweep_id, f64(sweep)] followed by [16, trace_id, f64 | complex128] for each trace
        for i in range(n_samples):
            val_body += struct.pack(">IId", _REC_TYPE_DEF, sweep_id, float(sweep_values[i]))
            for name, tid in zip(sig_names, trace_ids):
                v = signals[name][i]
                if is_complex[name]:
                    c = complex(v)
                    val_body += struct.pack(">IIdd", _REC_TYPE_DEF, tid, c.real, c.imag)
                else:
                    val_body += struct.pack(">IId", _REC_TYPE_DEF, tid, float(v))
    else:
        # Windowed block-interleaved VALUE frames (Cadence windowed PSF):
        # Each window: [20, count_in_window, window_byte_len]
        #   sweep_values[count] as >d
        #   for each trace: values[count] as >d or >dd
        ws = max(1, int(window_samples))
        for start in range(0, n_samples, ws):
            end = min(n_samples, start + ws)
            cnt = end - start
            chunk = bytearray()
            chunk += struct.pack(f">{cnt}d", *(float(x) for x in sweep_values[start:end]))
            for name in sig_names:
                vals = signals[name][start:end]
                if is_complex[name]:
                    flat = []
                    for v in vals:
                        c = complex(v)
                        flat.append(c.real)
                        flat.append(c.imag)
                    chunk += struct.pack(f">{2 * cnt}d", *flat)
                else:
                    chunk += struct.pack(f">{cnt}d", *(float(v) for v in vals))
            val_body += struct.pack(">III", _REC_WINDOW, cnt, len(chunk)) + chunk

    blob += _wrap_section(bytes(val_body), len(blob))

    # EOF TOC + data_size + magic
    data_size = len(blob)
    for sec_id, sec_off in toc:
        blob += struct.pack(">II", sec_id, sec_off)
    blob += struct.pack(">II", data_size, PSF_BIN_MAGIC)

    target.write_bytes(bytes(blob))
    return target


def write_psfxl_bundle(
    path: str | Path,
    *,
    sweep_name: str,
    sweep_values: list[float],
    signals: dict[str, list[float]],
    sweep_unit: str = "s",
    signal_units: dict[str, str] | None = None,
) -> Path:
    """Write a column-major PSFXL metadata + ``.dat`` binary bundle for tests/demos.

    Stores each trace contiguously in ``<path>.dat`` as little-endian IEEE-754
    float64 arrays so queries can ``seek()`` directly to any signal & time slice
    in O(1) I/O without touching unrelated traces.
    """
    meta_path = Path(path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    dat_path = meta_path.parent / (meta_path.name + ".dat")
    n = len(sweep_values)
    units = signal_units or {}

    offsets: dict[str, dict] = {}
    with dat_path.open("wb") as f:
        sweep_off = f.tell()
        f.write(struct.pack(f"<{n}d", *(float(x) for x in sweep_values)))
        for name, vals in signals.items():
            if len(vals) != n:
                raise ValueError(f"Signal {name!r} length mismatch")
            off = f.tell()
            f.write(struct.pack(f"<{n}d", *(float(v) for v in vals)))
            offsets[name] = {
                "offset": off,
                "dtype": "float64",
                "unit": units.get(name),
            }

    meta = {
        "schema": "spiceowl.psfxl-bundle.v1",
        "format": "psfxl",
        "dat_file": dat_path.name,
        "byte_order": "<",
        "sample_count": n,
        "sweep": {
            "name": sweep_name,
            "unit": sweep_unit,
            "offset": sweep_off,
            "min": float(sweep_values[0]) if n else None,
            "max": float(sweep_values[-1]) if n else None,
        },
        "traces": offsets,
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return meta_path


# ---------------------------------------------------------------------------
# Streaming Readers (PSF Binary, PSFXL, PSF ASCII)
# ---------------------------------------------------------------------------

class _PsfBinaryReader:
    """Zero-copy/streaming reader for big-endian Cadence PSF Binary files."""

    def __init__(self, path: Path):
        self.path = path
        self.size = path.stat().st_size
        self.header: dict = {}
        self.types: dict[int, dict] = {}
        self.sweep: dict = {}
        self.traces: list[dict] = []
        self.trace_by_name: dict[str, dict] = {}
        self.sections: dict[int, tuple[int, int]] = {}  # sec_id -> (body_start, body_end)
        self._windowed: bool = False
        self._windows: list[tuple[int, int, int, float, float]] = []  # (data_off, count, sample_start, x_min, x_max)
        self._frame_size: int = 0
        self._value_body_start: int = 0
        self._value_body_end: int = 0
        self._sample_count: int = 0
        self._x_min: float | None = None
        self._x_max: float | None = None
        self._parse_metadata()

    def _read_props(self, buf: bytes, off: int, end: int) -> tuple[dict, int]:
        props: dict = {}
        while off + 4 <= end:
            tag = _be_u32(buf, off)
            if tag == _REC_PROP_STR:
                k, off = _read_psf_str(buf, off + 4)
                v, off = _read_psf_str(buf, off)
                props[k] = v
            elif tag == _REC_PROP_INT:
                k, off = _read_psf_str(buf, off + 4)
                if off + 4 > end:
                    raise PsfError("Truncated integer property.", "psf_parse")
                props[k] = _be_i32(buf, off)
                off += 4
            elif tag == _REC_PROP_FLT:
                k, off = _read_psf_str(buf, off + 4)
                if off + 8 > end:
                    raise PsfError("Truncated float property.", "psf_parse")
                props[k] = _be_f64(buf, off)
                off += 8
            else:
                break
        return props, off

    def _parse_metadata(self) -> None:
        if self.size < 16:
            raise PsfError("PSF binary file is too small.", "psf_parse")
        with self.path.open("rb") as f:
            f.seek(self.size - 8)
            data_size, magic = struct.unpack(">II", f.read(8))
            if magic != PSF_BIN_MAGIC or data_size > self.size - 8 or data_size < 8:
                raise PsfError("Invalid PSF binary magic or TOC size.", "psf_parse")
            toc_bytes = self.size - 8 - data_size
            if toc_bytes <= 0 or toc_bytes % 8 != 0 or toc_bytes > 4096:
                raise PsfError("Invalid PSF binary TOC table.", "psf_parse")
            f.seek(data_size)
            toc_raw = f.read(toc_bytes)
            for i in range(0, len(toc_raw), 8):
                sec_id, sec_off = struct.unpack_from(">II", toc_raw, i)
                if sec_off + 8 > data_size:
                    raise PsfError("PSF binary section offset out of bounds.", "psf_parse")
                f.seek(sec_off)
                sec_tag, sec_end = struct.unpack(">II", f.read(8))
                if sec_tag != _REC_SECTION or sec_end > data_size or sec_end < sec_off + 8:
                    raise PsfError("Malformed PSF binary section header.", "psf_parse")
                self.sections[sec_id] = (sec_off + 8, sec_end)

            # Parse HEADER, TYPE, SWEEP, TRACE (bounded small metadata sections)
            for sec_id in (_SEC_HEADER, _SEC_TYPE, _SEC_SWEEP, _SEC_TRACE):
                if sec_id not in self.sections:
                    continue
                start, end = self.sections[sec_id]
                if end - start > 8 * 1024 * 1024:
                    raise PsfError("PSF binary metadata section exceeds 8 MiB budget.", "psf_limit")
                f.seek(start)
                buf = f.read(end - start)
                self._parse_section_buf(sec_id, buf)

            if _SEC_VALUE not in self.sections or not self.sweep:
                raise PsfError("PSF binary is missing SWEEP or VALUE section.", "psf_parse")

            v_start, v_end = self.sections[_SEC_VALUE]
            self._value_body_start = v_start
            self._value_body_end = v_end
            if v_end <= v_start:
                self._sample_count = 0
                return

            # Compute trace offsets within a non-windowed frame & inspect VALUE format
            # Sweep item: 4 (tag) + 4 (id) + 8 (f64) = 16 bytes
            # Trace item: 4 (tag) + 4 (id) + (16 if complex else 8)
            frame_offset = 16
            for idx, tr in enumerate(self.traces):
                tr["index"] = idx
                tr["frame_offset"] = frame_offset
                tr["value_bytes"] = 16 if tr["kind"] == "complex" else 8
                frame_offset += 8 + tr["value_bytes"]
            self._frame_size = frame_offset

            f.seek(v_start)
            first_tag = _be_u32(f.read(4), 0)
            if first_tag == _REC_WINDOW:
                self._windowed = True
                self._scan_windows(f, v_start, v_end)
            elif first_tag == _REC_TYPE_DEF:
                self._windowed = False
                v_len = v_end - v_start
                if self._frame_size <= 0 or v_len % self._frame_size != 0:
                    raise PsfError("Non-uniform PSF binary VALUE frame size.", "psf_unsupported")
                self._sample_count = v_len // self._frame_size
                if self._sample_count > 0:
                    self._x_min = self._read_sweep_at(f, 0)
                    self._x_max = self._read_sweep_at(f, self._sample_count - 1)
            else:
                raise PsfError(f"Unsupported PSF binary VALUE record tag: {first_tag}.", "psf_unsupported")

    def _parse_section_buf(self, sec_id: int, buf: bytes) -> None:
        off, end = 0, len(buf)
        if sec_id == _SEC_HEADER:
            self.header, _ = self._read_props(buf, off, end)
            return
        while off + 4 <= end:
            tag = _be_u32(buf, off)
            if tag == _REC_INDEX:
                # Skip index section table if present
                break
            if tag == _REC_TYPE_DEF:
                rec_id = _be_u32(buf, off + 4)
                name, off = _read_psf_str(buf, off + 8)
                if sec_id == _SEC_TYPE:
                    if off + 8 > end:
                        raise PsfError("Truncated PSF TYPE record.", "psf_parse")
                    _arr_type, dtype = struct.unpack_from(">II", buf, off)
                    off += 8
                    props, off = self._read_props(buf, off, end)
                    kind = {
                        _DTYPE_FLOAT64: "real",
                        _DTYPE_COMPLEX128: "complex",
                        _DTYPE_INT32: "integer",
                        _DTYPE_INT8: "integer",
                        _DTYPE_STR: "string",
                        _DTYPE_STRUCT: "struct",
                    }.get(dtype, "unknown")
                    self.types[rec_id] = {"id": rec_id, "name": name, "dtype": dtype, "kind": kind, "properties": props}
                elif sec_id == _SEC_SWEEP:
                    if off + 4 > end:
                        raise PsfError("Truncated PSF SWEEP record.", "psf_parse")
                    type_id = _be_u32(buf, off)
                    off += 4
                    props, off = self._read_props(buf, off, end)
                    tdef = self.types.get(type_id, {"kind": "real"})
                    self.sweep = {
                        "id": rec_id,
                        "name": name,
                        "type_id": type_id,
                        "kind": tdef["kind"],
                        "unit": props.get("units"),
                        "properties": props,
                    }
                elif sec_id == _SEC_TRACE:
                    if off + 4 > end:
                        raise PsfError("Truncated PSF TRACE record.", "psf_parse")
                    type_id = _be_u32(buf, off)
                    off += 4
                    props, off = self._read_props(buf, off, end)
                    tdef = self.types.get(type_id, {"kind": "real"})
                    entry = {
                        "id": rec_id,
                        "name": name,
                        "type_id": type_id,
                        "kind": tdef["kind"],
                        "unit": props.get("units"),
                        "properties": props,
                    }
                    self.traces.append(entry)
                    self.trace_by_name[name] = entry
            else:
                raise PsfError(f"Unsupported PSF binary record tag {tag} in section {sec_id}.", "psf_unsupported")

    def _scan_windows(self, f, v_start: int, v_end: int) -> None:
        pos = v_start
        sample_cursor = 0
        while pos + 12 <= v_end:
            f.seek(pos)
            tag, cnt, byte_len = struct.unpack(">III", f.read(12))
            if tag != _REC_WINDOW or cnt <= 0 or pos + 12 + byte_len > v_end:
                raise PsfError("Corrupt windowed PSF binary block.", "psf_parse")
            data_off = pos + 12
            f.seek(data_off)
            x_first = struct.unpack(">d", f.read(8))[0]
            if cnt > 1:
                f.seek(data_off + (cnt - 1) * 8)
                x_last = struct.unpack(">d", f.read(8))[0]
            else:
                x_last = x_first
            self._windows.append((data_off, cnt, sample_cursor, x_first, x_last))
            sample_cursor += cnt
            pos = data_off + byte_len
        self._sample_count = sample_cursor
        if self._windows:
            self._x_min = self._windows[0][3]
            self._x_max = self._windows[-1][4]

    def _read_sweep_at(self, f, idx: int) -> float:
        f.seek(self._value_body_start + idx * self._frame_size + 8)
        return struct.unpack(">d", f.read(8))[0]

    def _bisect_index(self, f, target_x: float, *, upper: bool) -> int:
        lo, hi = 0, self._sample_count
        while lo < hi:
            mid = (lo + hi) // 2
            val = self._read_sweep_at(f, mid)
            if (val <= target_x) if upper else (val < target_x):
                lo = mid + 1
            else:
                hi = mid
        return lo

    def summary(self) -> dict:
        size, digest, scope = _file_fingerprint(self.path)
        return {
            "name": self.path.name,
            "format": "psfbin",
            "windowed": self._windowed,
            "parser_backend": "spiceowl.psf_binary.native",
            "bytes": size,
            "fingerprint": digest,
            "fingerprint_scope": scope,
            "sweep": {
                "name": self.sweep.get("name"),
                "unit": self.sweep.get("unit"),
                "count": self._sample_count,
                "min": self._x_min,
                "max": self._x_max,
            },
            "signal_count": len(self.traces),
            "signals": [
                {"name": tr["name"], "kind": tr["kind"], "unit": tr["unit"]}
                for tr in self.traces
            ],
        }

    def iter_slice(self, signal_names: list[str], *, x_min: float | None = None,
                   x_max: float | None = None):
        """Yield ``(sweep_chunk, {sig: values_chunk})`` streams within ``[x_min, x_max]``.

        Only reads requested signals and time range from disk via ``seek()``.
        """
        selected = []
        for name in signal_names:
            if name not in self.trace_by_name:
                raise PsfError(f"Requested signal '{name}' was not found in dataset.", "psf_signal_missing")
            selected.append(self.trace_by_name[name])

        if self._sample_count == 0:
            return

        with self.path.open("rb") as f:
            if self._windowed:
                # Precompute per-trace byte offset multiplier inside each window
                # Window layout: sweep[cnt]*8, then for each trace: cnt * (16 if complex else 8)
                prefix_units = []
                acc = 8  # sweep takes 8 bytes per sample
                for tr in self.traces:
                    prefix_units.append(acc)
                    acc += tr["value_bytes"]
                for data_off, cnt, _start_i, w_min, w_max in self._windows:
                    if x_max is not None and w_min > x_max:
                        break
                    if x_min is not None and w_max < x_min:
                        continue
                    f.seek(data_off)
                    xs = struct.unpack(f">{cnt}d", f.read(cnt * 8))
                    # Trim to [x_min, x_max] inside this window
                    i0 = 0
                    while i0 < cnt and x_min is not None and xs[i0] < x_min:
                        i0 += 1
                    i1 = cnt
                    while i1 > i0 and x_max is not None and xs[i1 - 1] > x_max:
                        i1 -= 1
                    if i1 <= i0:
                        continue
                    n_sub = i1 - i0
                    xs_sub = xs[i0:i1]
                    cols: dict[str, list] = {}
                    for tr in selected:
                        t_off = data_off + prefix_units[tr["index"]] * cnt + i0 * tr["value_bytes"]
                        f.seek(t_off)
                        if tr["kind"] == "complex":
                            raw_vals = struct.unpack(f">{2 * n_sub}d", f.read(n_sub * 16))
                            cols[tr["name"]] = [
                                complex(raw_vals[2 * k], raw_vals[2 * k + 1]) for k in range(n_sub)
                            ]
                        else:
                            cols[tr["name"]] = list(struct.unpack(f">{n_sub}d", f.read(n_sub * 8)))
                    yield xs_sub, cols
            else:
                # Non-windowed fixed-frame PSF binary: binary search start/end index in O(log N)
                i_start = self._bisect_index(f, x_min, upper=False) if x_min is not None else 0
                i_end = self._bisect_index(f, x_max, upper=True) if x_max is not None else self._sample_count
                if i_end <= i_start:
                    return
                # Stream in bounded chunks (<= 2 MiB per disk read)
                chunk_samples = max(1, min(4096, (2 * 1024 * 1024) // max(1, self._frame_size)))
                for pos in range(i_start, i_end, chunk_samples):
                    n_sub = min(chunk_samples, i_end - pos)
                    f.seek(self._value_body_start + pos * self._frame_size)
                    buf = f.read(n_sub * self._frame_size)
                    xs_sub = []
                    cols = {tr["name"]: [] for tr in selected}
                    fs = self._frame_size
                    for k in range(n_sub):
                        base = k * fs
                        xs_sub.append(struct.unpack_from(">d", buf, base + 8)[0])
                        for tr in selected:
                            v_off = base + tr["frame_offset"] + 8
                            if tr["kind"] == "complex":
                                re_v, im_v = struct.unpack_from(">dd", buf, v_off)
                                cols[tr["name"]].append(complex(re_v, im_v))
                            else:
                                cols[tr["name"]].append(struct.unpack_from(">d", buf, v_off)[0])
                    yield xs_sub, cols

    def count_in_range(self, *, x_min: float | None = None, x_max: float | None = None) -> int:
        if self._sample_count == 0:
            return 0
        if x_min is None and x_max is None:
            return self._sample_count
        with self.path.open("rb") as f:
            if not self._windowed:
                i_start = self._bisect_index(f, x_min, upper=False) if x_min is not None else 0
                i_end = self._bisect_index(f, x_max, upper=True) if x_max is not None else self._sample_count
                return max(0, i_end - i_start)
            total = 0
            for data_off, cnt, _si, w_min, w_max in self._windows:
                if x_max is not None and w_min > x_max:
                    break
                if x_min is not None and w_max < x_min:
                    continue
                if (x_min is None or w_min >= x_min) and (x_max is None or w_max <= x_max):
                    total += cnt
                else:
                    f.seek(data_off)
                    xs = struct.unpack(f">{cnt}d", f.read(cnt * 8))
                    for x in xs:
                        if (x_min is None or x >= x_min) and (x_max is None or x <= x_max):
                            total += 1
            return total


class _PsfxlReader:
    """Column-major streaming reader for PSFXL metadata + ``.dat`` binary trace files."""

    def __init__(self, path: Path):
        self.path = path
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            meta = json.loads(raw)
        except ValueError:
            raise PsfError(
                "Proprietary PSFXL sub-variant requires Cadence psf/psf_utils conversion; "
                "configure -format psfbin or an explicit PSFXL reader.",
                "psf_unsupported",
            ) from None
        if not isinstance(meta, dict) or meta.get("schema") != "spiceowl.psfxl-bundle.v1":
            raise PsfError("Unsupported PSFXL metadata schema.", "psf_unsupported")
        self.meta = meta
        self.dat_path = path.parent / str(meta["dat_file"])
        if not self.dat_path.is_file() or self.dat_path.is_symlink():
            raise PsfError("PSFXL binary .dat companion file is missing.", "psf_missing")
        self.endian = "<" if meta.get("byte_order", "<") == "<" else ">"
        self.sample_count = int(meta.get("sample_count", 0))
        self.sweep = meta["sweep"]
        self.traces = meta["traces"]

    def _read_sweep_at(self, f, idx: int) -> float:
        f.seek(self.sweep["offset"] + idx * 8)
        return struct.unpack(f"{self.endian}d", f.read(8))[0]

    def _bisect_index(self, f, target_x: float, *, upper: bool) -> int:
        lo, hi = 0, self.sample_count
        while lo < hi:
            mid = (lo + hi) // 2
            val = self._read_sweep_at(f, mid)
            if (val <= target_x) if upper else (val < target_x):
                lo = mid + 1
            else:
                hi = mid
        return lo

    def summary(self) -> dict:
        size, digest, scope = _file_fingerprint(self.dat_path)
        return {
            "name": self.path.name,
            "format": "psfxl",
            "windowed": True,
            "parser_backend": "spiceowl.psfxl.native",
            "bytes": size + self.path.stat().st_size,
            "fingerprint": digest,
            "fingerprint_scope": scope,
            "sweep": {
                "name": self.sweep.get("name"),
                "unit": self.sweep.get("unit"),
                "count": self.sample_count,
                "min": self.sweep.get("min"),
                "max": self.sweep.get("max"),
            },
            "signal_count": len(self.traces),
            "signals": [
                {"name": name, "kind": "real", "unit": info.get("unit")}
                for name, info in self.traces.items()
            ],
        }

    def count_in_range(self, *, x_min: float | None = None, x_max: float | None = None) -> int:
        if self.sample_count == 0:
            return 0
        if x_min is None and x_max is None:
            return self.sample_count
        with self.dat_path.open("rb") as f:
            i_start = self._bisect_index(f, x_min, upper=False) if x_min is not None else 0
            i_end = self._bisect_index(f, x_max, upper=True) if x_max is not None else self.sample_count
            return max(0, i_end - i_start)

    def iter_slice(self, signal_names: list[str], *, x_min: float | None = None,
                   x_max: float | None = None):
        for name in signal_names:
            if name not in self.traces:
                raise PsfError(f"Requested signal '{name}' was not found in PSFXL dataset.", "psf_signal_missing")
        if self.sample_count == 0:
            return
        with self.dat_path.open("rb") as f:
            i_start = self._bisect_index(f, x_min, upper=False) if x_min is not None else 0
            i_end = self._bisect_index(f, x_max, upper=True) if x_max is not None else self.sample_count
            if i_end <= i_start:
                return
            chunk = 8192
            for pos in range(i_start, i_end, chunk):
                n_sub = min(chunk, i_end - pos)
                f.seek(self.sweep["offset"] + pos * 8)
                xs = struct.unpack(f"{self.endian}{n_sub}d", f.read(n_sub * 8))
                cols: dict[str, list[float]] = {}
                for name in signal_names:
                    t_off = self.traces[name]["offset"] + pos * 8
                    f.seek(t_off)
                    cols[name] = list(struct.unpack(f"{self.endian}{n_sub}d", f.read(n_sub * 8)))
                yield xs, cols


class _PsfAsciiReader:
    """Adapter presenting PSF ASCII files through the same streaming interface."""

    def __init__(self, path: Path):
        self.path = path
        self.parsed = parse_psf_ascii(path.read_text(encoding="utf-8", errors="replace"))
        self.values = self.parsed["values"]
        self.units = self.parsed.get("units") or {}
        self.xs = self.values["time"]
        self.signal_names = [name for name in self.values if name != "time"]

    def summary(self) -> dict:
        size, digest, scope = _file_fingerprint(self.path)
        return {
            "name": self.path.name,
            "format": "psfascii",
            "windowed": False,
            "parser_backend": "spiceowl.psf_ascii.native",
            "bytes": size,
            "fingerprint": digest,
            "fingerprint_scope": scope,
            "sweep": {
                "name": "time",
                "unit": self.units.get("time"),
                "count": len(self.xs),
                "min": self.xs[0] if self.xs else None,
                "max": self.xs[-1] if self.xs else None,
            },
            "signal_count": len(self.signal_names),
            "signals": [
                {"name": name, "kind": "real", "unit": self.units.get(name)}
                for name in self.signal_names
            ],
        }

    def count_in_range(self, *, x_min: float | None = None, x_max: float | None = None) -> int:
        return sum(
            1 for x in self.xs
            if (x_min is None or x >= x_min) and (x_max is None or x <= x_max)
        )

    def iter_slice(self, signal_names: list[str], *, x_min: float | None = None,
                   x_max: float | None = None):
        for name in signal_names:
            if name not in self.values or name == "time":
                raise PsfError(f"Requested signal '{name}' was not found in PSF ASCII dataset.", "psf_signal_missing")
        idxs = [
            i for i, x in enumerate(self.xs)
            if (x_min is None or x >= x_min) and (x_max is None or x <= x_max)
        ]
        if not idxs:
            return
        xs_sub = [self.xs[i] for i in idxs]
        cols = {
            name: [self.values[name][i] for i in idxs]
            for name in signal_names
        }
        yield xs_sub, cols


def open_waveform_reader(path: str | Path):
    """Open a streaming waveform reader for PSF Binary, PSFXL, or PSF ASCII."""
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise PsfError("Waveform path must be an existing regular file.", "psf_missing")
    fmt = detect_waveform_format(p)
    if fmt == "psfbin":
        return _PsfBinaryReader(p)
    if fmt == "psfxl":
        return _PsfxlReader(p)
    if fmt == "psfascii":
        return _PsfAsciiReader(p)
    raise PsfError(f"Unsupported or unrecognized waveform format for '{p.name}'.", "psf_unsupported")


# ---------------------------------------------------------------------------
# Directory Indexing, Envelope Downsampling, Exact Slicing & Cycle Analysis
# ---------------------------------------------------------------------------

def index_waveform_directory(raw_dir: str | Path) -> dict:
    """Scan a ``results.raw`` directory and build a lightweight waveform index."""
    root = Path(raw_dir)
    if not root.exists() or not root.is_dir():
        return {"schema": SCHEMA_INDEX, "datasets": [], "skipped": [], "total_raw_bytes": 0}

    datasets = []
    skipped = []
    total_raw_bytes = 0
    seen = 0
    for item in sorted(root.rglob("*")):
        if seen >= MAX_INDEX_FILES:
            skipped.append({"name": item.name, "reason": "index_file_limit"})
            break
        if item.is_symlink() or not item.is_file():
            continue
        seen += 1
        rel = item.relative_to(root).as_posix()
        size = item.stat().st_size
        total_raw_bytes += size
        if rel.endswith(".dat") and (item.parent / item.name[:-4]).is_file():
            # Companion binary payload of a PSFXL bundle; accounted for in its metadata entry.
            continue
        fmt = detect_waveform_format(item)
        if fmt == "unknown":
            continue
        try:
            reader = open_waveform_reader(item)
            info = reader.summary()
            info["dataset"] = rel
            datasets.append(info)
        except PsfError as exc:
            skipped.append({"name": rel, "format": fmt, "bytes": size, "reason": exc.code})
    return {
        "schema": SCHEMA_INDEX,
        "datasets": datasets,
        "skipped": skipped,
        "total_raw_bytes": total_raw_bytes,
    }


def _to_scalar_display(value) -> float:
    """Convert real or complex sample to a finite real value for plotting/envelopes."""
    if isinstance(value, complex):
        return abs(value)
    return float(value)


def _serialize_value(value):
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag, "mag": abs(value)}
    return float(value)


def query_waveform(
    path: str | Path,
    signals: list[str],
    *,
    x_min: float | None = None,
    x_max: float | None = None,
    max_points: int = 2000,
    mode: str = "auto",
) -> dict:
    """Query a waveform window with automatic exact-window vs Min-Max envelope downsampling.

    - ``mode="auto"``: returns ``representation="exact_window"`` (100% lossless
      raw points) whenever the requested window has ``<= max_points`` samples;
      otherwise performs streaming Min-Max envelope downsampling (never drops
      glitches/spikes/overshoot) tagged ``representation="downsampled_preview"``.
    - ``mode="exact"``: strictly returns full-precision lossless samples for
      measurement/``adctools`` (up to ``MAX_EXACT_SAMPLES``), refusing to
      silently downsample.
    """
    if not isinstance(signals, list) or not 1 <= len(signals) <= MAX_SIGNALS_PER_QUERY:
        raise DomainError(f"Select 1–{MAX_SIGNALS_PER_QUERY} signal names.", "invalid_waveform_query")
    if any(not isinstance(s, str) or not s.strip() for s in signals) or len(set(signals)) != len(signals):
        raise DomainError("Signals must be distinct non-empty names.", "invalid_waveform_query")
    if mode not in ("auto", "exact"):
        raise DomainError("mode must be 'auto' or 'exact'.", "invalid_waveform_query")
    if type(max_points) is not int or not 16 <= max_points <= MAX_PREVIEW_POINTS:
        raise DomainError(f"max_points must be an integer between 16 and {MAX_PREVIEW_POINTS}.", "invalid_waveform_query")
    if x_min is not None and x_max is not None and float(x_min) > float(x_max):
        raise DomainError("x_min must be <= x_max.", "invalid_waveform_query")

    reader = open_waveform_reader(path)
    meta = reader.summary()
    window_count = reader.count_in_range(x_min=x_min, x_max=x_max)

    if mode == "exact":
        if window_count > MAX_EXACT_SAMPLES:
            raise DomainError(
                f"Requested exact window has {window_count} samples (limit {MAX_EXACT_SAMPLES}). "
                "Narrow [x_min, x_max] or use cycle slicing.",
                "exact_window_limit",
            )
        use_exact = True
    else:
        use_exact = window_count <= max_points

    if use_exact:
        xs_out: list[float] = []
        series: dict[str, list] = {s: [] for s in signals}
        for xs_chunk, cols in reader.iter_slice(signals, x_min=x_min, x_max=x_max):
            xs_out.extend(float(x) for x in xs_chunk)
            for s in signals:
                series[s].extend(_serialize_value(v) for v in cols[s])
        return {
            "schema": SCHEMA_QUERY,
            "format": meta["format"],
            "parser_backend": meta["parser_backend"],
            "representation": "exact_window",
            "exact": True,
            "measurement_safe": True,
            "sweep": {
                "name": meta["sweep"]["name"],
                "unit": meta["sweep"]["unit"],
                "total_count": meta["sweep"]["count"],
                "window_count": len(xs_out),
                "min": xs_out[0] if xs_out else x_min,
                "max": xs_out[-1] if xs_out else x_max,
                "full_min": meta["sweep"]["min"],
                "full_max": meta["sweep"]["max"],
            },
            "x": xs_out,
            "signals": {
                s: {
                    "values": series[s],
                    "min": min((_to_scalar_display(v) for v in series[s]), default=None) if series[s] and not isinstance(series[s][0], dict) else None,
                    "max": max((_to_scalar_display(v) for v in series[s]), default=None) if series[s] and not isinstance(series[s][0], dict) else None,
                }
                for s in signals
            },
            "envelopes": None,
        }

    # Streaming Min-Max envelope downsampling in O(buckets) RAM (never loads full trace!)
    # Each bucket emits (t_at_min, v_min) and (t_at_max, v_max) in chronological order,
    # PLUS compact bucket envelope tuples [t_start, t_end, y_min, y_max] for band rendering.
    buckets = max(8, max_points // 2)
    eff_min = x_min if x_min is not None else meta["sweep"]["min"]
    eff_max = x_max if x_max is not None else meta["sweep"]["max"]
    span = (eff_max - eff_min) if (eff_min is not None and eff_max is not None) else 0.0

    # Per-signal bucket accumulators: [t_start, t_end, v_min, t_min, v_max, t_max]
    acc: dict[str, list[list | None]] = {s: [None] * buckets for s in signals}
    g_min: dict[str, float] = {}
    g_max: dict[str, float] = {}

    seen_idx = 0
    for xs_chunk, cols in reader.iter_slice(signals, x_min=x_min, x_max=x_max):
        for k, x in enumerate(xs_chunk):
            if span > 0:
                b = int((x - eff_min) / span * buckets)
            else:
                b = int(seen_idx / max(1, window_count) * buckets)
            if b < 0:
                b = 0
            elif b >= buckets:
                b = buckets - 1
            seen_idx += 1
            for s in signals:
                v = _to_scalar_display(cols[s][k])
                if not math.isfinite(v):
                    continue
                if s not in g_min or v < g_min[s]:
                    g_min[s] = v
                if s not in g_max or v > g_max[s]:
                    g_max[s] = v
                slot = acc[s][b]
                if slot is None:
                    acc[s][b] = [x, x, v, x, v, x]
                else:
                    if x < slot[0]:
                        slot[0] = x
                    if x > slot[1]:
                        slot[1] = x
                    if v < slot[2]:
                        slot[2] = v
                        slot[3] = x
                    if v > slot[4]:
                        slot[4] = v
                        slot[5] = x

    out_signals: dict[str, dict] = {}
    out_envelopes: dict[str, list[dict]] = {}
    for s in signals:
        pts_x: list[float] = []
        pts_y: list[float] = []
        envs: list[dict] = []
        for slot in acc[s]:
            if slot is None:
                continue
            t0, t1, vmin, tmin, vmax, tmax = slot
            envs.append({"t0": t0, "t1": t1, "min": vmin, "max": vmax})
            if tmin <= tmax:
                pts_x.append(tmin)
                pts_y.append(vmin)
                if tmax != tmin or vmax != vmin:
                    pts_x.append(tmax)
                    pts_y.append(vmax)
            else:
                pts_x.append(tmax)
                pts_y.append(vmax)
                pts_x.append(tmin)
                pts_y.append(vmin)
        out_signals[s] = {
            "x": pts_x,
            "values": pts_y,
            "min": g_min.get(s),
            "max": g_max.get(s),
        }
        out_envelopes[s] = envs

    return {
        "schema": SCHEMA_QUERY,
        "format": meta["format"],
        "parser_backend": meta["parser_backend"],
        "representation": "downsampled_preview",
        "exact": False,
        "measurement_safe": False,
        "downsampling": {
            "algorithm": "min_max_envelope",
            "buckets": buckets,
            "source_samples_in_window": window_count,
            "note": "Visual envelope preview preserves every peak and glitch; zoom in for exact_window samples.",
        },
        "sweep": {
            "name": meta["sweep"]["name"],
            "unit": meta["sweep"]["unit"],
            "total_count": meta["sweep"]["count"],
            "window_count": window_count,
            "min": eff_min,
            "max": eff_max,
            "full_min": meta["sweep"]["min"],
            "full_max": meta["sweep"]["max"],
        },
        "x": None,
        "signals": out_signals,
        "envelopes": out_envelopes,
    }


def _detect_cycle_edges(
    reader,
    *,
    period: float | None,
    clock_signal: str | None,
    threshold: float | None,
    edge: str,
    t_start: float | None,
    t_end: float | None,
) -> tuple[list[float], dict]:
    """Return sorted cycle boundary timestamps and detection provenance."""
    meta = reader.summary()
    s_min = meta["sweep"]["min"]
    s_max = meta["sweep"]["max"]
    if s_min is None or s_max is None or s_max <= s_min:
        raise DomainError("Dataset has no sweep range for cycle slicing.", "invalid_cycle_query")
    eff_start = float(t_start) if t_start is not None else float(s_min)
    eff_end = float(t_end) if t_end is not None else float(s_max)
    if eff_end <= eff_start:
        raise DomainError("t_end must be greater than t_start.", "invalid_cycle_query")

    if clock_signal is not None:
        if edge not in ("rising", "falling"):
            raise DomainError("edge must be 'rising' or 'falling'.", "invalid_cycle_query")
        # Auto-determine midpoint threshold if caller omitted threshold
        eff_thresh = threshold
        if eff_thresh is None:
            c_min, c_max = None, None
            for _xs, cols in reader.iter_slice([clock_signal], x_min=eff_start, x_max=eff_end):
                for v in cols[clock_signal]:
                    fv = _to_scalar_display(v)
                    if c_min is None or fv < c_min:
                        c_min = fv
                    if c_max is None or fv > c_max:
                        c_max = fv
            if c_min is None or c_max is None or c_max <= c_min:
                raise DomainError(f"Clock signal '{clock_signal}' has no switching transitions.", "invalid_cycle_query")
            eff_thresh = 0.5 * (c_min + c_max)

        edges: list[float] = []
        prev_x: float | None = None
        prev_v: float | None = None
        for xs_chunk, cols in reader.iter_slice([clock_signal], x_min=eff_start, x_max=eff_end):
            vals = cols[clock_signal]
            for x, raw_v in zip(xs_chunk, vals):
                v = _to_scalar_display(raw_v)
                if prev_v is not None and prev_x is not None:
                    crossed = (
                        (prev_v < eff_thresh <= v) if edge == "rising"
                        else (prev_v > eff_thresh >= v)
                    )
                    if crossed:
                        # Linear interpolation for sub-sample crossing precision
                        dv = v - prev_v
                        frac = (eff_thresh - prev_v) / dv if dv != 0 else 0.0
                        t_cross = prev_x + frac * (x - prev_x)
                        edges.append(t_cross)
                        if len(edges) > MAX_CYCLES_REPORTED + 1:
                            break
                prev_x, prev_v = x, v
            if len(edges) > MAX_CYCLES_REPORTED + 1:
                break
        if len(edges) < 2:
            raise DomainError(f"Found fewer than 2 {edge} edges on '{clock_signal}'.", "invalid_cycle_query")
        return edges, {
            "basis": "clock_signal",
            "clock_signal": clock_signal,
            "edge": edge,
            "threshold": eff_thresh,
        }

    if period is None or not isinstance(period, (int, float)) or not math.isfinite(period) or period <= 0:
        raise DomainError("Specify a positive numeric 'period' or a 'clock_signal'.", "invalid_cycle_query")
    n_cycles = int((eff_end - eff_start) / period)
    if n_cycles < 1:
        raise DomainError("Requested period exceeds dataset sweep span.", "invalid_cycle_query")
    capped = min(n_cycles, MAX_CYCLES_REPORTED)
    edges = [eff_start + k * float(period) for k in range(capped + 1)]
    return edges, {
        "basis": "fixed_period",
        "period": float(period),
        "truncated_cycles": n_cycles > MAX_CYCLES_REPORTED,
        "total_estimated_cycles": n_cycles,
    }


def analyze_cycles(
    path: str | Path,
    signals: list[str],
    *,
    period: float | None = None,
    clock_signal: str | None = None,
    threshold: float | None = None,
    edge: str = "rising",
    t_start: float | None = None,
    t_end: float | None = None,
    worst_metric: str = "peak_to_peak",
    worst_signal: str | None = None,
    cycle_index: int | None = None,
    context_cycles: int = 0,
    overlay_cycles: int = 10,
    max_points_per_cycle: int = 2000,
) -> dict:
    """Stream cycle-by-cycle metrics + lossless key/worst/selected intra-cycle waveforms + overlay.

    Solves the multi-thousand-cycle inspection problem:
    1. Computes per-cycle metrics (``v_min``, ``v_max``, ``v_pp``, ``v_avg``, ``v_end``,
       ``duration``) across up to 4096 cycles in a single O(1)-memory pass (~20–60 KB).
    2. Automatically extracts 100% lossless intra-cycle waveforms for:
       - ``first`` (cycle 0, startup transient)
       - ``steady_last`` (final complete cycle)
       - ``worst_case`` (cycle maximizing ``worst_metric``: ``peak_to_peak``,
         ``max_value``, ``min_value``, ``overshoot``, or ``period_deviation``)
       - ``selected`` (optional explicit ``cycle_index`` from user drill-down)
    3. Builds an aligned phase-overlay (``tau = t - t_cycle_start``) across the
       last ``overlay_cycles`` + worst-case cycle for eye/jitter inspection.
    """
    if not isinstance(signals, list) or not 1 <= len(signals) <= MAX_SIGNALS_PER_QUERY:
        raise DomainError(f"Select 1–{MAX_SIGNALS_PER_QUERY} signal names.", "invalid_cycle_query")
    if worst_metric not in ("peak_to_peak", "max_value", "min_value", "overshoot", "period_deviation"):
        raise DomainError("Unsupported worst_metric.", "invalid_cycle_query")
    target_sig = worst_signal or signals[0]
    if target_sig not in signals:
        raise DomainError("worst_signal must be one of the selected signals.", "invalid_cycle_query")
    if type(overlay_cycles) is not int or not 0 <= overlay_cycles <= MAX_OVERLAY_CYCLES:
        raise DomainError(f"overlay_cycles must be 0..{MAX_OVERLAY_CYCLES}.", "invalid_cycle_query")
    if type(context_cycles) is not int or not 0 <= context_cycles <= 5:
        raise DomainError("context_cycles must be 0..5.", "invalid_cycle_query")

    reader = open_waveform_reader(path)
    meta = reader.summary()
    edges, detection = _detect_cycle_edges(
        reader, period=period, clock_signal=clock_signal,
        threshold=threshold, edge=edge, t_start=t_start, t_end=t_end,
    )
    n_cycles = len(edges) - 1
    if cycle_index is not None and (type(cycle_index) is not int or not 0 <= cycle_index < n_cycles):
        raise DomainError(f"cycle_index must be between 0 and {n_cycles - 1}.", "invalid_cycle_query")

    # Single streaming pass computing exact per-cycle statistics
    stats: list[dict] = [
        {
            "cycle_index": k,
            "t_start": edges[k],
            "t_end": edges[k + 1],
            "duration": edges[k + 1] - edges[k],
            "sample_count": 0,
            "signals": {
                s: {"min": None, "max": None, "sum": 0.0, "count": 0, "end": None}
                for s in signals
            },
        }
        for k in range(n_cycles)
    ]

    cur = 0
    for xs_chunk, cols in reader.iter_slice(signals, x_min=edges[0], x_max=edges[-1]):
        for i, x in enumerate(xs_chunk):
            while cur < n_cycles and x >= edges[cur + 1]:
                cur += 1
            if cur >= n_cycles:
                break
            if x < edges[cur]:
                continue
            row = stats[cur]
            row["sample_count"] += 1
            for s in signals:
                v = _to_scalar_display(cols[s][i])
                if not math.isfinite(v):
                    continue
                st = row["signals"][s]
                if st["min"] is None or v < st["min"]:
                    st["min"] = v
                if st["max"] is None or v > st["max"]:
                    st["max"] = v
                st["sum"] += v
                st["count"] += 1
                st["end"] = v

    # Finalize per-cycle metrics and pick worst-case cycle
    durations = [r["duration"] for r in stats]
    mean_duration = sum(durations) / len(durations) if durations else 0.0
    cycle_metrics: list[dict] = []
    worst_idx = 0
    worst_score = float("-inf")

    # Estimate steady-state reference median for overshoot scoring
    end_vals = [
        r["signals"][target_sig]["max"]
        for r in stats[-min(20, n_cycles):]
        if r["signals"][target_sig]["max"] is not None
    ]
    steady_ref = sorted(end_vals)[len(end_vals) // 2] if end_vals else 0.0

    for row in stats:
        sig_out = {}
        for s in signals:
            st = row["signals"][s]
            if st["count"] > 0:
                vmin, vmax = st["min"], st["max"]
                vpp = vmax - vmin
                vavg = st["sum"] / st["count"]
                sig_out[s] = {
                    "min": vmin,
                    "max": vmax,
                    "peak_to_peak": vpp,
                    "avg": vavg,
                    "end": st["end"],
                }
            else:
                sig_out[s] = {"min": None, "max": None, "peak_to_peak": None, "avg": None, "end": None}

        entry = {
            "cycle_index": row["cycle_index"],
            "t_start": row["t_start"],
            "t_end": row["t_end"],
            "duration": row["duration"],
            "sample_count": row["sample_count"],
            "signals": sig_out,
        }
        cycle_metrics.append(entry)

        ts = sig_out[target_sig]
        if worst_metric == "period_deviation":
            score = abs(row["duration"] - mean_duration)
        elif ts["max"] is None:
            score = float("-inf")
        elif worst_metric == "peak_to_peak":
            score = ts["peak_to_peak"]
        elif worst_metric == "max_value":
            score = ts["max"]
        elif worst_metric == "min_value":
            score = -ts["min"]
        elif worst_metric == "overshoot":
            score = ts["max"] - steady_ref
        else:
            score = ts["peak_to_peak"]
        if score > worst_score:
            worst_score = score
            worst_idx = row["cycle_index"]

    def _extract_cycle_window(idx: int, ctx: int = 0) -> dict:
        c0 = max(0, idx - ctx)
        c1 = min(n_cycles - 1, idx + ctx)
        w = query_waveform(
            path, signals,
            x_min=edges[c0], x_max=edges[c1 + 1],
            max_points=max_points_per_cycle, mode="auto",
        )
        return {
            "cycle_index": idx,
            "cycle_span": [c0, c1],
            "t_start": edges[c0],
            "t_end": edges[c1 + 1],
            "duration": edges[idx + 1] - edges[idx],
            "representation": "exact_cycle" if w["exact"] else "downsampled_cycle",
            "exact": w["exact"],
            "metrics": cycle_metrics[idx],
            "waveform": w,
        }

    key_cycles: dict[str, dict] = {
        "first": _extract_cycle_window(0),
        "steady_last": _extract_cycle_window(n_cycles - 1),
        "worst_case": _extract_cycle_window(worst_idx),
    }
    if cycle_index is not None:
        key_cycles["selected"] = _extract_cycle_window(cycle_index, ctx=context_cycles)

    # Aligned cycle overlay (eye-style: relative time tau = t - t_cycle_start)
    overlay: list[dict] = []
    if overlay_cycles > 0:
        # Pick up to overlay_cycles evenly spaced from the last min(50, n_cycles) plus worst_idx
        tail_start = max(0, n_cycles - min(50, n_cycles))
        tail_count = n_cycles - tail_start
        if tail_count <= overlay_cycles:
            chosen = list(range(tail_start, n_cycles))
        else:
            step = (tail_count - 1) / max(1, overlay_cycles - 1)
            chosen = sorted({tail_start + int(round(i * step)) for i in range(overlay_cycles)})
        if worst_idx not in chosen:
            chosen = sorted(set(chosen[-(overlay_cycles - 1):] + [worst_idx])) if overlay_cycles > 1 else [worst_idx]
        per_cycle_budget = max(64, min(512, max_points_per_cycle))
        for k in chosen:
            w = query_waveform(
                path, [target_sig],
                x_min=edges[k], x_max=edges[k + 1],
                max_points=per_cycle_budget, mode="auto",
            )
            t0 = edges[k]
            if w["exact"]:
                tau = [x - t0 for x in w["x"]]
                vals = w["signals"][target_sig]["values"]
            else:
                tau = [x - t0 for x in w["signals"][target_sig]["x"]]
                vals = w["signals"][target_sig]["values"]
            overlay.append({
                "cycle_index": k,
                "is_worst": k == worst_idx,
                "t_start": t0,
                "duration": edges[k + 1] - t0,
                "exact": w["exact"],
                "tau": tau,
                "values": vals,
            })

    return {
        "schema": SCHEMA_CYCLES,
        "format": meta["format"],
        "parser_backend": meta["parser_backend"],
        "detection": detection,
        "cycle_count": n_cycles,
        "worst_metric": worst_metric,
        "worst_signal": target_sig,
        "worst_cycle_index": worst_idx,
        "sweep_unit": meta["sweep"]["unit"],
        "cycle_metrics": cycle_metrics,
        "key_cycles": key_cycles,
        "overlay": {
            "signal": target_sig,
            "cycles": overlay,
        },
    }


def extract_signal_for_adctools(
    path: str | Path,
    signal: str,
    *,
    x_min: float | None = None,
    x_max: float | None = None,
) -> dict:
    """Extract a single lossless full-precision trace strictly verified for ``adctools``."""
    result = query_waveform(path, [signal], x_min=x_min, x_max=x_max, mode="exact")
    if not result["exact"] or not result["measurement_safe"]:
        raise DomainError("Refusing to pass non-exact waveform data to scientific measurement.", "measurement_requires_exact")
    raw_vals = result["signals"][signal]["values"]
    if raw_vals and isinstance(raw_vals[0], dict):
        raise DomainError("adctools requires real scalar time-domain samples, not complex values.", "measurement_requires_real")
    return {
        "signal": signal,
        "format": result["format"],
        "parser_backend": result["parser_backend"],
        "exact": True,
        "measurement_safe": True,
        "time": result["x"],
        "samples": raw_vals,
        "sample_count": len(raw_vals),
        "sweep_min": result["sweep"]["min"],
        "sweep_max": result["sweep"]["max"],
    }


def generate_demo_multicycle_psf(
    path: str | Path,
    *,
    cycles: int = 500,
    samples_per_cycle: int = 40,
    period_s: float = 1e-8,
    glitch_cycle: int = 317,
    windowed: bool = True,
) -> Path:
    """Generate an original synthetic multi-cycle PSF Binary waveform with startup settling & a hidden glitch.

    Useful for offline testing and ``spiceowl serve --demo`` so users can
    immediately explore global Min-Max envelope zoom-in, automatic exact-window
    switching, worst-case cycle detection (finding ``glitch_cycle``), and
    cycle-by-cycle drill-down without a commercial simulator.
    """
    xs: list[float] = []
    v_out: list[float] = []
    v_clk: list[float] = []
    v_vdd: list[float] = []
    dt = period_s / samples_per_cycle
    tau_settle = period_s * 18.0
    for c in range(cycles):
        for s in range(samples_per_cycle):
            t = (c * samples_per_cycle + s) * dt
            phase = s / samples_per_cycle
            # Startup envelope settling to 0.90 V with intra-cycle switching ripple + ringing
            env = 0.90 * (1.0 - math.exp(-t / tau_settle))
            ripple = 0.045 * math.sin(2.0 * math.pi * phase) + 0.02 * math.exp(-6.0 * phase) * math.sin(10.0 * math.pi * phase)
            # Slow thermal/supply drift + sharp hidden glitch at glitch_cycle
            drift = 0.008 * math.sin(2.0 * math.pi * c / max(1, cycles))
            glitch = 0.0
            if c == glitch_cycle and 0.22 <= phase <= 0.34:
                glitch = 0.28 * math.sin(math.pi * (phase - 0.22) / 0.12)
            # Square-ish clock with finite rise/fall
            if 0.05 <= phase < 0.50:
                clk = 1.2 * min(1.0, (phase - 0.05) / 0.04)
            elif 0.50 <= phase < 0.54:
                clk = 1.2 * max(0.0, 1.0 - (phase - 0.50) / 0.04)
            else:
                clk = 0.0
            vdd = 1.20 - (0.09 if c == glitch_cycle and 0.20 <= phase <= 0.36 else 0.005 * abs(math.sin(2.0 * math.pi * phase)))
            xs.append(t)
            v_out.append(env + ripple + drift + glitch)
            v_clk.append(clk)
            v_vdd.append(vdd)
    return write_psf_binary(
        path,
        sweep_name="time",
        sweep_values=xs,
        signals={"v(out)": v_out, "v(clk)": v_clk, "v(vdd)": v_vdd},
        sweep_unit="s",
        signal_units={"v(out)": "V", "v(clk)": "V", "v(vdd)": "V"},
        header_props={"analysis": "tran", "synthetic": "true"},
        windowed=windowed,
        window_samples=samples_per_cycle * 4,
    )
