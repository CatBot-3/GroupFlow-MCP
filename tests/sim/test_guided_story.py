"""The Friday dinner story from the demo video, step by step, against the engine: every server outcome the video shows.

The story's script is private (private/demo_story.py) and the model's side (what the assistant says) varies between runs;
what GroupFlow decides must not vary, so it is pinned here: the draft that breaks a private limit is not sent and names nobody, the fix fits both
limits, the check-in reaches only the person who has not answered, a rumour changes nothing, and after the restaurant's own
notice only the dinner is asked again. It opens with the group being made: Alice's Alexa creates it and an invite, each friend
asks their own Alexa to join with the code and is added only after their own yes on their screen, Alice's Alexa asks before
adding dessert, and the draft reaches the others when Alice asks Alexa to tell everyone. When a confirmed limit breaks the
draft, Alice is told without being told whose it is.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from groupflow.checkins import CheckIns, RulesReviewer, hours_ahead
from groupflow.core.db import Db
from groupflow.engine import ASSISTANT, GroupFlow
from groupflow.world import approve_all, seed_world
from tests.conftest import WORLD

NO_GROUP = {"friday_dinner": "none", "group:friends": "none"}


def test_the_demo_starts_before_the_friends_group_exists():
    gf = GroupFlow(Db(":memory:"))
    seed_world(gf, WORLD, NO_GROUP)
    assert not gf.db.all("SELECT * FROM groups WHERE id = 'friends'")
    assert {p["id"] for p in gf.db.all("SELECT id FROM members")} >= {"alice", "bob", "chen", "dana"}
    # Other groups keep their seeded plans for the extras.
    assert any(p["id"] == "party" for p in gf.inspector("family", None)["plans"])


def story_friday() -> str:
    """A Friday at least three days away, so the 26 quiet hours before Wednesday's check-in never run past the dinner, whatever
    day and hour the tests run (on a Thursday evening "next friday" is tomorrow, and 26 hours later the evening is over)."""
    today = datetime.now(ZoneInfo("America/Toronto")).date()
    days = (4 - today.weekday()) % 7
    return (today + timedelta(days=days + (7 if days < 3 else 0))).isoformat()


def _story():
    gf = GroupFlow(Db(":memory:"))
    seed_world(gf, WORLD, NO_GROUP)
    out = {"gf": gf}
    out["group"] = gf.create_group("alice", "Friends")
    out["invite"] = gf.invite_to_group("alice", "friends")
    out["asked"] = [gf.request_join(who, out["invite"]["code"]) for who in ("bob", "chen", "dana")]
    out["members_before_joining"] = len(gf.db.all("SELECT member_id FROM group_members WHERE group_id = 'friends'"))
    out["joins"] = []
    for who in ("bob", "chen", "dana"):
        m = gf.identity.get(who)
        [waiting] = gf.human_inbox(m)["joins"]
        out["joins"].append(gf.human_decide_join(m, waiting["id"], True))
    out["alice_after_joins"] = gf.context("alice", mark_read=False).updates
    gf.reset_rate_limits()
    token = ASSISTANT.set(True)  # Alice's Alexa makes the plan, so the optional stop is offered instead of assumed
    try:
        created = gf.create_plan("alice", "evening_out", "Friday dinner", group="friends", start_date=story_friday())
        pid = out["pid"] = created.plan.id
        out["draft"] = created
        out["dessert"] = gf.answer_followup("alice", "Yes, add dessert.")
    finally:
        ASSISTANT.reset(token)
    out["with_dessert"] = gf.inspector("friends", None)["plans"][0]["working"]["activities"]
    out["told"] = gf.notify_people("alice", plan=pid, note="What works for you?")
    out["heard"] = {who: [n for n in gf.human_inbox(gf.identity.get(who))["notifications"] if n["kind"] == "notice"] for who in ("bob", "chen", "dana")}
    for who, conds in {"chen": [{"name": "available_from", "value": {"time": "18:15"}}],
                       "dana": [{"name": "diet", "value": {"tags": ["vegetarian"]}}, {"name": "home_by", "value": {"time": "21:30"}}]}.items():
        r = gf.set_my_conditions(who, conds, plan=pid, quote="I need this")
        assert r.needs_verification
        m = gf.identity.get(who)
        for c in gf.human_inbox(m)["conditions"]:
            if c["status"] == "unverified":
                gf.human_verify_condition(m, c["id"], c["verify_digest"])
    out["alice_told"] = [n for n in gf.human_inbox(gf.identity.get("alice"))["notifications"] if n["kind"] == "conflict"]
    out["others_told"] = [n for who in ("bob", "chen", "dana") for n in gf.human_inbox(gf.identity.get(who))["notifications"] if n["kind"] == "conflict"]
    gf.reset_rate_limits()
    out["fix"] = gf.find_alternatives("alice", plan=pid)
    gf.reset_rate_limits()
    out["send"] = gf.request_confirmations("alice", plan=pid)
    for who in ("alice", "bob", "chen"):
        approve_all(gf, gf.identity.get(who), pid)
    out["checkin"] = asyncio.run(CheckIns(gf, RulesReviewer()).run_pass(as_of=hours_ahead(gf, 26), quiet_hours=False, group_id="friends"))
    out["dana_updates"] = gf.context("dana", mark_read=False).updates
    approve_all(gf, gf.identity.get("dana"), pid)
    gf.reset_rate_limits()
    out["commit"] = gf.commit_plan("alice", plan=pid)
    out["rumour"] = gf.report_observation("bob", "place_unavailable", place="Restaurant C", plan=pid, day="Friday")
    out["status_after_rumour"] = gf._plan_row(pid)["status"]
    out["alice_after_rumour"] = gf.context("alice", mark_read=False).updates
    gf.inject_observation("friends", "place_unavailable", {"place_id": "restaurant_c", "from": "18:00", "to": "21:00",
                                                           "date": gf._plan_row(pid)["start_date"]}, source="demo_console", source_event_id="story-1")
    out["status_after_notice"] = gf._plan_row(pid)["status"]
    out["chen_after_notice"] = gf.context("chen", mark_read=False).updates
    gf.reset_rate_limits()
    out["repair"] = gf.find_alternatives("alice", plan=pid)
    gf.reset_rate_limits()
    out["resend"] = gf.request_confirmations("alice", plan=pid)
    for who in ("alice", "bob", "chen", "dana"):
        approve_all(gf, gf.identity.get(who), pid)
    gf.reset_rate_limits()
    out["recommit"] = gf.commit_plan("alice", plan=pid)
    return out


STORY = None


def story():
    global STORY
    if STORY is None:
        STORY = _story()
    return STORY


def test_monday_the_group_is_made_by_voice_and_nobody_is_added_for_them():
    s = story()
    assert s["group"]["outcome"] == "created" and s["group"]["group"]["id"] == "friends"
    assert [a["outcome"] for a in s["asked"]] == ["awaiting_confirmation"] * 3
    assert all(a["invited_by"] == "Alice" and a["group"]["name"] == "Friends" for a in s["asked"])
    assert s["members_before_joining"] == 1, "asking Alexa to join adds nobody; each person's own yes does"
    assert [j["outcome"] for j in s["joins"]] == ["joined"] * 3
    assert sum("joined Friends" in u for u in s["alice_after_joins"]) == 3


def test_monday_everyone_hears_the_draft_when_alice_asks_alexa_to_tell_them():
    s = story()
    for who, notes in s["heard"].items():
        assert len(notes) == 1, who
        assert notes[0]["text"].startswith("Alice asked Alexa to let you know about Friday dinner") and "Restaurant A" in notes[0]["text"]


def test_monday_alexa_asks_about_dessert_and_alice_chooses_it():
    s = story()
    d = s["draft"]
    assert d.plan.status == "DRAFT" and d.plan.agreed is None
    assert [a.title for a in d.plan.working.activities] == ["Meet up", "Dinner", "Ride home"]
    assert d.suggested_followup["question"] == "Do you want dessert after dinner, at Dessert Shop D?"
    assert s["dessert"]["action"]["result"]["outcome"] == "added"
    acts = {a["title"]: (a["when"], a["place"]) for a in s["with_dessert"]}
    assert acts == {"Meet up": ("18:00-18:15", "Central Hub"), "Dinner": ("18:30-19:30", "Restaurant A"),
                    "Dessert": ("19:45-20:15", "Dessert Shop D"), "Ride home": ("20:15-20:45", "Shared ride home")}
    assert set(d.plan.visible_to) == {"Alice", "Bob", "Chen", "Dana"}


def test_tuesday_alice_hears_the_draft_no_longer_fits_without_whose_limit():
    s = story()
    [told] = s["alice_told"]
    assert "the draft no longer fits everyone" in told["text"]
    assert "Meet up would start at 18:00, before a member's earliest time they can start." in told["text"]
    assert "smallest change" in told["text"], "it offers the fix Alice then asks for"
    assert not any(n in json.dumps(told) for n in ("Chen", "Dana"))
    assert s["others_told"] == [], "only the organizer can change the draft, so only she is told"


def test_tuesday_the_fix_fits_both_limits():
    fix = story()["fix"]
    assert fix.proof.explored == fix.proof.domain_size == 256
    changes = fix.alternatives[0].changes
    assert "Meet up: 18:00-18:15 -> 18:15-18:30" in changes
    assert "Dinner: Restaurant A -> Restaurant C" in changes and "Dinner: 18:30-19:30 -> 19:00-20:00" in changes
    assert "Ride home: 20:15-20:45 -> 20:45-21:15" in changes  # home by 9:15, inside 9:30
    send = story()["send"]
    assert send.outcome == "requested" and send.version == "P1" and set(send.waiting_for) == {"Alice", "Bob", "Chen", "Dana"}


def test_wednesday_only_dana_is_checked_in_with_and_alice_is_told():
    rounds = story()["checkin"]
    sent = {s["to"]: s["text"] for r in rounds for s in r["sent"]}
    assert set(sent) == {"dana", "alice"}
    assert "a no helps just as much as a yes" in sent["dana"]
    assert "from Dana" in sent["alice"] and "I reminded Dana" in sent["alice"]
    assert any(u.startswith("Check-in on Friday dinner") for u in story()["dana_updates"])


def test_wednesday_locked_in_with_a_simulated_booking():
    c = story()["commit"]
    assert c.outcome == "committed" and "simulated" in c.message


def test_friday_a_rumour_changes_nothing_and_reaches_alice():
    s = story()
    assert s["rumour"].outcome == "recorded" and not s["rumour"].verified
    assert s["status_after_rumour"] == "COMMITTED"
    assert any("Bob reported that Restaurant C is unavailable (not verified)" in u for u in s["alice_after_rumour"])


def test_friday_the_restaurants_notice_breaks_the_plan_and_everyone_hears():
    s = story()
    assert s["status_after_notice"] == "DEGRADED"
    assert any("Restaurant C became unavailable" in u for u in s["chen_after_notice"])


def test_friday_only_the_dinner_is_asked_again():
    s = story()
    assert s["repair"].alternatives[0].changes == ["Dinner: Restaurant C -> Restaurant E", "Dinner: cost CAD 27.00 -> CAD 26.00 each"]
    assert set(s["repair"].alternatives[0].unchanged) >= {"Meet up", "Dessert", "Ride home"}
    assert s["resend"].carried_forward == 12 and s["resend"].version == "P2"
    rows = s["gf"].inspector("friends", None)["plans"][0]
    assert rows["status"] == "COMMITTED" and s["recommit"].outcome == "committed"
