"""Keeping plans moving: reminders at a stated time (who gets them is worked out when they go off), and check-ins on plans that went
quiet while something waits, with the limits that keep them from nagging and the checks a reviewing model cannot get around."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from groupflow.core import util
from groupflow.core.db import Db
from groupflow.engine import ASSISTANT, GroupFlow, fixed_clock
from groupflow.engine.momentum import resolve_when
from groupflow.world import approve_all, seed_world
from tests.conftest import WORLD, code

NOW = "2026-10-17T14:00:00Z"  # Saturday, 10 am in Toronto. The party is next Saturday at 6 pm; Friday dinner is next Friday.
TZ = ZoneInfo("America/Toronto")


def world(stages: dict[str, str] | None = None) -> GroupFlow:
    """The demo world seeded at NOW, whatever today's date is, so every date below means the same thing on every run."""
    util.set_clock(lambda: datetime.fromisoformat(NOW.replace("Z", "+00:00")))
    try:
        engine = GroupFlow(Db(":memory:"))
        engine.set_clock(fixed_clock(NOW))
        seed_world(engine, WORLD, stages or {})
    finally:
        util.set_clock(None)
    return engine


@pytest.fixture
def gf():
    engine = world()
    token = ASSISTANT.set(True)
    yield engine
    ASSISTANT.reset(token)


@pytest.fixture
def dinner():
    """Friday dinner sent out for confirmation: Alice, Bob, Chen and Dana all still have to answer."""
    engine = world({"friday_dinner": "awaiting"})
    token = ASSISTANT.set(True)
    yield engine
    ASSISTANT.reset(token)


def at(engine: GroupFlow, stamp: str) -> None:
    engine.set_clock(fixed_clock(stamp))


def received(engine: GroupFlow, member: str, kind: str) -> list[str]:
    return [r["text"] for r in engine.db.all("SELECT text FROM notifications WHERE member_id = ? AND kind = ? ORDER BY rowid", member, kind)]


# ---------------------------------------------------------------- reminders


def test_spoken_times_resolve_exactly_or_not_at_all():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=TZ)
    party = datetime(2026, 10, 24, 18, 0, tzinfo=TZ)
    expected = {
        "tomorrow at 9": (10, 18, 9, 0), "tmrw morning": (10, 18, 9, 0), "in 2 hours": (10, 17, 12, 0), "in half an hour": (10, 17, 10, 30),
        "friday 6pm": (10, 23, 18, 0), "tonight": (10, 17, 19, 0), "saturday at 6pm": (10, 17, 18, 0), "saturday morning": (10, 24, 9, 0),
        "oct 20": (10, 20, 9, 0), "the 22nd": (10, 22, 9, 0),
        # Relative to the plan's own date
        "1 day before": (10, 23, 9, 0), "the day before": (10, 23, 9, 0), "the evening before": (10, 23, 18, 0),
        "2 hours before the party": (10, 24, 16, 0), "an hour before": (10, 24, 17, 0), "the morning of": (10, 24, 9, 0),
        "two days before at noon": (10, 22, 12, 0),
    }
    for said, (month, day, hour, minute) in expected.items():
        assert resolve_when(said, now, party) == (datetime(2026, month, day, hour, minute, tzinfo=TZ), ""), said
    for vague in ("later", "sometime next week", "in a bit", "this weekend", "end of the month", "whenever"):
        assert resolve_when(vague, now, party) == (None, "vague"), vague
    assert resolve_when("1 day before", now, None) == (None, "no_date")
    # "yesterday at 9" is in the past (and refused as such), never quietly moved to tomorrow like a bare "at 9".
    assert resolve_when("yesterday at 9", now, party)[0] < now < resolve_when("at 9", now, party)[0]


def test_a_reminder_reaches_the_speaker_at_its_time_with_the_plan_as_it_is_then(gf):
    res = gf.set_reminder("alice", when="tomorrow at 9", about="cake.order")
    assert res["outcome"] == "scheduled" and res["when"] == "Sun, Oct 18, 9:00 am" and res["to"] == "you" and res["about"] == ["Cake"]
    assert [r["id"] for r in gf.context("alice", mark_read=False).your_reminders] == [res["id"]]
    # Bob changes the cake before it goes off: the reminder reads the plan when it fires, not when it was set.
    gf.propose_change("bob", item="cake.order", value="Ice cream cake", quote="ice cream cake it is")
    at(gf, "2026-10-18T12:59:00Z")
    assert gf.deliver_due_reminders() == 0
    at(gf, "2026-10-18T13:00:00Z")
    assert gf.deliver_due_reminders() == 1 and gf.deliver_due_reminders() == 0
    [text] = received(gf, "alice", "reminder")
    assert text.startswith("Reminder you set for Linda's surprise party") and "Ice cream cake" in text
    assert gf.context("alice", mark_read=False).your_reminders == []
    assert any("Ice cream cake" in u for u in gf.context("alice").updates)


def test_whoever_has_not_answered_is_worked_out_when_the_reminder_goes_off(dinner):
    res = dinner.set_reminder("alice", when="tomorrow at 6pm", plan="friday_dinner", audience=["needs_answer"])
    assert res["to"] == "you, Bob, Chen and Dana" and "checked again when it goes off" in res["message"]
    approve_all(dinner, dinner.identity.get("bob"), "friday_dinner")
    approve_all(dinner, dinner.identity.get("alice"), "friday_dinner")
    at(dinner, "2026-10-18T22:00:00Z")
    assert dinner.deliver_due_reminders() == 2
    assert received(dinner, "bob", "reminder") == [] and received(dinner, "alice", "reminder") == []
    assert received(dinner, "chen", "reminder")[0].startswith("Reminder from Alice about Friday dinner")


def test_a_reminder_relative_to_an_item_counts_back_from_that_item_and_a_household_has_no_single_date(gf):
    home = gf._plan_row("home")
    assert gf._event_time(home, []) is None  # the book club entry is not when the household "happens"
    assert gf.set_reminder("linda", when="1 day before", about="Linda's book club")["when"] == "Wed, Oct 21, 9:00 am"
    err = code(lambda: gf.set_reminder("linda", when="1 day before", plan="home", note="bins"))
    assert err == "WHEN_UNCLEAR"


def test_vague_times_are_asked_with_concrete_options_and_the_answer_sets_it(gf):
    try:
        gf.set_reminder("alice", when="later", about="cake.order")
        raise AssertionError("a vague time was accepted")
    except Exception as err:  # noqa: BLE001
        assert err.code == "WHEN_UNCLEAR"
        question = err.details["followup"]
    assert question["options"] == ["This evening at 7", "Tomorrow at 9am", "The day before at 9am", "The morning of"]
    gf.ask_followup("alice", followup=question["id"])
    done = gf.answer_followup("alice", "the day before")
    assert done["action"]["ok"] and done["action"]["result"]["when"] == "Fri, Oct 23, 9:00 am"
    assert code(lambda: gf.set_reminder("alice", when="yesterday at 9", about="cake.order")) == "INVALID_ARGUMENT"


def test_reminders_reach_only_people_who_can_see_the_plan_and_notes_never_carry_what_they_cannot(gf):
    assert code(lambda: gf.set_reminder("alice", when="tomorrow at 9", plan="party", audience=["Linda"], note="balloons")) == "NOT_FOUND"
    leaky = lambda: gf.set_reminder("alice", when="tomorrow at 9", plan="home", audience=["everyone"], note="balloons for the surprise party")  # noqa: E731
    assert code(leaky) == "PRIVATE_NOTE"
    # Even a reminder only for the speaker: it lives in the family's Home plan and can be read out where Linda listens.
    assert code(lambda: gf.set_reminder("alice", when="tomorrow at 9", plan="home", note="candles for the surprise party")) == "PRIVATE_NOTE"
    assert gf.set_reminder("alice", when="tomorrow at 9", plan="party", note="candles for the surprise party")["to"] == "you"
    res = gf.set_reminder("alice", when="1 day before", plan="party", audience=["everyone"], note="bring the banner")
    assert res["to"] == "you, Bob and Charlie"
    at(gf, "2026-10-23T13:00:00Z")
    gf.deliver_due_reminders()
    assert received(gf, "linda", "reminder") == []
    assert received(gf, "bob", "reminder") == ['Reminder from Alice about Linda\'s surprise party. Alice says: "bring the banner"']


def test_someone_who_left_gets_nothing_and_only_the_creator_can_cancel(gf):
    res = gf.set_reminder("alice", when="tomorrow at 9", plan="home", audience=["Bob"], note="porch light")
    assert code(lambda: gf.set_reminder("bob", cancel=res["id"])) == "NOT_FOUND"
    gf.leave_group(gf.identity.get("bob"), "family")
    at(gf, "2026-10-18T13:00:00Z")
    assert gf.deliver_due_reminders() == 0 and received(gf, "bob", "reminder") == []
    other = gf.set_reminder("alice", when="in 2 hours", plan="party", note="call the bakery")
    assert gf.set_reminder("alice", cancel=other["id"])["outcome"] == "cancelled"
    at(gf, "2026-10-19T13:00:00Z")
    assert gf.deliver_due_reminders() == 0


# ---------------------------------------------------------------- check-ins on quiet plans


def test_a_plan_waiting_on_answers_counts_as_stuck_only_after_the_quiet_time(dinner):
    report = dinner.progress_report("friday_dinner")
    [blocker] = report["blockers"]
    assert blocker["kind"] == "confirm" and sorted(blocker["waiting_on"]) == ["alice", "bob", "chen", "dana"]
    assert not report["stuck"] and report["threshold_hours"] == 24  # dinner is less than a week away
    assert dinner.plans_due_for_review() == []
    later = dinner.progress_report("friday_dinner", as_of="2026-10-18T15:00:00Z")
    assert later["stuck"] and later["due"] and [d["to"] for d in later["drafts"]] == ["alice", "bob", "chen", "dana"]
    assert "friday_dinner" in dinner.plans_due_for_review("2026-10-18T15:00:00Z")
    # Nobody is reminded in the middle of the night; the next daytime pass picks it up.
    assert "friday_dinner" not in dinner.plans_due_for_review("2026-10-19T03:00:00Z")
    assert "friday_dinner" in dinner.plans_due_for_review("2026-10-19T03:00:00Z", quiet_hours=False)


def test_two_reminders_then_the_organizer_is_told_once_then_nothing(dinner):
    approve_all(dinner, dinner.identity.get("alice"), "friday_dinner")
    sent: list[dict[str, list[str]]] = []
    for stamp in ("2026-10-18T15:00:00Z", "2026-10-19T16:00:00Z", "2026-10-20T17:00:00Z"):
        at(dinner, stamp)
        assert dinner.plans_due_for_review() == ["friday_dinner"] or "friday_dinner" in dinner.plans_due_for_review()
        out = dinner.apply_review("friday_dinner", dinner.progress_report("friday_dinner"))
        sent.append({s["name"]: s["text"] for s in out["sent"]})
    assert sorted(sent[0]) == ["Alice", "Bob", "Chen", "Dana"] and sorted(sent[1]) == sorted(sent[0])
    assert sent[0]["Bob"].startswith("Check-in on Friday dinner: it has been waiting") and "a no helps just as much as a yes" in sent[0]["Bob"]
    assert "I reminded Bob, Chen and Dana." in sent[0]["Alice"]
    assert list(sent[2]) == ["Alice"] and "Bob, Chen and Dana have not answered after 2 reminders" in sent[2]["Alice"]
    at(dinner, "2026-10-21T18:00:00Z")
    assert "friday_dinner" not in dinner.plans_due_for_review()
    seen = dinner.review_plan("alice", plan="friday_dinner")
    assert "no more go out automatically" in seen["message"] and [x.split(" (")[0] for x in seen["no_answer_after_reminders"]] == ["Bob", "Chen", "Dana"]
    # Telling and reminding are not progress: the plan has been quiet since the organizer's answer the whole time.
    assert dinner.progress_report("friday_dinner")["quiet_for"] == "4 days"


def test_a_round_is_not_repeated_before_the_quiet_time_passes_again_and_real_progress_resets_it(dinner):
    at(dinner, "2026-10-18T15:00:00Z")
    dinner.apply_review("friday_dinner", dinner.progress_report("friday_dinner"))
    at(dinner, "2026-10-18T20:00:00Z")
    assert not dinner.progress_report("friday_dinner")["due"]
    approve_all(dinner, dinner.identity.get("chen"), "friday_dinner")
    report = dinner.progress_report("friday_dinner")
    assert not report["stuck"] and report["quiet_for"] == "less than an hour"


def test_the_organizer_can_turn_check_ins_off_and_only_the_organizer(dinner):
    assert code(lambda: dinner.review_plan("bob", plan="friday_dinner", check_ins="off")) == "FORBIDDEN"
    seen = dinner.review_plan("bob", plan="friday_dinner")
    assert seen["waiting"][0]["waiting_on"] == ["Alice", "you", "Chen", "Dana"] and "Waiting on: answers on the sent plan" in seen["message"]
    assert dinner.review_plan("alice", plan="friday_dinner", check_ins="off")["checkins"] == "off"
    assert "friday_dinner" not in dinner.plans_due_for_review("2026-10-18T15:00:00Z")
    assert dinner.review_plan("alice", plan="friday_dinner", check_ins="48h")["checkins"] == "after 48 quiet hours"
    assert "friday_dinner" not in dinner.plans_due_for_review("2026-10-18T15:00:00Z")
    assert "friday_dinner" in dinner.plans_due_for_review("2026-10-19T15:00:00Z")


def test_loose_ends_near_the_day_go_to_the_organizer_not_to_each_owner(gf):
    at(gf, "2026-10-18T15:00:00Z")  # the design review is on Tuesday morning
    report = gf.progress_report("design_review")
    assert report["blockers"] == [] and [b["label"] for b in report["loose_ends"]] == ["Share the design file before the meeting"]
    assert report["drafts"] == [] and [d["to"] for d in report["optional"]] == ["marcus"]
    out = gf.apply_review("design_review", report)
    assert [s["name"] for s in out["sent"]] == ["Priya"]
    assert '"Share the design file before the meeting" (Marcus)' in out["summary"]
    assert received(gf, "marcus", "nudge") == []


def test_a_finished_plan_is_never_chased(gf):
    gf.propose_change("charlie", item="party.date", value="Sunday, Oct 25, 6:00 pm", quote="can we do sunday instead")
    assert gf.progress_report("party", as_of="2026-10-20T15:00:00Z")["blockers"][0]["kind"] == "decide"
    # After the day only settling the shared costs is still asked for (Charlie owes for the cake and the room); once that is
    # settled, nothing is.
    after = gf.progress_report("party", as_of="2026-10-26T15:00:00Z")["blockers"]
    assert [b["kind"] for b in after] == ["settle"] and after[0]["waiting_on"] == ["charlie"]
    for to, amount in (("Alice", 45.32), ("Bob", 9.34)):
        gf.costs(to.lower(), "paid", plan="party", paid_by="Charlie", to="me", amount=amount)
    assert gf.progress_report("party", as_of="2026-10-26T15:00:00Z")["blockers"] == []
    assert "party" not in gf.plans_due_for_review("2026-10-26T15:00:00Z")


def test_check_ins_never_reach_anyone_outside_the_plan(gf):
    gf.propose_change("charlie", item="party.date", value="Sunday, Oct 25, 6:00 pm", quote="can we do sunday instead")
    at(gf, "2026-10-20T15:00:00Z")
    out = gf.apply_review("party", gf.progress_report("party"))
    assert [s["name"] for s in out["sent"]] == ["Alice"] and "Charlie's suggestion for Party date & time" in out["sent"][0]["text"]
    assert received(gf, "linda", "nudge") == []


# ---------------------------------------------------------------- a reviewing model: choose and word, within limits


def test_a_reviewing_model_can_only_choose_among_the_listed_people_and_its_words_are_checked(dinner):
    at(dinner, "2026-10-18T15:00:00Z")
    report = dinner.progress_report("friday_dinner")
    decision = {"stuck": True, "reason": "three answers missing and dinner is in five days",
                "nudges": [{"to": "bob", "text": "Hey Bob, still in for Friday dinner? A quick yes or no on your GroupFlow page helps everyone plan."},
                           {"to": "linda", "text": "Linda, your friends are planning a dinner."},
                           {"to": "chen", "text": "Chen, " + "please answer " * 40},
                           {"to": "bob", "text": "A second message for Bob."}],
                "organizer_summary": "Three answers are still missing, so I gave them a gentle nudge."}
    out = dinner.apply_review("friday_dinner", report, decision, source="model")
    sent = {s["to"]: s for s in out["sent"]}
    assert set(sent) == {"bob", "chen", "alice"}  # Linda is not in the plan; Dana was left out by the model; Alice gets the summary
    assert sent["bob"]["source"] == "model" and sent["bob"]["text"].startswith("Hey Bob, still in")
    assert sent["chen"]["source"] == "rules" and sent["chen"]["text"].startswith("Check-in on Friday dinner")  # too long: server wording
    assert out["summary"].endswith("Three answers are still missing, so I gave them a gentle nudge. I reminded Bob and Chen.")
    assert received(dinner, "linda", "nudge") == [] and received(dinner, "bob", "nudge") == [sent["bob"]["text"]]


def test_a_model_text_that_mentions_what_its_reader_cannot_see_falls_back_to_the_server_wording(gf):
    gf.propose_change("linda", item="Milk", plan="home", due="2026-10-16")
    at(gf, "2026-10-19T16:00:00Z")
    report = gf.progress_report("home")
    assert [(b["kind"], b["waiting_on"]) for b in report["blockers"]] == [("overdue", ["linda"])]
    decision = {"stuck": True, "reason": "overdue", "nudges": [{"to": "linda", "text": "Linda, milk for Linda's surprise party, please!"}]}
    out = gf.apply_review("home", report, decision, source="model")
    [message] = out["sent"]
    assert message["source"] == "rules" and "surprise" not in message["text"] and message["text"].startswith('Check-in on Home: "Milk", due')


def test_a_model_may_judge_the_quiet_fine_and_nobody_is_reminded(dinner):
    at(dinner, "2026-10-18T15:00:00Z")
    out = dinner.apply_review("friday_dinner", dinner.progress_report("friday_dinner"), {"stuck": False, "reason": "answers usually come Thursday"},
                              source="model")
    assert out["sent"] == [] and out["reviewed"] == "not stuck"
    assert all(received(dinner, m, "nudge") == [] for m in ("alice", "bob", "chen", "dana"))
    assert not dinner.progress_report("friday_dinner")["due"]  # reviewed this round; asked again after the next quiet stretch


def test_a_model_may_remind_the_owner_of_a_loose_end_the_rules_only_mention(gf):
    at(gf, "2026-10-18T15:00:00Z")
    report = gf.progress_report("design_review")
    decision = {"stuck": True, "reason": "the file is needed before Tuesday",
                "nudges": [{"to": "marcus", "text": "Hi Marcus, could you share the design file before Tuesday's review? If it's already out, just say so."}]}
    out = gf.apply_review("design_review", report, decision, source="model")
    assert [s["name"] for s in out["sent"]] == ["Marcus", "Priya"] and "I reminded Marcus." in out["summary"]
