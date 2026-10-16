"""Activities under the participants policy (the evening_out template): confirmations on each person's own page,
carry-forward, the commit gate, repairs and privacy of conditions."""
import json
import re

from groupflow.world import approve_all
from tests.conftest import code, plan_view, seeded

FRIENDS = ("alice", "bob", "chen", "dana")


def confirm_everyone(gf, plan="friday_dinner", members=FRIENDS, decision="approved"):
    for m in members:
        approve_all(gf, gf.identity.get(m), plan, decision)


def degraded():
    return seeded({"friday_dinner": "degraded"})


def test_the_seeded_dinner_starts_agreed():
    gf = seeded({"friday_dinner": "committed"})
    v = plan_view(gf, "dana", "friday_dinner")
    assert v.status == "COMMITTED" and v.agreed.label == "P1"
    assert [a.place for a in v.agreed.activities] == ["Central Hub", "Restaurant A", "Dessert Shop D", "Shared ride home"]
    assert v.agreed.your_total == "CAD 41.00"


def test_draft_to_agreed_through_each_persons_own_page():
    gf = seeded({"friday_dinner": "draft"})
    r = gf.request_confirmations("alice", plan="friday_dinner")
    assert r.outcome == "requested" and r.version == "P1" and r.waiting_for == ["Alice", "Bob", "Chen", "Dana"]
    assert gf.commit_plan("alice", plan="friday_dinner").outcome == "blocked"
    confirm_everyone(gf, members=("alice", "bob", "chen"))
    blocked = gf.commit_plan("alice", plan="friday_dinner")
    assert blocked.outcome == "blocked" and any("Dana has not confirmed" in b for b in blocked.blockers)
    confirm_everyone(gf, members=("dana",))
    assert plan_view(gf, "alice", "friday_dinner").status == "READY_TO_COMMIT"
    c = gf.commit_plan("alice", plan="friday_dinner")
    assert c.outcome == "committed" and len(c.bookings) == 3
    assert gf.commit_plan("alice", plan="friday_dinner").outcome == "already_committed"


def test_only_the_organizer_sends_and_commits():
    gf = seeded({"friday_dinner": "draft"})
    assert code(lambda: gf.request_confirmations("bob", plan="friday_dinner")) == "FORBIDDEN"
    gf.request_confirmations("alice", plan="friday_dinner")
    confirm_everyone(gf)
    assert code(lambda: gf.commit_plan("bob", plan="friday_dinner")) == "FORBIDDEN"


def test_a_closure_degrades_the_plan_and_the_repair_changes_only_the_dinner():
    gf = degraded()
    v = plan_view(gf, "bob", "friday_dinner")
    assert v.status == "DEGRADED" and "Restaurant A" in v.status_reason
    alts = gf.find_alternatives("alice", plan="friday_dinner")
    assert alts.proof.status == "optimal_within_domain"
    best = alts.alternatives[0]
    assert best.label == "P2" and best.changes[0] == "Dinner: Restaurant A -> Restaurant B"
    assert best.unchanged == ["Meet up", "Dessert", "Ride home"]
    assert best.cost_each_after == "CAD 43.00"
    assert plan_view(gf, "alice", "friday_dinner").status == "PROPOSED"


def test_why_not_c_names_no_one():
    gf = degraded()
    gf.find_alternatives("alice", plan="friday_dinner")
    e = gf.explain("bob", plan="friday_dinner", option="Restaurant C")
    assert e.options[0].option == "Restaurant C at 19:00" and not e.options[0].works
    assert "21:15" in e.options[0].reason and "latest time to be home" in e.options[0].reason
    text = " ".join([e.message] + [o.reason for o in e.options])
    for name in ("Alice", "Bob", "Chen", "Dana"):
        assert name not in text


def test_unchanged_parts_keep_their_approvals_and_only_the_dinner_is_asked_again():
    gf = degraded()
    gf.find_alternatives("alice", plan="friday_dinner")
    r = gf.request_confirmations("alice", plan="friday_dinner")
    assert r.carried_forward == 12
    inbox = gf.human_inbox(gf.identity.get("chen"))
    assert [p["text"].split(":")[1].strip().split(" on ")[0] for p in inbox["pending"]] == ["Dinner"]
    confirm_everyone(gf)
    assert gf.commit_plan("alice", plan="friday_dinner").bookings == ["cancel book Restaurant A for 4 at 18:30 (simulated, ref SIM-P1-DINNER)", "book Restaurant B for 4 at 18:30 (simulated, ref SIM-P2-DINNER)"]


def test_a_decline_with_a_lower_limit_makes_the_next_search_explain_the_clash():
    gf = degraded()
    gf.find_alternatives("alice", plan="friday_dinner")
    gf.request_confirmations("alice", plan="friday_dinner")
    confirm_everyone(gf, members=("alice", "bob", "chen"))
    dana = gf.identity.get("dana")
    item = gf.human_inbox(dana)["pending"][0]
    ch = gf.human_challenge(dana, item["scope_id"])
    gf.human_decide(dana, ch["challenge_id"], ch["nonce"], "rejected", new_limit={"kind": "budget", "value": {"amount": 40}})
    v = plan_view(gf, "alice", "friday_dinner")
    assert "Dana" in v.next_step and "declined" in v.next_step
    alts = gf.find_alternatives("alice", plan="friday_dinner")
    assert alts.alternatives == [] and alts.proof.status == "infeasible_within_domain"
    assert any("budget" in c for c in alts.conflict)
    # Dana's new limit (40) must not surface. The proof's timing and the random ids are left out of the scan: a 40 ms search or an
    # id containing "40" is not her budget, and matching them made this check flaky.
    shown = json.dumps({k: v for k, v in alts.model_dump().items() if k not in ("proof", "job")})
    assert not re.search(r"\b40\b", shown) and "Dana" not in " ".join(alts.conflict)


def test_conditions_said_to_alexa_are_drafts_until_the_person_confirms_them():
    gf = seeded({"friday_dinner": "committed"})
    r = gf.set_my_conditions("bob", [{"kind": "home_by", "value": {"time": "20:30"}}], plan="friday_dinner")
    assert r.needs_verification and r.conditions[0].status == "unverified"
    assert plan_view(gf, "alice", "friday_dinner").status == "COMMITTED"
    bob = gf.identity.get("bob")
    c = next(x for x in gf.human_inbox(bob)["conditions"] if x["status"] == "unverified")
    assert gf.human_verify_condition(bob, c["id"], c["verify_digest"])["outcome"] == "active"
    v = plan_view(gf, "alice", "friday_dinner")
    assert v.status == "DEGRADED"
    # Nobody else sees Bob's private limit.
    for other in ("alice", "chen", "dana"):
        assert "20:30" not in gf.context(other, mark_read=False).model_dump_json()
    assert "20:30" in gf.context("bob", mark_read=False).model_dump_json()


def test_conditions_cannot_name_unknown_kinds_or_tags(gf):
    assert code(lambda: gf.set_my_conditions("bob", [{"kind": "favourite_colour", "value": {}}], plan="friday_dinner")) == "INVALID_ARGUMENT"
    assert code(lambda: gf.set_my_conditions("bob", [{"kind": "diet", "value": {"tags": ["carnivore"]}}], plan="friday_dinner")) == "INVALID_ARGUMENT"


def test_a_members_report_stays_unverified_and_changes_nothing():
    gf = seeded({"friday_dinner": "committed"})
    r = gf.report_observation("bob", "place_unavailable", place="Restaurant A", plan="friday_dinner")
    assert r.outcome == "recorded" and not r.verified
    assert plan_view(gf, "alice", "friday_dinner").status == "COMMITTED"
    assert any("Bob reported" in u for u in gf.context("alice").updates)
    assert gf.report_observation("bob", "place_unavailable", place="Restaurant A", plan="friday_dinner").outcome == "duplicate"


def test_a_verified_price_change_that_breaks_a_budget_degrades_the_plan():
    gf = seeded({"friday_dinner": "committed"})
    out = gf.inject_observation("friends", "price_changed", {"place_id": "restaurant_a", "price_minor": 4000}, "demo_console", "p1")
    assert out["affected_plans"] == ["friday_dinner"]
    alts = gf.find_alternatives("alice", plan="friday_dinner")
    assert alts.alternatives and alts.alternatives[0].changes[0].startswith("Dinner: Restaurant A ->")


def test_challenges_are_single_use_and_bound_to_the_exact_version():
    gf = seeded({"friday_dinner": "awaiting"})
    chen = gf.identity.get("chen")
    item = gf.human_inbox(chen)["pending"][0]
    ch = gf.human_challenge(chen, item["scope_id"])
    gf.human_decide(chen, ch["challenge_id"], ch["nonce"], "approved")
    assert code(lambda: gf.human_decide(chen, ch["challenge_id"], ch["nonce"], "approved")) == "CHALLENGE_USED"
    # Someone else's scope cannot be answered.
    dana_item = gf.human_inbox(gf.identity.get("dana"))["pending"][0]
    assert code(lambda: gf.human_challenge(chen, dana_item["scope_id"])) == "FORBIDDEN"
    # A change after the challenge was issued makes it stale.
    ch2 = gf.human_challenge(gf.identity.get("dana"), dana_item["scope_id"])
    gf.propose_change("alice", item="dessert", start="20:00", plan="friday_dinner")
    assert code(lambda: gf.human_decide(gf.identity.get("dana"), ch2["challenge_id"], ch2["nonce"], "approved")) == "STALE_CANDIDATE"


def test_a_conditional_answer_counts_only_while_its_condition_holds():
    gf = seeded({"friday_dinner": "awaiting"})
    confirm_everyone(gf, members=("alice", "bob", "chen"))
    dana = gf.identity.get("dana")
    for item in gf.human_inbox(dana)["pending"]:
        ch = gf.human_challenge(dana, item["scope_id"])
        gf.human_decide(dana, ch["challenge_id"], ch["nonce"], "conditional", condition={"max_total_minor": 3000})
    blocked = gf.commit_plan("alice", plan="friday_dinner")
    assert blocked.outcome == "blocked" and any("condition that is not met" in b for b in blocked.blockers)


def test_withdrawing_after_the_commit_degrades_the_plan():
    gf = seeded({"friday_dinner": "committed"})
    dana = gf.identity.get("dana")
    receipt = gf.human_inbox(dana)["receipts"][0]
    assert gf.human_revoke(dana, receipt["receipt_id"])["plan_status"] == "DEGRADED"


def test_alexa_has_no_way_to_approve():
    from groupflow.mcp_server.tools import NAMES

    assert not any(word in n for n in NAMES for word in ("approve", "confirm_for", "decline", "withdraw"))
