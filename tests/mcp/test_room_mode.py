"""A host that does not say who will hear the reply (Alexa+ today, a kitchen Echo) gets room mode by default: summaries leave out
plans kept from someone in their group and the speaker's private limits, and such a plan is still served when it is named.
A personal device declares itself with X-GroupFlow-Listeners: me and gets everything the speaker may see."""
from __future__ import annotations

import json

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from groupflow.core.serve import serve_in_background
from groupflow.core.settings import ServerSettings
from groupflow.engine.core import ROOM_NOTE
from groupflow.runtime import build_runtime
from tests.conftest import person


@pytest.fixture
def room_server(tmp_path):
    settings = ServerSettings(db_path=str(tmp_path / "gf.db"), admin_key="k", allowed_origins=[], undeclared_audience="room")
    rt = build_runtime(settings, worker_interval_s=0.05)
    bg = serve_in_background(rt.app)
    bg.rt = rt
    yield bg
    bg.stop()
    rt.db.close()


def client(server, member: str, listeners: str | None = None) -> Client:
    headers = {"Authorization": f"Bearer {person(member).token}"}
    if listeners:
        headers["X-GroupFlow-Listeners"] = listeners
    return Client(streamable_http_client(f"{server.base}/mcp", http_client=httpx2.AsyncClient(headers=headers)), mode="legacy")


async def call(c: Client, tool: str, args: dict):
    res = await c.call_tool(tool, args)
    if res.is_error:
        return {"error": json.loads(res.content[0].text)["error"]}
    return res.structured_content


async def test_an_undeclared_device_leaves_the_surprise_out_until_it_is_named(room_server):
    async with client(room_server, "bob", "me") as bob:
        changed = await call(bob, "propose_change", {"item": "cake.order", "value": "Lemon cake from Rosa's", "quote": "make it lemon"})
        assert "error" not in changed, changed
        told = await call(bob, "notify_people", {"plan": "party", "about": ["cake.order"], "audience": ["Alice"]})
        assert "error" not in told, told
    async with client(room_server, "alice") as kitchen:
        summary = await call(kitchen, "get_context", {})
        text = json.dumps(summary)
        assert "Linda's surprise party" not in text and "Lemon cake" not in text
        assert {p["id"] for p in summary["plan_index"]} == {"home", "friday_dinner"}
        assert summary["shared_note"] == ROOM_NOTE and summary["your_conditions"] == []
        named = await call(kitchen, "get_context", {"plan": "Linda's surprise party"})
        assert named["active_plan_id"] == "party" and any("Lemon cake" in u for u in named["updates"])
    async with client(room_server, "alice", "me") as personal:
        summary = await call(personal, "get_context", {})
        assert "party" in {p["id"] for p in summary["plan_index"]} and summary["shared_note"] is None


async def test_unnamed_plan_choices_and_errors_never_read_out_a_hidden_title(room_server):
    async with client(room_server, "alice") as kitchen:
        unsure = await call(kitchen, "review_plan", {})
        assert unsure["error"]["code"] == "AMBIGUOUS_PLAN"
        assert "surprise" not in json.dumps(unsure).lower()
        missing = await call(kitchen, "get_context", {"plan": "the moon base"})
        assert missing["error"]["code"] == "NOT_FOUND" and "surprise" not in missing["error"]["message"].lower()
        # Naming it still works for writes too: the speaker chose to talk about it.
        named = await call(kitchen, "review_plan", {"plan": "Linda's surprise party"})
        assert "error" not in named, named


async def test_a_declared_room_still_narrows_to_what_every_listener_may_see(room_server):
    async with client(room_server, "alice", "group:family") as family_room:
        summary = await call(family_room, "get_context", {})
        assert "party" not in {p["id"] for p in summary["plan_index"]}
        named = await call(family_room, "get_context", {"plan": "Linda's surprise party"})
        assert named["error"]["code"] == "NOT_FOUND"  # Linda is listening: the plan does not exist in this conversation
