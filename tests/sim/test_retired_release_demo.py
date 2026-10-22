"""The retired release-project guided demo (demo/enterprise.yaml), kept as a test case.

Its steps used the project tools and project answers, which the eval harness does not drive, so the script is replayed here
against the engine, step for step, checking what each caption claimed: the stage is committed only once every commitment
and the stage authorization are in; a changed estimate voids only that person's commitment; the repair keeps unchanged
start times; and only the changed work (with the work that waits on it) and the stage authorization are asked again.
"""
from __future__ import annotations

from pathlib import Path

from groupflow.core.db import Db
from groupflow.core.identity import Member
from groupflow.engine import GroupFlow
from groupflow.world import load_world, seed_world

WORLD = load_world(Path(__file__).resolve().parents[2] / "demo" / "enterprise.yaml")
PEOPLE = ("ava", "ben", "mei", "lee")


def answer(gf: GroupFlow, plan: str) -> int:
    """Each person answers what is waiting for them on their own page (the old demo's "phones" step)."""
    n = 0
    for person in PEOPLE:
        member = Member(person, gf.name(person))
        for item in gf.human_project_inbox(member)["items"]:
            if item["plan_id"] != plan or item["status"] in ("approved", "carried_forward") or not item.get("can_answer", True):
                continue
            ch = gf.human_project_challenge(member, item["id"])
            gf.human_project_decide(member, ch["challenge_id"], ch["nonce"], "approve")
            n += 1
    return n


def test_the_release_demo_step_by_step():
    gf = GroupFlow(Db(":memory:"))
    seed_world(gf, WORLD)
    # 1. "Summarize the release project...": parallel workstreams, waiting for commitments and the stage authorization.
    project = gf.get_project("ava", "release")
    assert set(project["workstreams"]) >= {"Design", "Engineering", "QA", "Governance"}
    assert project["confirmation_totals"]["satisfied"] < project["confirmation_totals"]["required"]
    # A commit before anyone answered is refused by the gate.
    assert gf.commit_project("ava", "release")["outcome"] == "blocked"
    # 2. Each person answers only their own scopes; Ava separately authorizes the stage.
    assert answer(gf, "release") > 0
    # 3. "Commit the release stage schedule."
    assert gf.commit_project("ava", "release")["outcome"] == "committed"
    before = {s["item_id"]: s["start"] for s in gf.get_project("ava", "release")["revision"]["schedule"]}
    # 4. Ben changes his own estimate; the old commitment does not cover changed work.
    api = next(w for w in gf.get_project("ava", "release", limit=100)["work"] if w["title"] == "Implement API slice")
    gf.upsert_work("ben", "release", {"duration_minutes": 450}, item=api["id"])
    # 5. "Repair ... with the smallest changes": unchanged start times are kept.
    repaired = gf.repair_project("ava", "release")
    assert repaired["outcome"] == "proposed"
    after = {s["item_id"]: s["start"] for s in gf.get_project("ava", "release")["revision"]["schedule"]}
    assert after[api["id"]] == before[api["id"]]
    assert sum(1 for k, v in before.items() if after.get(k) != v) == 0
    # 6. "Request commitments": unchanged scopes carry forward; Ben's changed work and the stage authorization need answers.
    checks = gf.request_confirmations("ava", plan="release")["confirmations"]
    pending = [c for c in checks if c["status"] == "pending"]
    assert {c["kind"] for c in pending} == {"commitment", "authorization"}
    # Ben's changed work is asked again (and so is his work that waits on it); nobody else's commitment is.
    assert any(c["item"] == api["id"] for c in pending)
    assert {c["person"] for c in pending if c["kind"] == "commitment"} == {"Ben"}
    assert sum(1 for c in checks if c["status"] == "carried_forward") == len(checks) - len(pending)
    assert gf.commit_project("ava", "release")["outcome"] == "blocked"
    # 7. Only changed or pending scopes need new answers.
    assert answer(gf, "release") == len(pending)
    # 8. "Commit the repaired stage."
    assert gf.commit_project("ava", "release")["outcome"] == "committed"
