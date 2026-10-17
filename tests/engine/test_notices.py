"""Telling people about decisions (by role, never past a plan's audience) and server-held follow-up questions."""
from __future__ import annotations

import pytest

from groupflow.engine import ASSISTANT, LISTENERS, fixed_clock
from tests.conftest import code, seeded


@pytest.fixture
def gf():
    """The demo world, then engine calls made as an assistant's MCP tool calls are (offers and clarifying questions happen only there)."""
    engine = seeded()
    token = ASSISTANT.set(True)
    yield engine
    ASSISTANT.reset(token)


def test_offers_and_questions_exist_only_in_an_assistant_conversation():
    # Seeding the world, a person's own page or a fixture changes values with no conversation to ask in: no offers, no ghost questions.
    engine = seeded()
    assert engine.db.scalar("SELECT COUNT(*) FROM followups") == 0
    res = engine.propose_change("alice", item="party.venue", value="Lucky Star karaoke", quote="karaoke instead")
    assert res.outcome == "confirmed" and res.suggested_followup is None
    try:
        engine.notify_people("alice", note="running late")
    except Exception as err:  # noqa: BLE001
        assert err.code == "AMBIGUOUS_PLAN" and "followup" not in err.details
    token = ASSISTANT.set(True)
    try:
        offered = engine.propose_change("alice", item="party.venue", value="Rosa's Trattoria, back room", quote="back to rosa's")
    finally:
        ASSISTANT.reset(token)
    assert offered.suggested_followup is not None
    # An offer the assistant may never have asked is not reported as a question waiting for the speaker.
    view = engine.context("alice", mark_read=False)
    assert view.open_questions == [] and "question" not in view.headline


def unread(gf, member: str) -> list[str]:
    return [r["text"] for r in gf.db.all("SELECT text FROM notifications WHERE member_id = ? AND read_at IS NULL ORDER BY rowid", member)]


def notices(gf, member: str) -> list[str]:
    return [r["text"] for r in gf.db.all("SELECT text FROM notifications WHERE member_id = ? AND kind = 'notice' ORDER BY rowid", member)]


def test_everyone_means_everyone_who_can_see_the_plan_and_the_facts_come_from_the_plan(gf):
    res = gf.notify_people("alice", about="party.venue")
    assert res["outcome"] == "sent" and res["plan"] == "Linda's surprise party"
    assert sorted(r["name"] for r in res["recipients"]) == ["Bob", "Charlie"]
    text = notices(gf, "bob")[-1]
    assert text.startswith("Alice asked Alexa to let you know about Linda's surprise party") and 'Venue is "Rosa\'s Trattoria, back room"' in text
    # The guest of honour is never told, and the speaker is not told about their own update.
    assert notices(gf, "linda") == [] and notices(gf, "alice") == []
    assert "Nothing in the plan changed" in res["message"]
    # Telling people is not a change: the plan and its version stay as they were.
    assert gf._plan_row("party")["aggregate_version"] == seeded()._plan_row("party")["aggregate_version"]
    # Recipients hear it on their next turn, through the usual updates.
    assert any("Rosa's Trattoria" in u for u in gf.context("charlie").updates)


def test_a_named_person_who_cannot_see_the_plan_is_refused_by_name_and_by_family_word(gf):
    assert code(lambda: gf.notify_people("alice", plan="party", audience=["Linda"], note="see you Saturday")) == "NOT_FOUND"
    assert code(lambda: gf.notify_people("alice", plan="party", audience=["Mom"], note="see you Saturday")) == "NOT_FOUND"
    assert notices(gf, "linda") == []


def test_a_note_that_mentions_a_plan_kept_from_a_recipient_is_refused(gf):
    err = None
    try:
        gf.notify_people("alice", plan="home", note="don't forget the surprise party at Rosa's Trattoria on Saturday")
    except Exception as e:  # noqa: BLE001
        err = e
    assert err is not None and err.code == "PRIVATE_NOTE" and "Linda" in err.message
    assert notices(gf, "linda") == [] and notices(gf, "bob") == []
    # The same words are fine for people who can see the surprise.
    assert gf.notify_people("alice", plan="party", note="don't forget the surprise party at Rosa's Trattoria")["outcome"] == "sent"
    # An ordinary family note goes to the whole family.
    res = gf.notify_people("alice", plan="home", note="I'll grab milk on the way home")
    assert sorted(r["name"] for r in res["recipients"]) == ["Bob", "Charlie", "Linda"]


def test_levels_pick_people_by_their_role_in_the_plan(gf):
    # Friday dinner is committed; Alice organizes it.
    assert [r["name"] for r in gf.notify_people("bob", plan="friday_dinner", audience=["organizers"], note="running 10 min late")["recipients"]] == ["Alice"]
    # Owners of the item and of the work that watches it, with a reminder to check their part.
    res = gf.notify_people("alice", about="party.date", audience=["owners"])
    assert [(r["name"], r["role"]) for r in res["recipients"]] == [("Bob", "check"), ("Charlie", "check")]
    assert res["recipients"][0]["text"].endswith("Check that your part still works with this.")
    # A pending suggestion waits for the owner: needs_answer reaches exactly that person, with what they have to do.
    gf.propose_change("bob", item="household.book_club", value="Thursday, Oct 22, 8:00 pm at Margaret's", quote="can book club start at 8 instead")
    res = gf.notify_people("alice", about="household.book_club", audience=["needs_answer"])
    assert [(r["name"], r["role"]) for r in res["recipients"]] == [("Linda", "decide")]
    assert "waiting for your decision" in res["recipients"][0]["text"] and "Bob suggested" in res["recipients"][0]["text"]
    # Level words people actually say map onto the levels; asking only for yourself tells nobody.
    assert [r["name"] for r in gf.notify_people("dana", plan="friday_dinner", audience=["hosts"], note="I'll bring cash")["recipients"]] == ["Alice"]
    assert code(lambda: gf.notify_people("chen", plan="cs341_prep", audience=["organizers"], note="room booked")) == "NOT_FOUND"


def test_work_in_a_plan_kept_from_the_speaker_never_shapes_who_is_told(gf):
    # Bob's drive to the party (hidden from Linda) waits on the family's weekend plan.
    drive = gf.db.get("SELECT id FROM items WHERE plan_id = 'party' AND label = 'Drive Linda to the party'")["id"]
    gf.link_items("alice", drive, "household.weekend_plan")
    res = gf.notify_people("linda", about="household.weekend_plan", audience=["owners"])
    # Only the item's owner: Bob's hidden dependency is not a reason Linda's update reaches him, or something her result could show.
    assert [r["name"] for r in res["recipients"]] == ["Alice"]
    # Alice sees both plans, so for her Bob's dependent work counts.
    res = gf.notify_people("alice", about="household.weekend_plan", audience=["owners"])
    assert ("Bob", "check") in [(r["name"], r["role"]) for r in res["recipients"]]


def test_a_bare_reminder_needs_something_for_each_person_to_do(gf):
    # Nothing to tell and nothing for them to do: refused.
    assert code(lambda: gf.notify_people("alice", plan="home")) == "INVALID_ARGUMENT"
    # Someone whose decision is pending: the reminder says what to do.
    gf.propose_change("bob", item="household.book_club", value="Thursday, Oct 22, 8:00 pm at Margaret's", quote="can book club start at 8 instead")
    res = gf.notify_people("alice", plan="home", audience=["needs_answer"])
    assert res["recipients"][0]["text"] == "Alice asked Alexa to remind you about Home. It is waiting for your decision; tell Alexa yes or no."


def test_repeats_are_not_sent_twice_and_updates_are_rate_limited(gf):
    first = gf.notify_people("alice", about="party.venue")
    again = gf.notify_people("alice", about="party.venue")
    assert first["outcome"] == "sent" and again["outcome"] == "already_told"
    assert {s["reason"] for s in again["skipped"]} == {"already told"} and len(notices(gf, "bob")) == 1
    for n in range(4):
        gf.notify_people("alice", plan="party", note=f"reminder {n}")
    assert code(lambda: gf.notify_people("alice", plan="party", note="one more")) == "RATE_LIMITED"


def test_people_listening_to_a_shared_conversation_are_not_told_again(gf):
    token = LISTENERS.set(("alice", frozenset({"alice", "bob", "linda"})))
    try:
        res = gf.notify_people("alice", about="household.weekend_plan")
        # With Linda listening, the surprise party is not even in this conversation's scope.
        assert code(lambda: gf.notify_people("alice", about="party.venue")) == "NOT_FOUND"
    finally:
        LISTENERS.reset(token)
    assert [r["name"] for r in res["recipients"]] == ["Charlie"]
    assert sorted(s["name"] for s in res["skipped"]) == ["Bob", "Linda"]


def test_a_decision_offers_to_tell_the_others_and_yes_runs_exactly_that(gf):
    res = gf.propose_change("alice", item="party.venue", value="Lucky Star karaoke", quote="let's just do karaoke at lucky star")
    offer = res.suggested_followup
    assert res.outcome == "confirmed" and offer and offer["options"] == ["yes", "no"]
    # Charlie's invitations task watches the venue, so the change already flagged it for him; Bob has not heard, Linda never will.
    assert res.impacts.notified == ["Charlie"]
    assert offer["question"] == 'Want me to let Bob know that Venue is now "Lucky Star karaoke"?'
    # A second decision soon after widens the same offer, so one yes covers both.
    res2 = gf.propose_change("alice", item="party.date", value="Saturday, Oct 24, 7:00 pm", quote="and make it 7")
    assert res2.suggested_followup["id"] == offer["id"] and "Party date" in res2.suggested_followup["question"]
    out = gf.answer_followup("alice", "yeah go for it", followup=offer["id"])
    assert out["choice"] == "yes" and out["action"]["ok"] and out["action"]["tool"] == "notify_people"
    told = notices(gf, "bob")[-1]
    assert "Lucky Star karaoke" in told and "7:00 pm" in told
    assert notices(gf, "linda") == [] and notices(gf, "charlie") == []
    # Answered: the same id cannot run twice.
    assert code(lambda: gf.answer_followup("alice", "yes", followup=offer["id"])) == "NOT_FOUND"


def test_no_means_no_more_offers_for_a_while_and_people_already_told_are_left_out(gf):
    res = gf.propose_change("alice", item="party.venue", value="Lucky Star karaoke", quote="karaoke at lucky star")
    assert gf.answer_followup("alice", "nah", followup=res.suggested_followup["id"])["choice"] == "no"
    assert notices(gf, "bob") == []
    assert gf.propose_change("alice", item="party.date", value="Saturday, Oct 24, 7:00 pm", quote="make it 7").suggested_followup is None


def test_the_person_whose_suggestion_was_accepted_already_heard_so_the_offer_leaves_them_out(gf):
    pending = gf.propose_change("bob", item="guests.count", value="20 guests", quote="we're def more like 20 now")
    assert pending.outcome == "pending_review"
    decided = gf.decide_change("charlie", pending.request.request, "accept", quote="yeah ok 20")
    assert decided.outcome == "accepted"
    # Bob heard that his suggestion was accepted; Alice did not.
    assert decided.suggested_followup["question"] == 'Want me to let Alice know that Guest count is now "20 guests"?'


def test_no_offer_when_everyone_already_heard(gf):
    # Bob and Charlie both have work that watches the party date, so the change itself flags it for them.
    res = gf.propose_change("alice", item="party.date", value="Saturday, Oct 24, 7:00 pm", quote="make it 7")
    assert sorted(res.impacts.notified) == ["Bob", "Charlie"] and res.suggested_followup is None


def test_vague_times_are_asked_about_and_the_answer_runs_the_exact_change(gf):
    before = gf._current_value(gf._item("party.date"))
    assert code(lambda: gf.propose_change("alice", item="party.date", value="Saturday, Oct 24, 7:00 pm", quote="can we push it back a bit")) == "VAGUE_TIME"
    assert gf._current_value(gf._item("party.date")) == before
    # Concrete words pass: an amount, a time, a day.
    assert gf.propose_change("alice", item="party.date", value="Saturday, Oct 24, 6:30 pm", quote="push it back half an hour").outcome == "confirmed"
    asked = gf.ask_followup("alice", "Push it to 7 or 7:30?", [
        {"label": "7 pm", "action": {"tool": "propose_change", "arguments": {"item": "party.date", "value": "Saturday, Oct 24, 7:00 pm", "quote": "a bit later"}}},
        {"label": "7:30 pm", "action": {"tool": "propose_change", "arguments": {"item": "party.date", "value": "Saturday, Oct 24, 7:30 pm", "quote": "a bit later"}}},
    ], plan="party")
    assert asked["outcome"] == "pending" and asked["options"] == ["7 pm", "7:30 pm"]
    # Chat memory lost: the question is still on the server, and only the person asked can answer it.
    view = gf.context("alice", mark_read=False)
    assert [q["id"] for q in view.open_questions] == [asked["id"]] and "open question" in view.headline
    assert code(lambda: gf.answer_followup("bob", "7")) == "NOT_FOUND"
    out = gf.answer_followup("alice", "7")
    assert out["choice"] == "7 pm" and out["action"]["ok"] and out["action"]["result"]["outcome"] == "confirmed"
    assert gf._current_value(gf._item("party.date")) == "Saturday, Oct 24, 7:00 pm"
    said = gf.db.get("SELECT u.text FROM audit a JOIN utterances u ON u.id = a.utterance_id WHERE a.action = 'value.confirmed' ORDER BY a.seq DESC LIMIT 1")
    assert 'answered: "7 pm"' in said["text"]
    assert gf.context("alice", mark_read=False).open_questions == []


def test_an_answer_without_an_id_goes_to_the_newest_question_it_answers(gf):
    asked = gf.ask_followup("linda", "Start book club at 7:30 or 8?", [
        {"label": "7:30", "action": {"tool": "propose_change", "arguments": {"item": "household.book_club", "value": "Thursday, Oct 22, 7:30 pm at Margaret's"}}},
        {"label": "8:00", "action": {"tool": "propose_change", "arguments": {"item": "household.book_club", "value": "Thursday, Oct 22, 8:00 pm at Margaret's"}}},
    ])
    # A decision after the question: the server's offer is now the newest, and it is listed but marked as not asked.
    gf.propose_change("linda", item="household.book_club", value="Thursday, Oct 22, 6:30 pm at Margaret's", quote="actually 6:30 this week")
    view = gf.context("linda", mark_read=False)
    assert [q["id"] for q in view.open_questions] == [asked["id"]]  # the offer is answerable, but not listed as a question she heard
    # "8" answers the older time question, not the yes/no offer; then "yes please" answers the offer.
    timed = gf.answer_followup("linda", "8")
    assert timed["id"] == asked["id"] and timed["choice"] == "8:00"
    told = gf.answer_followup("linda", "yes please")
    assert told["question"].startswith("Want me to let Alice, Bob and Charlie know") and told["action"]["ok"]


def test_answers_match_by_label_number_order_and_yes_no(gf):
    match = gf._match_option
    opts = [{"label": "2 pm"}, {"label": "3 pm"}]
    assert match(opts, "2")["label"] == "2 pm"  # a time, not the second option
    assert match(opts, "the second one")["label"] == "3 pm"
    assert match([{"label": "yes"}, {"label": "no"}], "yep do it")["label"] == "yes"
    assert match([{"label": "yes"}, {"label": "no"}], "no thanks")["label"] == "no"
    assert match([{"label": "Home"}, {"label": "Friday dinner"}], "the dinner one")["label"] == "Friday dinner"
    assert match(opts, "whatever") is None


def test_an_ambiguous_request_comes_back_with_a_which_plan_question_that_replays_the_call(gf):
    try:
        gf.notify_people("alice", note="running late, start without me")
        raise AssertionError("expected AMBIGUOUS_PLAN")
    except Exception as err:  # noqa: BLE001
        assert getattr(err, "code", None) == "AMBIGUOUS_PLAN"
        followup = err.details["followup"]
    assert "Home" in followup["options"]
    out = gf.answer_followup("alice", "home", followup=followup["id"])
    assert out["action"]["ok"] and sorted(r["name"] for r in out["action"]["result"]["recipients"]) == ["Bob", "Charlie", "Linda"]
    assert "running late" in notices(gf, "linda")[-1]


def test_options_are_checked_and_questions_expire(gf):
    assert code(lambda: gf.ask_followup("alice", "Which?", ["only one"])) == "INVALID_ARGUMENT"
    assert code(lambda: gf.ask_followup("alice", "Which?", ["same", "Same"])) == "INVALID_ARGUMENT"
    assert code(lambda: gf.ask_followup("alice", "Delete it?", [{"label": "yes", "action": {"tool": "configure_project", "arguments": {}}}, "no"])) \
        == "INVALID_ARGUMENT"
    gf.set_clock(fixed_clock("2026-10-16T12:00:00Z"))
    asked = gf.ask_followup("alice", "Tell the family?", ["yes", "no"], plan="home")
    gf.set_clock(fixed_clock("2026-10-16T12:31:00Z"))
    assert gf.context("alice", mark_read=False).open_questions == []
    assert code(lambda: gf.answer_followup("alice", "yes", followup=asked["id"])) == "NOT_FOUND"


def test_a_shared_conversation_only_shows_questions_about_plans_everyone_there_can_see(gf):
    gf.ask_followup("alice", "Bring the cake in at 8?", ["yes", "no"], plan="party")
    gf.ask_followup("alice", "Add coffee to the list?", ["yes", "no"], plan="home")
    token = LISTENERS.set(("alice", frozenset({"alice", "linda"})))
    try:
        shown = [q["question"] for q in gf.context("alice", mark_read=False).open_questions]
    finally:
        LISTENERS.reset(token)
    assert shown == ["Add coffee to the list?"]
