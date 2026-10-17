"""Over real MCP: a reminder set, listed in the briefing and cancelled; a vague time answered through the server's own question; what a
plan waits on, and check-ins that only the organizer can change."""
from __future__ import annotations

from tests.mcp.test_mcp import call, client_for, server  # noqa: F401 - the fixture is reused


async def test_reminders_and_progress_over_mcp(server):  # noqa: F811
    async with client_for(server, "alice") as c:
        assert {"set_reminder", "review_plan"} <= {t.name for t in (await c.list_tools()).tools}
        vague = await call(c, "set_reminder", {"when": "later", "about": "cake.order"})
        question = vague["error"]["details"]["followup"]
        assert vague["error"]["code"] == "WHEN_UNCLEAR" and "Tomorrow at 9am" in question["options"]
        picked = await call(c, "answer_followup", {"choice": "tomorrow at 9am", "followup": question["id"]})
        assert picked["action"]["ok"] and picked["action"]["result"]["outcome"] == "scheduled"
        reminder = picked["action"]["result"]
        briefing = await call(c, "get_context", {"detail": "summary"})
        assert [r["id"] for r in briefing["your_reminders"]] == [reminder["id"]] and briefing["your_reminders"][0]["about"] == ["Cake"]
        assert (await call(c, "set_reminder", {"cancel": reminder["id"]}))["outcome"] == "cancelled"
        assert (await call(c, "get_context", {"detail": "summary"}))["your_reminders"] == []
        review = await call(c, "review_plan", {"plan": "friday_dinner"})
        assert review["plan"] == "Friday dinner" and review["message"]
    async with client_for(server, "bob") as c:
        refused = await call(c, "review_plan", {"plan": "friday_dinner", "check_ins": "off"})
        assert refused["error"]["code"] == "FORBIDDEN"
        mine = await call(c, "set_reminder", {"when": "in 2 hours", "plan": "home", "note": "porch light"})
        assert mine["to"] == "you"
    async with client_for(server, "linda") as c:
        # Linda cannot see the surprise party, so she cannot set reminders on it or find it by name.
        hidden = await call(c, "set_reminder", {"when": "tomorrow at 9", "plan": "party", "note": "x"})
        assert hidden["error"]["code"] == "NOT_FOUND"
