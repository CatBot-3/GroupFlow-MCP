import json
from datetime import date

import pytest

from groupflow.core.errors import DomainError
from groupflow.core.identity import Member
from groupflow.projects.models import ProjectConfig, RecurrenceSpec
from groupflow.projects.recurrence import occurrences
from groupflow.projects.scheduler import validate_schedule


def project(gf, title="Release", **kwargs):
    return gf.create_plan("priya", "project", title, group="team", start_date="2026-10-19", days=42, **kwargs).plan.id


def work(gf, pid, title="Build", owner="marcus", **spec):
    return gf.upsert_work("priya", pid, spec, title=title, owner=owner)["item"]


def answer_all(gf, pid):
    for person in ("priya", "marcus", "jun", "sofia"):
        member = Member(person, gf.name(person))
        for item in gf.human_project_inbox(member)["items"]:
            if item["plan_id"] != pid or item["status"] in ("approved", "carried_forward"):
                continue
            ch = gf.human_project_challenge(member, item["id"])
            gf.human_project_decide(member, ch["challenge_id"], ch["nonce"], "approve")


def sent(gf, pid):
    assert gf.repair_project("priya", pid)["outcome"] == "proposed"
    return gf.request_confirmations("priya", plan=pid)


def test_parallel_work_has_no_global_travel_chain(gf):
    pid = project(gf)
    a = work(gf, pid, owner="marcus", duration_minutes=120)
    b = work(gf, pid, "Design", owner="sofia", duration_minutes=120)
    sent(gf, pid)
    schedule = {x["item_id"]: x for x in gf.get_project("priya", pid)["revision"]["schedule"]}
    assert schedule[a]["start"] == schedule[b]["start"]
    assert gf.commit_project("priya", pid)["outcome"] == "blocked"
    answer_all(gf, pid)
    assert gf.context('priya',plan=pid).plans[0].status == 'READY_TO_COMMIT'
    assert gf.commit_plan("priya", plan=pid)["outcome"] == "committed"
    assert gf.commit_plan("priya", plan=pid)["outcome"] == "already_committed"


@pytest.mark.parametrize("shared_owner,capacity", [(True, 2), (False, 1), (False, 2)])
def test_people_and_shared_resources_enforce_actual_capacity(gf, shared_owner, capacity):
    pid = project(gf)
    gf.configure_project("priya", pid, {"resources": [{"id": "team.lab", "name": "Lab", "capacity": capacity}]})
    work(gf, pid, owner="marcus", duration_minutes=120, resources={"team.lab": 1})
    work(gf, pid, "Test", owner="marcus" if shared_owner else "sofia", duration_minutes=120, resources={"team.lab": 1})
    sent(gf, pid)
    a, b = gf.get_project("priya", pid)["revision"]["schedule"]
    overlaps = a["start"] < b["end"] and b["start"] < a["end"]
    assert overlaps == (not shared_owner and capacity == 2)


def test_dependencies_cycles_and_milestones(gf):
    pid = project(gf)
    a = work(gf, pid)
    b = work(gf, pid, "Release gate", owner="priya", duration_minutes=0, depends_on=[a])
    with pytest.raises(DomainError):
        gf.upsert_work("priya", pid, {"depends_on": [b]}, item=a)
    assert gf.get_project("priya", pid)["work"][0]["spec"]["depends_on"] == []
    sent(gf, pid)
    s = {x["item_id"]: x for x in gf.get_project("priya", pid)["revision"]["schedule"]}
    assert s[b]["start"] >= s[a]["end"] and s[b]["start"] == s[b]["end"]


@pytest.mark.parametrize("spec", [{"duration_minutes": 481}, {"duration_minutes": 17}, {"due": "2026-10-01"},
                                  {"locked_start": "2026-10-19T09:00"}, {"resources": {"other.secret": 1}}])
def test_bad_work_is_rejected_atomically(gf, spec):
    pid = project(gf)
    before = gf.get_project("priya", pid)
    with pytest.raises(DomainError):
        work(gf, pid, **spec)
    assert gf.get_project("priya", pid)["total"] == before["total"]


def test_deadline_lock_and_budget_infeasibility_do_not_publish(gf):
    pid = project(gf)
    work(gf, pid, due="2026-10-19", locked_start="2026-10-19T23:00:00-04:00")
    assert gf.repair_project("priya", pid)["outcome"] == "infeasible"
    assert gf.get_project("priya", pid)["revision"] is None
    gf.configure_project("priya", pid, {"budget_minor": 10})
    work(gf, pid, "Paid work", cost_minor=11)
    assert gf.repair_project("priya", pid)["outcome"] == "infeasible"


def test_scope_challenges_cannot_be_proxied_replayed_or_reused_after_change(gf):
    pid = project(gf)
    iid = work(gf, pid)
    sent(gf, pid)
    marcus, jun = Member("marcus", "Marcus"), Member("jun", "Jun")
    scope = gf.human_project_inbox(marcus)["items"][0]
    with pytest.raises(DomainError, match="not yours"):
        gf.human_project_challenge(jun, scope["id"])
    ch = gf.human_project_challenge(marcus, scope["id"])
    gf.human_project_decide(marcus, ch["challenge_id"], ch["nonce"], "approve")
    with pytest.raises(DomainError) as err:
        gf.human_project_decide(marcus, ch["challenge_id"], ch["nonce"], "approve")
    assert err.value.code == "CHALLENGE_USED"
    ch2 = gf.human_project_challenge(marcus, scope["id"])
    gf.upsert_work("priya", pid, {"duration_minutes": 120}, item=iid)
    with pytest.raises(DomainError) as err:
        gf.human_project_decide(marcus, ch2["challenge_id"], ch2["nonce"], "approve")
    assert err.value.code == "STALE"


def test_commitment_carry_is_scoped_and_authorization_binds_whole_stage(gf):
    pid = project(gf)
    a = work(gf, pid, owner="marcus")
    b = work(gf, pid, "Design", owner="sofia")
    sent(gf, pid)
    answer_all(gf, pid)
    gf.commit_project("priya", pid)
    before = {s["item_id"]: s["start"] for s in gf.get_project("priya", pid)["revision"]["schedule"]}
    gf.upsert_work("priya", pid, {"duration_minutes": 120}, item=a)
    result = gf.repair_project("priya", pid)
    assert result["proof"]["objectives"][:2] == [0, 0]
    request = gf.request_confirmations("priya", plan=pid)
    checks = request["confirmations"]
    assert next(s for s in checks if s["item"] == b)["status"] == "carried_forward"
    assert next(s for s in checks if s["item"] == a)["status"] == "pending"
    assert next(s for s in checks if s["kind"] == "authorization")["status"] == "pending"
    assert {s["item_id"]: s["start"] for s in gf.get_project("priya", pid)["revision"]["schedule"]} == before
    assert gf.commit_project("priya", pid)["outcome"] == "blocked"


def test_decline_or_revoke_cannot_be_hidden_by_older_approval(gf):
    pid = project(gf)
    work(gf, pid)
    sent(gf, pid)
    answer_all(gf, pid)
    gf.commit_project("priya", pid)
    member = Member("marcus", "Marcus")
    scope = gf.human_project_inbox(member)["items"][0]
    gf.human_project_revoke(member, scope["id"])
    assert gf.commit_project("priya", pid)["outcome"] == "blocked"
    sent(gf, pid)
    state = next(s for s in gf.get_project("priya", pid)["revision"]["confirmations"] if s["person"] == "Marcus")
    assert state["status"] == "revoked"


def test_cross_project_reservations_recheck_at_atomic_commit(gf):
    a, b = project(gf, "A"), project(gf, "B")
    work(gf, a, owner="marcus", duration_minutes=120)
    work(gf, b, owner="marcus", duration_minutes=120)
    sent(gf, a); sent(gf, b)
    answer_all(gf, a); answer_all(gf, b)
    assert gf.commit_project("priya", a)["outcome"] == "committed"
    assert gf.commit_project("priya", b)["outcome"] == "blocked"
    assert gf.db.scalar("SELECT count(*) FROM resource_reservations WHERE plan_id=?", b) == 0
    sent(gf, b)
    answer_all(gf, b)
    assert gf.commit_project("priya", b)["outcome"] == "committed"


def test_private_calendars_affect_solver_without_disclosing_values(gf):
    pid = project(gf)
    work(gf, pid)
    gf.human_project_calendar(Member("marcus", "Marcus"), pid, {"start": "13:00", "end": "17:00"})
    sent(gf, pid)
    mine, other = gf.get_project("marcus", pid), gf.get_project("sofia", pid)
    assert mine["your_calendar"]["start"] == "13:00"
    assert "private_calendars" not in json.dumps(other)
    assert other["your_calendar"]["start"] == "09:00"
    assert other["revision"]["schedule"][0]["start"].endswith("17:00+00:00")


def test_workspace_roles_cannot_be_self_granted_and_are_rechecked(gf):
    pid = project(gf)
    member = Member("jun", "Jun")
    with pytest.raises(DomainError):
        gf.human_workspace_role(member, "team", "jun", "approver", True)
    with pytest.raises(DomainError):
        gf.configure_project("priya", pid, {"approvers": ["jun"]})
    gf.human_workspace_role(Member("priya", "Priya"), "team", "jun", "approver", True)
    gf.configure_project("priya", pid, {"approvers": ["jun"]})
    work(gf, pid)
    sent(gf, pid)
    answer_all(gf, pid)
    gf.human_workspace_role(Member("priya", "Priya"), "team", "jun", "approver", False)
    assert gf.commit_project("priya", pid)["outcome"] == "blocked"
    with pytest.raises(DomainError):
        gf.get_project("alice", pid)
    with pytest.raises(DomainError):
        work(gf, pid, owner="alice")


def test_recurrence_idempotent_independent_exceptions_and_rolling_window(gf):
    pid = project(gf, lifecycle="ongoing")
    created = gf.manage_recurrence("priya", pid, "create", title="Weekly report", owner="jun",
                                   rule={"dtstart": "2026-10-19", "frequency": "WEEKLY", "count": 10})
    assert created["count"] == 6
    assert gf.manage_recurrence("priya", pid, "expand", series=created["series"])["count"] == 0
    iid = created["created"][0]
    gf.upsert_work("priya", pid, {"not_before": "2026-10-20", "due": "2026-10-20"}, item=iid)
    gf.manage_recurrence("priya", pid, "expand", series=created["series"])
    original = next(w for w in gf.get_project("priya", pid)["work"] if w["id"] == iid)
    assert original["recurrence_id"] == "2026-10-19" and original["spec"]["due"] == "2026-10-20"
    sent(gf, pid); answer_all(gf, pid); gf.commit_project("priya", pid)
    gf.configure_project("priya", pid, {"window_start": "2026-11-30"})
    expanded = gf.manage_recurrence("priya", pid, "expand", series=created["series"])
    assert expanded["count"] == 4
    sent(gf, pid)
    assert all(s["status"] == "pending" for s in gf.get_project("priya", pid)["revision"]["confirmations"])


def test_dst_occurrences_preserve_local_work_time_with_different_utc_offsets(gf):
    pid = project(gf)
    gf.configure_project("priya", pid, {"calendar": {"weekdays": [6], "start": "09:00", "end": "17:00"}})
    gf.manage_recurrence("priya", pid, "create", title="Sunday review", owner="jun",
                         rule={"dtstart": "2026-10-25", "frequency": "WEEKLY", "count": 2})
    sent(gf, pid)
    schedule = sorted(gf.get_project("priya", pid)["revision"]["schedule"], key=lambda s: s["start"])
    assert schedule[0]["start"] == "2026-10-25T13:00+00:00"
    assert schedule[1]["start"] == "2026-11-01T14:00+00:00"


def test_recurrence_count_exdates_and_until():
    rule = RecurrenceSpec(dtstart=date(2026, 10, 19), count=3, exdates=[date(2026,10,26)])
    assert occurrences(rule, date(2026,10,1),date(2026,12,1)) == [date(2026,10,19),date(2026,11,2)]


def test_pagination_and_workstream_filter(gf):
    pid = project(gf)
    for i in range(15):
        work(gf, pid, str(i), workstream="Engineering" if i < 10 else "Design")
    page = gf.get_project("jun", pid, workstream="Engineering", limit=3)
    assert page["total"] == 10 and len(page["work"]) == 3 and page["next_offset"] == 3
    assert len(gf.context("jun", plan=pid).plans[0].tasks) == 12


def test_independent_validator_rejects_tampered_schedule(gf):
    pid = project(gf)
    work(gf, pid)
    sent(gf, pid)
    view = gf.get_project("priya", pid)
    schedule = view["revision"]["schedule"]
    schedule[0]["end"] = schedule[0]["start"]
    assert validate_schedule(schedule, gf._project_work(gf._plan_row(pid)), ProjectConfig.model_validate(view["config"]),
                             view["timezone"], view["window_start"], view["days"], {}, [])
