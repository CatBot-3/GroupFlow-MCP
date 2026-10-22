"""The retired guided demos, kept as test cases: replaying each one's reference trajectory against the real server passes
every check its captions claimed, with all safety counters at zero, and a model that only says "Okay." fails them."""
from __future__ import annotations

import pytest

from groupflow.evals.runner import load_scenarios, suite_paths
from tests.evals.test_realistic import acts, play, safety

DEMOS = load_scenarios(suite_paths(["demos"]))


def test_every_retired_guided_demo_is_here():
    assert {s["id"] for s in DEMOS} == {"DEMO-FAMILY", "DEMO-FRIENDS", "DEMO-COTTAGE", "DEMO-TEAM", "DEMO-STUDY"}
    for sc in DEMOS:
        assert all("reference" in step for step in sc["steps"] if "say" in step), sc["id"]


@pytest.mark.parametrize("sc", DEMOS, ids=lambda s: s["id"])
async def test_the_reference_trajectory_passes(sc):
    r = await play(sc)
    assert r.passed, r.failures
    assert safety(r) == 0


@pytest.mark.parametrize("sc", [s for s in DEMOS if acts(s)], ids=lambda s: s["id"])
async def test_a_silent_model_fails(sc):
    r = await play(sc, "silent")
    assert not r.passed
