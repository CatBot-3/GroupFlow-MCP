"""Deciding things together: votes and decide-by deadlines, RSVPs and the bring list, and shared costs. Each test states the rule the server
enforces, because the model in front of it can be wrong, and these are the places where a wrong guess would cost someone money, a promise
or a say."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from groupflow.core import util
from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.engine import ASSISTANT, GroupFlow, fixed_clock
from groupflow.engine.costs import parse_money, settle_up, split_equal
from groupflow.world import seed_world
from tests.conftest import WORLD, code

NOW = "2026-10-17T14:00:00Z"  # Saturday 10 am in Toronto; Linda's party is next Saturday at 6 pm.


def world(stages: dict[str, str] | None = None) -> GroupFlow:
    util.set_clock(lambda: datetime.fromisoformat(NOW.replace("Z", "+00:00")))
    try:
        engine = GroupFlow(Db(":memory:"))
        engine.set_clock(fixed_clock(NOW))
        seed_world(engine, WORLD, stages or {})
    finally:
        util.set_clock(None)
    # Start from no seeded votes, answers or costs, so each test states its own.
    for table in ("poll_votes", "polls", "rsvps", "bring_items", "expenses", "settlements"):
        engine.db.run(f"DELETE FROM {table}")
    engine.db.run("DELETE FROM notifications")
    return engine


@pytest.fixture
def gf():
    engine = world()
    token = ASSISTANT.set(True)
    yield engine
    ASSISTANT.reset(token)


def at(engine: GroupFlow, stamp: str) -> None:
    engine.set_clock(fixed_clock(stamp))


def received(engine: GroupFlow, member: str, kind: str) -> list[str]:
    return [r["text"] for r in engine.db.all("SELECT text FROM notifications WHERE member_id = ? AND kind = ? ORDER BY rowid", member, kind)]


def view(engine: GroupFlow, member: str, plan: str):
    return engine.context(member, plan=plan, mark_read=False).plans[0]


def value(engine: GroupFlow, item: str) -> str | None:
    return engine._current_value(engine._item(item))


# ================================================================ votes


def test_a_vote_is_open_to_the_plan_and_only_the_plan(gf):
    r = gf.poll("alice", "start", plan="party", question="Which cake flavour?", options=["Chocolate", "Vanilla", "Lemon"], closes="Thursday 6pm")
    assert r["outcome"] == "started" and r["poll"]["closes"].startswith("Thu")
    pid = r["poll"]["id"]
    assert received(gf, "bob", "poll") and received(gf, "charlie", "poll") and not received(gf, "linda", "poll")
    # Linda cannot see the plan, so there is no vote for her to find, and no hint that there is one.
    assert code(lambda: gf.vote("linda", "chocolate", poll=pid)) == "NOT_FOUND"
    assert gf.vote("bob", "vanilla")["poll"]["your_vote"] == "Vanilla"
    assert gf.vote("charlie", "the first one")["poll"]["your_vote"] == "Chocolate"
    changed = gf.vote("bob", "lemon please")
    assert changed["message"].startswith("Changed.") and changed["poll"]["votes"] == {"Chocolate": ["Charlie"], "Vanilla": [], "Lemon": ["you"]}
    assert code(lambda: gf.vote("bob", "strawberry")) == "INVALID_ARGUMENT"
    # Votes are open: the plan view says who chose what, and who has not answered yet.
    polls = view(gf, "charlie", "party").polls
    assert polls[0]["votes"]["Lemon"] == ["Bob"] and polls[0]["not_voted"] == [] and polls[0]["decides"] == "Alice"
    # Everyone but the decider has answered, so the decider is told the votes are in.
    assert any("Everyone has answered" in t for t in received(gf, "alice", "poll"))


def test_people_who_have_not_voted_see_it_as_a_to_do(gf):
    gf.poll("alice", "start", plan="party", question="Which cake flavour?", options=["Chocolate", "Vanilla"])
    todos = gf.context("bob", mark_read=False).your_todos
    assert any(t.kind == "vote" and "cake flavour" in t.text for t in todos)
    gf.vote("bob", "no preference")
    assert not any(t.kind == "vote" for t in gf.context("bob", mark_read=False).your_todos)
    assert not any(t.kind == "vote" for t in gf.context("alice", mark_read=False).your_todos)  # she decides it; she does not vote on it


def test_an_activity_is_never_settled_by_a_vote(gf):
    gf2 = world({"friday_dinner": "committed"})
    with pytest.raises(DomainError) as err:
        gf2.poll("alice", "start", plan="friday_dinner", item="Dinner", question="Where for dinner?", options=["Thai", "Pizza"])
    assert err.value.code == "CONSENT_REQUIRED" and "confirms it on their own page" in err.value.message
    # A plain vote with no item is fine: the organizer then changes the plan and sends it out for confirmation.
    assert gf2.poll("alice", "start", plan="friday_dinner", question="Where for dinner?", options=["Thai", "Pizza"])["outcome"] == "started"


def test_the_owner_of_a_detail_decides_a_vote_about_it(gf):
    r = gf.poll("charlie", "start", plan="party", item="Cake", question="Which cake?", options=["Chocolate from Lucia's", "Lemon from Lucia's"])
    assert r["poll"]["decides"] == "Bob"
    # A deadline that lets silence decide belongs to the person who could decide anyway.
    assert code(lambda: gf.poll("charlie", "start", plan="party", item="Venue", proposal="Rosa's main room", closes="Thursday 6pm")) == "FORBIDDEN"
    gf.vote("alice", "lemon")
    assert code(lambda: gf.decide_poll("charlie", "lemon")) == "FORBIDDEN"
    d = gf.decide_poll("bob", "lemon")
    assert d["outcome"] == "decided" and value(gf, "cake.order") == "Lemon from Lucia's"
    assert any("decided" in t and "Lemon from Lucia's" in t for t in received(gf, "charlie", "poll"))
    assert code(lambda: gf.vote("alice", "chocolate")) == "NOT_FOUND"  # decided: no open vote left


def test_silence_settles_a_proposal_only_by_its_announced_deadline(gf):
    r = gf.poll("alice", "start", plan="party", item="Venue", proposal="Rosa's Trattoria, main room", closes="Thursday 6pm")
    assert r["poll"]["kind"] == "unless someone objects" and "unless someone objects" in received(gf, "bob", "poll")[0]
    assert gf.close_due_polls("2026-10-22T21:00:00Z") == 0  # Thursday 5 pm: not yet
    assert value(gf, "party.venue") == "Rosa's Trattoria, back room"
    assert gf.close_due_polls("2026-10-22T22:00:01Z") == 1
    assert value(gf, "party.venue") == "Rosa's Trattoria, main room"
    assert any("settled" in t and "nobody objected" in t for t in received(gf, "charlie", "poll"))


def test_one_objection_stops_the_default_and_goes_to_the_decider(gf):
    gf.poll("alice", "start", plan="party", item="Venue", proposal="Rosa's Trattoria, main room", closes="Thursday 6pm")
    r = gf.vote("charlie", "I object", reason="the main room is too loud for speeches")
    assert "stops the default" in r["message"]
    assert any("too loud" in t for t in received(gf, "alice", "poll"))
    gf.close_due_polls("2026-10-23T00:00:00Z")
    assert value(gf, "party.venue") == "Rosa's Trattoria, back room"
    assert any("nothing was applied" in t for t in received(gf, "alice", "poll"))
    assert any(t.kind == "decide" for t in gf.context("alice", mark_read=False).your_todos)


def test_a_default_never_overwrites_a_value_that_changed_meanwhile(gf):
    gf.poll("alice", "start", plan="party", item="Venue", proposal="Rosa's Trattoria, main room", closes="Thursday 6pm")
    gf.propose_change("alice", item="Venue", value="Luigi's patio")
    gf.close_due_polls("2026-10-23T00:00:00Z")
    assert value(gf, "party.venue") == "Luigi's patio"


def test_a_choice_with_a_fallback_takes_the_leader_or_the_named_default(gf):
    gf.poll("alice", "start", plan="party", item="Party date & time", question="What time?", options=["Saturday 5pm", "Saturday 7pm"],
            closes="Thursday 6pm", fallback="Saturday 5pm")
    gf.vote("bob", "7pm")
    gf.vote("charlie", "5pm")
    gf.close_due_polls("2026-10-23T00:00:00Z")
    assert value(gf, "party.date") == "Saturday 5pm"  # a tie: the default everyone was told about
    assert any("no clear winner" in t for t in received(gf, "bob", "poll"))
    gf.poll("alice", "start", plan="party", question="Theme?", options=["Seventies", "Garden"], closes="Thursday 6pm", fallback="leading")
    gf.vote("bob", "garden")
    gf.close_due_polls("2026-10-23T00:00:00Z")
    row = gf.db.get("SELECT * FROM polls WHERE question = 'Theme?'")
    assert row["status"] == "decided" and row["decided_option"] == "Garden" and row["decided_how"] == "leading"


def test_a_tie_with_no_named_default_goes_back_to_the_decider_and_a_check_in(gf):
    gf.poll("alice", "start", plan="party", question="Theme?", options=["Seventies", "Garden"], closes="Thursday 6pm", fallback="leading")
    gf.vote("bob", "garden")
    gf.vote("charlie", "seventies")
    gf.close_due_polls("2026-10-23T00:00:00Z")
    assert gf.db.get("SELECT status FROM polls")["status"] == "closed"
    report = gf.progress_report("party", "2026-10-23T14:00:00Z")
    assert any(b["kind"] == "decide_poll" and b["waiting_on"] == ["alice"] for b in report["blockers"])


def test_check_ins_ask_whoever_has_not_voted(gf):
    gf.poll("alice", "start", plan="party", question="Theme?", options=["Seventies", "Garden"])
    gf.vote("bob", "garden")
    report = gf.progress_report("party", "2026-10-21T15:00:00Z")
    vote = next(b for b in report["blockers"] if b["kind"] == "vote")
    assert vote["waiting_on"] == ["charlie"]
    assert any(d["to"] == "charlie" and "no preference" in d["text"] for d in report["drafts"])


def test_a_vague_deadline_is_asked_not_guessed(gf):
    with pytest.raises(DomainError) as err:
        gf.poll("alice", "start", plan="party", item="Venue", proposal="Rosa's Trattoria, main room", closes="soon")
    assert err.value.code == "WHEN_UNCLEAR"
    follow = err.value.details["followup"]
    assert len(follow["options"]) >= 2
    r = gf.answer_followup("alice", follow["options"][0])
    assert r["action"]["ok"] and r["action"]["result"]["outcome"] == "started"
    # No deadline at all is not a default either: silence can only settle it by a time everyone is told.
    assert code(lambda: gf.poll("alice", "start", plan="party", item="Cake", proposal="Lemon")) == "FORBIDDEN"
    assert code(lambda: gf.poll("bob", "start", plan="party", item="Cake", proposal="Lemon")) == "WHEN_UNCLEAR"


def test_a_vote_cannot_carry_a_hidden_plan_to_people_kept_from_it(gf):
    # Linda sees the household plan, not her surprise party.
    with pytest.raises(DomainError) as err:
        gf.poll("alice", "start", plan="home", question="Saturday: Rosa's Trattoria or home?", options=["Rosa's Trattoria", "Home"])
    assert err.value.code == "PRIVATE_NOTE"


def test_a_vote_with_one_option_or_too_many_open_is_refused(gf):
    assert code(lambda: gf.start_poll("alice", plan="party", question="Cake?", options=["Chocolate"])) == "INVALID_ARGUMENT"
    # Through the tool, one option is a proposal settled unless someone objects, which needs a deadline everyone is told.
    assert code(lambda: gf.poll("alice", "start", plan="party", question="Cake?", options=["Chocolate"])) == "WHEN_UNCLEAR"
    for i in range(5):
        gf.poll("alice", "start", plan="party", question=f"Question {i}?", options=["A", "B"])
    assert code(lambda: gf.poll("alice", "start", plan="party", question="One more?", options=["A", "B"])) == "RATE_LIMITED"
    assert gf.cancel_poll("alice", poll=gf.db.get("SELECT id FROM polls LIMIT 1")["id"])["outcome"] == "cancelled"
    assert code(lambda: gf.cancel_poll("bob", poll=gf.db.get("SELECT id FROM polls WHERE status = 'open' LIMIT 1")["id"])) == "FORBIDDEN"


def test_two_open_votes_are_told_apart_by_the_answer(gf):
    gf.poll("alice", "start", plan="party", question="Cake?", options=["Chocolate", "Lemon"])
    gf.poll("alice", "start", plan="party", question="Music?", options=["Jazz", "Disco"])
    assert gf.vote("bob", "disco")["poll"]["question"] == "Music?"
    assert gf.vote("bob", "lemon")["poll"]["question"] == "Cake?"
    assert code(lambda: gf.vote("bob", "no preference")) == "AMBIGUOUS_POLL"


# ================================================================ RSVPs and the bring list


def test_each_person_answers_for_themselves_and_the_headcount_adds_up(gf):
    assert gf.rsvp("bob", "yes", plan="party", plus_ones=1)["rsvp"]["headcount"] == 2
    assert gf.rsvp("charlie", "not sure", plan="party")["rsvp"]["headcount_with_maybes"] == 3
    v = view(gf, "alice", "party").rsvp
    assert v["coming"] == ["Bob +1"] and v["maybe"] == ["Charlie"] and v["not_answered"] == ["you"]
    assert any("Bob is coming with 1 more" in t for t in received(gf, "alice", "rsvp"))
    assert gf.rsvp("charlie", "can't make it", plan="party")["rsvp"]["not_coming"] == ["you"]
    assert gf.rsvp("bob", "yes", plan="party", plus_ones=1)["outcome"] == "unchanged"
    assert code(lambda: gf.rsvp("bob", "yes", plan="party", plus_ones=11)) == "INVALID_ARGUMENT"
    assert code(lambda: gf.rsvp("linda", "yes", plan="party")) == "NOT_FOUND"
    assert any(t.kind == "rsvp" for t in gf.context("alice", mark_read=False).your_todos)


def test_dietary_needs_stay_private_conditions_and_show_without_names():
    gf = world({"friday_dinner": "committed"})
    gf.rsvp("bob", "yes", plan="friday_dinner")
    gf.set_my_conditions("bob", [{"kind": "diet", "value": {"tags": ["vegetarian"]}}], plan="friday_dinner")
    assert "dietary_needs" not in view(gf, "alice", "friday_dinner").rsvp  # a draft counts only once Bob confirms it on his page
    bob = gf.identity.get("bob")
    c = next(x for x in gf.human_inbox(bob)["conditions"] if x["status"] == "unverified")
    gf.human_verify_condition(bob, c["id"], c["verify_digest"])
    r = view(gf, "alice", "friday_dinner").rsvp
    assert r["dietary_needs"] == ["vegetarian"] and "Bob" not in json.dumps({k: v for k, v in r.items() if k != "coming"})


def test_the_bring_list_is_claimed_by_the_people_who_bring(gf):
    r = gf.bring_list("alice", "add", ["chips", "ice", "paper plates"], plan="party")
    assert r["outcome"] == "updated" and [b["bringing"] for b in r["bring_list"]] == [None, None, None]
    assert any("bring list" in t for t in received(gf, "bob", "bring"))
    assert gf.bring_list("bob", "bring", "some chips", plan="party")["bring_list"][0]["bringing"] == "you"
    taken = gf.bring_list("charlie", "bring", "the chips", plan="party")
    assert taken["outcome"] == "unchanged" and "Bob is already bringing chips" in taken["message"]
    assert "only they can drop it" in gf.bring_list("charlie", "unclaim", "chips", plan="party")["message"]
    # "I'll bring a salad" adds it and claims it at once.
    assert gf.bring_list("charlie", "bring", "salad", plan="party")["bring_list"][-1] == {"id": gf.db.get(
        "SELECT id FROM bring_items WHERE label = 'salad'")["id"], "thing": "salad", "bringing": "you"}
    assert code(lambda: gf.bring_list("bob", "remove", "ice", plan="party")) == "FORBIDDEN"  # Alice added it
    assert gf.bring_list("alice", "remove", "salad", plan="party")["outcome"] == "updated"  # the organizer may
    assert any("no longer need to bring it" in t for t in received(gf, "charlie", "bring"))


def test_saying_no_frees_what_you_were_bringing(gf):
    gf.bring_list("alice", "add", ["chips", "ice"], plan="party")
    gf.bring_list("bob", "bring", ["chips", "ice"], plan="party")
    r = gf.rsvp("bob", "no", plan="party")
    assert "took you off chips and ice" in r["message"]
    assert all(b["bringing"] is None for b in view(gf, "alice", "party").bring_list)
    assert any("still needs someone" in t for t in received(gf, "charlie", "rsvp"))


def test_unclaimed_things_and_missing_answers_come_up_in_check_ins(gf):
    gf.rsvp("bob", "yes", plan="party")
    gf.bring_list("alice", "add", ["ice"], plan="party")
    report = gf.progress_report("party", "2026-10-22T14:00:00Z")  # Thursday: two days to go
    rsvp = next(b for b in report["blockers"] if b["kind"] == "rsvp")
    assert rsvp["waiting_on"] == ["charlie"]
    assert any(b["kind"] == "unclaimed" and b["label"] == "ice" for b in report["loose_ends"])
    at(gf, "2026-10-22T14:00:00Z")
    gf.apply_review("party", report)
    summary = " ".join(received(gf, "alice", "nudge"))
    assert "Nobody is bringing ice yet" in summary


# ================================================================ shared costs


def test_money_helpers_split_exactly_and_settle_in_few_payments():
    assert parse_money("$1,200.50") == 120050 and parse_money("24.5 dollars") == 2450 and parse_money("lots") is None
    assert split_equal(1000, {"a": 1, "b": 1, "c": 1}) == {"a": 334, "b": 333, "c": 333}
    assert split_equal(900, {"a": 2, "b": 1}) == {"a": 600, "b": 300}
    assert settle_up({"a": 40, "b": -20, "c": -20}) == [("b", "a", 20), ("c", "a", 20)]
    assert settle_up({"a": 30, "b": 10, "c": -25, "d": -15}) == [("c", "a", 25), ("d", "a", 5), ("d", "b", 10)]


@pytest.mark.parametrize('amount', ['-12.50', '$-12.50', '-$12.50', 'CAD -12.50', '(12.50)', '1,2.50', '12.50 garbage', float('nan'), float('inf')])
def test_invalid_or_negative_amounts_never_record_a_payment(gf, amount):
    for action, extra in [('add', {'what': 'food'}), ('paid', {'to': 'Bob'})]:
        assert code(lambda: gf.costs('alice', action, plan='party', amount=amount, **extra)) == 'INVALID_ARGUMENT'
    assert not gf.db.scalar('SELECT count(*) FROM expenses')
    assert not gf.db.scalar('SELECT count(*) FROM settlements')


def test_named_repayments_resolve_without_confirming_the_wrong_payment(gf):
    gf.costs('alice', 'paid', plan='party', to='Bob', amount=10)
    gf.costs('charlie', 'paid', plan='party', to='Bob', amount=20)
    assert view(gf, 'bob', 'party').costs['pending_repayments']
    assert code(lambda: gf.costs('charlie', 'confirm', plan='party', what='Alice')) == 'FORBIDDEN'
    assert gf.costs('bob', 'confirm', plan='party', what='Alice')['outcome'] == 'confirmed'
    assert gf.db.get("SELECT status FROM settlements WHERE from_id = 'charlie'")['status'] == 'pending'
    assert code(lambda: gf.costs('bob', 'confirm', plan='party', what='Alice')) == 'NOT_FOUND'


def test_ambiguous_sender_requires_a_payment_reference(gf):
    gf.costs('alice', 'paid', plan='party', to='Bob', amount=10)
    gf.costs('alice', 'paid', plan='party', to='Bob', amount=20)
    assert code(lambda: gf.costs('bob', 'confirm', plan='party', what='Alice')) == 'AMBIGUOUS_EXPENSE'
    assert gf.costs('bob', 'confirm', plan='party', what='Alice 20')['outcome'] == 'confirmed'
    assert gf.db.get('SELECT status FROM settlements WHERE amount_minor = 1000')['status'] == 'pending'


def test_show_distinguishes_planned_cost_from_an_empty_payment_ledger(gf):
    result = gf.costs('dana', 'show', plan='friday_dinner')
    assert '41' in str(result['planned_cost']['your_total'])
    assert result['costs']['your_balance'] == 'no expenses recorded'
    assert 'not mean the plan is free' in result['message']


def test_an_assignment_to_somebody_else_cannot_claim_for_the_speaker(gf):
    assert code(lambda: gf.bring_list('alice', 'bring', ['speaker'], plan='party', quote='Put Charlie down for the speaker')) == 'SELF_ONLY'
    assert not gf.db.scalar('SELECT count(*) FROM bring_items')
    assert gf.bring_list('alice', 'bring', ['speaker'], plan='party', quote="I'll bring a speaker for Charlie")['outcome'] == 'updated'


@pytest.mark.parametrize('reaction', ['Music again? we did that last time', 'That venue, seriously?'])
def test_reactions_cannot_create_a_suggestion_or_vote(gf, reaction):
    gf.poll('alice', 'start', plan='party', question='Music?', options=['Jazz', 'Disco'])
    marker = gf.db.scalar('SELECT count(*) FROM audit')
    assert code(lambda: gf.propose_change('alice', item='party.venue', value='another venue', certainty='tentative', quote=reaction)) == 'NO_ACTION_REQUESTED'
    assert code(lambda: gf.poll('bob', 'vote', plan='party', choice='Jazz', quote=reaction)) == 'NO_ACTION_REQUESTED'
    assert gf.db.scalar('SELECT count(*) FROM audit') == marker
    assert not gf.db.scalar('SELECT count(*) FROM poll_votes')


def test_the_payer_records_an_expense_and_everyone_sees_their_share(gf):
    r = gf.costs("alice", "add", plan="party", amount="$60", what="balloons and candles")
    assert r["outcome"] == "added" and "split 3 ways" in r["message"]
    c = view(gf, "bob", "party").costs
    assert c["your_balance"] == "you owe CAD 20.00" and c["settle_up"] == ["you pay Alice CAD 20.00", "Charlie pays Alice CAD 20.00"]
    assert any("your share is CAD 20.00" in t for t in received(gf, "charlie", "costs"))
    assert view(gf, "alice", "party").costs["your_balance"] == "you are owed CAD 40.00"


def test_someone_else_s_payment_counts_once_they_confirm_it(gf):
    r = gf.costs("alice", "add", plan="party", amount=45, what="pizza", paid_by="Bob")
    assert r["outcome"] == "pending"
    assert view(gf, "charlie", "party").costs["your_balance"] == "settled"
    assert any(t.kind == "confirm_cost" for t in gf.context("bob", mark_read=False).your_todos)
    assert code(lambda: gf.costs("charlie", "confirm", plan="party", expense="pizza")) == "FORBIDDEN"
    assert gf.costs("bob", "confirm", plan="party")["outcome"] == "confirmed"
    assert view(gf, "charlie", "party").costs["your_balance"] == "you owe CAD 15.00"


def test_a_repayment_counts_once_the_receiver_confirms_it(gf):
    gf.costs("alice", "add", plan="party", amount=60, what="balloons")
    r = gf.costs("bob", "paid", plan="party", to="Alice")  # no amount: what the balances say Bob owes her
    assert r["outcome"] == "pending" and "CAD 20.00" in received(gf, "alice", "costs")[-1]
    assert view(gf, "bob", "party").costs["your_balance"] == "you owe CAD 20.00"
    gf.costs("alice", "confirm", plan="party")
    assert view(gf, "bob", "party").costs["your_balance"] == "settled"
    # Recorded by the person who got the money, it counts at once.
    assert gf.costs("alice", "paid", plan="party", paid_by="Charlie", to="me", amount=20)["outcome"] == "settled"
    assert view(gf, "charlie", "party").costs["your_balance"] == "settled"


def test_the_default_split_follows_the_rsvps_and_their_guests(gf):
    gf.rsvp("bob", "yes", plan="party", plus_ones=1)
    gf.rsvp("alice", "yes", plan="party")
    gf.rsvp("charlie", "no", plan="party")
    gf.costs("alice", "add", plan="party", amount=90, what="food")
    shares = json.loads(gf.db.get("SELECT shares_json FROM expenses")["shares_json"])
    assert shares == {"alice": 3000, "bob": 6000}
    gf.costs("alice", "add", plan="party", amount=30, what="wine", split_with=["me", "Charlie"])
    assert json.loads(gf.db.get("SELECT shares_json FROM expenses WHERE description = 'wine'")["shares_json"]) == {"alice": 1500, "charlie": 1500}


def test_only_the_payer_recorder_or_organizer_removes_an_expense(gf):
    gf.costs("bob", "add", plan="party", amount=30, what="streamers")
    assert code(lambda: gf.costs("charlie", "remove", plan="party", expense="streamers")) == "FORBIDDEN"
    assert gf.costs("alice", "remove", plan="party", expense="streamers")["outcome"] == "removed"
    assert view(gf, "charlie", "party").costs is None  # nothing left to show
    assert any("removed CAD 30.00 for streamers" in t and t.endswith("settled.") for t in received(gf, "charlie", "costs"))


def test_costs_stay_inside_the_plan(gf):
    assert code(lambda: gf.costs("linda", "add", plan="party", amount=10, what="cake")) == "NOT_FOUND"
    with pytest.raises(DomainError) as err:
        gf.costs("alice", "add", plan="home", amount=20, what="Rosa's Trattoria deposit")
    assert err.value.code == "PRIVATE_NOTE"


def test_settling_up_comes_up_after_the_day_and_not_before(gf):
    gf.costs("alice", "add", plan="party", amount=60, what="balloons")
    before = gf.progress_report("party", "2026-10-22T14:00:00Z")
    assert not any(b["kind"] == "settle" for b in before["blockers"])
    after = gf.progress_report("party", "2026-10-27T15:00:00Z")  # the Tuesday after the party
    settle = next(b for b in after["blockers"] if b["kind"] == "settle")
    assert settle["waiting_on"] == ["bob", "charlie"]
    assert any(d["to"] == "bob" and "you owe Alice CAD 20.00" in d["text"] for d in after["drafts"])
    assert any(t.kind == "settle" for t in gf.context("bob", mark_read=False).your_todos) is False  # the context clock is still before the day
    at(gf, "2026-10-27T15:00:00Z")
    assert any(t.kind == "settle" for t in gf.context("bob", mark_read=False).your_todos)
    late = gf.progress_report("party", "2026-12-01T15:00:00Z")
    assert not any(b["kind"] == "settle" for b in late["blockers"])  # a month on, it stops asking


def test_a_shared_word_is_not_the_same_thing(gf):
    gf.bring_list("charlie", "add", ["birthday banner"], plan="party")
    r = gf.bring_list("bob", "bring", ["birthday candles"], plan="party")
    assert [(b["thing"], b["bringing"]) for b in r["bring_list"]] == [("birthday banner", None), ("birthday candles", "you")]
    assert gf.bring_list("bob", "unclaim", ["candles"], plan="party")["outcome"] == "updated"


def test_a_trip_keeps_its_packing_list_instead(gf):
    with pytest.raises(DomainError) as err:
        gf.bring_list("chen", "bring", ["sunscreen"], plan="cottage_weekend")
    assert "packing list" in err.value.message
