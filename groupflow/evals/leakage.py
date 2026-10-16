"""Does an evaluation sentence appear in what the model is told?

A scenario whose sentence (or a close copy) is written into the system prompt or a tool description
measures recall of an example, not understanding. This module collects everything the model can be
told before or during a turn (the host prompts, the server instructions, every tool's title,
description and parameter descriptions, and the template catalogue it can read on demand) and
reports, per scenario utterance, which word 5-grams it shares with that text.

The original suite was written alongside the prompt and shares sentences with it; the realistic
held-out suite must share none (tests/evals/test_realistic.py enforces this).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import re
from typing import Any

N = 5


def _list_tools() -> list[Any]:
    from mcp.server.mcpserver import MCPServer

    from groupflow.mcp_server import tools
    from groupflow.mcp_server.common import ToolRunner

    server = MCPServer(name="corpus", instructions=tools.INSTRUCTIONS)
    tools.register(server, None, ToolRunner())  # the engine is only used inside tool bodies, which never run here
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:  # safe inside or outside a running event loop
        return pool.submit(asyncio.run, server.list_tools()).result()


def _schema_texts(schema: Any) -> list[str]:
    out: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "description" and isinstance(value, str):
                out.append(value)
            else:
                out += _schema_texts(value)
    elif isinstance(schema, list):
        for value in schema:
            out += _schema_texts(value)
    return out


def prompt_corpus() -> list[str]:
    """Every text the model can be given by the host or the server, in a stable order."""
    from groupflow.mcp_server.tools import INSTRUCTIONS
    from groupflow.sim.agent import PROJECT_WORKFLOW, SURFACES, check_message, system_prompt
    from groupflow.templates import load_templates
    from groupflow.templates import presets_for

    corpus = [INSTRUCTIONS, PROJECT_WORKFLOW, check_message("PERSON")]
    corpus += [system_prompt("PERSON", "TODAY", surface) for surface in SURFACES]
    for t in _list_tools():
        corpus += [t.name, t.title or "", t.description or "", *_schema_texts(t.input_schema)]
    for t in load_templates().values():
        corpus += [t.name, t.description]
        corpus += [f"{i.label} {i.description or ''} {' '.join(i.intent_terms)}" for i in t.item_types]
        corpus += [f"{c.label} {c.hint or ''}" for c in t.conditions]
        corpus += [str(p) for p in presets_for(t.id)]
    return [c for c in corpus if c]


def corpus_hash(corpus: list[str] | None = None) -> str:
    return "sha256:" + hashlib.sha256("\n".join(corpus if corpus is not None else prompt_corpus()).encode("utf-8")).hexdigest()


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("’", "").replace("'", ""))


def grams(text: str, n: int = N) -> set[tuple[str, ...]]:
    w = words(text)
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def corpus_grams(corpus: list[str] | None = None, n: int = N) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for text in corpus if corpus is not None else prompt_corpus():
        out |= grams(text, n)
    return out


def utterances(scenarios: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(scenario id, text) for everything a person says in the scenarios: turns to Alexa and group chat messages."""
    return [(sc["id"], step["text"]) for sc in scenarios for step in sc.get("steps", []) if ("say" in step or "chat" in step) and step.get("text")]


def overlap_report(scenarios: list[dict[str, Any]], corpus: list[str] | None = None, n: int = N) -> dict[str, Any]:
    corpus = corpus if corpus is not None else prompt_corpus()
    known = corpus_grams(corpus, n)
    hits = []
    for sid, text in utterances(scenarios):
        shared = sorted(" ".join(g) for g in grams(text, n) & known)
        if shared:
            hits.append({"scenario": sid, "text": text, "shared": shared})
    return {"n": n, "utterances": len(utterances(scenarios)), "with_overlap": len(hits), "hits": hits, "corpus_sha256": corpus_hash(corpus)}


def names_in_corpus(names: list[str], corpus: list[str] | None = None) -> list[str]:
    text = "\n".join(corpus if corpus is not None else prompt_corpus()).lower()
    return [n for n in names if re.search(r"\b" + re.escape(n.lower()) + r"\b", text)]
