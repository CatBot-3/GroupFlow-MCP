"""Over real MCP: a vote started, answered and decided; an RSVP with a guest and a claimed dish; an expense another person confirms. The
speaker is fixed by the connection, so nobody can vote, answer or confirm a payment for someone else."""
from __future__ import annotations

from tests.mcp.test_mcp import call, client_for, server  # noqa: F401 - the fixture is reused


async def test_deciding_together_over_mcp(server):  # noqa: F811
    async with client_for(server, "alice") as c:
        assert {"poll", "rsvp", "bring_list", "costs"} <= {t.name for t in (await c.list_tools()).tools}
        started = await call(c, "poll", {"action": "start", "plan": "party", "question": "Which music?", "options": ["Jazz", "Disco"]})
        assert started["outcome"] == "started"
        refused = await call(c, "poll", {"action": "start", "plan": "friday_dinner", "item": "Dinner", "question": "Where?", "options": ["A", "B"]})
        assert refused["error"]["code"] in ("CONSENT_REQUIRED", "NOT_FOUND")
        assert (await call(c, "bring_list", {"action": "add", "plan": "party", "things": ["ice", "chips"]}))["outcome"] == "updated"
        assert (await call(c, "bring_list", {"action": "bring", "plan": "cottage_weekend", "things": "sunscreen"}))["error"]["code"] in (
            "INVALID_ARGUMENT", "NOT_FOUND")
        assert (await call(c, "costs", {"action": "add", "plan": "party", "amount": 45, "what": "pizza", "paid_by": "Bob"}))["outcome"] == "pending"
    async with client_for(server, "bob") as c:
        todos = (await call(c, "get_context", {"detail": "summary"}))["your_todos"]
        assert {"vote", "confirm_cost"} <= {t["kind"] for t in todos}
        voted = await call(c, "poll", {"action": "vote", "choice": "disco"})
        assert voted["poll"]["your_vote"] == "Disco"
        assert (await call(c, "poll", {"action": "decide", "question": "music", "choice": "disco"}))["error"]["code"] == "FORBIDDEN"
        assert (await call(c, "rsvp", {"answer": "yes", "plan": "party", "plus_ones": 1}))["rsvp"]["headcount"] == 2
        listed = (await call(c, "bring_list", {"action": "bring", "plan": "party", "things": "chips"}))["bring_list"]
        assert next(b for b in listed if b["thing"] == "chips")["bringing"] == "you"
        assert (await call(c, "costs", {"action": "confirm", "plan": "party"}))["outcome"] == "confirmed"
    async with client_for(server, "alice") as c:
        decided = await call(c, "poll", {"action": "decide", "question": "music", "choice": "disco"})
        assert decided["outcome"] == "decided"
        party = (await call(c, "get_context", {"plan": "party"}))["plans"][0]
        assert party["rsvp"]["coming"] == ["Bob +1"] and party["costs"]["your_balance"].startswith("you are owed")
    async with client_for(server, "linda") as c:
        # The party is kept from Linda: none of it can be found, voted on or paid into from her connection.
        assert (await call(c, "rsvp", {"answer": "yes", "plan": "party"}))["error"]["code"] == "NOT_FOUND"
        assert (await call(c, "costs", {"action": "show", "plan": "party"}))["error"]["code"] == "NOT_FOUND"


async def test_the_own_page_votes_answers_and_confirms_as_the_person(server):  # noqa: F811
    from tests.mcp.test_mcp import Phone

    async with client_for(server, "alice") as c:
        await call(c, "poll", {"action": "start", "plan": "party", "question": "Which music?", "options": ["Jazz", "Disco"]})
        await call(c, "costs", {"action": "add", "plan": "party", "amount": 30, "what": "streamers", "paid_by": "Charlie"})
    charlie = await Phone(server, "charlie").login()
    try:
        party = next(p for p in (await charlie.inbox())["together"] if p["plan_id"] == "party")
        vote = next(v for v in party["polls"] if v["question"] == "Which music?")
        assert vote["can_vote"] and not vote["can_decide"] and party["to_confirm"]
        r = await charlie.http.post(f"{charlie.base}/human/polls/vote", headers=charlie.headers(True), json={"poll": vote["id"], "choice": "Jazz"})
        assert r.status_code == 200 and r.json()["poll"]["your_vote"] == "Jazz"
        # Deciding is Alice's; the page refuses it for Charlie exactly as Alexa would.
        r = await charlie.http.post(f"{charlie.base}/human/polls/vote", headers=charlie.headers(True),
                                    json={"poll": vote["id"], "choice": "Jazz", "decide": True})
        assert r.status_code == 403
        r = await charlie.http.post(f"{charlie.base}/human/rsvp", headers=charlie.headers(True), json={"plan": "party", "answer": "maybe"})
        assert r.json()["rsvp"]["maybe"] == ["you"]
        r = await charlie.http.post(f"{charlie.base}/human/bring", headers=charlie.headers(True),
                                    json={"plan": "party", "action": "bring", "things": ["streamers"]})
        assert r.json()["outcome"] == "updated"
        entry = party["to_confirm"][0]["id"]
        r = await charlie.http.post(f"{charlie.base}/human/costs/confirm", headers=charlie.headers(True), json={"plan": "party", "id": entry, "answer": "yes"})
        assert r.json()["outcome"] == "confirmed"
        # A write without the page's CSRF token is refused.
        r = await charlie.http.post(f"{charlie.base}/human/rsvp", headers=charlie.headers(), json={"plan": "party", "answer": "yes"})
        assert r.status_code == 403
    finally:
        await charlie.http.aclose()
