"""Independent local-time contract. Cases were frozen before implementation review.

The legacy-row edits below deliberately bypass entry to exercise persisted bad
snapshots independently of the new entry validator.
"""
import json

import pytest

from groupflow.core.errors import DomainError
from groupflow.engine.agreement import snapshot_from_dict, snapshot_to_dict
from groupflow.repair.local_time import validate_interval
from groupflow.repair.model import Activity, Catalog, Problem, Snapshot
from groupflow.repair.validate import violations
from groupflow.world import approve_all


ZONE = "America/Toronto"


def own_plan(gf, day="2026-11-01", days=2):
    return gf.create_plan("alice", "general_event", "Independent clock test",
                          group="friends", start_date=day, days=days,
                          audience=["alice"], starter=False).plan.id


def add_activity(gf, plan, **kw):
    return gf.propose_change("alice", plan=plan, new_type="activity",
                             title="Clock check", start="12:00", end="13:00", **kw)


def snapshot(gf, plan):
    p = gf._plan_row(plan)
    return gf._snapshot(gf._working_rev(p))


def mutate_revision(gf, revision_id, **changes):
    row = gf.db.get("SELECT snapshot_json FROM plan_revisions WHERE id=?", revision_id)
    raw = json.loads(row["snapshot_json"])
    raw["activities"][0].update(changes)
    gf.db.run("UPDATE plan_revisions SET snapshot_json=? WHERE id=?", json.dumps(raw), revision_id)


def valid_activity(gf, day="2026-11-01"):
    plan = own_plan(gf, day)
    add_activity(gf, plan)
    return plan


@pytest.mark.parametrize("day,start,end,folds", [
    ("2026-03-08", "02:30", "03:30", {}),
    ("2026-11-01", "01:15", "02:30", {}),
    ("2026-11-01", "00:30", "01:30", {}),
    ("2026-11-01", "01:45", "01:15", {"start_fold": 1, "end_fold": 0}),
    ("2026-11-01", "01:30", "02:30", {"start_fold": 2}),
])
def test_entry_rejects_gap_ambiguity_and_utc_negative_atomically(gf, day, start, end, folds):
    """T01-T03/T05: no published item or revision from invalid input."""
    plan = own_plan(gf, day)
    before = {t: gf.db.scalar(f"SELECT count(*) FROM {t}") for t in ("items", "plan_revisions", "audit")}
    with pytest.raises(DomainError) as exc:
        gf.propose_change("alice", plan=plan, new_type="activity", title="Invalid clock",
                          start=start, end=end, **folds)
    assert exc.value.code == "INVALID_EVENT_TIME"
    assert {t: gf.db.scalar(f"SELECT count(*) FROM {t}") for t in before} == before


@pytest.mark.parametrize("start_fold,expected", [(0, "20261101T053000Z"), (1, "20261101T063000Z")])
def test_explicit_start_fold_survives_entry_confirmation_commit_and_calendar(gf, start_fold, expected):
    """T04/T13: each repeated occurrence must retain its distinct UTC instant."""
    plan = own_plan(gf)
    gf.propose_change("alice", plan=plan, new_type="activity", title="Repeated hour",
                      start="01:30", end="02:30", start_fold=start_fold)
    assert snapshot(gf, plan).activities[0].start_fold == start_fold
    assert gf.request_confirmations("alice", plan=plan).outcome == "requested"
    approve_all(gf, gf.identity.get("alice"), plan)
    assert gf.commit_plan("alice", plan=plan).outcome == "committed"
    link = gf.issue_calendar("alice")
    calendar = gf.calendar_feed(link["url"].split("/")[-1][:-4])
    assert f"DTSTART:{expected}" in calendar
    assert "DTEND:20261101T073000Z" in calendar


@pytest.mark.parametrize("end_fold,expected", [(0, "20261101T053000Z"), (1, "20261101T063000Z")])
def test_explicit_end_fold_survives_calendar(gf, end_fold, expected):
    plan = own_plan(gf)
    gf.propose_change("alice", plan=plan, new_type="activity", title="Repeated end",
                      start="00:30", end="01:30", end_fold=end_fold)
    assert gf.request_confirmations("alice", plan=plan).outcome == "requested"
    approve_all(gf, gf.identity.get("alice"), plan)
    assert gf.commit_plan("alice", plan=plan).outcome == "committed"
    link = gf.issue_calendar("alice")
    assert f"DTEND:{expected}" in gf.calendar_feed(link["url"].split("/")[-1][:-4])


def test_positive_utc_repeated_interval_with_backwards_wall_clock(gf):
    """T06: the elapsed interval is 30 minutes although wall end is earlier."""
    plan = own_plan(gf)
    result = gf.propose_change("alice", plan=plan, new_type="activity", title="Repeated crossing",
                               start="01:45", end="01:15", start_fold=0, end_fold=1)
    assert result.outcome == "draft_updated"
    assert result.problems == []
    assert gf.request_confirmations("alice", plan=plan).outcome == "requested"
    approve_all(gf, gf.identity.get("alice"), plan)
    assert gf.commit_plan("alice", plan=plan).outcome == "committed"


def test_public_cross_midnight_with_explicit_end_day(gf):
    """T07: explicit dates disambiguate an overnight interval."""
    plan = own_plan(gf, "2026-03-07")
    result = gf.propose_change("alice", plan=plan, new_type="activity", title="Overnight",
                               day="2026-03-07", start="23:30", end="01:30", end_day="2026-03-08")
    assert result.outcome == "draft_updated"
    activity = snapshot(gf, plan).activities[0]
    assert (activity.start, activity.end) == (1410, 1530)
    assert result.problems == []


def test_public_cross_midnight_rejects_next_day_gap(gf):
    """T08: checking only the start date misses the following day's DST gap."""
    plan = own_plan(gf, "2026-03-07")
    with pytest.raises(DomainError) as exc:
        gf.propose_change("alice", plan=plan, new_type="activity", title="Overnight gap",
                          day="2026-03-07", start="23:30", end="02:30", end_day="2026-03-08")
    assert exc.value.code == "INVALID_EVENT_TIME"
    assert gf._working_rev(gf._plan_row(plan)) is None
    assert gf.db.scalar("SELECT count(*) FROM items WHERE plan_id=? AND shape='activity'", plan) == 0


@pytest.mark.parametrize("day,start,end,start_fold,end_fold,expected", [
    ("2026-03-08", 150, 210, None, None, False),
    ("2026-11-01", 75, 150, None, None, False),
    ("2026-11-01", 30, 90, None, None, False),
    ("2026-11-01", 105, 75, 1, 0, False),
    ("2026-11-01", 105, 75, 0, 1, True),
    ("2026-11-01", 90, 150, 0, None, True),
    ("2026-11-01", 90, 150, 1, None, True),
    ("2026-03-07", 1410, 1530, None, None, True),
    ("2026-03-07", 1410, 1590, None, None, False),
])
def test_candidate_and_shared_validator_agree(day, start, end, start_fold, end_fold, expected):
    """T05/T06/T09/T13: independent full-candidate validation shares semantics."""
    a = Activity("clock", "Clock", "activity", start, end, None, ("alice",), (("alice", 0),),
                 start_fold=start_fold, end_fold=end_fold)
    s = Snapshot("clock-plan", day, 2, ZONE, "CAD", (a,))
    problem = Problem(s, Catalog({}, {}), ())
    time_errors = [v for v in violations(problem, s) if v.code == "INVALID_EVENT_TIME"]
    assert not time_errors if expected else time_errors
    if expected:
        lo, hi = validate_interval(day, start, end, ZONE, start_fold, end_fold)
        assert hi > lo
        assert snapshot_from_dict(snapshot_to_dict(s)) == s
    else:
        with pytest.raises(DomainError, match="."):
            validate_interval(day, start, end, ZONE, start_fold, end_fold)


@pytest.mark.parametrize("day,start,end", [("2026-03-08", 150, 210), ("2026-11-01", 75, 150)])
def test_invalid_legacy_snapshot_cannot_request_confirmations(gf, day, start, end):
    """T10: legacy invalid rows never become approval requests."""
    plan = valid_activity(gf, day)
    rev = gf._plan_row(plan)["draft_revision_id"]
    mutate_revision(gf, rev, start=start, end=end)
    approvals = gf.db.scalar("SELECT count(*) FROM approval_receipts")
    rounds = gf.db.scalar("SELECT count(*) FROM confirmation_rounds")
    result = gf.request_confirmations("alice", plan=plan)
    assert result.outcome == "infeasible"
    assert gf.db.scalar("SELECT count(*) FROM approval_receipts") == approvals
    assert gf.db.scalar("SELECT count(*) FROM confirmation_rounds") == rounds
    assert not gf._plan_row(plan)["candidate_revision_id"]


def test_invalid_candidate_is_blocked_on_human_confirmation(gf):
    """T10: corruption after challenge issuance is revalidated at answer time."""
    plan = valid_activity(gf)
    gf.request_confirmations("alice", plan=plan)
    alice = gf.identity.get("alice")
    pending = next(x for x in gf.human_inbox(alice)["pending"] if x["plan_id"] == plan)
    challenge = gf.human_challenge(alice, pending["scope_id"])
    revision = gf._plan_row(plan)["candidate_revision_id"]
    mutate_revision(gf, revision, start=75, end=150)
    before = gf.db.scalar("SELECT count(*) FROM approval_receipts")
    with pytest.raises(DomainError) as exc:
        gf.human_decide(alice, challenge["challenge_id"], challenge["nonce"], "approved")
    assert exc.value.code in {"INVALID_EVENT_TIME", "INFEASIBLE", "STALE_CANDIDATE"}
    assert gf.db.scalar("SELECT count(*) FROM approval_receipts") == before


def test_invalid_approved_candidate_cannot_commit(gf):
    """T11: validating entry and confirmation alone is insufficient."""
    plan = valid_activity(gf)
    gf.request_confirmations("alice", plan=plan)
    approve_all(gf, gf.identity.get("alice"), plan)
    mutate_revision(gf, gf._plan_row(plan)["candidate_revision_id"], start=75, end=150)
    result = gf.commit_plan("alice", plan=plan)
    assert result.outcome == "blocked"
    assert not gf._plan_row(plan)["active_revision_id"]
    assert gf.db.scalar("SELECT count(*) FROM calendar_events WHERE plan_id=?", plan) == 0


def test_invalid_active_snapshot_is_rejected_before_calendar_publication(gf):
    """T12: legacy snapshots must not be silently dropped or normalized."""
    plan = valid_activity(gf)
    gf.request_confirmations("alice", plan=plan)
    approve_all(gf, gf.identity.get("alice"), plan)
    assert gf.commit_plan("alice", plan=plan).outcome == "committed"
    mutate_revision(gf, gf._plan_row(plan)["active_revision_id"], start=75, end=150)
    link = gf.issue_calendar("alice")
    before = gf.db.all("SELECT * FROM calendar_events")
    with pytest.raises(DomainError) as exc:
        gf.calendar_feed(link["url"].split("/")[-1][:-4])
    assert exc.value.code == "INVALID_EVENT_TIME"
    assert gf.db.all("SELECT * FROM calendar_events") == before
