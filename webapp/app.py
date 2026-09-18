"""COBOL graph explorer — browse a graphify code graph and ask an LLM about it.

The LLM never sees the COBOL source, only facts retrieved from the graph, and is
told to answer strictly from them. Every claim it makes can be traced to a
file:line already on screen.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAPH_PATH = Path(os.environ.get(
    "GRAPH_PATH", str(REPO_ROOT / "carddemo-graph/graphify-out/graph.json")))
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:4000")
GATEWAY_MODEL = os.environ.get("GATEWAY_MODEL", "anthropic-prod/fast")
AGENTGATEWAY_CONFIG = Path(os.environ.get(
    "AGENTGATEWAY_CONFIG", "~/.config/agentgateway/config.yaml")).expanduser()


def _gateway_key() -> str:
    """The key this app presents to agentgateway.

    Prefer the environment. Otherwise read it out of agentgateway's own config,
    where it already lives — so the secret is never passed on a command line
    (visible in `ps`) or copied into a second file to drift out of sync.
    """
    env = os.environ.get("GATEWAY_API_KEY", "")
    if env:
        return env
    try:
        import yaml
        cfg = yaml.safe_load(AGENTGATEWAY_CONFIG.read_text())
        return cfg["llm"]["policies"]["apiKey"]["keys"][0]["key"]
    except Exception:
        return ""


GATEWAY_API_KEY = _gateway_key()

# Source files live next to the graph: <corpus>/graphify-out/graph.json
CORPUS_ROOT = Path(os.environ.get("CORPUS_ROOT", str(GRAPH_PATH.parent.parent)))

app = FastAPI(title="COBOL Graph Explorer")

# ---------------------------------------------------------------- graph model

class Graph:
    def __init__(self, path: Path):
        raw = json.loads(path.read_text())
        self.nodes: dict[str, dict] = {n["id"]: n for n in raw["nodes"]}
        self.links: list[dict] = raw["links"]
        self.out: dict[str, list[dict]] = defaultdict(list)
        self.inc: dict[str, list[dict]] = defaultdict(list)
        for e in self.links:
            self.out[e["source"]].append(e)
            self.inc[e["target"]].append(e)
        self._classify()

    def _classify(self) -> None:
        """Derive a node kind. graphify persists only its own fields, so the
        extractor's `kind` does not survive the write — rebuild it from edges."""
        for n in self.nodes.values():
            n["kind"] = "stub" if not n.get("source_file") else "file"
        for e in self.links:
            src, tgt = self.nodes.get(e["source"]), self.nodes.get(e["target"])
            if not src or not tgt:
                continue
            if e["relation"] == "defines" and src["kind"] == "file":
                tgt["kind"] = "copybook" if src["label"].lower().endswith(".cpy") else "program"
            elif e["relation"] == "contains" and tgt.get("kind") == "file":
                tgt["kind"] = "paragraph"

    def label(self, nid: str) -> str:
        return self.nodes.get(nid, {}).get("label", nid)

    def find(self, term: str) -> list[dict]:
        t = term.strip().lower()
        if not t:
            return []
        exact = [n for n in self.nodes.values() if n["label"].lower() == t]
        partial = [n for n in self.nodes.values()
                   if t in n["label"].lower() and n["label"].lower() != t]
        return exact + partial

    def detail(self, nid: str) -> dict:
        n = self.nodes.get(nid)
        if not n:
            raise HTTPException(404, f"no node {nid}")
        def edge(e, direction):
            other = e["target"] if direction == "out" else e["source"]
            return {"id": other, "label": self.label(other),
                    "kind": self.nodes.get(other, {}).get("kind", "?"),
                    "relation": e["relation"], "context": e.get("context", ""),
                    "confidence": e.get("confidence", ""),
                    "where": f'{e.get("source_file","")}:{e.get("source_location","")}'}
        return {"node": n,
                "outgoing": [edge(e, "out") for e in self.out[nid]],
                "incoming": [edge(e, "in") for e in self.inc[nid]]}

    def neighbourhood(self, nid: str, hops: int = 1) -> list[str]:
        seen, frontier = {nid}, [nid]
        for _ in range(hops):
            nxt = []
            for cur in frontier:
                for e in self.out[cur] + self.inc[cur]:
                    for other in (e["source"], e["target"]):
                        if other not in seen:
                            seen.add(other); nxt.append(other)
            frontier = nxt
        return list(seen)

    def stats(self) -> dict:
        kinds = defaultdict(int)
        for n in self.nodes.values():
            kinds[n["kind"]] += 1
        rels = defaultdict(int)
        for e in self.links:
            rels[f'{e["relation"]}/{e.get("context","-")}'] += 1
        programs = sorted(n["label"] for n in self.nodes.values() if n["kind"] == "program")
        copybooks = sorted(n["label"] for n in self.nodes.values() if n["kind"] == "copybook")
        return {"nodes": len(self.nodes), "edges": len(self.links),
                "kinds": dict(kinds), "relations": dict(sorted(rels.items(), key=lambda kv: -kv[1])),
                "programs": programs, "copybooks": copybooks}

G = Graph(GRAPH_PATH)

# ------------------------------------------------------------------ retrieval

def facts_for(question: str, max_chars: int = 14000) -> tuple[str, list[str]]:
    """Pull the slice of the graph a question is about, as plain text lines.

    Matching is by label mention: COBOL names (CBTRN02C, CVACT01Y, 1000-ACCTFILE-GET-NEXT)
    are distinctive enough that substring matching on word-ish tokens works well.
    """
    q = question.upper()
    hits = [n for n in G.nodes.values()
            if len(n["label"]) >= 4 and n["label"].upper().split(".")[0] in q]
    hits.sort(key=lambda n: -len(n["label"]))
    hits = hits[:8]
    lines: list[str] = []
    used: list[str] = []
    for n in hits:
        used.append(n["label"])
        d = G.detail(n["id"])
        lines.append(f'\n## {n["label"]} ({n["kind"]}) defined in {n.get("source_file","?")}')
        for e in d["outgoing"][:60]:
            lines.append(f'  {n["label"]} --{e["relation"]}'
                         f'{"/" + e["context"] if e["context"] else ""}--> {e["label"]}'
                         f' [{e["confidence"]}] {e["where"]}')
        for e in d["incoming"][:60]:
            lines.append(f'  {e["label"]} --{e["relation"]}'
                         f'{"/" + e["context"] if e["context"] else ""}--> {n["label"]}'
                         f' [{e["confidence"]}] {e["where"]}')
    if not hits:
        # No named entity: give the shape of the system instead of nothing.
        s = G.stats()
        lines.append(f'System overview: {s["nodes"]} nodes, {s["edges"]} edges.')
        lines.append(f'Programs ({len(s["programs"])}): {", ".join(s["programs"])}')
        lines.append(f'Copybooks ({len(s["copybooks"])}): {", ".join(s["copybooks"])}')
        lines.append("Edge types: " + ", ".join(f"{k}={v}" for k, v in s["relations"].items()))
        for e in G.links:
            if e.get("context") in ("cics_xctl", "call"):
                lines.append(f'  {G.label(e["source"])} --{e.get("context")}--> '
                             f'{G.label(e["target"])} {e.get("source_file")}:{e.get("source_location")}')
    text = "\n".join(lines)
    return text[:max_chars], used

SYSTEM = """You explain a legacy COBOL system to a developer who has never read COBOL.

You are given FACTS extracted from a static code graph. Answer ONLY from those facts.
If the facts do not settle something, say so plainly — never guess at COBOL behaviour
that is not in the facts.

Conventions you should explain as you go, because the reader does not know COBOL:
- PERFORM calls a paragraph inside the same program (like a local function call).
- CALL invokes a separate compiled program.
- EXEC CICS XCTL transfers control to another program and does NOT return — this is how
  mainframe screens hand off to each other.
- COPY includes a copybook: a shared record layout, like an #include or a shared type.
- A dataset is a VSAM file (mainframe indexed file storage).
- Edges marked INFERRED were resolved through a variable (e.g. MOVE 'COMEN01C' TO WS-PGM),
  so they are strong but not certain; EXTRACTED edges are literal in the source.

Cite file:line in parentheses for the specific claims you make. Be concise and concrete.
Use short paragraphs or bullets. Lead with the answer, not with preamble."""


class Ask(BaseModel):
    question: str
    model: str | None = None


@app.post("/api/ask")
async def ask(body: Ask):
    if not GATEWAY_API_KEY:
        raise HTTPException(503, f"No gateway key: set GATEWAY_API_KEY, or keep one in {AGENTGATEWAY_CONFIG}.")
    facts, used = facts_for(body.question)
    payload = {
        "model": body.model or GATEWAY_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"FACTS FROM THE CODE GRAPH:\n{facts}\n\n"
                                        f"QUESTION: {body.question}"},
        ],
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            r = await client.post(f"{GATEWAY_URL}/v1/chat/completions",
                                  headers={"Authorization": f"Bearer {GATEWAY_API_KEY}"},
                                  json=payload)
        except httpx.RequestError as e:
            raise HTTPException(502, f"gateway unreachable at {GATEWAY_URL}: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"gateway {r.status_code}: {r.text[:300]}")
    data = r.json()
    return {"answer": data["choices"][0]["message"]["content"],
            "entities": used,
            "facts_chars": len(facts),
            "model": data.get("model", payload["model"])}


class Investigate(BaseModel):
    question: str
    model: str | None = None


@app.post("/api/investigate")
async def investigate(body: Investigate):
    """Agent mode: the model drives, reading real COBOL through function tools.
    Slower and costlier than /api/ask — use it for behaviour, not structure.

    The harness is LangChain deepagents (`cobol_deep_agent`), which plans with a
    todo list and can hand a long call chain to a subagent; the tools it drives
    are in `cobol_tools`."""
    if not GATEWAY_API_KEY:
        raise HTTPException(503, f"No gateway key: set GATEWAY_API_KEY, or keep one in {AGENTGATEWAY_CONFIG}.")
    import cobol_deep_agent
    cobol_deep_agent.configure(G, CORPUS_ROOT)
    try:
        out = await cobol_deep_agent.investigate(
            body.question, GATEWAY_URL, body.model or GATEWAY_MODEL, GATEWAY_API_KEY)
    except Exception as e:
        raise HTTPException(502, f"agent failed: {type(e).__name__}: {e}")
    return {"answer": out["answer"], "trace": out["trace"],
            "todos": out.get("todos", []),
            "model": body.model or GATEWAY_MODEL, "mode": "agent", "engine": "deepagents"}


@app.post("/api/investigate/stream")
async def investigate_stream(body: Investigate):
    """The same investigation, streamed as SSE so the UI can show the plan being
    written and worked through rather than only its finished state."""
    if not GATEWAY_API_KEY:
        raise HTTPException(503, f"No gateway key: set GATEWAY_API_KEY, or keep one in {AGENTGATEWAY_CONFIG}.")
    import cobol_deep_agent
    cobol_deep_agent.configure(G, CORPUS_ROOT)

    async def events():
        try:
            async for ev in cobol_deep_agent.investigate_stream(
                    body.question, GATEWAY_URL, body.model or GATEWAY_MODEL, GATEWAY_API_KEY):
                if ev.get("type") == "answer":
                    ev = {**ev, "model": body.model or GATEWAY_MODEL,
                          "mode": "agent", "engine": "deepagents"}
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:                      # the stream is already open, so
            yield "data: " + json.dumps({           # the error has to travel in it
                "type": "error", "error": f"{type(e).__name__}: {e}"}) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/stats")
def stats():
    return G.stats()


@app.get("/api/search")
def search(q: str):
    return [{"id": n["id"], "label": n["label"], "kind": n["kind"],
             "file": n.get("source_file", "")} for n in G.find(q)[:50]]


@app.get("/api/node")
def node(id: str):
    return G.detail(id)


@app.get("/api/impact")
def impact(label: str, depth: int = 2):
    """Who depends on this thing — reverse traversal, the modernization question."""
    matches = [n for n in G.nodes.values() if n["label"].upper() == label.upper()]
    if not matches:
        raise HTTPException(404, f"no node labelled {label}")
    start = max(matches, key=lambda n: len(G.inc[n["id"]]))["id"]
    seen, frontier, out = {start}, [start], []
    for d in range(1, depth + 1):
        nxt = []
        for cur in frontier:
            for e in G.inc[cur]:
                s = e["source"]
                if s in seen:
                    continue
                seen.add(s); nxt.append(s)
                out.append({"label": G.label(s), "kind": G.nodes[s]["kind"], "depth": d,
                            "via": e["relation"],
                            "where": f'{e.get("source_file")}:{e.get("source_location")}'})
        frontier = nxt
    return {"root": G.label(start), "affected": out}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
