"""COBOL extractor (IBM Enterprise dialect, EXEC CICS/SQL aware).

Follows the bespoke-extractor pattern of graphify/extractors/fortran.py:
tree-sitter parse -> walk nodes -> collect nodes/edges -> second pass for
INFERRED call edges.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extractors.base import _file_stem, _make_id, _read_text

# CICS commands that transfer control to another program. XCTL does not return
# (the classic pseudo-conversational screen flow); LINK does.
_CICS_CALL_CMDS = {"cics_xctl": "cics_xctl", "cics_link": "cics_link"}
# CICS commands that touch a VSAM file, keyed by the option naming the dataset.
_CICS_FILE_CMDS = {
    "cics_read", "cics_readnext", "cics_readprev", "cics_startbr", "cics_endbr",
    "cics_write", "cics_rewrite", "cics_delete",
}


def extract_cobol(path: Path) -> dict:
    """Extract programs, paragraphs, PERFORM/CALL/XCTL edges, COPY includes and
    file assignments from a COBOL source file or copybook."""
    try:
        import tree_sitter_cobol as tscobol
        from tree_sitter import Language, Parser
    except ImportError:
        return {"nodes": [], "edges": [], "error": "tree-sitter-cobol not installed"}

    try:
        language = Language(tscobol.language())
        parser = Parser(language)
        source = path.read_bytes()
        tree = parser.parse(source)
        root = tree.root_node
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    stem = _file_stem(path)
    str_path = str(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()

    def add_node(nid: str, label: str, line: int, **extra) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append({"id": nid, "label": label, "file_type": "code",
                          "source_file": str_path, "source_location": f"L{line}", **extra})

    def add_stub(name: str) -> str:
        """SOURCELESS stub for a symbol defined in another file (a called program,
        a copybook). Emitting it unsourced lets the corpus-level rewire collapse it
        onto the real definition instead of forking a phantom duplicate."""
        nid = _make_id(name)
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append({"id": nid, "label": name, "file_type": "code",
                          "source_file": "", "source_location": "", "origin_file": str_path})
        return nid

    def add_edge(src: str, tgt: str, relation: str, line: int,
                 confidence: str = "EXTRACTED", weight: float = 1.0,
                 context: str | None = None) -> None:
        edge = {"source": src, "target": tgt, "relation": relation,
                "confidence": confidence, "source_file": str_path,
                "source_location": f"L{line}", "weight": weight}
        if context:
            edge["context"] = context
        edges.append(edge)

    def word(node) -> str | None:
        """First WORD token under node, upper-cased (COBOL is case-insensitive)."""
        for c in node.children:
            if c.type == "WORD":
                return _read_text(c, source).upper()
            if c.type == "identifier":
                return _read_text(c, source).upper().strip()
        return None

    file_nid = _make_id(str(path))
    add_node(file_nid, path.name, 1)

    # ---- pass 1: program, paragraphs, and the literal/MOVE tables -----------
    program_nid = file_nid
    program_name = None
    paragraphs: list[tuple[str, int]] = []   # (node_id, start_byte)
    values: dict[str, str] = {}              # data item -> VALUE 'literal'
    moves: dict[str, set[str]] = {}          # target item -> {source items/literals}

    def walk_defs(node) -> None:
        nonlocal program_nid, program_name
        t = node.type
        if t == "program_id_paragraph" and program_name is None:
            program_name = word(node)
            if program_name:
                program_nid = _make_id(stem, program_name)
                line = node.start_point[0] + 1
                add_node(program_nid, program_name, line, kind="program")
                add_edge(file_nid, program_nid, "defines", line)
        elif t == "paragraph":
            name = word(node)
            if name:
                nid = _make_id(stem, name)
                line = node.start_point[0] + 1
                add_node(nid, name, line, kind="paragraph")
                add_edge(program_nid, nid, "contains", line)
                paragraphs.append((nid, node.start_byte))
        elif t == "data_description":
            name = None
            literal = None
            for c in node.children:
                if c.type == "entry_name":
                    name = _read_text(c, source).upper().strip()
                elif c.type == "value_clause":
                    for gc in c.children:
                        if gc.type == "string_literal":
                            literal = _read_text(gc, source).strip("'\" ").upper()
            if name and literal:
                values[name] = literal
        elif t == "move_statement":
            kids = [c for c in node.children if c.type in ("identifier", "string_literal")]
            if len(kids) >= 2:
                src_txt = _read_text(kids[0], source).strip("'\" ").upper().strip()
                for tgt in kids[1:]:
                    moves.setdefault(_read_text(tgt, source).upper().strip(), set()).add(src_txt)
        for child in node.children:
            walk_defs(child)

    walk_defs(root)

    # A copybook has no PROGRAM-ID, so nothing carries its name. Give it a node
    # labelled with its bare stem (CVACT01Y), which is exactly how `COPY CVACT01Y`
    # names it: graphify's stub rewire matches on label, and the file node's own
    # label (CVACT01Y.cpy) is rejected as type-like because of the dot. Without
    # this every referencing program mints its own phantom copy of the copybook.
    if program_name is None:
        book = path.stem.upper()
        program_nid = _make_id(stem, book)
        add_node(program_nid, book, 1, kind="copybook")
        add_edge(file_nid, program_nid, "defines", 1)

    def resolve(name: str, depth: int = 3) -> set[str]:
        """Resolve a data item to the program-name literals it can hold.

        The CICS idiom is two hops: `MOVE LIT-MENUPGM TO CDEMO-TO-PROGRAM` where
        LIT-MENUPGM is declared `PIC X(8) VALUE 'COMEN01C'`. Anything unresolved
        falls back to the variable name itself, so the dispatch point still shows
        up in the graph.
        """
        out: set[str] = set()
        seen: set[str] = set()
        stack = [(name, depth)]
        while stack:
            cur, d = stack.pop()
            if cur in seen or d < 0:
                continue
            seen.add(cur)
            if cur in values:
                out.add(values[cur])
            for src in moves.get(cur, ()):  # noqa: B007
                if src in values:
                    out.add(values[src])
                else:
                    stack.append((src, d - 1))
        return out

    # ---- pass 2: control-flow and dependency edges --------------------------
    paragraphs.sort(key=lambda p: p[1])

    def enclosing(byte_offset: int) -> str:
        scope = program_nid
        for nid, start in paragraphs:
            if start <= byte_offset:
                scope = nid
            else:
                break
        return scope

    def cics_option(cmd_node, option_name: str) -> str | None:
        """Value of a named EXEC CICS option, e.g. PROGRAM(...) or DATASET(...)."""
        for opt in cmd_node.children:
            if opt.type != "cics_option":
                continue
            words = [c for c in opt.children if c.type == "WORD"]
            if words and _read_text(words[0], source).upper() == option_name and len(words) > 1:
                return _read_text(words[1], source).upper().strip()
        return None

    def walk_refs(node) -> None:
        t = node.type
        line = node.start_point[0] + 1
        scope = enclosing(node.start_byte)

        if t == "perform_statement":
            # PERFORM X / PERFORM X THRU Y — every WORD child is a paragraph name.
            for c in node.children:
                if c.type != "WORD":
                    continue
                tgt = _make_id(stem, _read_text(c, source).upper())
                if tgt in seen_ids and tgt != scope:
                    add_edge(scope, tgt, "calls", line, context="perform")

        elif t == "call_statement":
            first = node.children[0] if node.children else None
            if first is not None and first.type == "string_literal":
                callee = _read_text(first, source).strip("'\" ").upper()
                add_edge(scope, add_stub(callee), "calls", line, context="call")
            elif first is not None:
                var = _read_text(first, source).upper().strip()
                targets = resolve(var) or {var}
                for tgt in targets:
                    add_edge(scope, add_stub(tgt), "calls", line,
                             confidence="INFERRED", context="dynamic_call")

        elif t == "copy_statement":
            book = word(node)
            if book:
                add_edge(program_nid, add_stub(book), "imports", line, context="copy")

        elif t == "exec_cics_statement":
            for cmd in node.children:
                ct = cmd.type
                if ct in _CICS_CALL_CMDS:
                    var = cics_option(cmd, "PROGRAM")
                    if var:
                        targets = resolve(var) or {var}
                        for tgt in targets:
                            add_edge(scope, add_stub(tgt), "calls", line,
                                     confidence="INFERRED", context=_CICS_CALL_CMDS[ct])
                elif ct in _CICS_FILE_CMDS:
                    var = cics_option(cmd, "DATASET") or cics_option(cmd, "FILE")
                    if var:
                        for tgt in (resolve(var) or {var}):
                            add_edge(scope, add_stub(tgt), "references", line,
                                     confidence="INFERRED", context="cics_file")

        elif t == "select_statement":
            words = [c for c in node.children if c.type == "WORD"]
            if len(words) >= 2:
                dataset = _read_text(words[1], source).upper()
                add_edge(program_nid, add_stub(dataset), "references", line,
                         context="dataset")

        for child in node.children:
            walk_refs(child)

    walk_refs(root)
    return {"nodes": nodes, "edges": edges}
