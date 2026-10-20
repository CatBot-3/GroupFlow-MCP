"""Independent collaboration acceptance cases, frozen before source review.

Only local synthetic people/catalogs are used. The oracle is the public contract
that was frozen with these cases.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import pytest

from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.core.identity import Person, seed_people
from groupflow.engine import GroupFlow, fixed_clock
from groupflow.world import approve_all


PEOPLE = [
    Person("iris", "Iris", "synthetic-iris-token", "synthetic-iris-pass", "#112233"),
    Person("mila", "Mila", "synthetic-mila-token", "synthetic-mila-pass", "#223344"),
    Person("orin", "Orin", "synthetic-orin-token", "synthetic-orin-pass", "#334455"),
    Person("vera", "Vera", "synthetic-vera-token", "synthetic-vera-pass", "#445566"),
]
NOW = "2026-10-18T12:00:00Z"
EXPIRY = "2026-10-18T13:00:00Z"
LATER = "2026-10-18T14:00:00Z"
PLAN = "finch-kiln-review"
GROUP = "finch-kiln-club"


@dataclass
class Lab:
    gf: GroupFlow
    first: str
    second: str
    plan: str = PLAN


def make_lab(db: Db | None = None) -> Lab:
    """A new scenario with no demo-world plans, conditions or transcripts."""
    gf = GroupFlow(db or Db(":memory:"), solver_budget_s=1.0, method="enumeration")
    gf.set_clock(fixed_clock(NOW))
    seed_people(gf.db, PEOPLE)
    gf.add_group(GROUP, "Finch kiln club", "America/Toronto", "CAD", [
        {"id": "iris", "role": "organizer", "confirmation_mode": "scoped"},
        {"id": "mila", "confirmation_mode": "scoped"},
        {"id": "orin", "confirmation_mode": "scoped"},
    ])
    gf.create_plan("iris", "evening_out", "Finch kiln review", group=GROUP,
                   start_date="2026-12-09", starter=False, plan_id=PLAN)
    catalog = {
        "version": "synthetic-finch-1", "default_travel": 0,
        "travel_minutes": [["kiln", "kiln", 0]],
        "places": [
            {"id": "finch_studio", "name": "Atelier Finch", "kind": "meal", "zone": "kiln",
             "open": "08:00", "close": "23:00", "capacity": 8, "price": 11,
             "tags": ["step_free", "vegetarian", "nut_free"]},
            {"id": "finch_annex", "name": "Finch Annex", "kind": "meal", "zone": "kiln",
             "open": "08:00", "close": "23:00", "capacity": 8, "price": 11,
             "tags": ["step_free", "vegetarian", "nut_free"]},
        ],
    }
    gf.db.run("UPDATE plans SET catalog_json=? WHERE id=?", json.dumps(catalog), PLAN)
    first = gf.propose_change("iris", plan=PLAN, new_type="stop", title="Clay rehearsal",
                              start="13:00", end="13:45", place="finch_studio", participants=["iris", "mila"]).item
    second = gf.propose_change("iris", plan=PLAN, new_type="stop", title="Glaze pickup",
                               start="15:00", end="15:30", place="finch_studio", participants=["iris", "mila", "orin"]).item
    gf.reset_rate_limits()
    return Lab(gf, first, second)


@pytest.fixture
def lab():
    fixture = make_lab()
    try:
        yield fixture
    finally:
        fixture.gf.db.close()


def database_state(gf: GroupFlow) -> dict:
    """All persisted tables and sequence rows, not selected effect counters."""
    tables = gf.db.all("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")
    return {
        table["name"]: {
            "schema": table["sql"],
            "rows": sorted((json.dumps(row, sort_keys=True, default=str)
                            for row in gf.db.all('SELECT * FROM "' + table["name"].replace('"', '""') + '"'))),
        }
        for table in tables
    }


def diagnostic_business_state(gf: GroupFlow) -> dict:
    """The server permits only its persistent diagnostic counter, including its own sequence."""
    result = database_state(gf)
    result.pop('diagnostic_queries', None)
    if 'sqlite_sequence' in result:
        result['sqlite_sequence']['rows'] = [row for row in result['sqlite_sequence']['rows'] if json.loads(row)['name'] != 'diagnostic_queries']
    return result


def freeze_committed(lab: Lab) -> None:
    gf = lab.gf
    assert gf.request_confirmations("iris", plan=lab.plan).outcome == "requested"
    for member in ("iris", "mila", "orin"):
        approve_all(gf, gf.identity.get(member), lab.plan)
    assert gf.commit_plan("iris", plan=lab.plan).outcome == "committed"
    gf.reset_rate_limits()


def expect_code(fn, expected: set[str]) -> DomainError:
    with pytest.raises(DomainError) as caught:
        fn()
    assert caught.value.code in expected, caught.value.to_json()
    return caught.value


def impact_pairs(lab: Lab, impact: dict, field: str) -> set[tuple[str, str]]:
    titles = {"Clay rehearsal": lab.first, "Glaze pickup": lab.second}
    return {(row["member"], titles.get(row["activity"], row["activity"])) for row in impact[field]}


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return parsed


def restrict_capacity(lab: Lab) -> None:
    catalog = json.loads(lab.gf.db.scalar("SELECT catalog_json FROM plans WHERE id=?", lab.plan))
    for place in catalog["places"]:
        place["capacity"] = 1
    lab.gf.db.run("UPDATE plans SET catalog_json=? WHERE id=?", json.dumps(catalog), lab.plan)


def assert_capacity_witness(lab: Lab, suggestion: dict) -> None:
    """Independently check the witness without trusting its reported proof."""
    snapshot = suggestion["snapshot"]
    assert snapshot["plan_id"] == lab.plan
    activities = {row["id"]: row for row in snapshot["activities"]}
    assert set(activities) == {lab.first, lab.second}
    assert set(activities[lab.first]["participants"]) == {"iris", "mila"}
    assert set(activities[lab.second]["participants"]) == {"iris", "mila", "orin"}
    catalog = json.loads(lab.gf.db.scalar("SELECT catalog_json FROM plans WHERE id=?", lab.plan))
    places = {row["id"]: row for row in catalog["places"]}
    overloaded = set()
    for activity in activities.values():
        assert not activity["cancelled"]
        place = places[activity["place_id"]]
        assert activity["kind"] == place["kind"]
        assert 8 * 60 <= activity["start"] < activity["end"] <= 23 * 60
        assert set(activity["cost_shares"].values()) == {1100}
        if len(activity["participants"]) > place["capacity"]:
            overloaded.add(place["id"])
    assert len(overloaded) == 1, "A single catalog capacity change must make this witness feasible"
    altered = places[next(iter(overloaded))]
    assert altered["name"] in suggestion["changes"][0]
    assert "capacity" in suggestion["changes"][0].lower()
    for member in ("iris", "mila", "orin"):
        attended = sorted((row for row in activities.values() if member in row["participants"]), key=lambda row: row["start"])
        assert all(a["end"] <= b["start"] for a, b in zip(attended, attended[1:]))


def inject_price(lab: Lab, event: str, valid_until: str = EXPIRY) -> dict:
    return lab.gf.inject_observation(GROUP, "price_changed", {"place_id": "finch_studio", "price_minor": 1100},
                                     source="synthetic_kiln_operator", source_event_id=event,
                                     verified=True, valid_until=valid_until)


def test_preview_public_delta_carry_scope_and_total_rollback(lab):
    freeze_committed(lab)
    gf = lab.gf
    arguments = {"plan": lab.plan, "item": lab.first, "place": "finch_annex"}
    expected = gf.state_token(lab.plan)
    before = database_state(gf)
    preview = gf.preview_action("iris", "propose_change", arguments, expected)
    assert preview["outcome"] == "preview"
    assert preview["expected"] == expected
    assert database_state(gf) == before
    change = next(row for row in preview["changes"] if row["item"] == lab.first)
    assert change["plan"] == lab.plan
    assert "Atelier Finch" in json.dumps(change["before"])
    assert "Finch Annex" in json.dumps(change["after"])
    assert set(preview["affected_participants"]) == {"Iris", "Mila"}
    assert impact_pairs(lab, preview["approval_impact"], "needs_confirmation") == {
        ("Iris", lab.first), ("Mila", lab.first)}
    assert impact_pairs(lab, preview["approval_impact"], "carried") == {
        ("Iris", lab.second), ("Mila", lab.second), ("Orin", lab.second)}
    assert "action_receipt" not in preview["result"]
    actual = gf.apply_action("iris", "propose_change", arguments, "preview-then-apply", expected)
    assert actual["outcome"] == preview["result"]["outcome"]
    requested = gf.request_confirmations("iris", plan=lab.plan)
    assert requested.carried_forward == 3
    assert len(gf.human_inbox(gf.identity.get("mila"))["pending"]) == 1


def test_preview_new_ids_and_successful_nested_preview_do_not_persist(lab):
    gf = lab.gf
    arguments = {"plan": lab.plan, "new_type": "stop", "title": "Firing inspection",
                 "start": "17:00", "end": "17:30", "place": "finch_annex"}
    before = database_state(gf)
    gf.db.tx(lambda: gf.preview_action("iris", "propose_change", arguments, gf.state_token(lab.plan)))
    assert database_state(gf) == before
    result = gf.apply_action("iris", "propose_change", arguments, "first-real-inspection", gf.state_token(lab.plan))
    assert result["item"]
    assert gf.db.scalar("SELECT count(*) FROM items WHERE label='Firing inspection'") == 1
    assert gf.db.scalar("SELECT count(*) FROM action_receipts WHERE request_key='first-real-inspection'") == 1


def test_preview_stale_unauthorized_and_invalid_paths_are_read_only(lab):
    gf = lab.gf
    token = gf.state_token(lab.plan)
    gf.bring_list("orin", "bring", ["glaze cloth"], plan=lab.plan)
    request = gf.propose_change("mila", plan=lab.plan, item=lab.first, place="finch_annex").request.request
    before = database_state(gf)
    expect_code(lambda: gf.preview_action("iris", "propose_change", {
        "plan": lab.plan, "item": lab.first, "place": "finch_annex"}, token), {"STALE"})
    assert database_state(gf) == before
    expect_code(lambda: gf.preview_action("mila", "decide_change", {"request": request, "decision": "accept"},
                                         gf.state_token(lab.plan)), {"FORBIDDEN"})
    expect_code(lambda: gf.preview_action("vera", "propose_change", {
        "plan": lab.plan, "item": lab.first, "place": "finch_annex"}, token), {"NOT_FOUND", "FORBIDDEN"})
    expect_code(lambda: gf.preview_action("iris", "propose_change", {
        "plan": lab.plan, "item": lab.first, "place": "not-a-catalog-place"}, gf.state_token(lab.plan)), {"NOT_FOUND"})
    assert database_state(gf) == before


def test_recipes_are_exact_current_authorized_and_cannot_survive_change(lab):
    gf = lab.gf
    organizer = gf.resolve_action("iris", "Clay rehearsal", plan=lab.plan)
    member = gf.resolve_action("mila", "Clay rehearsal", plan=lab.plan)
    assert organizer["targets"][0]["item"] == lab.first
    recipes = organizer["next_actions"]
    assert recipes
    for recipe in recipes:
        assert recipe["reason"]
        if "tool" in recipe:
            assert recipe["arguments"]["plan"] == lab.plan
            assert recipe["expected"] == gf.state_token(lab.plan)
            assert not ({"actor_id", "member_id", "as", "self"} & recipe["arguments"].keys())
        else:
            assert recipe["human_page"]
    organizer_only = {"request_confirmations", "commit_plan", "find_alternatives"}
    assert not any(row.get("tool") in organizer_only for row in member["next_actions"])
    recipe = next(row for row in recipes if row.get("tool") == "request_confirmations")
    gf.bring_list("orin", "bring", ["kiln gloves"], plan=lab.plan)
    before = database_state(gf)
    expect_code(lambda: gf.apply_action("iris", recipe["tool"], recipe["arguments"],
                                       "stale-recipe", recipe["expected"]), {"STALE"})
    assert database_state(gf) == before
    assert gf.db.scalar("SELECT count(*) FROM approval_receipts") == 0


def test_pending_decision_cards_route_personal_consent_to_human_page(lab):
    gf = lab.gf
    gf.request_confirmations("iris", plan=lab.plan)
    for member in ("iris", "mila", "orin"):
        cards = gf.decision_inbox(member)["cards"]
        card = next(row for row in cards if row["plan"] == lab.plan)
        assert card["next_actions"]
        assert any(row.get("human_page") for row in card["next_actions"])
        assert not any("approve" in row.get("tool", "") for row in card["next_actions"])
        if member != "iris":
            assert not any(row.get("tool") in {"request_confirmations", "commit_plan"}
                           for row in card["next_actions"])
    assert gf.db.scalar("SELECT count(*) FROM approval_receipts") == 0


def test_capacity_hypotheticals_are_feasible_bounded_and_read_only(lab):
    gf = lab.gf
    restrict_capacity(lab)
    ordinary = gf.planning_options("iris", lab.plan)
    assert not ordinary["alternatives"]
    before = diagnostic_business_state(gf)
    queries = gf.db.scalar('SELECT COUNT(*) FROM diagnostic_queries')
    hypothetical = gf.planning_options("iris", lab.plan, mode="relaxations")
    assert diagnostic_business_state(gf) == before
    assert gf.db.scalar('SELECT COUNT(*) FROM diagnostic_queries') == queries + 1
    assert hypothetical["search_scope"]
    assert 1 <= len(hypothetical["suggestions"]) <= 12
    for row in hypothetical["suggestions"]:
        assert row["hypothetical"] is True
        assert row["changes"] and len(row["changes"]) == 1
        assert row["snapshot"]
        assert row["proof"]
        assert "feasible" in json.dumps(row["proof"]).lower() or "optimal_within_domain" in json.dumps(row["proof"])
        assert_capacity_witness(lab, row)
    assert "globally minimal" not in json.dumps(hypothetical).lower()


def test_personal_hard_block_cannot_become_a_public_relaxation_or_leak(lab):
    gf = lab.gf
    gf.human_set_condition(gf.identity.get("mila"), "budget", {"amount": "0.37"}, lab.plan)
    ordinary = gf.planning_options("iris", lab.plan)
    assert not ordinary["alternatives"]
    for member in ("iris", "orin"):
        before = diagnostic_business_state(gf)
        queries = gf.db.scalar('SELECT COUNT(*) FROM diagnostic_queries')
        hypothetical = gf.planning_options(member, lab.plan, mode="relaxations")
        assert diagnostic_business_state(gf) == before
        assert gf.db.scalar('SELECT COUNT(*) FROM diagnostic_queries') == queries + 1
        assert hypothetical["suggestions"] == []
        public = json.dumps(hypothetical).lower()
        assert "0.37" not in public and "37" not in public
        assert "mila" not in public
        assert "voluntar" in public and ("condition" in public or "constraint" in public)


def test_catalog_and_trusted_facts_have_provenance_expiry_and_freshness(lab):
    gf = lab.gf
    before = database_state(gf)
    static = gf.planning_options("mila", lab.plan, mode="facts")
    assert database_state(gf) == before
    assert static["facts"]
    for fact in static["facts"]:
        assert {"source", "verified_at", "valid_until", "freshness", "verification", "value"} <= fact.keys()
        assert fact["verification"] == "unverified"
        assert "estimate" in json.dumps(fact).lower()
        assert fact["verified_at"] is None
    result = inject_price(lab, "finch-price-verified")
    before = database_state(gf)
    facts = gf.planning_options("orin", lab.plan, mode="facts")
    assert database_state(gf) == before
    observed = next(f for f in facts["facts"] if f["id"] == result["observation_id"])
    assert observed["source"] == "synthetic_kiln_operator"
    assert observed["verification"] == "verified"
    assert instant(observed["verified_at"]) == instant(NOW)
    assert instant(observed["valid_until"]) == instant(EXPIRY)
    assert observed["freshness"] in {"current", "fresh"}
    assert "live provider" not in json.dumps(facts).lower() or "no live provider" in json.dumps(facts).lower()


def test_expired_fact_blocks_resending_human_confirmation_and_commit_until_recheck(lab):
    gf = lab.gf
    observed = inject_price(lab, "finch-price-short-lived")
    assert gf.request_confirmations("iris", plan=lab.plan).outcome == "requested"
    pending = next(row for row in gf.human_inbox(gf.identity.get("mila"))["pending"] if row["activity_id"] == lab.first)
    challenge = gf.human_challenge(gf.identity.get("mila"), pending["scope_id"])
    for member in ("iris", "orin"):
        approve_all(gf, gf.identity.get(member), lab.plan)
    gf.set_clock(fixed_clock(LATER))
    facts = gf.planning_options("iris", lab.plan, mode="facts")
    fact = next(row for row in facts["facts"] if row["id"] == observed["observation_id"])
    assert fact["freshness"] in {"stale", "expired"}
    assert gf.request_confirmations("iris", plan=lab.plan).outcome in {"infeasible", "blocked"}
    before_receipts = gf.db.scalar("SELECT count(*) FROM approval_receipts")
    refusal = expect_code(lambda: gf.human_decide(gf.identity.get("mila"), challenge["challenge_id"], challenge["nonce"], "approved"),
                          {"INFEASIBLE", "STALE", "STALE_FACTS", "STALE_FACT", "STALE_CANDIDATE"})
    assert "fact" in refusal.message.lower() or "expir" in refusal.message.lower()
    assert gf.db.scalar("SELECT count(*) FROM approval_receipts") == before_receipts
    blocked = gf.commit_plan("iris", plan=lab.plan)
    assert blocked.outcome == "blocked"
    assert any("fact" in row.lower() or "recheck" in row.lower() or "expir" in row.lower() for row in blocked.blockers)
    inject_price(lab, "finch-price-rechecked", "2026-10-18T16:00:00Z")
    assert gf.request_confirmations("iris", plan=lab.plan).outcome in {"requested", "ready"}
    for member in ("iris", "mila", "orin"):
        approve_all(gf, gf.identity.get(member), lab.plan)
    committed = gf.commit_plan("iris", plan=lab.plan)
    assert committed.outcome == "committed", committed.model_dump()


@pytest.mark.parametrize("invalid_until", ["invalid", "2026-10-18T11:59:59Z", "2026-10-18T13:00:00"])
def test_invalid_or_expired_fact_cannot_masquerade_as_current(lab, invalid_until):
    gf = lab.gf
    try:
        result = inject_price(lab, "finch-invalid-" + invalid_until, invalid_until)
    except DomainError as err:
        assert err.code in {"INVALID_ARGUMENT", "INVALID_FACT_TIME", "INVALID_OBSERVATION"}
        return
    facts = gf.planning_options("iris", lab.plan, mode="facts")
    fact = next(row for row in facts["facts"] if row["id"] == result["observation_id"])
    assert fact["freshness"] not in {"current", "fresh"}
    assert gf.request_confirmations("iris", plan=lab.plan).outcome in {"infeasible", "blocked"}
