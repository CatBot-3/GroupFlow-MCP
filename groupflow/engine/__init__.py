"""GroupFlow engine: one generic core for any group plan, with templates for kinds of plans."""
from __future__ import annotations

import json
from typing import Any

from groupflow.core.db import Db
from groupflow.core.identity import Identity

from .agreement import Agreement
from .collaboration import Collaboration
from .core import LISTENERS, UNSET, Scope, fixed_clock
from .membership import Membership


class GroupFlow(Membership, Collaboration, Agreement):
    """Everything the MCP tools, the human-only API and the demo console call."""

    def __init__(self, db: Db, identity: Identity | None = None, solver_budget_s: float = 3.0, method: str = "auto"):
        super().__init__(db, identity)
        self.solver_budget_s = solver_budget_s
        self.method = method

    # ------------------------------------------------------------ demo inspector (never reachable through MCP)

    def groups(self) -> list[dict[str, Any]]:
        out = []
        for g in self.db.all("SELECT * FROM groups ORDER BY rowid"):
            members = self.db.all("SELECT gm.*, m.name, m.color FROM group_members gm JOIN members m ON m.id = gm.member_id WHERE gm.group_id = ? "
                                  "ORDER BY gm.rowid", g["id"])
            plans = self.db.all("SELECT id, title, template, status FROM plans WHERE group_id = ? AND status <> 'CANCELLED' ORDER BY created_at, rowid", g["id"])
            out.append({"id": g["id"], "name": g["name"], "description": g["description"],
                        "members": [{"id": m["member_id"], "name": m["name"], "label": m["label"], "role": m["role"], "color": m["color"]} for m in members],
                        "plans": plans})
        return out

    def inspector(self, group_id: str | None = None, view_as: str | None = None) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            scope: Scope = self.scope_for(view_as) if view_as else self.inspector_scope(group_id)
            plans = [p for p in scope.plans.values() if group_id is None or p["group_id"] == group_id]
            out_plans = []
            for p in plans:
                view = self._plan_view(scope, p).model_dump()
                space = self._space(p["space_id"])
                view.update(template=p["template"], space=space["name"] if space else p["space_id"], audience=self.plan_audience(p),
                            organizer_id=p["organizer_id"], start_date=p["start_date"], days=p["days"])
                revs = []
                for r in self.db.all("SELECT * FROM plan_revisions WHERE plan_id = ? ORDER BY created_at, rowid", p["id"]):
                    if r["kind"] == "discarded" and r["label"] == "Draft":
                        continue
                    v = self._version_view(p, r, view_as, None).model_dump()
                    v.update(id=r["id"], kind=r["kind"], source=r["source"], created_at=r["created_at"], parent_id=r["parent_id"], alternate=bool(r["alternate"]))
                    revs.append(v)
                view["revisions"] = revs
                cand = self._revision(p["candidate_revision_id"])
                view["gate"] = self.gate(p, cand) if cand else []
                job = self.db.get("SELECT * FROM repair_jobs WHERE plan_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", p["id"])
                viewer = view_as or p["organizer_id"]
                view["repair"] = self.get_alternatives(viewer, job=job["id"]).model_dump() if job and self.scope_for(viewer).sees_plan(p["id"]) else None
                view["explain"] = None
                if job and job["status"] == "done" and cand and self.scope_for(viewer).sees_plan(p["id"]):
                    try:
                        view["explain"] = self._explain_activity(self.scope_for(viewer), viewer, p, None, None).model_dump()
                    except Exception:  # noqa: BLE001 - the board must render even if an explanation fails
                        view["explain"] = None
                view["audit"] = [
                    {"seq": a["seq"], "at": a["at"], "who": self.name(a["actor_id"]) if a["actor_id"] else None, "action": a["action"], "detail": a["detail"],
                     "said": self._quote_for(scope, a["utterance_id"])}
                    for a in self.db.all("SELECT * FROM audit WHERE plan_id = ? ORDER BY seq DESC LIMIT 60", p["id"])
                    if not a["private_to"] or a["private_to"] == view_as
                ]
                view["conditions"] = [dict(self._condition_view(c).model_dump(), owner=self.name(c["owner_id"]), owner_id=c["owner_id"])
                                      for c in self._condition_rows(p, None, ("active", "unverified"))
                                      if view_as is None or c["owner_id"] == view_as or c["visibility"] == "group"]
                view["executions"] = self.db.all("SELECT activity_id, action, status, provider_reference, simulated FROM executions WHERE plan_id = ? "
                                                 "ORDER BY created_at", p["id"])
                out_plans.append(view)
            groups = [g for g in self.groups() if group_id is None or g["id"] == group_id]
            return {"scope": view_as or "inspector", "group": groups[0] if group_id and groups else None, "plans": out_plans,
                    "observations": [dict(o, payload=json.loads(o["payload_json"])) for o in self.db.all(
                        "SELECT id, kind, payload_json, verification, source, received_at FROM observations " + ("WHERE group_id = ? " if group_id else "")
                        + "ORDER BY received_at DESC LIMIT 10", *([group_id] if group_id else []))]}
        return self.db.tx(run)


__all__ = ["GroupFlow", "LISTENERS", "UNSET", "fixed_clock"]
