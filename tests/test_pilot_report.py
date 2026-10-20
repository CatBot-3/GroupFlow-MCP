"""The pilot report counts confirmations asked per completed change, from the database alone."""
import importlib.util
from pathlib import Path

from groupflow.core.db import Db
from groupflow.engine import GroupFlow
from groupflow.world import seed_world
from tests.conftest import WORLD

ROOT = Path(__file__).resolve().parents[1]


def test_the_seeded_world_reports_asked_and_kept_confirmations(tmp_path):
    db = Db(str(tmp_path / "pilot.db"))
    seed_world(GroupFlow(db), WORLD, {})
    db.close()
    spec = importlib.util.spec_from_file_location("pilot_report", ROOT / "scripts" / "pilot_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    r = mod.report(str(tmp_path / "pilot.db"))
    assert r["completed_changes"] >= 1 and r["confirmations_asked"] >= 1 and r["plans"] >= 7
