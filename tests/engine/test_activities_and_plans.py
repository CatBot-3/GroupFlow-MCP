"""Editing activities, plans over several days, plans made on the spot, and the demo world itself."""
from groupflow.world import approve_all, load_world, seed_group, wipe_group
from tests.conftest import WORLD, code, plan_view, seeded, task

# ---------------------------------------------------------------- activity edits


def test_explicit_subset_cannot_default_to_group_visibility(gf):
    before = gf.db.scalar('SELECT count(*) FROM plans')
    words = 'Plan a game night, just with Dana and Bob.'
    assert code(lambda: gf.create_plan('chen', 'general_event', 'Game night', group='friends', quote=words)) == 'AUDIENCE_REQUIRED'
    assert gf.db.scalar('SELECT count(*) FROM plans') == before
    created = gf.create_plan('chen', 'general_event', 'Game night', group='friends', audience=['Dana', 'Bob'], quote=words)
    assert set(created.plan.visible_to) == {'Chen', 'Dana', 'Bob'}


def test_explicit_whole_group_and_negated_subset_keep_normal_creation(gf):
    created = gf.create_plan('bob', 'general_event', 'Movie night', group='friends', quote='Not just with Dana: include everyone in the friends group.')
    assert set(created.plan.visible_to) == {'Alice', 'Bob', 'Chen', 'Dana'}


def test_the_organizer_edits_the_draft_directly():
    gf = seeded({"friday_dinner": "draft"})
    r = gf.propose_change("alice", item="dessert", start="20:00", plan="friday_dinner")
    assert r.outcome == "draft_updated" and r.plan_status == "DRAFT"
    assert plan_view(gf, "alice", "friday_dinner").working.activities[2].when == "20:00-20:30"


def test_changing_an_agreed_plan_makes_a_new_version_that_needs_confirmation():
    gf = seeded({"friday_dinner": "committed"})
    r = gf.propose_change("alice", item="dessert", place="Dessert Shop F", plan="friday_dinner")
    assert r.outcome == "candidate_created" and r.plan_status == "PROPOSED"
    assert r.problems, "Dessert Shop F is uptown: the ride home no longer fits, and the server says so"
    v = plan_view(gf, "bob", "friday_dinner")
    assert v.agreed.label == "P1" and v.working.label == "P2"
    assert "Dessert: Dessert Shop D -> Dessert Shop F" in v.working.changes
    assert gf.propose_change("alice", item="dessert", place="Dessert Shop D", plan="friday_dinner").outcome == "candidate_updated"
    again = gf.propose_change("alice", item="meetup", start="17:45", plan="friday_dinner")
    assert again.outcome == "candidate_updated" and again.problems == []
    assert gf.request_confirmations("alice", plan="friday_dinner").carried_forward == 12


def test_someone_else_s_activity_change_is_a_request_to_the_organizer():
    gf = seeded({"friday_dinner": "committed"})
    r = gf.propose_change("chen", item="dinner", start="19:00", plan="friday_dinner", quote="Can we eat at 7?")
    assert r.outcome == "pending_review" and "Alice" in r.message
    assert plan_view(gf, "alice", "friday_dinner").status == "COMMITTED"
    req = gf.context("alice").decisions_for_you[0]
    assert req.what == "Dinner" and "19:00" in req.proposed
    d = gf.decide_change("alice", req.request, "accept")
    assert d.outcome == "accepted" and d.plan_status == "PROPOSED"


def test_dropping_an_agreed_activity_keeps_it_visible_as_dropped():
    gf = seeded({"friday_dinner": "committed"})
    assert gf.propose_change("alice", item="dessert", remove=True, plan="friday_dinner").outcome == "candidate_created"
    acts = plan_view(gf, "dana", "friday_dinner").working.activities
    assert [a.status for a in acts if a.id == "dessert"] == ["dropped"]


def test_places_must_come_from_the_catalog_where_there_is_one(gf):
    assert code(lambda: gf.propose_change("alice", item="dinner", place="Joe's Diner", plan="friday_dinner")) == "NOT_FOUND"


# ---------------------------------------------------------------- several days


def test_a_trip_draft_that_breaks_a_private_condition_is_not_sent_and_the_search_fixes_it(gf):
    r = gf.request_confirmations("dana", plan="cottage_weekend")
    assert r.outcome == "infeasible" and r.problems == ["Day hike at Sat 10:00 overlaps a member's busy time."]
    alts = gf.find_alternatives("dana", plan="cottage_weekend")
    assert alts.alternatives[0].changes == ["Day hike: Sat 10:00-13:00 -> Sat 11:00-14:00"]
    assert gf.request_confirmations("dana", plan="cottage_weekend").outcome == "requested"
    for m in ("dana", "chen", "omar", "priya"):
        approve_all(gf, gf.identity.get(m), "cottage_weekend")
    c = gf.commit_plan("dana", plan="cottage_weekend")
    assert c.outcome == "committed" and any("Pine Lake Cottage" in b for b in c.bookings)
    assert task(gf, "dana", "Book the minivan").blocked_by == []


def test_a_meeting_moves_within_the_week_when_someone_becomes_busy(gf):
    gf.set_my_conditions("marcus", [{"kind": "busy", "value": {"day": "Tue", "from": "09:00", "to": "12:00"}}], plan="design_review")
    marcus = gf.identity.get("marcus")
    c = next(x for x in gf.human_inbox(marcus)["conditions"] if x["status"] == "unverified")
    gf.human_verify_condition(marcus, c["id"], c["verify_digest"])
    assert plan_view(gf, "priya", "design_review").status == "DEGRADED"
    alts = gf.find_alternatives("priya", plan="design_review")
    assert alts.alternatives[0].changes == ["Team meeting: Tue 10:00-11:00 -> Tue 14:00-15:00"]
    e = gf.explain("jun", plan="design_review", option="Birch room")
    assert "room needs" in e.options[0].reason and "Marcus" not in e.options[0].reason


def test_a_verified_closure_on_one_day_moves_only_that_session(gf):
    gf.inject_observation("study", "place_unavailable", {"place_id": "library_room_3", "from": "17:00", "to": "21:00",
                                                         "date": plan_view(gf, "chen", "cs341_prep").dates[:0] or None} | {}, "demo", "x")
    # No date given: the closure applies to every day, so both sessions must move.
    alts = gf.find_alternatives("chen", plan="cs341_prep")
    assert alts.alternatives and all("Library room 5" in c or "Engineering lounge" in c for c in alts.alternatives[0].changes)


# ---------------------------------------------------------------- plans made on the spot


def test_an_ad_hoc_plan_is_visible_only_to_its_audience(gf):
    r = gf.create_plan("chen", "general_event", "Sunday picnic", group="friends", start_date="Sunday", audience=["Dana", "Bob"])
    assert r.plan.visible_to == ["Bob", "Chen", "Dana"] and "hidden from Alice" in r.message
    assert gf.propose_change("chen", plan="Sunday picnic", new_type="activity", title="Picnic", start="12:00", end="14:00",
                             place="High Park").outcome == "draft_updated"
    assert "Sunday picnic" not in [p.title for p in gf.context("alice").plans]
    assert gf.request_confirmations("chen", plan="Sunday picnic").waiting_for == ["Bob", "Chen", "Dana"]


def test_a_new_surprise_is_hidden_from_its_guest(gf):
    gf.create_plan("chen", "surprise_party", "Dana's birthday", group="friends", start_date="2026-11-20", hidden_from=["Dana"])
    assert "Dana's birthday" not in gf.context("dana").model_dump_json()
    plan = gf.context("alice").plans
    assert "Dana's birthday" in [p.title for p in plan]


def test_the_group_must_be_clear_when_someone_is_in_several(gf):
    assert code(lambda: gf.create_plan("alice", "general_event", "Movie night")) == "AMBIGUOUS_GROUP"
    assert gf.create_plan("alice", "general_event", "Movie night", group="Friday friends").outcome == "created"
    assert code(lambda: gf.create_plan("alice", "no_such_template", "X")) == "INVALID_ARGUMENT"


def test_a_private_plan_is_seen_by_its_maker_alone(gf):
    r = gf.create_plan("bob", "general_event", "Gift ideas", private=True)
    assert r.outcome == "created" and r.plan.visible_to == ["Bob"] and "Only you can see it" in r.message
    added = gf.propose_change("bob", plan=r.plan.id, new_type="detail", title="Gift idea", value="Pottery class")
    assert added.outcome in ("created", "confirmed")
    for other in ("alice", "charlie", "linda", "chen", "dana"):
        assert "Pottery" not in gf.context(other).model_dump_json()
    assert "Pottery class" in gf.context("bob").model_dump_json()


def test_an_unknown_place_points_at_place_details_in_other_plans(gf):
    from groupflow.core.errors import DomainError

    try:
        gf.propose_change("bob", plan="friday_dinner", item="dinner", place="Luigi's", certainty="tentative")
    except DomainError as err:
        assert err.code == "NOT_FOUND" and "party.venue" in err.message
    else:
        raise AssertionError("an unknown place must be refused")
    # Chen cannot see Linda's party, so his hint does not mention it (the potluck he is in has a venue of its own).
    try:
        gf.propose_change("chen", plan="friday_dinner", item="dinner", place="Luigi's")
    except DomainError as err:
        assert "party.venue" not in err.message and "surprise" not in err.message.lower() and "Rosa" not in err.message
        assert "event.venue" in err.message


def test_a_guessed_plan_dot_item_id_reaches_the_real_item_and_never_makes_a_copy(gf):
    # The model saw party.date and party.venue and guessed party.cake for Bob's Cake (id cake.order).
    r = gf.propose_change("alice", item="party.cake", plan="party", value="Lemon cake")
    assert r.outcome == "pending_review" and r.item == "cake.order", "Bob owns the cake, so Alice's change waits for him"
    assert gf.db.scalar("SELECT COUNT(*) FROM items WHERE label LIKE '%cake%' AND shape = 'detail'") == 1
    assert code(lambda: gf.propose_change("alice", item="party.balloons", plan="party", value="red")) == "NOT_FOUND"
    assert gf.propose_change("alice", item="dress code", plan="party", value="casual", new_type="detail").outcome == "confirmed"


def test_an_answered_ask_is_no_longer_read_out_as_news():
    from groupflow.world import approve_all

    gf = seeded({"friday_dinner": "proposed"})
    gf.request_confirmations("alice", plan="friday_dinner")
    assert any("asked you to confirm" in u for u in gf.context("dana", mark_read=False).updates)
    approve_all(gf, gf.identity.get("dana"), "friday_dinner")
    assert not any("asked you to confirm" in u for u in gf.context("dana", mark_read=False).updates)
    assert any("asked you to confirm" in u for u in gf.context("bob", mark_read=False).updates), "Bob has not answered yet"


def test_several_plans_make_the_server_ask_which_one(gf):
    assert code(lambda: gf.set_my_conditions("chen", [{"kind": "busy", "value": {"from": "10:00", "to": "11:00"}}])) == "AMBIGUOUS_PLAN"


# ---------------------------------------------------------------- the demo world


def test_every_stage_of_the_dinner_can_be_seeded():
    for stage, status in [("draft", "DRAFT"), ("proposed", "PROPOSED"), ("awaiting", "AWAITING_CONFIRMATIONS"), ("committed", "COMMITTED"),
                          ("degraded", "DEGRADED")]:
        gf = seeded({"friday_dinner": stage})
        assert gf._plan_row("friday_dinner")["status"] == status, stage


def test_resetting_one_group_leaves_the_others_alone(gf):
    gf.propose_change("alice", item="party.date", value="Sunday, Oct 25, 6:00 pm")
    gf.propose_change("dana", item="trip.meeting_point", value="Chen's place")
    wipe_group(gf.db, "cottage")
    seed_group(gf, WORLD, "cottage", {})
    assert gf.context("dana", plan="cottage_weekend", mark_read=False).plans[0].details[0].value == "Dana's place, 4:30 pm"
    party = gf.context("alice", plan="party", mark_read=False).plans[0]
    assert party.details[0].value == "Sunday, Oct 25, 6:00 pm"


def test_the_world_file_is_consistent():
    w = load_world()
    ids = {p.id for p in w.people}
    for g in w.groups:
        assert {m["id"] for m in g["members"]} <= ids
    assert {p["group"] for p in w.plans} <= {g["id"] for g in w.groups}
    assert len({p.token for p in w.people}) == len(w.people)
