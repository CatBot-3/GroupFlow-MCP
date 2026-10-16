"""Details under the owner policy (the surprise party template), and what Linda can and cannot see."""
import json
import re

from groupflow.core.identity import Identity
from tests.conftest import code, detail, item_id, task

SUNDAY = "Sunday, Oct 25, 6:00 pm"
SATURDAY = "Saturday, Oct 24, 6:00 pm"
SECRETS = ["party", "rosa", "cake", "invitation", "surprise", "lucia", "planners", "decorate"]


def linda_knows(e) -> str:
    """Everything Linda could learn, flattened to one string."""
    searched = e.search("linda", "party surprise cake venue rosa").model_dump()
    searched.pop("query")  # a search echoes Linda's own words back
    parts = [e.context("linda", mark_read=False).model_dump_json(), e.explain("linda").model_dump_json(), json.dumps(searched),
             e.history("linda", plan="home").model_dump_json()]
    return "\n".join(parts).lower()


def test_owner_committed_value_is_confirmed(gf):
    r = gf.propose_change("alice", item="party.date", value=SUNDAY)
    assert r.outcome == "confirmed"
    assert detail(gf, "alice", "party.date").value == SUNDAY
    assert "was \"Saturday" in r.message


def test_non_owner_committed_value_goes_to_the_owner(gf):
    r = gf.propose_change("charlie", item="party.date", value=SUNDAY, quote="Rosa double-booked")
    assert r.outcome == "pending_review"
    assert "Alice owns" in r.message
    d = detail(gf, "bob", "party.date")
    assert d.value == SATURDAY and d.status == "disputed"
    assert d.pending[0].said == "Rosa double-booked" and d.pending[0].firm
    assert [x.what for x in gf.context("alice").decisions_for_you] == ["Party date & time"]


def test_tentative_never_changes_the_plan_even_from_the_owner(gf):
    assert gf.propose_change("bob", item="cake.order", value="Ice cream cake", certainty="tentative").outcome == "tentative"
    d = detail(gf, "bob", "cake.order")
    assert d.value == "Chocolate cake from Lucia's Bakery" and d.status == "has_suggestions"
    assert gf.propose_change("alice", item="guests.count", value="25 guests", certainty="tentative").outcome == "tentative"
    assert detail(gf, "alice", "guests.count").value == "18 guests"


def test_tentative_does_not_block_but_a_firm_dispute_does(gf):
    gf.propose_change("alice", item="guests.count", value="25 guests", certainty="tentative")
    assert task(gf, "bob", "Order the cake").blocked_by == []
    gf.propose_change("charlie", item="party.date", value=SUNDAY)
    invites = task(gf, "charlie", "Send invitations")
    assert invites.blocked_by and "waiting for Alice" in invites.blocked_by[0]


def test_repeats_are_support_even_reworded(gf):
    assert gf.propose_change("bob", item="party.date", value="saturday the 24th at 6").outcome == "corroborated"
    assert gf.propose_change("alice", item="guests.count", value="eighteen people").outcome == "corroborated"


def test_the_same_pending_proposal_is_not_duplicated(gf):
    gf.propose_change("charlie", item="party.date", value=SUNDAY)
    assert gf.propose_change("bob", item="party.date", value="Sunday the 25th at 6").outcome == "already_pending"
    assert len(gf.context("alice").decisions_for_you) == 1


def test_accept_confirms_reopens_flags_and_notifies(gf):
    p = gf.propose_change("charlie", item="party.date", value=SUNDAY)
    r = gf.decide_change("alice", p.request.request, "accept")
    assert r.outcome == "accepted" and SUNDAY in r.message
    assert sorted(r.impacts.reopened) == ["Book Rosa's back room", "Order the cake"]
    assert sorted(r.impacts.flagged) == ["Drive Linda to the party", "Send invitations", "Weekend family plan"]
    charlie = gf.context("charlie")
    assert any(re.search(r"Alice confirmed your change", u) for u in charlie.updates)
    assert gf.context("charlie").updates == [], "updates are delivered once"


def test_reject_keeps_the_plan_and_tells_the_requester(gf):
    p = gf.propose_change("charlie", item="party.date", value=SUNDAY)
    assert gf.decide_change("alice", p.request.request, "reject").outcome == "rejected"
    d = detail(gf, "alice", "party.date")
    assert d.value == SATURDAY and d.status == "confirmed"
    assert any("declined" in u for u in gf.context("charlie").updates)


def test_amend_confirms_a_different_value(gf):
    p = gf.propose_change("charlie", item="party.date", value=SUNDAY)
    assert gf.decide_change("alice", p.request.request, "amend", amended_value="Sunday, Oct 25, 5:00 pm").outcome == "amended"
    assert detail(gf, "alice", "party.date").value == "Sunday, Oct 25, 5:00 pm"


def test_only_the_owner_or_an_organizer_decides(gf):
    p = gf.propose_change("alice", item="cake.order", value="Lemon cake")
    assert p.outcome == "pending_review", "Alice does not own the cake"
    assert code(lambda: gf.decide_change("charlie", p.request.request, "accept")) == "FORBIDDEN"
    # Alice organizes the party plan, so she may decide even though Bob owns the cake.
    assert gf.decide_change("alice", p.request.request, "accept").outcome == "accepted"


def test_a_decided_request_cannot_be_decided_again(gf):
    p = gf.propose_change("charlie", item="party.date", value=SUNDAY)
    gf.decide_change("alice", p.request.request, "accept")
    assert code(lambda: gf.decide_change("alice", p.request.request, "reject")) == "REQUEST_CLOSED"


def test_competing_proposals_close_each_other(gf):
    a = gf.propose_change("charlie", item="party.date", value=SUNDAY)
    b = gf.propose_change("bob", item="party.date", value="Friday, Oct 23, 7:00 pm")
    assert len(gf.context("alice").decisions_for_you) == 2
    gf.decide_change("alice", a.request.request, "accept")
    assert code(lambda: gf.decide_change("alice", b.request.request, "accept")) == "REQUEST_CLOSED"
    assert any('suggestion "Friday, Oct 23, 7:00 pm" was closed' in u for u in gf.context("bob").updates)


def test_a_stale_request_is_refused_and_stays_closed(gf):
    p = gf.propose_change("charlie", item="party.date", value=SUNDAY)
    gf.db.run("UPDATE items SET version = version + 1 WHERE id = 'party.date'")
    assert code(lambda: gf.decide_change("alice", p.request.request, "accept")) == "STALE_REQUEST"
    assert detail(gf, "alice", "party.date").value == SATURDAY
    assert code(lambda: gf.decide_change("alice", p.request.request, "accept")) == "REQUEST_CLOSED"


def test_the_owner_setting_a_value_closes_a_matching_proposal(gf):
    gf.propose_change("charlie", item="party.date", value=SUNDAY)
    assert gf.propose_change("alice", item="party.date", value="Sunday the 25th at 6 pm").outcome == "confirmed"
    assert any("matching your suggestion" in u for u in gf.context("charlie").updates)


def test_the_proposer_can_withdraw_their_own_request_but_not_accept_it(gf):
    p = gf.propose_change("charlie", item="party.date", value="Monday, Oct 26, 6:00 pm")
    assert code(lambda: gf.decide_change("charlie", p.request.request, "accept")) == "FORBIDDEN"
    r = gf.decide_change("charlie", p.request.request, "reject")
    assert r.outcome == "rejected" and "Withdrew" in r.message
    assert detail(gf, "alice", "party.date").pending == []
    assert any("withdrew" in u for u in gf.context("alice", mark_read=False).updates)
    other = gf.propose_change("charlie", item="party.date", value="Tuesday, Oct 27, 6:00 pm")
    assert code(lambda: gf.decide_change("bob", other.request.request, "reject")) == "FORBIDDEN"


def test_a_new_detail_belongs_to_the_speaker(gf):
    r = gf.propose_change("bob", item="party.playlist", title="Playlist", value="Linda's 80s favourites", plan="party")
    assert r.outcome == "confirmed" and r.plan == "Linda's surprise party"
    assert detail(gf, "bob", "party.playlist").owner == "Bob"


def test_empty_values_are_rejected(gf):
    assert code(lambda: gf.propose_change("alice", item="party.date", value="  ")) == "INVALID_ARGUMENT"


def test_history_keeps_the_original_words(gf):
    p = gf.propose_change("charlie", item="party.date", value=SUNDAY, quote="Rosa's double-booked Saturday")
    gf.decide_change("alice", p.request.request, "accept", quote="Yes, confirm it")
    h = gf.history("bob", item="party.date")
    actions = [x.action for x in h.entries]
    assert actions[:2] == ["item.created", "value.confirmed"]
    assert "value.proposed" in actions and "request.accepted" in actions
    assert any(x.said == "Rosa's double-booked Saturday" for x in h.entries)
    assert any(x.said == "Let's throw Mom's surprise party on Saturday the 24th at 6." for x in h.entries)
    assert h.current == SUNDAY


# ---------------------------------------------------------------- visibility

def test_linda_sees_only_the_home_plan(gf):
    c = gf.context("linda")
    assert [p.title for p in c.plans] == ["Home"]
    assert sorted(d.id for d in c.plans[0].details) == ["household.book_club", "household.weekend_plan"]


def test_nothing_leaks_to_linda_through_a_full_cycle(gf):
    def check(stage: str) -> None:
        text = linda_knows(gf)
        for word in SECRETS:
            assert word not in text, f'"{word}" leaked to Linda {stage}'

    check("at start")
    p = gf.propose_change("charlie", item="party.date", value=SUNDAY, quote="party moves")
    check("while a change is pending")
    gf.decide_change("alice", p.request.request, "accept")
    check("after the change was confirmed")
    gf.propose_change("alice", item="household.weekend_plan", value="Family dinner at home, Sunday evening")
    check("after the cover plan was updated")
    assert detail(gf, "linda", "household.weekend_plan").value == "Family dinner at home, Sunday evening"


def test_the_cover_plan_flag_is_hidden_from_linda(gf):
    gf.propose_change("alice", item="party.date", value=SUNDAY)
    assert len(detail(gf, "alice", "household.weekend_plan").flags) == 1
    assert detail(gf, "linda", "household.weekend_plan").flags == []
    assert [f.label for f in gf.context("alice").affected] == ["Weekend family plan", "Book Rosa's back room"] or \
        "Weekend family plan" in [f.label for f in gf.context("alice", mark_read=False).affected]


def test_blocked_status_is_computed_per_viewer(gf):
    porch = item_id(gf, "Fix the porch light")
    gf.link_items("alice", porch, item_id(gf, "Send invitations"))
    assert task(gf, "linda", "Fix the porch light").blocked_by == []
    assert task(gf, "bob", "Fix the porch light").blocked_by
    assert "invitation" not in gf.explain("linda", item=porch).model_dump_json().lower()


def test_hidden_items_cannot_be_reached_by_guessing_ids(gf):
    invites = item_id(gf, "Send invitations")
    assert code(lambda: gf.propose_change("linda", item=invites, status="done")) in ("NOT_FOUND", "INVALID_ARGUMENT")
    assert code(lambda: gf.explain("linda", item=invites)) == "NOT_FOUND"
    assert code(lambda: gf.history("linda", item="party.date")) == "NOT_FOUND"
    assert code(lambda: gf.propose_change("linda", item="party.date", value="Monday")) == "NOT_FOUND"
    p = gf.propose_change("charlie", item="party.date", value="Sunday")
    assert code(lambda: gf.decide_change("linda", p.request.request, "accept")) == "NOT_FOUND"
    assert code(lambda: gf.context("linda", plan="party")) == "NOT_FOUND"


def test_a_hidden_task_cannot_be_given_to_someone_who_cannot_see_it(gf):
    assert code(lambda: gf.propose_change("alice", item=item_id(gf, "Send invitations"), owner="linda")) == "NOT_FOUND"
    assert code(lambda: gf.propose_change("alice", new_type="task", title="Buy balloons", owner="linda", plan="party")) == "NOT_FOUND"


def test_a_new_task_without_a_plan_lands_where_its_topic_lives(gf):
    party = gf.propose_change("bob", new_type="task", title="Buy candles for the party", owner="charlie")
    assert party.plan == "Linda's surprise party"
    assert "candles" not in gf.context("linda").model_dump_json().lower()
    assert gf.propose_change("linda", title="Fix the gutter", owner="bob").plan == "Home"
    # Names are not topics: "Linda" appears in both plans and must not decide anything.
    assert gf.propose_change("alice", new_type="chore", title="Call Linda's sister", owner="bob").plan == "Home"
    assert gf.propose_change("bob", new_type="task", title="Party playlist", owner="charlie", plan="party").plan == "Linda's surprise party"


def test_unknown_people_are_rejected(gf):
    assert code(lambda: gf.context("mallory")) == "UNAUTHENTICATED"
    assert Identity(gf.db).by_token("not-a-token") is None


def test_open_items_anyone_may_change(gf):
    milk = item_id(gf, "Milk")
    r = gf.propose_change("alice", item=milk, status="done")
    assert r.outcome == "updated"
    assert gf.propose_change("charlie", new_type="shopping", title="Eggs").outcome == "created"
    assert gf.propose_change("bob", item="Eggs", remove=True).outcome == "removed"
    assert task(gf, "linda", "Eggs") is None
