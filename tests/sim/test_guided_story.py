"""The guided demo story, step by step, against the engine: every server outcome its captions claim.

The model's side (what Alexa says) varies between runs and is rehearsed with scripts/play_demo.py; what GroupFlow decides
must not vary, so it is pinned here: the draft that breaks a private limit is not sent and names nobody, the fix fits both
limits, the check-in reaches only the person who has not answered, a rumour changes nothing, and after the restaurant's own
notice only the dinner is asked again.
"""
from __future__ import annotations

import asyncio
import json

from groupflow.checkins import CheckIns, RulesReviewer, hours_ahead
from groupflow.core.db import Db
from groupflow.engine import GroupFlow
from groupflow.sim.demo_script import FRIENDS_SCRIPT, SCRIPTS
from groupflow.world import approve_all, seed_world
from tests.conftest import WORLD

KINDS = {"say", "verify", "phones", "skip", "event"}


def test_the_script_is_one_story_in_four_days():
    assert set(SCRIPTS) == {"friends"}
    assert len(FRIENDS_SCRIPT) == 21
    assert {s["kind"] for s in FRIENDS_SCRIPT} <= KINDS
    assert [s["chapter"] for s in FRIENDS_SCRIPT if s.get("chapter")] == ["Monday", "Tuesday", "Wednesday", "Friday"]
    for s in FRIENDS_SCRIPT:
        assert s["note"] and len(s["note"]) < 170
        if s["kind"] == "say":
            assert s["member"] in {"alice", "bob", "chen", "dana"} and s["text"]
    skip = next(s for s in FRIENDS_SCRIPT if s["kind"] == "skip")
    # 48 hours would land after a Friday dinner when the run is recorded on a Thursday; a plan less than a week away is quiet
    # after 24 hours, so 26 still fires.
    assert 24 < skip["hours"] < 48


def test_the_demo_starts_with_the_friends_and_no_plan():
    gf = GroupFlow(Db(":memory:"))
    seed_world(gf, WORLD, {"friday_dinner": "none"})
    assert gf.inspector("friends", None)["plans"] == []
    assert len(gf.db.all("SELECT member_id FROM group_members WHERE group_id = 'friends'")) == 4
    # Other groups keep their seeded plans for the extras.
    assert any(p["id"] == "party" for p in gf.inspector("family", None)["plans"])


def _story():
    gf = GroupFlow(Db(":memory:"))
    seed_world(gf, WORLD, {"friday_dinner": "none"})
    out = {"gf": gf}
    created = gf.create_plan("alice", "evening_out", "Friday dinner", group="friends", start_date="next friday")
    pid = out["pid"] = created.plan.id
    out["draft"] = created
    for who, conds in {"chen": [{"name": "available_from", "value": {"time": "18:15"}}],
                       "dana": [{"name": "diet", "value": {"tags": ["vegetarian"]}}, {"name": "home_by", "value": {"time": "21:30"}}]}.items():
        r = gf.set_my_conditions(who, conds, plan=pid, quote="I need this")
        assert r.needs_verification
        m = gf.identity.get(who)
        for c in gf.human_inbox(m)["conditions"]:
            if c["status"] == "unverified":
                gf.human_verify_condition(m, c["id"], c["verify_digest"])
    gf.reset_rate_limits()
    out["send_draft"] = gf.request_confirmations("alice", plan=pid)
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


def test_monday_a_draft_nobody_has_agreed_to():
    d = story()["draft"]
    assert d.plan.status == "DRAFT" and d.plan.agreed is None
    acts = {a.title: (a.when, a.place) for a in d.plan.working.activities}
    assert acts == {"Meet up": ("18:00-18:15", "Central Hub"), "Dinner": ("18:30-19:30", "Restaurant A"),
                    "Dessert": ("19:45-20:15", "Dessert Shop D"), "Ride home": ("20:15-20:45", "Shared ride home")}
    assert set(d.plan.visible_to) == {"Alice", "Bob", "Chen", "Dana"}


def test_tuesday_the_draft_is_refused_without_naming_anyone():
    r = story()["send_draft"]
    assert r.outcome == "infeasible" and r.waiting_for == []
    assert r.problems == ["Meet up would start at 18:00, before a member's earliest time they can start."]
    assert not any(n in json.dumps(r.model_dump()) for n in ("Chen", "Dana"))


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
