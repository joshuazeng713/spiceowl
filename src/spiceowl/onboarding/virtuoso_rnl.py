"""Universal Cadence Virtuoso OpenAccess -> RNL 0.3 geometry & symbol converter.

Works across arbitrary remote Cadence Virtuoso installations (IC6 / ICADVM / IC23)
without requiring any custom third-party binary on the remote EDA server:
1. ``REMOTE_OA_DUMP_SKILL`` is a pure, read-only Cadence SKILL script executed via
   ``dbAccess -load`` (fastest, framework license only) with automatic fallback to
   ``virtuoso -nograph -nocdsinit -replay`` or ``ocean -nograph``.
2. It recursively extracts schematic instances (exact ``xy`` & ``orient``), symbol
   graphics & pin offsets, wire paths, net labels, I/O pins, and Spectre CDF
   ``termOrder`` into bounded JSON (``oa_geometry.json``).
3. ``inject_rnl_geometry()`` aligns that OA geometry with the Cadence ``si``
   Spectre netlist and emits self-contained RNL 0.3 comments (``// @RNL { ... }``)
   directly inside each ``subckt ... ends`` block and the top-level circuit so the
   resulting ``.scs`` file BOTH simulates directly in Spectre AND renders with full
   Virtuoso coordinates, orientations, symbols, wires, labels, and hierarchy in
   CircuitGit.
"""

from __future__ import annotations

from collections import defaultdict
import math
import re
from typing import Any

VALID_ORIENTS = frozenset({"R0", "R90", "R180", "R270", "MX", "MY", "MXR90", "MYR90"})
_PRIMITIVE_NON_ELECTRICAL_LIBS = frozenset({"basic"})
_SUPPLY_TAP_CELLS = frozenset({"gnd", "vdd", "vss", "vcc"})
_NON_ELECTRICAL_CELLS = frozenset({
    "gnd", "vdd", "vss", "vcc", "ipin", "opin", "iopin", "noConn", "patch", "cds_thru",
    "sympin", "bussetp",
})
_CANONICAL_PIN_RANK = {
    "d": 0, "g": 1, "s": 2, "b": 3, "bulk": 3, "sub": 3,
    "c": 0, "e": 2,
    "plus": 0, "p": 0, "pos": 0, "in": 0, "vin": 0, "a": 0,
    "minus": 1, "n": 1, "neg": 1, "out": 1, "vout": 1,
}

# Pure read-only Cadence SKILL script (IC6 / ICADVM / IC23 compatible).
# Placeholders: __LIB__, __CELL__, __VIEW__, __OUT_JSON__
REMOTE_OA_DUMP_SKILL = r"""
procedure(soJsonEsc(s)
  let((out i ch len)
    if(!stringp(s) then
      ""
    else
      out = ""
      len = strlen(s)
      for(i 1 len
        ch = substring(s i 1)
        cond(
          (ch == "\\" out = strcat(out "\\\\"))
          (ch == "\"" out = strcat(out "\\\""))
          (ch == "\n" out = strcat(out "\\n"))
          (ch == "\r" out = strcat(out "\\r"))
          (ch == "\t" out = strcat(out "\\t"))
          (t out = strcat(out ch))
        )
      )
      out
    )
  )
)

procedure(soJsonStr(s)
  strcat("\"" soJsonEsc(s) "\"")
)

procedure(soNum(v)
  cond(
    (fixp(v) sprintf(nil "%d" v))
    (floatp(v) sprintf(nil "%.6g" v))
    (t "0")
  )
)

procedure(soPtJson(pt)
  if(listp(pt) && length(pt) >= 2 then
    strcat("[" soNum(car(pt)) "," soNum(cadr(pt)) "]")
  else
    "[0,0]"
  )
)

procedure(soBoxJson(bb)
  if(listp(bb) && length(bb) >= 2 && listp(car(bb)) && listp(cadr(bb)) then
    strcat("[" soNum(caar(bb)) "," soNum(cadar(bb)) "," soNum(caadr(bb)) "," soNum(cadadr(bb)) "]")
  else
    "null"
  )
)

procedure(soPtsJson(pts)
  let((parts first p)
    parts = "["
    first = t
    foreach(p pts
      when(!first parts = strcat(parts ","))
      first = nil
      parts = strcat(parts soPtJson(p))
    )
    strcat(parts "]")
  )
)

procedure(soGetTermOrder(lib cell)
  let((dd cdf simInfo spec to parts first item)
    to = nil
    errset(
      progn(
        dd = ddGetObj(lib cell)
        when(dd
          cdf = cdfGetBaseCellCDF(dd)
          when(cdf
            simInfo = cdf->simInfo
            when(simInfo
              spec = simInfo->spectre
              when(spec && listp(spec->termOrder)
                to = spec->termOrder
              )
              when(!to && simInfo->hspiceD && listp(simInfo->hspiceD->termOrder)
                to = simInfo->hspiceD->termOrder
              )
              when(!to && simInfo->auCdl && listp(simInfo->auCdl->termOrder)
                to = simInfo->auCdl->termOrder
              )
            )
          )
        )
      )
      nil
    )
    if(listp(to) && length(to) > 0 then
      parts = "["
      first = t
      foreach(item to
        when(!first parts = strcat(parts ","))
        first = nil
        parts = strcat(parts soJsonStr(if(symbolp(item) symbolToString(item) if(stringp(item) item sprintf(nil "%L" item)))))
      )
      strcat(parts "]")
    else
      "[]"
    )
  )
)

procedure(soDumpSymbolJson(sym lib cell view)
  let((out first tm p0 pbox sh lpp lname lpurp otype txt xy orient just h ell sa ea)
    out = strcat(
      "{\"lib\":" soJsonStr(lib)
      ",\"cell\":" soJsonStr(cell)
      ",\"view\":" soJsonStr(view)
      ",\"dbu\":" soNum(if(sym~>DBUPerUU sym~>DBUPerUU 160.0))
      ",\"bBox\":" soBoxJson(sym~>bBox)
      ",\"termOrder\":" soGetTermOrder(lib cell)
      ",\"terms\":["
    )
    first = t
    foreach(tm sym~>terminals
      p0 = car(tm~>pins)
      pbox = if(p0 && p0~>fig p0~>fig~>bBox nil)
      when(!first out = strcat(out ","))
      first = nil
      out = strcat(out
        "{\"name\":" soJsonStr(tm~>name)
        ",\"dir\":" soJsonStr(if(stringp(tm~>direction) tm~>direction "inputOutput"))
        ",\"bBox\":" soBoxJson(pbox)
        "}"
      )
    )
    out = strcat(out "],\"shapes\":[")
    first = t
    foreach(sh sym~>shapes
      lpp = sh~>lpp
      lname = if(listp(lpp) && stringp(car(lpp)) car(lpp) "")
      lpurp = if(listp(lpp) && length(lpp) >= 2 && stringp(cadr(lpp)) cadr(lpp) "")
      otype = if(stringp(sh~>objType) sh~>objType "")
      when(member(lname list("device" "instance" "pin" "annotate" "text" "wire")) &&
           member(otype list("line" "path" "rect" "ellipse" "arc" "polygon" "label"))
        when(!first out = strcat(out ","))
        first = nil
        txt = if(otype == "label" && stringp(sh~>theLabel) sh~>theLabel "")
        xy = if(otype == "label" sh~>xy nil)
        orient = if(otype == "label" && stringp(sh~>orient) sh~>orient "R0")
        just = if(otype == "label" && stringp(sh~>justify) sh~>justify "lowerLeft")
        h = if(otype == "label" && numberp(sh~>height) sh~>height 0.0625)
        ell = if(otype == "arc" sh~>ellipseBBox nil)
        sa = if(otype == "arc" && numberp(sh~>startAngle) sh~>startAngle 0.0)
        ea = if(otype == "arc" && numberp(sh~>stopAngle) sh~>stopAngle 0.0)
        out = strcat(out
          "{\"type\":" soJsonStr(otype)
          ",\"layer\":" soJsonStr(lname)
          ",\"purpose\":" soJsonStr(lpurp)
          ",\"pts\":" if(listp(sh~>points) soPtsJson(sh~>points) "[]")
          ",\"bBox\":" soBoxJson(sh~>bBox)
          ",\"text\":" soJsonStr(txt)
          ",\"xy\":" if(xy soPtJson(xy) "null")
          ",\"orient\":" soJsonStr(orient)
          ",\"justify\":" soJsonStr(just)
          ",\"height\":" soNum(h)
          ",\"ellipseBox\":" soBoxJson(ell)
          ",\"startAngle\":" soNum(sa)
          ",\"stopAngle\":" soNum(ea)
          "}"
        )
      )
    )
    strcat(out "]}")
  )
)

let((visitedCells visitedSyms queue cellJsons symJsons maxCells
     topLib topCell topView item lib cell view key cv
     cjson first inst ilib icell iview mkey master nlAct
     it firstIt sh lpp lname lpurp otype netName tm p0 pfig
     symCv childSch outPort)
  topLib = "__LIB__"
  topCell = "__CELL__"
  topView = "__VIEW__"
  maxCells = 48
  visitedCells = makeTable("soCells" nil)
  visitedSyms = makeTable("soSyms" nil)
  cellJsons = nil
  symJsons = nil
  queue = list(list(topLib topCell topView))

  while(queue && length(cellJsons) < maxCells
    item = car(queue)
    queue = cdr(queue)
    lib = car(item)
    cell = cadr(item)
    view = caddr(item)
    key = strcat(lib "/" cell "/" view)
    when(!visitedCells[key]
      visitedCells[key] = t
      cv = nil
      errset(cv = dbOpenCellViewByType(lib cell view "" "r") nil)
      when(cv
        ; Also capture this cell's own symbol view if it exists
        symCv = nil
        errset(symCv = dbOpenCellViewByType(lib cell "symbol" "" "r") nil)
        when(symCv
          mkey = strcat(lib "/" cell "/symbol")
          when(!visitedSyms[mkey]
            visitedSyms[mkey] = t
            symJsons = cons(strcat(soJsonStr(mkey) ":" soDumpSymbolJson(symCv lib cell "symbol")) symJsons)
          )
          dbClose(symCv)
        )

        cjson = strcat(
          "{\"lib\":" soJsonStr(lib)
          ",\"cell\":" soJsonStr(cell)
          ",\"view\":" soJsonStr(view)
          ",\"dbu\":" soNum(if(cv~>DBUPerUU cv~>DBUPerUU 160.0))
          ",\"instances\":["
        )
        first = t
        foreach(inst cv~>instances
          ilib = if(stringp(inst~>libName) inst~>libName "")
          icell = if(stringp(inst~>cellName) inst~>cellName "")
          iview = if(stringp(inst~>viewName) inst~>viewName "")
          nlAct = ""
          errset(
            when(inst~>nlAction nlAct = sprintf(nil "%L" inst~>nlAction))
            nil
          )
          master = inst~>master
          when(master
            mkey = strcat(ilib "/" icell "/" iview)
            when(!visitedSyms[mkey]
              visitedSyms[mkey] = t
              symJsons = cons(strcat(soJsonStr(mkey) ":" soDumpSymbolJson(master ilib icell iview)) symJsons)
            )
          )
          when(!first cjson = strcat(cjson ","))
          first = nil
          cjson = strcat(cjson
            "{\"name\":" soJsonStr(if(stringp(inst~>name) inst~>name ""))
            ",\"lib\":" soJsonStr(ilib)
            ",\"cell\":" soJsonStr(icell)
            ",\"view\":" soJsonStr(iview)
            ",\"purpose\":" soJsonStr(if(stringp(inst~>purpose) inst~>purpose ""))
            ",\"xy\":" soPtJson(inst~>xy)
            ",\"orient\":" soJsonStr(if(stringp(inst~>orient) inst~>orient "R0"))
            ",\"nlAction\":" soJsonStr(nlAct)
            ",\"instTerms\":["
          )
          firstIt = t
          foreach(it inst~>instTerms
            when(it~>name && it~>net && it~>net~>name
              when(!firstIt cjson = strcat(cjson ","))
              firstIt = nil
              cjson = strcat(cjson "[" soJsonStr(it~>name) "," soJsonStr(it~>net~>name) "]")
            )
          )
          cjson = strcat(cjson "]}")

          ; Queue subcircuit schematic if it is not a primitive/stop-view cell
          when(ilib != "" && icell != "" &&
               !member(ilib list("analogLib" "basic" "US_8ths" "sample" "ahdlLib" "rfLib")) &&
               !ddGetObj(ilib icell "spectre") &&
               ddGetObj(ilib icell "schematic")
            childSch = strcat(ilib "/" icell "/schematic")
            when(!visitedCells[childSch]
              queue = append(queue list(list(ilib icell "schematic")))
            )
          )
        )

        cjson = strcat(cjson "],\"terminals\":[")
        first = t
        foreach(tm cv~>terminals
          p0 = car(tm~>pins)
          pfig = if(p0 p0~>fig nil)
          when(!first cjson = strcat(cjson ","))
          first = nil
          cjson = strcat(cjson
            "{\"name\":" soJsonStr(tm~>name)
            ",\"dir\":" soJsonStr(if(stringp(tm~>direction) tm~>direction "inputOutput"))
            ",\"xy\":" if(pfig && pfig~>xy soPtJson(pfig~>xy) "null")
            ",\"bBox\":" if(pfig soBoxJson(pfig~>bBox) "null")
            ",\"orient\":" soJsonStr(if(pfig && stringp(pfig~>orient) pfig~>orient "R0"))
            "}"
          )
        )

        cjson = strcat(cjson "],\"wires\":[")
        first = t
        foreach(sh cv~>shapes
          lpp = sh~>lpp
          lname = if(listp(lpp) && stringp(car(lpp)) car(lpp) "")
          lpurp = if(listp(lpp) && length(lpp) >= 2 && stringp(cadr(lpp)) cadr(lpp) "")
          otype = if(stringp(sh~>objType) sh~>objType "")
          when(lname == "wire" && lpurp == "drawing" && member(otype list("line" "path")) && listp(sh~>points)
            netName = if(sh~>net && stringp(sh~>net~>name) sh~>net~>name "")
            when(!first cjson = strcat(cjson ","))
            first = nil
            cjson = strcat(cjson
              "{\"net\":" soJsonStr(netName)
              ",\"pts\":" soPtsJson(sh~>points)
              "}"
            )
          )
        )

        cjson = strcat(cjson "],\"labels\":[")
        first = t
        foreach(sh cv~>shapes
          lpp = sh~>lpp
          lname = if(listp(lpp) && stringp(car(lpp)) car(lpp) "")
          lpurp = if(listp(lpp) && length(lpp) >= 2 && stringp(cadr(lpp)) cadr(lpp) "")
          otype = if(stringp(sh~>objType) sh~>objType "")
          when(otype == "label" && lname == "wire" && stringp(sh~>theLabel)
            netName = if(sh~>net && stringp(sh~>net~>name) sh~>net~>name sh~>theLabel)
            when(!first cjson = strcat(cjson ","))
            first = nil
            cjson = strcat(cjson
              "{\"net\":" soJsonStr(netName)
              ",\"text\":" soJsonStr(sh~>theLabel)
              ",\"xy\":" soPtJson(sh~>xy)
              ",\"orient\":" soJsonStr(if(stringp(sh~>orient) sh~>orient "R0"))
              "}"
            )
          )
        )

        cjson = strcat(cjson "]}")
        cellJsons = cons(cjson cellJsons)
        dbClose(cv)
      )
    )
  )

  outPort = outfile("__OUT_JSON__" "w")
  when(outPort
    fprintf(outPort "{\"ok\":true,\"top\":{\"lib\":%s,\"cell\":%s,\"view\":%s},\"cells\":["
      soJsonStr(topLib) soJsonStr(topCell) soJsonStr(topView))
    first = t
    foreach(cjson reverse(cellJsons)
      when(!first fprintf(outPort ","))
      first = nil
      fprintf(outPort "%s" cjson)
    )
    fprintf(outPort "],\"symbols\":{")
    first = t
    foreach(cjson reverse(symJsons)
      when(!first fprintf(outPort ","))
      first = nil
      fprintf(outPort "%s" cjson)
    )
    fprintf(outPort "}}\n")
    close(outPort)
  )
)
exit()
"""


def _fmt_num(val: float) -> str:
    v = round(float(val), 3)
    if abs(v) < 1e-6:
        v = 0.0
    if abs(v - round(v)) < 1e-4:
        return str(int(round(v)))
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def _fmt_pt(x: float, y: float) -> str:
    return f"({_fmt_num(x)}, {_fmt_num(y)})"


def _rnl_quote(s: str) -> str:
    escaped = (
        str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", "")
    )
    return f'"{escaped}"'


def _transform_point(px: float, py: float, ox: float, oy: float, orient: str) -> tuple[float, float]:
    """Apply Cadence OA orientation + translation to a local symbol point."""
    if orient == "R90":
        rx, ry = -py, px
    elif orient == "R180":
        rx, ry = -px, -py
    elif orient == "R270":
        rx, ry = py, -px
    elif orient == "MX":
        rx, ry = px, -py
    elif orient == "MY":
        rx, ry = -px, py
    elif orient == "MXR90":
        rx, ry = py, px
    elif orient == "MYR90":
        rx, ry = -py, -px
    else:
        rx, ry = px, py
    return (ox + rx, oy + ry)


def _snap_key(x: float, y: float) -> tuple[int, int]:
    """Quantize RNL coordinates to 0.05 grid step for reliable pin/junction snapping."""
    return (int(round(x * 20.0)), int(round(y * 20.0)))


def _join_spectre_logical_lines(text: str) -> list[tuple[int, int, str]]:
    """Return list of (start_line_idx, end_line_idx, joined_text) for Spectre continuations."""
    raw_lines = text.splitlines()
    out: list[tuple[int, int, str]] = []
    i = 0
    n = len(raw_lines)
    while i < n:
        start = i
        buf = raw_lines[i]
        while buf.rstrip().endswith("\\") and i + 1 < n:
            buf = buf.rstrip()[:-1] + " " + raw_lines[i + 1].lstrip()
            i += 1
        out.append((start, i, buf))
        i += 1
    return out


_SUBCKT_RE = re.compile(r"^\s*subckt\s+([A-Za-z0-9_.\-$\\]+)(?:\s+(.*))?$", re.IGNORECASE)
_ENDS_RE = re.compile(r"^\s*ends(?:\s+([A-Za-z0-9_.\-$\\]+))?\s*$", re.IGNORECASE)
_INST_RE = re.compile(r"^\s*([A-Za-z0-9_.\-$\\]+)\s*\(([^)]*)\)\s+([A-Za-z0-9_.\-$\\]+)(?:\s|$)")
_SKIP_STMT_KW = frozenset({
    "simulator", "global", "parameters", "include", "ahdl_include", "section",
    "endsection", "model", "subckt", "ends", "options", "save", "saveoptions",
    "simulatoroptions", "tran", "dc", "ac", "stb", "noise", "pss", "pac", "pnoise",
    "info", "alter", "ic", "nodeset", "statistics", "process", "mismatch",
})


def _parse_spectre_scopes(netlist_text: str) -> dict[str, dict[str, Any]]:
    """Parse subcircuit & top-level instance names, net lists, and line insertion points."""
    raw_lines = netlist_text.splitlines()
    logical = _join_spectre_logical_lines(netlist_text)
    scopes: dict[str, dict[str, Any]] = {
        "__top__": {"name": "__top__", "ports": [], "instances": {}, "insert_after": len(raw_lines) - 1, "end_line": None}
    }
    current = "__top__"
    last_top_inst_line = -1

    for start_idx, end_idx, line in logical:
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("*"):
            continue
        ms = _SUBCKT_RE.match(line)
        if ms:
            sname = ms.group(1).strip()
            ports = [p for p in (ms.group(2) or "").split() if p and "=" not in p]
            scopes[sname] = {
                "name": sname,
                "ports": ports,
                "instances": {},
                "insert_after": end_idx,
                "end_line": None,
            }
            current = sname
            continue
        me = _ENDS_RE.match(line)
        if me and current != "__top__":
            scopes[current]["end_line"] = start_idx
            current = "__top__"
            continue
        first_tok = stripped.split()[0].lower()
        if first_tok in _SKIP_STMT_KW:
            continue
        mi = _INST_RE.match(line)
        if mi:
            iname = mi.group(1).strip()
            nets = [tok.strip() for tok in mi.group(2).split() if tok.strip()]
            model = mi.group(3).strip()
            scopes[current]["instances"][iname] = {"name": iname, "nets": nets, "model": model}
            if current == "__top__":
                last_top_inst_line = end_idx
            else:
                scopes[current]["insert_after"] = end_idx

    if last_top_inst_line >= 0:
        scopes["__top__"]["insert_after"] = last_top_inst_line
    return scopes


def _box_center(box: list[float] | None) -> tuple[float, float] | None:
    if not isinstance(box, list) or len(box) < 4:
        return None
    return ((float(box[0]) + float(box[2])) * 0.5, (float(box[1]) + float(box[3])) * 0.5)


def _scale_val(v: float, scale: float) -> float:
    return float(v) * scale


def _arc_three_points(shape: dict[str, Any], scale: float) -> list[tuple[float, float]] | None:
    ell = shape.get("ellipseBox") or shape.get("bBox")
    if not isinstance(ell, list) or len(ell) < 4:
        return None
    x1, y1, x2, y2 = [float(c) * scale for c in ell[:4]]
    cx, cy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
    rx, ry = abs(x2 - x1) * 0.5, abs(y2 - y1) * 0.5
    if rx < 1e-4 or ry < 1e-4:
        return None
    sa = float(shape.get("startAngle") or 0.0)
    ea = float(shape.get("stopAngle") or 0.0)
    if abs(ea - sa) < 1e-4:
        # Fallback: approximate top/bottom arc from bounding box
        return [(x1, y1), (cx, y2), (x2, y1)]
    # OA angles are in radians when small (< 2*pi + 0.1) or degrees otherwise
    if abs(sa) <= 6.4 and abs(ea) <= 6.4:
        a0, a1 = sa, ea
    else:
        a0, a1 = math.radians(sa), math.radians(ea)
    am = (a0 + a1) * 0.5
    return [
        (cx + rx * math.cos(a0), cy + ry * math.sin(a0)),
        (cx + rx * math.cos(am), cy + ry * math.sin(am)),
        (cx + rx * math.cos(a1), cy + ry * math.sin(a1)),
    ]


def _build_symbol_definition(
    sym_data: dict[str, Any],
    sym_var: str,
    *,
    terminal_binding: dict[str, int | str],
    is_block_symbol: bool = False,
    emit_labels: bool = True,
) -> tuple[str, dict[int | str, tuple[float, float]]]:
    """Build an RNL @DEFINE-SYMBOL block from OA symbol data and return pin local coords."""
    dbu = float(sym_data.get("dbu") or 160.0)
    scale = dbu / 10.0 if dbu > 0 else 16.0
    shapes = sym_data.get("shapes") or []
    terms = sym_data.get("terms") or []

    # Compute visual bounding box from device/instance shapes (excluding verbose annotate labels)
    xs: list[float] = []
    ys: list[float] = []
    stmts: list[str] = []
    name_pos: tuple[float, float] | None = None
    model_pos: tuple[float, float] | None = None

    def add_xy(px: float, py: float) -> None:
        xs.append(px)
        ys.append(py)

    for sh in shapes:
        stype = sh.get("type")
        layer = sh.get("layer") or ""
        pts = sh.get("pts") or []
        bb = sh.get("bBox")
        if stype in ("line", "path", "polygon") and layer in ("device", "instance", "wire") and len(pts) >= 2:
            spts = [(_scale_val(p[0], scale), _scale_val(p[1], scale)) for p in pts if isinstance(p, list) and len(p) >= 2]
            if stype == "polygon" and len(spts) >= 3 and spts[0] != spts[-1]:
                spts.append(spts[0])
            if len(spts) >= 2:
                for x, y in spts:
                    add_xy(x, y)
                stmts.append("    @LINE {" + " ".join(_fmt_pt(x, y) for x, y in spts) + "}")
        elif stype == "rect" and layer in ("device", "instance") and isinstance(bb, list) and len(bb) >= 4:
            x1, y1, x2, y2 = [_scale_val(c, scale) for c in bb[:4]]
            add_xy(x1, y1)
            add_xy(x2, y2)
            stmts.append(f"    @RECTANGLE {{{_fmt_pt(x1, y1)} {_fmt_pt(x2, y2)}}}")
        elif stype == "ellipse" and layer in ("device", "instance") and isinstance(bb, list) and len(bb) >= 4:
            x1, y1, x2, y2 = [_scale_val(c, scale) for c in bb[:4]]
            cx, cy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
            r = max(0.15, (abs(x2 - x1) + abs(y2 - y1)) * 0.25)
            add_xy(cx - r, cy - r)
            add_xy(cx + r, cy + r)
            stmts.append(f"    {_fmt_pt(cx, cy)} @CIRCLE(radius={_fmt_num(r)})")
        elif stype == "arc" and layer in ("device", "instance"):
            arc_pts = _arc_three_points(sh, scale)
            if arc_pts:
                for x, y in arc_pts:
                    add_xy(x, y)
                stmts.append("    @ARC {" + " ".join(_fmt_pt(x, y) for x, y in arc_pts) + "}")
        elif stype == "label":
            txt = (sh.get("text") or "").strip()
            xy = sh.get("xy")
            if not isinstance(xy, list) or len(xy) < 2:
                continue
            lx, ly = _scale_val(xy[0], scale), _scale_val(xy[1], scale)
            if "cdsName" in txt or txt == "[@instanceName]" or (layer == "annotate" and "name" in txt.lower()):
                name_pos = (lx, ly)
            elif "cdsParam" in txt or "partName" in txt or txt == "[@partName]":
                if model_pos is None:
                    model_pos = (lx, ly)
            elif layer == "pin" and txt and is_block_symbol and not txt.startswith("cds"):
                orient = sh.get("orient") or "R0"
                rot = ", rotate=90" if orient in ("R90", "R270", "MXR90", "MYR90") else ""
                stmts.append(f'    {_fmt_pt(lx, ly)} @TEXT({_rnl_quote(txt)}, size=1, halign="middle", valign="middle"{rot})')

    # Pins
    pin_local_coords: dict[int | str, tuple[float, float]] = {}
    emitted_terminals: set[int | str] = set()
    for tm in terms:
        tname = str(tm.get("name") or "").strip()
        if not tname or tname not in terminal_binding:
            continue
        bound = terminal_binding[tname]
        if bound in emitted_terminals:
            continue
        ctr = _box_center(tm.get("bBox"))
        if ctr is None:
            continue
        px, py = _scale_val(ctr[0], scale), _scale_val(ctr[1], scale)
        add_xy(px, py)
        emitted_terminals.add(bound)
        pin_local_coords[bound] = (px, py)
        if isinstance(bound, int):
            stmts.append(f"    {_fmt_pt(px, py)} @PIN(terminal={bound})")
        else:
            stmts.append(f"    {_fmt_pt(px, py)} @PIN(terminal={_rnl_quote(bound)})")

    if not xs or not ys:
        bb = sym_data.get("bBox")
        if isinstance(bb, list) and len(bb) >= 4:
            xs = [_scale_val(bb[0], scale), _scale_val(bb[2], scale)]
            ys = [_scale_val(bb[1], scale), _scale_val(bb[3], scale)]
        else:
            xs = [-2.0, 2.0]
            ys = [-2.0, 2.0]

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if abs(max_x - min_x) < 0.2:
        min_x -= 1.0
        max_x += 1.0
    if abs(max_y - min_y) < 0.2:
        min_y -= 1.0
        max_y += 1.0

    # Ensure there is at least a subtle outline if symbol had no drawable geometry
    if not any(s.lstrip().startswith(("@LINE", "@RECTANGLE", "@CIRCLE", "@ARC")) for s in stmts):
        stmts.insert(0, f"    @RECTANGLE {{{_fmt_pt(min_x, min_y)} {_fmt_pt(max_x, max_y)}}}")

    if emit_labels:
        nx, ny = name_pos if name_pos is not None else (max_x + 0.5, max_y + 0.5)
        mx, my = model_pos if model_pos is not None else ((min_x + max_x) * 0.5, min_y - 1.0)
        stmts.append(f'    ? @TEXT("${{name}}", size=0.8, halign="start", valign="baseline", key="name") => @PLACE({_fmt_num(nx)}, {_fmt_num(ny)})')
        stmts.append(f'    ? @TEXT("${{model}}", size=0.8, halign="middle", valign="baseline", key="model") => @PLACE({_fmt_num(mx)}, {_fmt_num(my)})')

    box_str = f"[{_fmt_num(min_x)}, {_fmt_num(min_y)}, {_fmt_num(max_x)}, {_fmt_num(max_y)}]"
    body = ";\n".join(stmts)
    return f"  {sym_var} = @DEFINE-SYMBOL(box={box_str}) {{\n{body}\n  }}", pin_local_coords


def _canonical_term_sort(terms: list[str]) -> list[str]:
    return sorted(
        terms,
        key=lambda t: (_CANONICAL_PIN_RANK.get(t.lower(), 100), t.lower()),
    )


def _resolve_terminal_binding(
    inst: dict[str, Any],
    sym_data: dict[str, Any],
    netlist_inst: dict[str, Any],
    subckt_ports: dict[str, list[str]],
) -> dict[str, int | str]:
    """Map OA symbol terminal names -> RNL terminal index (1..N) or subckt port name."""
    model = netlist_inst.get("model") or ""
    if model in subckt_ports and subckt_ports[model]:
        ports = subckt_ports[model]
        mapping: dict[str, int | str] = {}
        for idx, pname in enumerate(ports, start=1):
            mapping[pname] = idx
        # Also match by case-insensitive fallback
        lower_map = {p.lower(): idx for idx, p in enumerate(ports, start=1)}
        for tm in sym_data.get("terms") or []:
            tn = str(tm.get("name") or "").strip()
            if tn and tn not in mapping and tn.lower() in lower_map:
                mapping[tn] = lower_map[tn.lower()]
        return mapping

    # 1. Try CDF spectre termOrder directly
    term_order = [str(t).strip() for t in (sym_data.get("termOrder") or []) if str(t).strip()]
    netlist_nets = netlist_inst.get("nets") or []
    if term_order and len(term_order) == len(netlist_nets):
        return {t: idx for idx, t in enumerate(term_order, start=1)}

    # 2. Match OA instTerms (termName -> netName) against exact positional nets in Spectre netlist
    inst_terms = inst.get("instTerms") or []
    by_term: dict[str, str] = {}
    for pair in inst_terms:
        if isinstance(pair, list) and len(pair) == 2:
            by_term[str(pair[0]).strip()] = str(pair[1]).strip()
    if by_term and netlist_nets:
        used_indices: set[int] = set()
        mapping = {}
        # Prefer exact net name match (respecting term_order or canonical analog ordering)
        raw_sym_terms = [str(tm.get("name") or "").strip() for tm in (sym_data.get("terms") or []) if str(tm.get("name") or "").strip()]
        ordered_terms = list(dict.fromkeys(term_order + _canonical_term_sort(raw_sym_terms)))
        for tname in ordered_terms:
            if not tname or tname not in by_term:
                continue
            target_net = by_term[tname]
            for idx, nname in enumerate(netlist_nets, start=1):
                if idx in used_indices:
                    continue
                if nname == target_net or (target_net in ("gnd!", "0") and nname == "0"):
                    mapping[tname] = idx
                    used_indices.add(idx)
                    break
        # Assign remaining unmatched terminals in order
        for tname in ordered_terms:
            if tname and tname not in mapping:
                for idx in range(1, len(netlist_nets) + 1):
                    if idx not in used_indices:
                        mapping[tname] = idx
                        used_indices.add(idx)
                        break
        if mapping:
            return mapping

    # 3. Fallback: symbol terminal order 1..N
    sym_terms = [str(tm.get("name") or "").strip() for tm in (sym_data.get("terms") or []) if str(tm.get("name") or "").strip()]
    return {t: idx for idx, t in enumerate(sym_terms, start=1)}


def _build_cell_rnl_block(
    cell_data: dict[str, Any],
    scope: dict[str, Any],
    symbols_by_key: dict[str, dict[str, Any]],
    subckt_ports: dict[str, list[str]],
    *,
    is_top: bool,
) -> tuple[str, dict[str, int]]:
    """Build the complete `// @RNL(author="spiceowl-virtuoso") { ... }` block for one scope."""
    dbu = float(cell_data.get("dbu") or 160.0)
    scale = dbu / 10.0 if dbu > 0 else 16.0
    scope_instances = scope.get("instances") or {}
    statements: list[str] = []
    stats = {"placed": 0, "wires": 0, "ports": 0, "labels": 0}

    # Collect all known net names present in the Spectre netlist scope so we never reference
    # an undeclared net (which RNL would flag as a diagnostic).
    known_nets: set[str] = set(scope.get("ports") or [])
    for nl_inst in scope_instances.values():
        for nn in nl_inst.get("nets") or []:
            known_nets.add(nn)

    def normalize_net(raw_net: str) -> str:
        n = raw_net.strip()
        if n in known_nets:
            return n
        if n in ("gnd!", "GND!", "0") and "0" in known_nets:
            return "0"
        if n.endswith("!") and n[:-1] in known_nets:
            return n[:-1]
        return ""

    # 1. If this cell is a subcircuit and has its own symbol view, emit %block_symbol
    if not is_top:
        self_sym_key = f"{cell_data.get('lib')}/{cell_data.get('cell')}/symbol"
        self_sym = symbols_by_key.get(self_sym_key)
        ports = scope.get("ports") or []
        if self_sym and ports:
            port_binding: dict[str, int | str] = {p: p for p in ports}
            low_ports = {p.lower(): p for p in ports}
            for tm in self_sym.get("terms") or []:
                tn = str(tm.get("name") or "").strip()
                if tn and tn not in port_binding and tn.lower() in low_ports:
                    port_binding[tn] = low_ports[tn.lower()]
            blk_def, _ = _build_symbol_definition(
                self_sym,
                "%block_symbol",
                terminal_binding=port_binding,
                is_block_symbol=True,
            )
            statements.append(blk_def)
            statements.append("  # @SET-SYMBOL %block_symbol")

    # 2. Emit symbols and placements for every electrical instance matching the Spectre netlist
    sym_cache: dict[tuple[str, tuple[tuple[str, int | str], ...]], tuple[str, dict[int | str, tuple[float, float]]]] = {}
    pin_world_lookup: dict[tuple[int, int], list[tuple[str, int | str, str]]] = defaultdict(list)
    # Also map bus instances like I0<3:0> -> [I0_3, I0_2, I0_1, I0_0]
    bus_prefix_map: dict[str, list[str]] = defaultdict(list)
    for nl_name in scope_instances:
        if "_" in nl_name:
            base, tail = nl_name.rsplit("_", 1)
            if tail.isdigit():
                bus_prefix_map[base].append(nl_name)

    placed_nl_names: set[str] = set()
    bundle_counter = 0
    tap_counter = 0
    port_world_lookup: dict[tuple[int, int], tuple[str, str]] = {}

    for inst in cell_data.get("instances") or []:
        if inst.get("purpose") == "pin":
            continue
        ilib = str(inst.get("lib") or "")
        icell = str(inst.get("cell") or "")
        iview = str(inst.get("view") or "")
        nl_act = str(inst.get("nlAction") or "").lower()
        if "ignore" in nl_act:
            continue

        # Render Virtuoso ground / supply tap symbols (basic/gnd, vdd, vss, vcc) with their
        # original OA symbol geometry attached to the matching Spectre net.
        if icell.lower() in _SUPPLY_TAP_CELLS:
            xy = inst.get("xy")
            if isinstance(xy, list) and len(xy) >= 2:
                tap_net = ""
                for pair in inst.get("instTerms") or []:
                    if isinstance(pair, list) and len(pair) == 2:
                        cand = normalize_net(str(pair[1] or ""))
                        if cand:
                            tap_net = cand
                            break
                if not tap_net and icell.lower() == "gnd":
                    tap_net = normalize_net("0")
                if tap_net:
                    ox, oy = _scale_val(xy[0], scale), _scale_val(xy[1], scale)
                    orient = str(inst.get("orient") or "R0")
                    if orient not in VALID_ORIENTS:
                        orient = "R0"
                    mkey = f"{ilib}/{icell}/{iview}"
                    sym_data = symbols_by_key.get(mkey)
                    tap_sym_var = None
                    tap_pin_locals: dict[int | str, tuple[float, float]] = {}
                    if sym_data and (sym_data.get("terms") or []):
                        t0 = str(sym_data["terms"][0].get("name") or "").strip()
                        if t0:
                            cache_key = (f"tap:{mkey}", ((t0, 1),))
                            if cache_key not in sym_cache:
                                tap_sym_var = f"%symbol_{len(sym_cache)}"
                                sym_def, tap_pin_locals = _build_symbol_definition(
                                    sym_data,
                                    tap_sym_var,
                                    terminal_binding={t0: 1},
                                    is_block_symbol=False,
                                    emit_labels=False,
                                )
                                statements.append(sym_def)
                                sym_cache[cache_key] = (tap_sym_var, tap_pin_locals)
                            else:
                                tap_sym_var, tap_pin_locals = sym_cache[cache_key]
                    if tap_sym_var:
                        tvar = f"%tap_{tap_counter}"
                        tap_counter += 1
                        statements.append(
                            f"  {tvar} = $IO_PORT @ATTACH &{_rnl_quote(tap_net)} => @PLACE({_fmt_num(ox)}, {_fmt_num(oy)}) => @ORIENT({_rnl_quote(orient)})"
                        )
                        statements.append(f"  {tvar} @SET-SYMBOL {tap_sym_var}")
                        lx, ly = tap_pin_locals.get(1, (0.0, 0.0))
                        wx, wy = _transform_point(lx, ly, ox, oy, orient)
                        port_world_lookup[_snap_key(wx, wy)] = (tvar, tap_net)
                        stats["ports"] += 1
            continue

        if ilib in _PRIMITIVE_NON_ELECTRICAL_LIBS or icell in _NON_ELECTRICAL_CELLS:
            continue

        raw_name = str(inst.get("name") or "").strip()
        if not raw_name:
            continue

        matched_names: list[str] = []
        if raw_name in scope_instances:
            matched_names = [raw_name]
        elif "<" in raw_name:
            base = raw_name.split("<", 1)[0]
            if base in bus_prefix_map:
                matched_names = sorted(bus_prefix_map[base])
        if not matched_names:
            continue

        xy = inst.get("xy")
        if not isinstance(xy, list) or len(xy) < 2:
            continue
        ox, oy = _scale_val(xy[0], scale), _scale_val(xy[1], scale)
        orient = str(inst.get("orient") or "R0")
        if orient not in VALID_ORIENTS:
            orient = "R0"

        mkey = f"{ilib}/{icell}/{iview}"
        sym_data = symbols_by_key.get(mkey)
        primary_nl = scope_instances[matched_names[0]]
        pin_locals: dict[int | str, tuple[float, float]] = {}
        sym_var = None

        if sym_data:
            binding = _resolve_terminal_binding(inst, sym_data, primary_nl, subckt_ports)
            cache_key = (mkey, tuple(sorted(binding.items())))
            if cache_key not in sym_cache:
                sym_var = f"%symbol_{len(sym_cache)}"
                sym_def, pin_locals = _build_symbol_definition(
                    sym_data,
                    sym_var,
                    terminal_binding=binding,
                    is_block_symbol=False,
                )
                statements.append(sym_def)
                sym_cache[cache_key] = (sym_var, pin_locals)
            else:
                sym_var, pin_locals = sym_cache[cache_key]

        if len(matched_names) == 1:
            iname = matched_names[0]
            if iname in placed_nl_names:
                continue
            placed_nl_names.add(iname)
            if sym_var:
                statements.append(f"  #{_rnl_quote(iname)} @SET-SYMBOL {sym_var}")
            statements.append(
                f"  #{_rnl_quote(iname)} @PLACE({_fmt_num(ox)}, {_fmt_num(oy)}) => @ORIENT({_rnl_quote(orient)})"
            )
            stats["placed"] += 1
            # Record world pin positions for wire snapping
            nl_nets = primary_nl.get("nets") or []
            for term_id, (lx, ly) in pin_locals.items():
                wx, wy = _transform_point(lx, ly, ox, oy, orient)
                net_hint = ""
                if isinstance(term_id, int) and 1 <= term_id <= len(nl_nets):
                    net_hint = nl_nets[term_id - 1]
                pin_world_lookup[_snap_key(wx, wy)].append((iname, term_id, net_hint))
        else:
            # Bus bundle (e.g., I0<3:0>)
            unplaced_members = [m for m in matched_names if m not in placed_nl_names]
            if not unplaced_members:
                continue
            for m in unplaced_members:
                placed_nl_names.add(m)
            bvar = f"%bundle_{bundle_counter}"
            bundle_counter += 1
            members_str = " ".join(f"#{_rnl_quote(m)}" for m in unplaced_members)
            statements.append(f"  {bvar} = {{{members_str}}} @BUNDLE")
            if sym_var:
                statements.append(f"  {bvar} @SET-SYMBOL {sym_var}")
            statements.append(f"  {bvar} @PLACE({_fmt_num(ox)}, {_fmt_num(oy)}) => @ORIENT({_rnl_quote(orient)})")
            statements.append(f"  {bvar} @TEXT({_rnl_quote(raw_name)}, key=\"name\")")
            stats["placed"] += len(unplaced_members)

    # 3. Emit schematic I/O ports for subcircuit terminals and top-level pins
    if cell_data.get("terminals") or []:
        in_sym_emitted = False
        out_sym_emitted = False
        allowed_ports = set(scope.get("ports") or []) if not is_top else known_nets
        for idx, tm in enumerate(cell_data.get("terminals") or []):
            tname = normalize_net(str(tm.get("name") or "").strip())
            if not tname or tname not in allowed_ports:
                continue
            xy = tm.get("xy")
            if not isinstance(xy, list) or len(xy) < 2:
                ctr = _box_center(tm.get("bBox"))
                if ctr is None:
                    continue
                px, py = _scale_val(ctr[0], scale), _scale_val(ctr[1], scale)
            else:
                px, py = _scale_val(xy[0], scale), _scale_val(xy[1], scale)
            tdir = str(tm.get("dir") or "inputOutput").lower()
            orient = str(tm.get("orient") or "R0")
            if orient not in VALID_ORIENTS:
                orient = "R0"
            pvar = f"%port_{idx}"
            if tdir == "output":
                if not out_sym_emitted:
                    statements.append(
                        '  %output_port_symbol = @DEFINE-SYMBOL(box=[0, -1, 2, 1]) {\n'
                        '    @LINE(fill="linecolor", linestyle="none") {(0, -0.9) (0, 0.9) (1.1, 0.9) (2, 0) (1.1, -0.9) (0, -0.9)};\n'
                        '    (0, 0) @PIN(terminal=1, direction="left");\n'
                        '    ? @TEXT("${name}", key="name", halign="middle") => @PLACE(1, 1.6)\n'
                        '  }'
                    )
                    out_sym_emitted = True
                psym = "%output_port_symbol"
            else:
                if not in_sym_emitted:
                    statements.append(
                        '  %input_port_symbol = @DEFINE-SYMBOL(box=[-2, -1, 0, 1]) {\n'
                        '    @LINE(fill="linecolor", linestyle="none") {(-2, -0.9) (-2, 0.9) (-0.9, 0.9) (0, 0) (-0.9, -0.9) (-2, -0.9)};\n'
                        '    (0, 0) @PIN(terminal=1, direction="right");\n'
                        '    ? @TEXT("${name}", key="name", halign="middle") => @PLACE(-1, 1.6)\n'
                        '  }'
                    )
                    in_sym_emitted = True
                psym = "%input_port_symbol"
            statements.append(
                f"  {pvar} = $IO_PORT @ATTACH &{_rnl_quote(tname)} => @PLACE({_fmt_num(px)}, {_fmt_num(py)}) => @ORIENT({_rnl_quote(orient)})"
            )
            statements.append(f"  {pvar} @SET-SYMBOL {psym}")
            statements.append(f"  {pvar} @TEXT({_rnl_quote(tname)}, key=\"name\")")
            port_world_lookup[_snap_key(px, py)] = (pvar, tname)
            stats["ports"] += 1

    # 4. Emit wires snapped to exact instance pins & shared points, with junctions and net labels
    raw_segments: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    pt_degree: dict[tuple[int, int], int] = defaultdict(int)
    for w in cell_data.get("wires") or []:
        net = normalize_net(str(w.get("net") or ""))
        if not net:
            continue
        pts = w.get("pts") or []
        spts = [(_scale_val(p[0], scale), _scale_val(p[1], scale)) for p in pts if isinstance(p, list) and len(p) >= 2]
        for a, b in zip(spts, spts[1:]):
            if _snap_key(a[0], a[1]) == _snap_key(b[0], b[1]):
                continue
            raw_segments.append((net, a, b))
            pt_degree[_snap_key(a[0], a[1])] += 1
            pt_degree[_snap_key(b[0], b[1])] += 1

    point_vars: dict[tuple[int, int], str] = {}
    point_coords: dict[tuple[int, int], tuple[float, float]] = {}

    def endpoint_token(net: str, x: float, y: float) -> str:
        sk = _snap_key(x, y)
        # Prefer snapping to an instance pin on the same net
        candidates = pin_world_lookup.get(sk) or []
        for iname, tid, nhint in candidates:
            if not nhint or nhint == net or (nhint == "0" and net in ("0", "gnd!")):
                if isinstance(tid, int):
                    return f"#{_rnl_quote(iname)}:{tid}"
                return f"#{_rnl_quote(iname)}:{_rnl_quote(tid)}"
        if sk in port_world_lookup and port_world_lookup[sk][1] == net:
            return f"{port_world_lookup[sk][0]}:1"
        if sk not in point_vars:
            pvar = f"%p{len(point_vars)}"
            point_vars[sk] = pvar
            point_coords[sk] = (x, y)
            statements.append(f"  {pvar} = {_fmt_pt(x, y)}")
        return point_vars[sk]

    wire_records: list[tuple[str, str, tuple[float, float], tuple[float, float]]] = []
    for idx, (net, a, b) in enumerate(raw_segments):
        tok_a = endpoint_token(net, a[0], a[1])
        tok_b = endpoint_token(net, b[0], b[1])
        if tok_a == tok_b:
            continue
        wvar = f"%wire_{idx}"
        statements.append(f"  {wvar} = &{_rnl_quote(net)} @WIRE(mode=\"straight\") {{{tok_a} {tok_b}}}")
        wire_records.append((wvar, net, a, b))
        stats["wires"] += 1

    # Attach wire labels to nearest wire segment of the same net
    for lb in cell_data.get("labels") or []:
        net = normalize_net(str(lb.get("net") or lb.get("text") or ""))
        txt = str(lb.get("text") or "").strip()
        xy = lb.get("xy")
        if not net or not txt or not isinstance(xy, list) or len(xy) < 2:
            continue
        lx, ly = _scale_val(xy[0], scale), _scale_val(xy[1], scale)
        best_w = None
        best_d2 = float("inf")
        for wvar, wnet, a, b in wire_records:
            if wnet != net:
                continue
            mx, my = (a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5
            d2 = (lx - mx) ** 2 + (ly - my) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_w = wvar
        if best_w is not None:
            orient = str(lb.get("orient") or "R0")
            rot = ", rotate=90" if orient in ("R90", "R270", "MXR90", "MYR90") else ""
            statements.append(
                f'  {best_w} @TEXT({_rnl_quote(txt)}, size=1, halign="middle", valign="baseline"{rot}) => @PLACE({_fmt_num(lx)}, {_fmt_num(ly)})'
            )
            stats["labels"] += 1

    # Emit junctions wherever 3+ wire endpoints meet at a point variable
    for sk, pvar in point_vars.items():
        if pt_degree.get(sk, 0) >= 3:
            statements.append(f"  $JUNCTION @ATTACH {pvar}")

    if not statements:
        return "", stats

    block_lines = ['// @RNL(author="spiceowl-virtuoso") {']
    for i, st in enumerate(statements):
        suffix = ";" if i < len(statements) - 1 else ""
        for j, subline in enumerate(st.splitlines()):
            tail = suffix if j == len(st.splitlines()) - 1 else ""
            block_lines.append("// " + subline + tail)
    block_lines.append("// }")
    return "\n".join(block_lines), stats


def inject_rnl_geometry(
    netlist_text: str,
    oa_geometry: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Inject RNL 0.3 geometry blocks into a Spectre netlist exported by Cadence ``si``.

    Returns ``(annotated_netlist_text, summary_stats)``.
    """
    if not isinstance(oa_geometry, dict) or not oa_geometry.get("ok"):
        return netlist_text, {"applied": False, "cells": 0, "placed": 0, "wires": 0, "ports": 0, "labels": 0}

    scopes = _parse_spectre_scopes(netlist_text)
    subckt_ports = {
        name: sc["ports"]
        for name, sc in scopes.items()
        if name != "__top__" and sc.get("ports")
    }
    symbols_by_key = oa_geometry.get("symbols") if isinstance(oa_geometry.get("symbols"), dict) else {}
    top_cell_name = str((oa_geometry.get("top") or {}).get("cell") or "")
    cells = oa_geometry.get("cells") or []

    insertions: dict[int, list[str]] = defaultdict(list)
    totals = {"applied": False, "cells": 0, "placed": 0, "wires": 0, "ports": 0, "labels": 0}

    for cell_data in cells:
        if not isinstance(cell_data, dict):
            continue
        cname = str(cell_data.get("cell") or "")
        is_top = (cname == top_cell_name) and (cname not in scopes)
        scope = scopes.get("__top__") if is_top else scopes.get(cname)
        if not scope:
            continue
        rnl_block, st = _build_cell_rnl_block(
            cell_data,
            scope,
            symbols_by_key,
            subckt_ports,
            is_top=is_top,
        )
        if not rnl_block:
            continue
        insert_line = scope["end_line"] - 1 if (not is_top and scope.get("end_line") is not None) else scope["insert_after"]
        insertions[max(0, insert_line)].append(rnl_block)
        totals["applied"] = True
        totals["cells"] += 1
        totals["placed"] += st["placed"]
        totals["wires"] += st["wires"]
        totals["ports"] += st["ports"]
        totals["labels"] += st["labels"]

    if not totals["applied"]:
        return netlist_text, totals

    raw_lines = netlist_text.splitlines()
    out_lines: list[str] = []
    # RNL spec 2.2: line 1 of a SPICE/Spectre file is the title line and is never read as RNL.
    # Always place the RNL 0.3 header record on line 2 so both Spectre and RNL parsers accept it cleanly.
    start_idx = 0
    if '@RNL(type="header")' not in netlist_text:
        if raw_lines and raw_lines[0].lstrip().startswith(("//", "*")):
            out_lines.append(raw_lines[0])
            out_lines.append('// @RNL(type="header") { @VERSION("0.3") }')
            if 0 in insertions:
                out_lines.extend(insertions[0])
            start_idx = 1
        else:
            out_lines.append("// Exported from Cadence Virtuoso schematic")
            out_lines.append('// @RNL(type="header") { @VERSION("0.3") }')
    for idx in range(start_idx, len(raw_lines)):
        out_lines.append(raw_lines[idx])
        if idx in insertions:
            for blk in insertions[idx]:
                out_lines.append(blk)
    return "\n".join(out_lines) + "\n", totals
