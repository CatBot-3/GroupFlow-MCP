"""The pilot's one impact number, from a GroupFlow database: confirmations asked per completed change.

For every confirmation round whose version became the agreed plan, count the approvals people had to give (scopes that were not
carried forward) and the approvals the server kept. Beside it: corrections people made to their own recorded actions and plans
left broken or cancelled. Read-only; prints counts, never names.

    python scripts/pilot_report.py --db data/groupflow.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def report(db_path: str) -> dict:
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = lambda sql, *a: con.execute(sql, a).fetchall()  # noqa: E731
    rounds = q("SELECT r.id, r.revision_id, v.kind FROM confirmation_rounds r JOIN plan_revisions v ON v.id = r.revision_id")
    done = [r for r in rounds if r["kind"] in ("committed", "superseded")]
    asked = kept = 0
    for r in done:
        scopes = q("SELECT id FROM approval_scopes WHERE revision_id = ?", r["revision_id"])
        carried = {x["scope_id"] for x in q("SELECT scope_id FROM approval_carry_forwards WHERE target_revision_id = ?", r["revision_id"])}
        asked += sum(1 for s in scopes if s["id"] not in carried)
        kept += sum(1 for s in scopes if s["id"] in carried)
    corrections = q("SELECT COUNT(*) AS n FROM action_receipts WHERE corrected_by IS NOT NULL")[0]["n"]
    broken = q("SELECT COUNT(*) AS n FROM plans WHERE status IN ('DEGRADED', 'CANCELLED')")[0]["n"]
    plans = q("SELECT COUNT(*) AS n FROM plans")[0]["n"]
    con.close()
    return {"completed_changes": len(done), "confirmations_asked": asked, "confirmations_kept": kept,
            "asked_per_completed_change": round(asked / len(done), 2) if done else None,
            "corrections": corrections, "plans": plans, "plans_broken_or_cancelled": broken}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/groupflow.db")
    args = p.parse_args(argv)
    r = report(args.db)
    print(f"Completed changes: {r['completed_changes']}; confirmations asked {r['confirmations_asked']}, kept by the server {r['confirmations_kept']}"
          f" ({r['asked_per_completed_change']} asked per completed change).")
    print(f"Corrections of recorded actions: {r['corrections']}. Plans broken or cancelled: {r['plans_broken_or_cancelled']} of {r['plans']}.")
    print("Sample size is what this database holds; a pilot report must state it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
