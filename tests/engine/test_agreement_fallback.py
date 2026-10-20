"""Independent personal agreement/fallback acceptance.

Cases were frozen before implementation inspection. The synthetic lab has
deliberately different scopes, prices and people from the demo scenario.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.core.identity import Person, seed_people
from groupflow.engine import GroupFlow, fixed_clock
from groupflow.world import approve_all


NOW = "2026-10-19T12:00:00Z"
EXPIRY = "2026-10-19T15:00:00Z"
LATER = "2026-10-19T16:00:00Z"
PLAN = "birch-supper"
GROUP = "birch-club"
SECRET = "PRIVATE-AGREEMENT-REVIEW-9ba27"
PEOPLE = [
    Person("nora", "Nora", "synthetic-nora-token", "synthetic-nora-pass", "#112233"),
    Person("amir", "Amir", "synthetic-amir-token", "synthetic-amir-pass", "#223344"),
    Person("bea", "Bea", "synthetic-bea-token", "synthetic-bea-pass", "#334455"),
    Person("eli", "Eli", "synthetic-eli-token", "synthetic-eli-pass", "#445566"),
    Person("owen", "Owen", "synthetic-owen-token", "synthetic-owen-pass", "#556677"),
]


@dataclass
class Lab:
    gf: GroupFlow
    first: str
    second: str
    third: str
    foreign: str


def make_lab(db: Db | None = None, *, whole_member: bool = False) -> Lab:
    gf = GroupFlow(db or Db(":memory:"), solver_budget_s=0.8, method="enumeration")
    gf.set_clock(fixed_clock(NOW))
    seed_people(gf.db, PEOPLE)
    gf.add_group(GROUP, "Birch club", "America/Toronto", "CAD", [
        {"id": "nora", "role": "organizer"},
        {"id": "amir", "confirmation_mode": "whole_plan" if whole_member else "scoped"},
        {"id": "bea"},
        {"id": "eli"},
    ])
    catalog = {
        "version": "synthetic-birch-1", "default_travel": 0,
        "travel_minutes": [["birch", "birch", 0]],
        "places": [
            {"id": f"birch_{letter}", "name": f"Birch {letter.upper()}", "kind": "meal", "zone": "birch",
             "open": "08:00", "close": "23:00", "capacity": 8, "price": amount,
             "tags": ["step_free", "vegetarian", "nut_free"]}
            for letter, amount in (("a", 11), ("b", 12), ("c", 13), ("d", 14))
        ],
    }
    for pid, title in ((PLAN, "Birch supper"), ("birch-other", "Birch other")):
        gf.create_plan("nora", "evening_out", title, group=GROUP, start_date="2026-10-25",
                       starter=False, plan_id=pid)
        gf.db.run("UPDATE plans SET catalog_json=? WHERE id=?", json.dumps(catalog), pid)
    first = gf.propose_change("nora", plan=PLAN, new_type="stop", title="Early tasting",
                              start="13:00", end="13:30", place="birch_a", participants=["nora", "amir", "bea"]).item
    second = gf.propose_change("nora", plan=PLAN, new_type="stop", title="Late tasting",
                               start="15:00", end="15:30", place="birch_a", participants=["nora", "amir"]).item
    third = gf.propose_change("nora", plan=PLAN, new_type="stop", title="Private pickup",
                              start="17:00", end="17:15", place="birch_a", participants=["bea"]).item
    foreign = gf.propose_change("nora", plan="birch-other", new_type="stop", title="Early tasting",
                                start="13:00", end="13:30", place="birch_a", participants=["nora", "amir"]).item
    gf.request_confirmations("nora", plan=PLAN)
    for mid in ("nora", "amir", "bea"):
        approve_all(gf, gf.identity.get(mid), PLAN)
    assert gf.commit_plan("nora", plan=PLAN).outcome == "committed"
    gf.reset_rate_limits()
    return Lab(gf, first, second, third, foreign)


@pytest.fixture
def lab():
    fixture = make_lab()
    try:
        yield fixture
    finally:
        fixture.gf.db.close()


def state(gf: GroupFlow) -> dict:
    """Full persisted state makes atomicity assertions independent of new tables."""
    return {
        table["name"]: sorted(json.dumps(r, sort_keys=True, default=str) for r in gf.db.all(
            'SELECT * FROM "' + table["name"].replace('"', '""') + '"'))
        for table in gf.db.all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    }


def denied(fn, codes=("FORBIDDEN", "NOT_FOUND", "INVALID_ARGUMENT")):
    with pytest.raises(DomainError) as caught:
        fn()
    assert caught.value.code in codes, str(caught.value)
    return caught.value


def activity_id(lab: Lab, item: str) -> str:
    """Read the preexisting domain representation, rather than guess item syntax."""
    row = lab.gf.db.get("SELECT * FROM items WHERE id=?", item)
    assert row is not None
    return item


def requirements(gf: GroupFlow) -> dict[tuple[str, str], str]:
    plan = gf._plan_row(PLAN)
    rev = gf._revision(plan["candidate_revision_id"] or plan["active_revision_id"])
    return {(scope["member_id"], scope["activity_id"]): status for scope, status, _ in gf._requirements(rev)}


def make_challenge(lab: Lab, who="amir", **overrides):
    args = {"activity": lab.first, "places": ["birch_b"], "expires_at": EXPIRY,
            "max_total_minor": 2500, "latest_end": "18:00"}
    args.update(overrides)
    return lab.gf.human_fallback_challenge(lab.gf.identity.get(who), PLAN, **args)


def grant(lab: Lab, who="amir", **overrides):
    ch = make_challenge(lab, who, **overrides)
    answer = lab.gf.human_fallback_decide(lab.gf.identity.get(who), ch["challenge_id"], ch["nonce"], "approve")
    return ch, answer


def use_branch(lab: Lab, place="birch_b", **changes):
    lab.gf.propose_change("nora", item=lab.first, plan=PLAN, place=place, **changes)
    lab.gf.request_confirmations("nora", plan=PLAN)
    return requirements(lab.gf)


def test_personal_comparison_has_readable_exact_own_scopes(lab):
    page = lab.gf.human_agreements(lab.gf.identity.get("amir"), PLAN)
    first, second, third = (activity_id(lab, x) for x in (lab.first, lab.second, lab.third))
    assert {x["activity_id"] for x in page["items"]} == {first, second}
    assert third not in {x["activity_id"] for x in page["items"]}
    assert page["version_token"] == lab.gf.state_token(PLAN)
    for item in page["items"]:
        assert item["after"] and item["why"]
        assert item["status"] in {"approved", "carried_forward"}
    assert "Birch A" in json.dumps(page) and "13:00" in json.dumps(page)
    denied(lambda: lab.gf.human_agreements(lab.gf.identity.get("owen"), PLAN))


def test_contest_is_own_scope_and_preserves_other_answers(lab):
    gf = lab.gf
    before = requirements(gf)
    result = gf.human_contest(gf.identity.get("amir"), PLAN, lab.first, SECRET, gf.state_token(PLAN))
    after = requirements(gf)
    target = ("amir", activity_id(lab, lab.first))
    assert result["outcome"] in {"contested", "withdrawn"}
    assert after[target] not in {"approved", "carried_forward", "fallback_approved"}
    assert {k: v for k, v in after.items() if k != target} == {k: v for k, v in before.items() if k != target}
    assert gf._plan_row(PLAN)["status"] != "COMMITTED"


def test_carried_source_is_withdrawn_without_revoking_unrelated_scopes(lab):
    gf = lab.gf
    gf.propose_change("nora", item=lab.first, plan=PLAN, place="birch_b")
    assert gf.request_confirmations("nora", plan=PLAN).carried_forward >= 3
    before = requirements(gf)
    target = ("amir", activity_id(lab, lab.second))
    assert before[target] == "carried_forward"
    source = gf.db.get("SELECT r.* FROM approval_carry_forwards c JOIN approval_receipts r ON r.id=c.receipt_id "
                       "JOIN approval_scopes s ON s.id=c.scope_id WHERE s.member_id='amir' AND s.activity_id=? "
                       "AND c.target_revision_id=?", target[1], gf._plan_row(PLAN)["candidate_revision_id"])
    assert source is not None
    gf.human_contest(gf.identity.get("amir"), PLAN, lab.second, SECRET, gf.state_token(PLAN))
    assert gf.db.scalar("SELECT status FROM approval_receipts WHERE id=?", source["id"]) != "valid"
    after = requirements(gf)
    assert after[target] not in {"approved", "carried_forward", "fallback_approved"}
    assert {k: v for k, v in after.items() if k != target} == {k: v for k, v in before.items() if k != target}


def test_contest_stale_and_foreign_targets_fail_atomically(lab):
    gf = lab.gf
    old = gf.state_token(PLAN)
    gf.propose_change("nora", item=lab.first, plan=PLAN, place="birch_b")
    before = state(gf)
    denied(lambda: gf.human_contest(gf.identity.get("amir"), PLAN, lab.first, SECRET, old), ("STALE",))
    assert state(gf) == before
    for who, target in (("owen", lab.first), ("amir", lab.third), ("amir", lab.foreign), ("amir", "missing")):
        before = state(gf)
        denied(lambda: gf.human_contest(gf.identity.get(who), PLAN, target, SECRET, gf.state_token(PLAN)))
        assert state(gf) == before


def test_contest_reason_is_personal_and_not_public_audit_or_context(lab):
    gf = lab.gf
    gf.human_contest(gf.identity.get("amir"), PLAN, lab.first, SECRET, gf.state_token(PLAN))
    assert SECRET in json.dumps(gf.human_agreements(gf.identity.get("amir"), PLAN))
    for other in ("nora", "bea", "eli"):
        assert SECRET not in json.dumps(gf.human_agreements(gf.identity.get(other), PLAN))
        assert SECRET not in gf.context(other, plan=PLAN, mark_read=False).model_dump_json()
    assert SECRET not in json.dumps(gf.db.all("SELECT * FROM audit WHERE private_to IS NULL"))
    assert SECRET not in json.dumps(gf.db.all("SELECT * FROM notifications WHERE member_id<>'amir'"))


def test_fallback_is_a_personal_nonce_bound_finite_preview(lab):
    gf = lab.gf
    ch = make_challenge(lab, places=["birch_b", "birch_c"])
    assert ch["challenge_id"] and ch["nonce"] and len(ch["branches"]) == 2
    shown = json.dumps(ch)
    assert "Birch B" in shown and "Birch C" in shown and "13:00" in shown
    for who, nonce in (("bea", ch["nonce"]), ("amir", "wrong-nonce")):
        before = state(gf)
        denied(lambda: gf.human_fallback_decide(gf.identity.get(who), ch["challenge_id"], nonce, "approve"))
        assert state(gf) == before
    result = gf.human_fallback_decide(gf.identity.get("amir"), ch["challenge_id"], ch["nonce"], "approve")
    assert result["authorization_id"]
    before = state(gf)
    denied(lambda: gf.human_fallback_decide(gf.identity.get("amir"), ch["challenge_id"], ch["nonce"], "approve"), ("CHALLENGE_USED",))
    assert state(gf) == before
    assert gf.human_fallbacks(gf.identity.get("bea"), PLAN)["authorizations"] == []
    denied(lambda: gf.human_fallbacks(gf.identity.get("owen"), PLAN))


def test_three_exact_fallback_branches_do_not_create_ordinary_approvals(lab):
    gf = lab.gf
    counts = {table: gf.db.scalar(f"SELECT count(*) FROM {table}") for table in
              ("plan_revisions", "approval_receipts", "repair_jobs", "executions", "confirmation_rounds")}
    token = gf.state_token(PLAN)
    challenge = make_challenge(lab, places=["birch_b", "birch_c", "birch_d"])
    assert len(challenge["branches"]) == 3
    assert gf.state_token(PLAN) == token
    gf.human_fallback_decide(gf.identity.get("amir"), challenge["challenge_id"], challenge["nonce"], "approve")
    assert gf.state_token(PLAN) != token
    assert {table: gf.db.scalar(f"SELECT count(*) FROM {table}") for table in counts} == counts


@pytest.mark.parametrize("bad", [
    {"places": []}, {"places": ["birch_a", "birch_b", "birch_c", "birch_d"]},
    {"places": ["birch_b", "birch_b"]}, {"places": ["not-a-venue"]},
    {"expires_at": "2026-10-19T11:00:00Z"}, {"max_total_minor": 2299}, {"latest_end": "12:45"},
])
def test_invalid_fallback_branches_and_bounds_create_nothing(lab, bad):
    before = state(lab.gf)
    denied(lambda: make_challenge(lab, **bad), ("INVALID_ARGUMENT", "INFEASIBLE", "EXPIRED", "NOT_FOUND"))
    assert state(lab.gf) == before


def test_fallback_challenge_rejects_stale_dependencies_atomically(lab):
    gf = lab.gf
    ch = make_challenge(lab)
    gf.propose_change("nora", item=lab.first, plan=PLAN, start="13:15", end="13:45")
    before = state(gf)
    denied(lambda: gf.human_fallback_decide(gf.identity.get("amir"), ch["challenge_id"], ch["nonce"], "approve"),
           ("STALE", "STALE_CANDIDATE"))
    assert state(gf) == before


def test_fallback_challenge_expiry_and_nonparticipant_fail_without_writes(lab):
    gf = lab.gf
    before = state(gf)
    denied(lambda: make_challenge(lab, "eli"), ("NOT_FOUND", "FORBIDDEN"))
    assert state(gf) == before
    denied(lambda: make_challenge(lab, activity=lab.foreign), ("NOT_FOUND", "FORBIDDEN"))
    assert state(gf) == before
    ch = make_challenge(lab)
    gf.set_clock(fixed_clock("2026-10-19T12:11:00Z"))
    before = state(gf)
    denied(lambda: gf.human_fallback_decide(gf.identity.get("amir"), ch["challenge_id"], ch["nonce"], "approve"),
           ("CHALLENGE_EXPIRED",))
    assert state(gf) == before


def test_exact_fallback_satisfies_only_own_activity_and_normal_gate(lab):
    gf = lab.gf
    grant(lab)
    before = requirements(gf)
    after = use_branch(lab)
    own = ("amir", activity_id(lab, lab.first))
    assert after[own] == "fallback_approved"
    assert after[("amir", activity_id(lab, lab.second))] == "carried_forward"
    assert after[("nora", own[1])] == "pending" and after[("bea", own[1])] == "pending"
    assert gf.commit_plan("nora", plan=PLAN).outcome == "blocked"
    for who in ("nora", "bea"):
        approve_all(gf, gf.identity.get(who), PLAN)
    assert requirements(gf)[own] == "fallback_approved"
    assert gf.commit_plan("nora", plan=PLAN).outcome == "committed"
    assert len(gf.human_fallbacks(gf.identity.get("amir"), PLAN)["authorizations"]) == 1
    assert len(before) == len(after)


def test_unrelated_activity_and_other_plan_changes_preserve_exact_permission(lab):
    gf = lab.gf
    grant(lab)
    gf.propose_change("nora", item=lab.foreign, plan="birch-other", place="birch_c")
    gf.propose_change("nora", item=lab.third, plan=PLAN, place="birch_c")
    assert use_branch(lab)[("amir", activity_id(lab, lab.first))] == "fallback_approved"


def test_other_participants_new_hard_limit_blocks_a_matching_fallback(lab):
    gf = lab.gf
    grant(lab)
    target = ("amir", activity_id(lab, lab.first))
    assert use_branch(lab)[target] == "fallback_approved"
    gf.human_set_condition(gf.identity.get("bea"), "budget", {"amount": "10"}, PLAN)
    assert requirements(gf)[target] != "fallback_approved"
    assert gf.commit_plan("nora", plan=PLAN).outcome == "blocked"


def test_new_price_invalidates_previously_matching_candidate_without_new_edit(lab):
    gf = lab.gf
    grant(lab)
    target = ("amir", activity_id(lab, lab.first))
    assert use_branch(lab)[target] == "fallback_approved"
    gf.inject_observation(GROUP, "price_changed", {"place_id": "birch_b", "price_minor": 1250}, "synthetic", "birch-price-later")
    assert requirements(gf)[target] != "fallback_approved"
    assert gf.commit_plan("nora", plan=PLAN).outcome == "blocked"


@pytest.mark.parametrize("change", ["venue", "time", "participants", "price", "condition", "feasible_condition", "membership"])
def test_permission_cannot_match_changed_exact_branch_dependencies(lab, change):
    gf = lab.gf
    grant(lab)
    if change == "venue":
        use_branch(lab, "birch_c")
    elif change == "time":
        use_branch(lab, start="13:15", end="13:45")
    elif change == "participants":
        use_branch(lab, participants=["nora", "amir", "bea", "eli"])
    elif change == "price":
        gf.inject_observation(GROUP, "price_changed", {"place_id": "birch_b", "price_minor": 1250}, "synthetic", "birch-price-1")
        use_branch(lab)
    elif change == "condition":
        gf.human_set_condition(gf.identity.get("amir"), "budget", {"amount": "22"}, PLAN)
        gf.propose_change("nora", item=lab.first, plan=PLAN, place="birch_b")
        # An infeasible candidate may correctly refuse to open a round.
        try:
            gf.request_confirmations("nora", plan=PLAN)
        except DomainError as exc:
            assert exc.code in {"INFEASIBLE", "BLOCKED"}
    elif change == "feasible_condition":
        gf.human_set_condition(gf.identity.get("amir"), "budget", {"amount": "100"}, PLAN)
        use_branch(lab)
    else:
        gf.leave_group(gf.identity.get("eli"), GROUP)
        use_branch(lab)
    assert requirements(gf).get(("amir", activity_id(lab, lab.first))) != "fallback_approved"
    assert gf.commit_plan("nora", plan=PLAN).outcome != "committed"


def test_decline_expiry_and_owner_revocation_stop_fallback(lab):
    gf = lab.gf
    ch = make_challenge(lab)
    gf.human_fallback_decide(gf.identity.get("amir"), ch["challenge_id"], ch["nonce"], "decline")
    assert use_branch(lab)[("amir", activity_id(lab, lab.first))] != "fallback_approved"
    # A fresh lab removes any ambiguity about a challenge on a changed candidate.
    other = make_lab()
    try:
        _, approved = grant(other)
        own = ("amir", activity_id(other, other.first))
        assert use_branch(other)[own] == "fallback_approved"
        before = state(other.gf)
        denied(lambda: other.gf.human_fallback_revoke(other.gf.identity.get("bea"), approved["authorization_id"]))
        assert state(other.gf) == before
        other.gf.set_clock(fixed_clock(LATER))
        assert requirements(other.gf)[own] != "fallback_approved"
        other.gf.set_clock(fixed_clock(NOW))
        other.gf.human_fallback_revoke(other.gf.identity.get("amir"), approved["authorization_id"])
        assert requirements(other.gf)[own] != "fallback_approved"
        before = state(other.gf)
        again = other.gf.human_fallback_revoke(other.gf.identity.get("amir"), approved["authorization_id"])
        assert again["outcome"] in {"already_revoked", "revoked"}
        assert state(other.gf) == before
    finally:
        other.gf.db.close()


@pytest.mark.parametrize("committed", [False, True])
def test_revoking_old_fallback_preserves_a_newer_exact_personal_approval(lab, committed):
    gf = lab.gf
    _, approved = grant(lab)
    target = ("amir", activity_id(lab, lab.first))
    assert use_branch(lab)[target] == "fallback_approved"
    scope = gf.db.get("SELECT id FROM approval_scopes WHERE revision_id=? AND member_id='amir' AND activity_id=?",
                      gf._plan_row(PLAN)["candidate_revision_id"], target[1])
    challenge = gf.human_challenge(gf.identity.get("amir"), scope["id"])
    gf.human_decide(gf.identity.get("amir"), challenge["challenge_id"], challenge["nonce"], "approved")
    for who in ("nora", "bea"):
        approve_all(gf, gf.identity.get(who), PLAN)
    assert requirements(gf)[target] == "approved"
    if committed:
        assert gf.commit_plan("nora", plan=PLAN).outcome == "committed"
    statuses = requirements(gf)
    gf.human_fallback_revoke(gf.identity.get("amir"), approved["authorization_id"])
    assert requirements(gf) == statuses
    assert gf._plan_row(PLAN)["status"] == ("COMMITTED" if committed else "READY_TO_COMMIT")


@pytest.mark.parametrize("answer", ["rejected", "contest"])
def test_fallback_cannot_bypass_an_explicit_rejection_or_contest(lab, answer):
    gf = lab.gf
    grant(lab)
    target = ("amir", activity_id(lab, lab.first))
    assert use_branch(lab)[target] == "fallback_approved"
    if answer == "contest":
        gf.human_contest(gf.identity.get("amir"), PLAN, lab.first, SECRET, gf.state_token(PLAN))
    else:
        plan = gf._plan_row(PLAN)
        scope = gf.db.get("SELECT id FROM approval_scopes WHERE revision_id=? AND member_id='amir' AND activity_id=?",
                          plan["candidate_revision_id"], target[1])
        ch = gf.human_challenge(gf.identity.get("amir"), scope["id"])
        gf.human_decide(gf.identity.get("amir"), ch["challenge_id"], ch["nonce"], "rejected", reason=SECRET)
    assert requirements(gf)[target] != "fallback_approved"
    for who in ("nora", "bea"):
        approve_all(gf, gf.identity.get(who), PLAN)
    assert gf.commit_plan("nora", plan=PLAN).outcome == "blocked"


@pytest.mark.parametrize("answer", ["rejected", "contest"])
def test_refusal_cannot_be_bypassed_by_a_later_revision_with_same_terms(lab, answer):
    gf = lab.gf
    grant(lab)
    target = ("amir", activity_id(lab, lab.first))
    assert use_branch(lab)[target] == "fallback_approved"
    if answer == "contest":
        gf.human_contest(gf.identity.get("amir"), PLAN, lab.first, SECRET, gf.state_token(PLAN))
    else:
        scope = gf.db.get("SELECT id FROM approval_scopes WHERE revision_id=? AND member_id='amir' AND activity_id=?",
                          gf._plan_row(PLAN)["candidate_revision_id"], target[1])
        challenge = gf.human_challenge(gf.identity.get("amir"), scope["id"])
        gf.human_decide(gf.identity.get("amir"), challenge["challenge_id"], challenge["nonce"], "rejected")
    assert requirements(gf)[target] != "fallback_approved"
    # Editing somebody else's stop creates a new revision with exactly the same
    # declined personal terms. That must not revive earlier broad permission.
    gf.propose_change("nora", item=lab.third, plan=PLAN, place="birch_c")
    gf.request_confirmations("nora", plan=PLAN)
    assert requirements(gf)[target] != "fallback_approved"


def test_new_personal_answer_resolves_only_own_matching_contest(lab):
    gf = lab.gf
    use_branch(lab)
    gf.human_contest(gf.identity.get("amir"), PLAN, lab.first, SECRET, gf.state_token(PLAN))
    gf.human_contest(gf.identity.get("amir"), PLAN, lab.second, SECRET + "-other", gf.state_token(PLAN))
    target = activity_id(lab, lab.first)
    scope = gf.db.get("SELECT id FROM approval_scopes WHERE revision_id=? AND member_id='amir' AND activity_id=?",
                      gf._plan_row(PLAN)["candidate_revision_id"], target)
    ch = gf.human_challenge(gf.identity.get("amir"), scope["id"])
    # A malformed answer must not accidentally clear either objection.
    before = state(gf)
    denied(lambda: gf.human_decide(gf.identity.get("amir"), ch["challenge_id"], "wrong", "approved"))
    assert state(gf) == before
    gf.human_decide(gf.identity.get("amir"), ch["challenge_id"], ch["nonce"], "approved")
    statuses = requirements(gf)
    assert statuses[("amir", target)] == "approved"
    assert statuses[("amir", activity_id(lab, lab.second))] not in {"approved", "carried_forward", "fallback_approved"}


def test_fallback_cannot_satisfy_an_expired_verified_fact(lab):
    gf = lab.gf
    grant(lab)
    assert use_branch(lab)[("amir", activity_id(lab, lab.first))] == "fallback_approved"
    gf.inject_observation(GROUP, "price_changed", {"place_id": "birch_b", "price_minor": 1200}, "synthetic-price-source",
                          "same-price-expiring", valid_until="2026-10-19T12:05:00Z")
    assert requirements(gf)[("amir", activity_id(lab, lab.first))] == "fallback_approved"
    for who in ("nora", "bea"):
        approve_all(gf, gf.identity.get(who), PLAN)
    gf.set_clock(fixed_clock("2026-10-19T12:06:00Z"))
    assert requirements(gf)[("amir", activity_id(lab, lab.first))] != "fallback_approved"
    assert gf.commit_plan("nora", plan=PLAN).outcome == "blocked"


def test_fallback_approved_last_updates_readiness_for_the_exact_open_scope(lab):
    gf = lab.gf
    use_branch(lab)
    for who in ("nora", "bea"):
        approve_all(gf, gf.identity.get(who), PLAN)
    own = ("amir", activity_id(lab, lab.first))
    assert requirements(gf)[own] == "pending"
    grant(lab)
    assert requirements(gf)[own] == "fallback_approved"
    assert gf._plan_row(PLAN)["status"] == "READY_TO_COMMIT"
    assert gf.commit_plan("nora", plan=PLAN).outcome == "committed"


@pytest.mark.parametrize("committed", [False, True])
def test_expired_fallback_denies_immediately_and_reconciles_public_status(lab, committed):
    gf = lab.gf
    grant(lab)
    use_branch(lab)
    for who in ("nora", "bea"):
        approve_all(gf, gf.identity.get(who), PLAN)
    assert gf._plan_row(PLAN)["status"] == "READY_TO_COMMIT"
    if committed:
        assert gf.commit_plan("nora", plan=PLAN).outcome == "committed"
    before_requirements = requirements(gf)
    unrelated = gf._plan_row("birch-other")
    gf.set_clock(fixed_clock(LATER))
    target = ("amir", activity_id(lab, lab.first))
    assert requirements(gf)[target] != "fallback_approved"
    if not committed:
        assert gf.commit_plan("nora", plan=PLAN).outcome == "blocked"
    assert gf.reassess_coordination() == [PLAN]
    assert gf._plan_row(PLAN)["status"] == ("DEGRADED" if committed else "AWAITING_CONFIRMATIONS")
    after_requirements = requirements(gf)
    assert {k: v for k, v in before_requirements.items() if k != target} == {
        k: v for k, v in after_requirements.items() if k != target}
    assert gf._plan_row("birch-other") == unrelated
    before = state(gf)
    assert gf.reassess_coordination() == []
    assert state(gf) == before


def test_expired_fallback_dependency_is_reconciled_after_persisted_restart(tmp_path):
    path = tmp_path / "birch-expired-commit.db"
    fixture = make_lab(Db(str(path)))
    grant(fixture)
    use_branch(fixture)
    for who in ("nora", "bea"):
        approve_all(fixture.gf, fixture.gf.identity.get(who), PLAN)
    assert fixture.gf.commit_plan("nora", plan=PLAN).outcome == "committed"
    fixture.gf.db.close()
    reopened = GroupFlow(Db(str(path)))
    reopened.set_clock(fixed_clock(LATER))
    try:
        assert reopened.reassess_coordination() == [PLAN]
        assert reopened._plan_row(PLAN)["status"] == "DEGRADED"
        assert reopened.context("nora", plan=PLAN, mark_read=False).plans[0].status == "DEGRADED"
    finally:
        reopened.db.close()


def test_whole_plan_mode_refuses_activity_fallback_permission():
    fixture = make_lab(whole_member=True)
    try:
        before = state(fixture.gf)
        denied(lambda: make_challenge(fixture), ("INVALID_ARGUMENT", "UNSUPPORTED", "FORBIDDEN"))
        assert state(fixture.gf) == before
    finally:
        fixture.gf.db.close()


def test_whole_plan_contest_withdraws_only_the_actors_whole_plan_scope():
    fixture = make_lab(whole_member=True)
    try:
        before = requirements(fixture.gf)
        own_keys = [key for key in before if key[0] == "amir"]
        assert len(own_keys) == 1 and before[own_keys[0]] == "approved"
        fixture.gf.human_contest(fixture.gf.identity.get("amir"), PLAN, fixture.first, SECRET,
                                fixture.gf.state_token(PLAN))
        after = requirements(fixture.gf)
        assert after[own_keys[0]] not in {"approved", "carried_forward", "fallback_approved"}
        assert {key: value for key, value in before.items() if key[0] != "amir"} == {
            key: value for key, value in after.items() if key[0] != "amir"}
    finally:
        fixture.gf.db.close()


def test_persisted_grants_contests_and_revoke_survive_database_reopen(tmp_path):
    path = tmp_path / "birch.db"
    fixture = make_lab(Db(str(path)))
    _, approved = grant(fixture)
    assert use_branch(fixture)[("amir", activity_id(fixture, fixture.first))] == "fallback_approved"
    first, second, third, foreign = fixture.first, fixture.second, fixture.third, fixture.foreign
    fixture.gf.human_contest(fixture.gf.identity.get("amir"), PLAN, second, SECRET, fixture.gf.state_token(PLAN))
    fixture.gf.db.close()
    reopened = GroupFlow(Db(str(path)), solver_budget_s=0.8, method="enumeration")
    reopened.set_clock(fixed_clock(NOW))
    fixture = Lab(reopened, first, second, third, foreign)
    try:
        assert requirements(reopened)[("amir", activity_id(fixture, first))] == "fallback_approved"
        assert requirements(reopened)[("amir", activity_id(fixture, second))] not in {"approved", "carried_forward"}
        assert SECRET in json.dumps(reopened.human_agreements(reopened.identity.get("amir"), PLAN))
        assert reopened.human_fallbacks(reopened.identity.get("amir"), PLAN)["authorizations"]
        reopened.human_fallback_revoke(reopened.identity.get("amir"), approved["authorization_id"])
    finally:
        reopened.db.close()
    reopened = GroupFlow(Db(str(path)))
    reopened.set_clock(fixed_clock(NOW))
    try:
        assert requirements(reopened)[("amir", first)] != "fallback_approved"
    finally:
        reopened.db.close()


def test_one_use_challenge_and_decline_survive_database_reopen(tmp_path):
    path = tmp_path / "birch-challenge.db"
    fixture = make_lab(Db(str(path)))
    challenge = make_challenge(fixture)
    fixture.gf.db.close()
    reopened = GroupFlow(Db(str(path)))
    reopened.set_clock(fixed_clock(NOW))
    try:
        answered = reopened.human_fallback_decide(reopened.identity.get("amir"), challenge["challenge_id"], challenge["nonce"], "decline")
        assert answered["outcome"] == "declined"
        assert reopened.human_fallbacks(reopened.identity.get("amir"), PLAN)["authorizations"] == []
    finally:
        reopened.db.close()
    reopened = GroupFlow(Db(str(path)))
    reopened.set_clock(fixed_clock(NOW))
    try:
        before = state(reopened)
        denied(lambda: reopened.human_fallback_decide(reopened.identity.get("amir"), challenge["challenge_id"], challenge["nonce"], "approve"),
               ("CHALLENGE_USED",))
        assert state(reopened) == before
    finally:
        reopened.db.close()


def test_no_mcp_tool_or_guarded_action_can_grant_fallback_permission(lab):
    from groupflow.mcp_server.tools import NAMES

    assert len(NAMES) == 35
    assert not any(part in name for name in NAMES for part in ("fallback_approve", "human_fallback", "approve_for"))
    before = state(lab.gf)
    denied(lambda: lab.gf.apply_action("amir", "human_fallback_decide", {
        "plan": PLAN, "challenge_id": "fake", "nonce": "fake", "decision": "approve",
    }, "not-a-consent-capability", lab.gf.state_token(PLAN)), ("INVALID_ARGUMENT", "FORBIDDEN", "UNKNOWN_TOOL"))
    assert state(lab.gf) == before
