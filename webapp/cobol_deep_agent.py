"""Investigation agent, LangChain deepagents harness.

Same six COBOL tools as `cobol_agent` (both import `cobol_tools`); what differs
is the loop around them. deepagents adds, on top of a plain tool-calling agent:

- `write_todos`, a planning scratchpad the model keeps as it works. A CardDemo
  question like "how does a payment get rejected" is a multi-program trace, and
  the todo list is what stops it answering after the first program it reads.
  `TodoListMiddleware` is not in the default stack, so it is added below —
  without it the prompt would reference a tool that does not exist.
- a filesystem, rooted at the COBOL corpus and read-only. The default
  `StateBackend` is an empty scratchpad, and a model that reaches for `ls` or
  `read_file` first — they do — finds nothing there and reports that it cannot
  access the COBOL. Pointing `FilesystemBackend` at the corpus makes that
  instinct correct instead of wrong. Writes are denied outright, so the agent
  cannot edit the corpus it is describing.
- `task`, which spawns a subagent whose context is discarded once it reports
  back. The `tracer` subagent below exists so that following one PERFORM chain
  20 paragraphs deep does not crowd out the main thread's context.

No shell tool is configured, which matters because this app binds 0.0.0.0:
`LocalShellBackend` and the sandbox backends are what grant `execute`, and we
use neither.
"""
from __future__ import annotations

from pathlib import Path

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

import cobol_tools
from cobol_tools import INSTRUCTIONS, TOOL_FUNCTIONS

# LangChain infers each schema from the signature and docstring, same as the
# Agents SDK does, so the two harnesses expose identical tools.
TOOLS = [tool(fn) for fn in TOOL_FUNCTIONS]

# Named so the trace can tell our tools from the harness's own built-ins.
COBOL_TOOL_NAMES = {fn.__name__ for fn in TOOL_FUNCTIONS}

SYSTEM_PROMPT = INSTRUCTIONS + """

You are running in a harness with a todo list, a scratch filesystem and subagents.

The filesystem tools (`ls`, `glob`, `grep`, `read_file`) are rooted at the COBOL
corpus itself: `cbl/` holds the 31 programs, `cpy/` the 30 copybooks. They are
read-only, and they return RAW fixed-form lines — sequence numbers, comments and
all. Prefer `read_paragraph` and `read_lines`, which strip that noise and know
where each paragraph ends; fall back to `read_file` only to see a whole file at
once. Never report that you cannot access the COBOL: if a path fails, list `cbl/`
or call `find_symbol` to get the real filename.

Plan before you read, always. Your FIRST action on any question is `write_todos`
with 2-6 items naming what you will look at — one per program, paragraph chain or
check, phrased as the thing to find out ("read COBIL00C payment validation"), not
as "step 1". Then work the list:
- exactly one item `in_progress` at a time, and mark it `completed` the moment it
  is answered — call `write_todos` again with the full updated list to do that;
- add an item when the code sends you somewhere you had not anticipated, which is
  the normal case in COBOL — an XCTL to a program you had not listed;
- do not answer while an item is still `in_progress`.
The person asking watches this list as you work, so keep it honest and current.

Use `task` with the `tracer` subagent to follow a long PERFORM/CALL chain when you
want the conclusion without the twenty intermediate paragraph listings."""

TRACER = {
    "name": "tracer",
    "description": ("Follows one call chain (PERFORM/CALL/XCTL) through the COBOL and "
                    "reports back what it does and the file:line citations, without "
                    "returning every paragraph it read along the way."),
    "system_prompt": (
        "You follow a single COBOL control-flow chain to its end and report back.\n"
        "Use `dependencies` to find the next hop and `read_paragraph` to see what each "
        "step does. Stop when the chain ends or leaves the program set.\n"
        "Report: the ordered chain, what each step does in one line, and the file:line "
        "for each. Note any edge marked INFERRED — it was resolved through a variable "
        "and may be wrong. Be terse; your caller wants the conclusion, not the listings."),
    "tools": TOOLS,
}


def configure(graph, corpus_root: Path) -> None:
    cobol_tools.configure(graph, corpus_root)


def _trace_from_messages(messages) -> list[dict]:
    """Rebuild the tool trace from the message stream.

    `cobol_tools.TRACE` sees only our own tools; the harness's `write_todos`,
    `task` and filesystem calls are just as much a part of what happened, and
    the UI should show the planning, not hide it.
    """
    out = []
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            args = dict(call.get("args") or {})
            name = call.get("name", "?")
            if name == "write_todos":                      # summarise, don't dump
                todos = args.get("todos") or []
                args = {"todos": len(todos),
                        "doing": next((t.get("content") for t in todos
                                       if isinstance(t, dict) and t.get("status") == "in_progress"), "")}
            elif name == "task":
                args = {"subagent": args.get("subagent_type", ""),
                        "task": str(args.get("description") or args.get("task") or "")[:120]}
            out.append({"tool": name,
                        "builtin": name not in COBOL_TOOL_NAMES,
                        **{k: v for k, v in args.items() if v not in (None, "")}})
    return out


def _final_text(messages) -> str:
    """The last thing the model actually said, ignoring tool-call-only turns."""
    for m in reversed(messages):
        if getattr(m, "type", "") != "ai":
            continue
        if isinstance(m.content, str) and m.content.strip():
            return m.content
        if isinstance(m.content, list):
            text = "".join(b.get("text", "") for b in m.content if isinstance(b, dict))
            if text.strip():
                return text
    return ""


def build(base_url: str, model: str, api_key: str, temperature: float = 0.1):
    """A configured deep agent. Cheap enough to build per request, and doing so
    keeps the graph free of state carried over from someone else's question."""
    root = cobol_tools.corpus_root()
    llm = ChatOpenAI(
        model=model,
        base_url=f"{base_url.rstrip('/')}/v1",
        api_key=api_key,
        temperature=temperature,
        timeout=120,
    )
    return create_deep_agent(
        model=llm,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        backend=FilesystemBackend(root_dir=str(root), virtual_mode=True),
        # Read-only: this serves a web app on 0.0.0.0, and nothing it does should
        # be able to change the corpus. No shell either — `execute` needs a
        # sandbox or LocalShellBackend, and we configure neither.
        permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],
        middleware=[TodoListMiddleware()],
        subagents=[TRACER],
    )


async def investigate(question: str, base_url: str, model: str, api_key: str,
                      max_turns: int = 24) -> dict:
    cobol_tools.TRACE.clear()
    agent = build(base_url, model, api_key)
    # recursion_limit counts graph steps, not model turns; a tool call and its
    # response are two, so this is roughly max_turns round trips.
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"recursion_limit": max_turns * 2},
    )
    messages = result.get("messages", [])
    answer = _final_text(messages)
    todos = result.get("todos") or []
    return {"answer": answer or "(the agent returned no text)",
            "trace": _trace_from_messages(messages),
            "todos": [t for t in todos if isinstance(t, dict)]}


async def investigate_stream(question: str, base_url: str, model: str, api_key: str,
                             max_turns: int = 24):
    """The same run, as an event stream: plan first, then each step as it happens.

    A finished todo list is all `completed` and says nothing about how the agent
    got there, so the UI watches the run instead — `stream_mode="values"` hands
    us the whole state after every graph step, and we emit only what changed.
    """
    cobol_tools.TRACE.clear()
    agent = build(base_url, model, api_key)
    seen_calls, last_todos, answer = 0, None, ""
    async for state in agent.astream(
        {"messages": [{"role": "user", "content": question}]},
        config={"recursion_limit": max_turns * 2},
        stream_mode="values",
    ):
        todos = [t for t in (state.get("todos") or []) if isinstance(t, dict)]
        if todos != last_todos:
            last_todos = todos
            yield {"type": "todos", "todos": todos}
        trace = _trace_from_messages(state.get("messages", []))
        for step in trace[seen_calls:]:
            yield {"type": "tool", **step}
        seen_calls = len(trace)
        answer = _final_text(state.get("messages", [])) or answer
    yield {"type": "answer", "answer": answer or "(the agent returned no text)",
           "todos": last_todos or []}
