"""What the voice-first story relies on: joining a group by voice waits for the person's own yes, an evening plan made through an
assistant asks about its optional stops instead of assuming them, and a confirmed limit that breaks a draft is reported to the
organizer without saying whose it is."""
from __future__ import annotations

import pytest

from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.engine import ASSISTANT, GroupFlow
from groupflow.world import seed_world
from tests.conftest import WORLD


@pytest.fixture
def gf():
    engine = GroupFlow(Db(":memory:"))
    seed_world(engine, WORLD, {"friday_dinner": "none", "group:friends": "none"})
    return engine


def members(gf, group):
    return {r["member_id"] for r in gf.db.all("SELECT member_id FROM group_members WHERE group_id = ? AND status = 'active'", group)}


def test_joining_by_voice_waits_for_the_persons_own_yes(gf):
    gf.create_group("alice", "Friends")
    code = gf.invite_to_group("alice", "friends")["code"]
    asked = gf.request_join("bob", code)
    assert asked["outcome"] == "awaiting_confirmation" and asked["invited_by"] == "Alice" and asked["group"]["name"] == "Friends"
    assert members(gf, "friends") == {"alice"}, "asking by voice adds nobody"
    assert gf.request_join("bob", code)["outcome"] == "awaiting_confirmation"
    bob = gf.identity.get("bob")
    [waiting] = gf.human_inbox(bob)["joins"]
    assert waiting["group"]["name"] == "Friends" and waiting["invited_by"] == "Alice"
    assert gf.human_decide_join(bob, waiting["id"], True)["outcome"] == "joined"
    assert members(gf, "friends") == {"alice", "bob"} and gf.human_inbox(bob)["joins"] == []
    assert any("Bob joined Friends" in n["text"] for n in gf.human_inbox(gf.identity.get("alice"))["notifications"])
    # Someone else cannot answer Bob's join, a no joins nobody, and a wrong code is refused.
    gf.request_join("chen", code)
    chen = gf.identity.get("chen")
    [chens] = gf.human_inbox(chen)["joins"]
    with pytest.raises(DomainError):
        gf.human_decide_join(gf.identity.get("dana"), chens["id"], True)
    assert gf.human_decide_join(chen, chens["id"], False)["outcome"] == "declined" and "chen" not in members(gf, "friends")
    with pytest.raises(DomainError):
        gf.request_join("dana", "ZZZZ-ZZZZ")


def test_an_assistant_asks_about_dessert_instead_of_adding_it(gf):
    gf.create_group("alice", "Friends")
    token = ASSISTANT.set(True)
    try:
        made = gf.create_plan("alice", "evening_out", "Friday dinner", group="friends", start_date="Friday")
        assert [a.title for a in made.plan.working.activities] == ["Meet up", "Dinner", "Ride home"]
        assert made.suggested_followup["question"] == "Do you want dessert after dinner, at Dessert Shop D?"
        added = gf.answer_followup("alice", "yes please")
        assert added["action"]["result"]["outcome"] == "added"
        titles = [a["title"] for a in gf.inspector("friends", None)["plans"][0]["working"]["activities"]]
        assert titles == ["Meet up", "Dinner", "Dessert", "Ride home"]
        other = gf.create_plan("alice", "evening_out", "Saturday drinks", group="friends", start_date="Saturday")
        assert gf.answer_followup("alice", "no", followup=other.suggested_followup["id"])["choice"] == "no"
        assert len(gf._latest_snapshot(gf._plan_row(other.plan.id)).activities) == 3
    finally:
        ASSISTANT.reset(token)
    # Outside an assistant conversation (seeding, tests, the lab) the template's starter is complete.
    assert len(gf.create_plan("alice", "evening_out", "Brunch", group="friends", start_date="Sunday").plan.working.activities) == 4


def test_the_organizer_hears_when_a_confirmed_limit_breaks_the_draft_without_whose(gf):
    seed = GroupFlow(Db(":memory:"))
    seed_world(seed, WORLD, {"friday_dinner": "none"})
    pid = seed.create_plan("alice", "evening_out", "Friday dinner", group="friends", start_date="Friday").plan.id
    seed.set_my_conditions("chen", [{"name": "available_from", "value": {"time": "18:15"}}], plan=pid, quote="I can't get there before quarter past six")
    chen = seed.identity.get("chen")
    for c in seed.human_inbox(chen)["conditions"]:
        seed.human_verify_condition(chen, c["id"], c["verify_digest"])
    told = [n["text"] for n in seed.human_inbox(seed.identity.get("alice"))["notifications"] if n["kind"] == "conflict"]
    assert told == ["Friday dinner: the draft no longer fits everyone. Meet up would start at 18:00, before a member's earliest time they can "
                    "start. Want me to look for the smallest change that works for everyone?"]
    assert "Chen" not in told[0]
    assert not any(n["kind"] == "conflict" for who in ("bob", "chen", "dana") for n in seed.human_inbox(seed.identity.get(who))["notifications"])
    # A second limit that the draft also breaks in the same way does not repeat the message.
    seed.set_my_conditions("dana", [{"name": "available_from", "value": {"time": "18:10"}}], plan=pid, quote="after six ten")
    dana = seed.identity.get("dana")
    for c in seed.human_inbox(dana)["conditions"]:
        seed.human_verify_condition(dana, c["id"], c["verify_digest"])
    assert len([n for n in seed.human_inbox(seed.identity.get("alice"))["notifications"] if n["kind"] == "conflict"]) == 1
