"""Investigation agent: graph tools for navigation, source tools for behaviour.

The graph answers "who calls whom"; only the source answers "what does it compute".
Every source tool is anchored by graph coordinates, so the agent reads the right
30 lines instead of grepping 180 KB files.

COBOL fixed-form handling is central, not incidental: columns 1-6 are sequence
numbers, column 7 is an indicator (`*` comment, `-` continuation), code lives in
8-72, and 73-80 is ignored. A tool that returns raw lines feeds the model noise
and comment text it cannot tell apart from code.
"""
from __future__ import annotations

import re
from pathlib import Path

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, Runner, function_tool, set_tracing_disabled
from openai import AsyncOpenAI

_G = None          # graph, injected by app.py
_ROOT = Path(".")  # corpus root, injected by app.py
TRACE: list[dict] = []


def configure(graph, corpus_root: Path) -> None:
    global _G, _ROOT
    _G, _ROOT = graph, Path(corpus_root)


def _trace(tool: str, **kw) -> None:
    TRACE.append({"tool": tool, **kw})


def _strip_fixed_form(line: str) -> str | None:
    """Return the code area of a fixed-form line, or None if it carries no code."""
    if len(line) < 7:
        return line.rstrip() or None
    indicator = line[6]
    if indicator in ("*", "/"):          # comment line
        return None
    body = line[7:72].rstrip()
    return body or None


def _resolve_file(file: str) -> str:
    """Accept either a path (cbl/COBIL00C.cbl) or a bare program/copybook name.

    The model reaches for the name it just saw in a graph answer, so resolving it
    here saves a wasted round trip through `dependencies`.
    """
    if (_ROOT / file).exists():
        return file
    stem = file.upper().split(".")[0]
    for n in _G.nodes.values():
        src = n.get("source_file", "")
        if src and n["label"].upper().split(".")[0] == stem:
            return src
    return file


def _slice(file: str, start: int, end: int, keep_comments: bool = False) -> str:
    file = _resolve_file(file)
    path = (_ROOT / file).resolve()
    if not str(path).startswith(str(_ROOT.resolve())):
        return f"refused: {file} is outside the corpus"
    if not path.exists():
        return f"no such file: {file}"
    out = []
    for i, raw in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if i < start or i > end:
            continue
        code = raw.rstrip() if keep_comments else _strip_fixed_form(raw)
        if code:
            out.append(f"{i:5} {code}")
    return "\n".join(out) or "(no code lines in that range)"


# ------------------------------------------------------------------ the tools

@function_tool
def find_symbol(name: str) -> str:
    """Look up a COBOL program, copybook or paragraph by name. Returns each match
    with its kind and the file it lives in. Use this first when a name is unfamiliar."""
    _trace("find_symbol", name=name)
    hits = _G.find(name)[:12]
    if not hits:
        return f"nothing named {name}"
    return "\n".join(f'{h["label"]} ({h["kind"]}) {h.get("source_file","")} '
                     f'{h.get("source_location","")}' for h in hits)


@function_tool
def dependencies(name: str) -> str:
    """What a program/paragraph depends on and what depends on it: PERFORM, CALL,
    EXEC CICS XCTL, COPY and dataset edges, each with file:line. Structure only —
    for what the code actually computes, read the source."""
    _trace("dependencies", name=name)
    hits = [n for n in _G.nodes.values() if n["label"].upper() == name.upper()]
    if not hits:
        return f"nothing named {name}"
    node = max(hits, key=lambda n: len(_G.out[n["id"]]) + len(_G.inc[n["id"]]))
    d = _G.detail(node["id"])
    lines = [f'{node["label"]} ({node["kind"]}) in {node.get("source_file")}']
    for e in d["outgoing"][:80]:
        rel = e["relation"] + (f'/{e["context"]}' if e["context"] else "")
        lines.append(f'  -> {rel} {e["label"]} [{e["confidence"]}] {e["where"]}')
    for e in d["incoming"][:40]:
        rel = e["relation"] + (f'/{e["context"]}' if e["context"] else "")
        lines.append(f'  <- {rel} {e["label"]} [{e["confidence"]}] {e["where"]}')
    return "\n".join(lines)


@function_tool
def read_paragraph(program: str, paragraph: str) -> str:
    """Read the actual COBOL source of one paragraph, comments stripped and
    sequence numbers removed. This is how you find out what the code DOES —
    the conditions it tests, the fields it moves, the arithmetic it performs.
    Prefer this over reading whole files."""
    _trace("read_paragraph", program=program, paragraph=paragraph)
    prog = next((n for n in _G.nodes.values()
                 if n["label"].upper() == program.upper() and n["kind"] == "program"), None)
    if not prog:
        return f"no program {program}"
    file = prog.get("source_file", "")
    # Every paragraph of this program, in source order — the next one's start line
    # is this one's end. COBOL has no closing delimiter, so the graph IS the boundary.
    paras = sorted(
        ((_G.nodes[e["target"]]["label"],
          int(str(_G.nodes[e["target"]].get("source_location", "L0")).lstrip("L") or 0))
         for e in _G.out[prog["id"]] if e["relation"] == "contains"),
        key=lambda p: p[1])
    if not paras:
        return f"{program} has no paragraphs in the graph"
    want = paragraph.upper()
    for i, (label, start) in enumerate(paras):
        if label.upper() == want:
            end = paras[i + 1][1] - 1 if i + 1 < len(paras) else start + 400
            return f"{program}.{label}  ({file}:L{start}-L{end})\n" + _slice(file, start, end)
    return (f"{program} has no paragraph {paragraph}. It has: "
            + ", ".join(p[0] for p in paras[:40]))


@function_tool
def read_lines(file: str, start: int, end: int) -> str:
    """Read a line range of a source file (comments stripped). Use it to follow a
    file:line citation from the graph, or to read a program's opening DIVISIONs.
    Keep ranges under ~150 lines."""
    _trace("read_lines", file=file, start=start, end=end)
    return _slice(file, start, min(end, start + 300))


@function_tool
def read_comments(file: str, start: int, end: int) -> str:
    """Read a line range INCLUDING comment lines. COBOL programs usually carry a
    header comment block stating what the program is for — read lines 1-30 of a
    file with this tool when you need that intent."""
    _trace("read_comments", file=file, start=start, end=end)
    return _slice(file, start, min(end, start + 120), keep_comments=True)


@function_tool
def grep_cobol(pattern: str, file_filter: str = "") -> str:
    """Search the code area of every COBOL source for a regex (case-insensitive),
    skipping comments and sequence numbers. Use it for what the graph does NOT
    model: arithmetic (COMPUTE, ADD, MULTIPLY), data movement (MOVE), literals,
    and business terms. file_filter narrows to filenames containing that string."""
    _trace("grep_cobol", pattern=pattern, file_filter=file_filter)
    try:
        rx = re.compile(pattern, re.I)
    except re.error as e:
        return f"bad regex: {e}"
    out, hits = [], 0
    for path in sorted(_ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".cbl", ".cpy"):
            continue
        if file_filter and file_filter.lower() not in path.name.lower():
            continue
        rel = str(path.relative_to(_ROOT))
        for i, raw in enumerate(path.read_text(errors="replace").splitlines(), 1):
            code = _strip_fixed_form(raw)
            if code and rx.search(code):
                out.append(f"{rel}:L{i}  {code.strip()}")
                hits += 1
                if hits >= 60:
                    return "\n".join(out) + "\n… truncated at 60 hits; narrow the pattern."
    return "\n".join(out) or f"no matches for {pattern}"


TOOLS = [find_symbol, dependencies, read_paragraph, read_lines, read_comments, grep_cobol]

INSTRUCTIONS = """You investigate a legacy COBOL system for a developer who has never read COBOL.

Work in this order:
1. `dependencies` to see the structure — who calls what, which files are touched.
2. `read_paragraph` / `read_lines` to see what the code actually does.
3. `grep_cobol` for data flow the graph cannot model: COMPUTE/ADD/MULTIPLY arithmetic,
   MOVE statements, literals, business terms.
4. `read_comments` on lines 1-30 of a file for the author's own statement of intent.

Do not answer a behavioural question ("what does it calculate", "when does it reject a
payment") from the graph alone — read the source. Do not guess a program's purpose from
its name; names mislead (CBACT is ACCOUNT, not activity).

COBOL for a newcomer, explain as you go:
- PERFORM calls a paragraph in the same program (a local function call).
- CALL invokes a separate compiled program.
- EXEC CICS XCTL hands control to another program and never returns — mainframe screen flow.
- COPY includes a copybook: a shared record layout.
- A dataset is a VSAM file. PIC clauses declare field types; S9(10)V99 is a signed
  decimal with 2 places.

Cite file:line for every specific claim. Lead with the answer. Be concrete and brief;
quote the COBOL line that settles a point rather than describing it vaguely."""


async def investigate(question: str, base_url: str, model: str, api_key: str,
                      max_turns: int = 12) -> dict:
    TRACE.clear()
    set_tracing_disabled(True)
    client = AsyncOpenAI(base_url=f"{base_url.rstrip('/')}/v1", api_key=api_key)
    agent = Agent(
        name="cobol-investigator",
        instructions=INSTRUCTIONS,
        model=OpenAIChatCompletionsModel(model=model, openai_client=client),
        model_settings=ModelSettings(temperature=0.1),
        tools=TOOLS,
    )
    result = await Runner.run(agent, question, max_turns=max_turns)
    return {"answer": result.final_output, "trace": list(TRACE)}
