"""BMS map parser: the 3270 screen layouts behind the CICS programs.

CardDemo's screens are not COBOL. Each is a `.bms` mapset written in IBM
assembler macro syntax, which the BMS macro assembler turns into two things: a
load module CICS uses to paint the terminal, and a COBOL copybook of the
fields (the "symbolic map", `TYPE=DSECT`). The programs `COPY` the latter and
never mention a coordinate, so the layout exists *only* in the `.bms` file.

Three macros matter. `DFHMSD` opens a mapset, `DFHMDI` declares one map with a
`SIZE=(rows,cols)`, and a `DFHMDF` per field pins it down.

Two details drive everything below, and both are easy to get wrong:

* `POS=(row,col)` locates the field's **attribute byte**, not its first
  character — data starts one column later. Get this off by one and every
  centred heading in the corpus lands a column to the left.
* Continuation lines resume at **column 16**, with column 72 holding the
  continuation mark. For keyword operands that is just the next keyword, but a
  quoted `INITIAL=` string can also split across lines, and there the space at
  column 16 is part of the literal. Joining on `strip()` instead turns
  `'...from the list'` into `'...from thelist'`.

Fields with no name are static labels. `LENGTH=0` fields are real and load
bearing: they exist to place a lone attribute byte that stops the preceding
input field from running on across the screen.
"""
from __future__ import annotations

import re
from pathlib import Path

# A named field is `NAME    DFHMDF ...`; a static label omits the name.
_MACRO = re.compile(r"^(\S*)\s*DFHMD([IF])\s+(.*)$")
_KV = re.compile(r"(\w+)=(\([^)]*\)|'(?:[^']|'')*'|[^,\s]+)")
_INITIAL = re.compile(r"INITIAL='((?:[^']|'')*)'")
_CONT_COL = 71   # 0-based index of column 72, the continuation mark
_RESUME_COL = 15  # 0-based index of column 16, where a continuation resumes

# ATTRB keywords that make a field typeable. Everything else is protected:
# BMS defaults a field with no ATTRB at all to protected, normal intensity.
_INPUT_ATTRS = {"UNPROT", "NUM"}


def _logical_lines(text: str) -> list[str]:
    """Join continuation lines into one statement each, preserving literals."""
    out: list[str] = []
    buf = ""
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("*") or not line.strip():
            continue
        body = line[_RESUME_COL:] if buf else line
        if len(line) > _CONT_COL and line[_CONT_COL] != " ":
            # Keep trailing blanks: inside a split literal they are data.
            buf += body[: _CONT_COL - (_RESUME_COL if buf else 0)]
        else:
            out.append((buf + body).strip())
            buf = ""
    if buf:
        out.append(buf.strip())
    return out


def _operands(args: str) -> tuple[dict[str, str], str | None]:
    """Split a macro's operands, lifting INITIAL out before comma-splitting so
    a literal containing commas cannot be mistaken for more keywords."""
    initial = None
    m = _INITIAL.search(args)
    if m:
        initial = m.group(1).replace("''", "'")
        args = args[: m.start()] + args[m.end():]
    kv = {k: v for k, v in _KV.findall(args)}
    return kv, initial


def _ints(value: str | None, default: tuple[int, ...]) -> tuple[int, ...]:
    nums = re.findall(r"\d+", value or "")
    return tuple(int(n) for n in nums) if nums else default


def parse_mapset(path: Path) -> dict:
    """Parse one `.bms` file into `{mapset, maps: [{map, rows, cols, fields}]}`."""
    text = path.read_text(encoding="latin-1")
    mapset = path.stem.upper()
    maps: list[dict] = []

    for line in _logical_lines(text):
        m = _MACRO.match(line)
        if not m:
            continue
        name, kind, args = m.group(1), m.group(2), m.group(3)
        kv, initial = _operands(args)

        if kind == "I":
            rows, cols = _ints(kv.get("SIZE"), (24, 80))[:2]
            maps.append({"map": name or mapset, "rows": rows, "cols": cols,
                         "fields": []})
            continue

        if not maps:  # a DFHMDF before any DFHMDI: malformed, skip it
            continue
        row, col = _ints(kv.get("POS"), (1, 1))[:2]
        attrb = re.findall(r"\w+", kv.get("ATTRB", ""))
        length = int(kv.get("LENGTH", len(initial) if initial else 0))
        maps[-1]["fields"].append({
            "name": name or None,
            "row": row, "col": col, "len": length,
            "attrb": attrb,
            "color": kv.get("COLOR"),
            "hilight": kv.get("HILIGHT"),
            "initial": initial,
            "picin": (kv.get("PICIN") or "").strip("'") or None,
            "mustfill": "MUSTFILL" in kv.get("VALIDN", ""),
            "input": bool(_INPUT_ATTRS & set(attrb)),
        })

    return {"mapset": mapset, "maps": maps}


def load_mapsets(root: Path) -> dict[str, dict]:
    """Parse every `.bms` under `root`, keyed by mapset name."""
    return {p.stem.upper(): parse_mapset(p) for p in sorted(root.glob("*.bms"))}


# ------------------------------------------------------- program <-> mapset

# Most programs name their mapset inline. The five COACT*/COCRD* programs do
# not, and they split two ways: COCRDLIC passes its own working-storage
# LIT-THISMAPSET, while the other four pass CCARD-NEXT-MAPSET, a copybook field
# that only ever gets LIT-THISMAPSET moved into it. Resolving a mapset name
# therefore needs a VALUE lookup plus one hop of MOVE, the same shape as the
# INFERRED edges the graph extractor builds.
#
# The VALUE is also space-padded to its PIC, so `PIC X(8) VALUE 'COACTVW '`
# carries a trailing blank that is not part of the mapset name.
_MAPSET_LIT = re.compile(r"MAPSET\s*\(\s*'\s*([A-Z0-9]+)\s*'\s*\)")
_MAPSET_VAR = re.compile(r"MAPSET\s*\(\s*([A-Z][A-Z0-9-]*)\s*\)")
_MAX_HOPS = 2


def _literal_of(text: str, var: str, hops: int = _MAX_HOPS) -> str | None:
    """Resolve a COBOL identifier to its literal, following MOVE if needed."""
    if hops <= 0:
        return None
    name = re.escape(var)
    m = re.search(rf"\b{name}\s+PIC\s+X\(\d+\)\s+VALUE\s*'\s*([A-Z0-9]+)\s*'", text)
    if m:
        return m.group(1)
    m = re.search(rf"MOVE\s+([A-Z][A-Z0-9-]*)\s+TO\s+{name}\b", text)
    if m:
        return _literal_of(text, m.group(1), hops - 1)
    return None


def mapsets_for_source(text: str) -> set[str]:
    """Mapset names a COBOL source sends or receives, literal or via a variable."""
    found = set(_MAPSET_LIT.findall(text))
    for var in _MAPSET_VAR.findall(text):
        lit = _literal_of(text, var)
        if lit:
            found.add(lit)
    return found


def program_index(cbl_root: Path) -> dict[str, list[str]]:
    """Map each mapset to the programs that use it, and vice versa is a flip."""
    index: dict[str, list[str]] = {}
    for src in sorted(cbl_root.glob("*.[Cc][Bb][Ll]")):
        for ms in mapsets_for_source(src.read_text(encoding="latin-1")):
            index.setdefault(ms, []).append(src.stem.upper())
    return index


# ---------------------------------------------------------------- rendering

def cells(mp: dict, data: dict[str, str] | None = None) -> list[list[dict | None]]:
    """Lay a map out as a rows x cols grid of cells, one per screen position.

    Each cell is `{"ch", "field"}` or None for an untouched position. The
    attribute byte at `POS` is left empty on purpose: it occupies a column and
    displays as a blank on a real 3270.
    """
    data = data or {}
    grid: list[list[dict | None]] = [[None] * mp["cols"] for _ in range(mp["rows"])]
    for idx, f in enumerate(mp["fields"]):
        text = data.get(f["name"], f["initial"] or "")
        text = text[: f["len"]].ljust(f["len"])
        for i, ch in enumerate(text):
            c = f["col"] + i          # +1 for the attribute byte, -1 for 0-based
            if 0 <= c < mp["cols"] and 0 <= f["row"] - 1 < mp["rows"]:
                grid[f["row"] - 1][c] = {"ch": ch, "field": idx}
    return grid


def to_text(mp: dict, data: dict[str, str] | None = None) -> str:
    """The map as plain text, for a terminal or a test."""
    return "\n".join(
        "".join(c["ch"] if c else " " for c in row).rstrip()
        for row in cells(mp, data)
    )
