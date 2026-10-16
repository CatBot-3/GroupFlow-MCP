"""Long-term project layer. Human commitments and stage authorization are distinct.

MCP can prepare schedules and ask people; only the authenticated human routes
issue/consume challenges. All resource reservations are replaced atomically at
commit after revalidating against the other projects' current reservations.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from groupflow.core.errors import DomainError
from groupflow.core.util import canonical_json, digest, hash_secret, iso_now, new_id, new_secret, now
from groupflow.projects.models import Calendar, ProjectConfig, RecurrenceSpec, WorkSpec
from groupflow.projects.recurrence import occurrences
from groupflow.projects.scheduler import solve, validate_schedule


def parsed(model, data):
    try:
        return model.model_validate(data)
    except (ValidationError, ValueError) as err:
        raise DomainError("INVALID_ARGUMENT", str(err)) from err


class Collaboration:
    def is_organizer(self, plan, member_id):
        return super().is_organizer(plan, member_id) or bool(member_id and self._is_project(plan)
               and self._in_space(member_id, plan["space_id"]) and self._workspace_role(plan["group_id"], member_id, "lead"))

    def _is_project(self, plan) -> bool:
        return plan["template"] == "project"

    def _project(self, actor: str, ref: str):
        scope = self.scope_for(actor)
        p = self.resolve_plan(scope, ref)
        if not self._is_project(p):
            raise DomainError("INVALID_ARGUMENT", "This is not a project. Create a plan with template=project.")
        return p

    def _project_config_row(self, p):
        return self.db.get("SELECT * FROM project_config WHERE plan_id = ?", p["id"])

    def _project_config(self, p) -> ProjectConfig:
        row = self._project_config_row(p)
        return ProjectConfig.model_validate_json(row["config_json"]) if row else ProjectConfig()

    def _workspace_admin(self, group: str, member: str) -> bool:
        role = self._group_role(group, member)
        return bool(role and role["status"] == "active" and role["role"] == "organizer")

    def _workspace_role(self, group: str, member: str, role: str) -> bool:
        return self._workspace_admin(group, member) or bool(self.db.get(
            "SELECT 1 AS yes FROM workspace_roles WHERE group_id=? AND member_id=? AND role=?", group, member, role))

    def _project_lead(self, p, actor: str):
        if not (p["organizer_id"] == actor or self._workspace_role(p["group_id"], actor, "lead")):
            raise DomainError("FORBIDDEN", "Only the project organizer or a workspace lead may change project policy or publish a schedule.")

    def _project_touch(self, p, actor: str, action: str, detail: str):
        row = self._project_config_row(p)
        if row and row["current_revision"]:
            self.db.run("UPDATE project_revisions SET status='superseded' WHERE id=? AND status <> 'committed'", row["current_revision"])
        self.db.run("UPDATE project_config SET current_revision=NULL, version=version+1 WHERE plan_id=?", p["id"])
        p = self._bump(p["id"], "DEGRADED" if row and row["committed_revision"] else "DRAFT", "Project inputs changed; prepare and confirm a schedule.")
        self._audit(actor, action, "plan", p["id"], p, detail)
        self._notify([m for m in self.plan_audience(p) if m != actor], p, "project", detail)

    def create_plan(self, actor_id, template, title, *args, **kwargs):
        self._rate_limit(actor_id)
        def run():
            result = super(Collaboration, self).create_plan(actor_id, template, title, *args, **kwargs)
            p = self._plan_row(result.plan.id)
            if self._is_project(p):
                approvers = [m for m in self.plan_audience(p) if self._workspace_role(p["group_id"], m, "approver")]
                cfg = ProjectConfig(approvers=approvers)
                self.db.run("INSERT INTO project_config(plan_id, config_json) VALUES (?,?)", p["id"], cfg.model_dump_json())
                self._bump(p["id"], "DRAFT", "Add structured work, then prepare a resource schedule.")
                result.plan = self._plan_view(self.scope_for(actor_id), self._plan_row(p["id"]))
            return result
        return self.db.tx(run)

    def configure_project(self, actor: str, plan: str, changes: dict, expected_version: int | None = None) -> dict:
        self._rate_limit(actor)
        def run():
            p = self._project(actor, plan)
            self._project_lead(p, actor)
            if expected_version is not None and expected_version != p["aggregate_version"]:
                raise DomainError("STALE", "The project changed; reload before changing policy.")
            allowed = {"stage", "approvers", "budget_minor", "calendar", "approval_days", "resources", "window_start", "days"}
            if set(changes) - allowed:
                raise DomainError("INVALID_ARGUMENT", "Unknown configuration fields. Private calendars can only be set on the person's page.")
            cfg = self._project_config(p).model_dump(mode="json")
            audience = self.plan_audience(p)
            data = dict(changes)
            for m in data.get("approvers", []):
                if m not in audience or not self._workspace_role(p["group_id"], m, "approver"):
                    raise DomainError("FORBIDDEN", "Approvers must be visible workspace approvers/admins. An admin grants that role on their own page.")
            if "calendar" in data and data["calendar"].get("unavailable"):
                raise DomainError("INVALID_ARGUMENT", "Shared calendar sets work hours only; personal busy windows belong on the person's page.")
            resources = data.pop("resources", [])
            if resources and not self._workspace_admin(p["group_id"], actor):
                raise DomainError("FORBIDDEN", "Only a workspace admin may define shared resource capacity.")
            for resource in resources:
                if set(resource) - {"id", "name", "capacity"}:
                    raise DomainError("INVALID_ARGUMENT", "Resource fields: id, name, capacity.")
                rid = resource.get("id", "")
                capacity = resource.get("capacity", 1)
                if not re.fullmatch(re.escape(p["group_id"]) + r"\.[a-zA-Z0-9_-]{1,60}", rid) or isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 1000:
                    raise DomainError("INVALID_ARGUMENT", "Resource id must be group_id.name; capacity is an integer 1..1000.")
                previous = self.db.get("SELECT * FROM workspace_resources WHERE id=?", rid)
                if previous and previous["capacity"] != capacity and self.db.get("SELECT 1 FROM resource_reservations WHERE resource_id=?", rid):
                    raise DomainError("INVALID_ARGUMENT", "A reserved resource's capacity cannot be changed; release/replan affected projects first.")
                self.db.run("INSERT INTO workspace_resources(id,group_id,name,capacity) VALUES (?,?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,capacity=excluded.capacity,version=workspace_resources.version+1",
                            rid, p["group_id"], resource.get("name") or rid, capacity)
            window_start, window_days = data.pop("window_start", None), data.pop("days", None)
            if window_start is not None or window_days is not None:
                ext = self.db.get("SELECT * FROM plan_extensions WHERE plan_id=?", p["id"])
                if window_start is not None and window_start != p["start_date"] and ext["lifecycle"] != "ongoing":
                    raise DomainError("INVALID_ARGUMENT", "Rolling the window requires lifecycle=ongoing.")
                try:
                    day = date.fromisoformat(window_start or p["start_date"]).isoformat()
                except ValueError as err:
                    raise DomainError("INVALID_ARGUMENT", "window_start must be an ISO local date.") from err
                nd = p["days"] if window_days is None else window_days
                if isinstance(nd, bool) or not isinstance(nd, int) or not 1 <= nd <= 366:
                    raise DomainError("INVALID_ARGUMENT", "days must be an integer 1..366.")
                self.db.run("UPDATE plans SET start_date=?,days=? WHERE id=?", day, nd, p["id"])
                self.db.run("UPDATE plan_extensions SET planning_window_days=? WHERE plan_id=?", nd, p["id"])
            cfg.update(data)
            validated = parsed(ProjectConfig, cfg)
            self.db.run("UPDATE project_config SET config_json=? WHERE plan_id=?", validated.model_dump_json(), p["id"])
            self._project_touch(p, actor, "project.configured", f"{self.name(actor)} updated project stage or scheduling policy.")
            return {"outcome": "updated", "project": self.get_project(actor, p["id"])}
        return self.db.tx(run)

    def upsert_work(self, actor: str, plan: str, spec: dict, item: str | None = None, title: str | None = None,
                    owner: str | None = None, expected_version: int | None = None) -> dict:
        self._rate_limit(actor)
        def run():
            p = self._project(actor, plan)
            scope = self.scope_for(actor)
            target = self.resolve_item(scope, item, p["id"]) if item else None
            if target and target["shape"] != "task":
                raise DomainError("INVALID_ARGUMENT", "Structured work must be a task or milestone.")
            if target and target["owner_id"] != actor:
                self._project_lead(p, actor)
            if not target:
                self._project_lead(p, actor)
            if expected_version is not None and expected_version != p["aggregate_version"]:
                raise DomainError("STALE", "The project changed; reload before editing work.")
            old = self.db.get("SELECT * FROM project_work WHERE item_id=?", target["id"]) if target else None
            merged = json.loads(old["spec_json"]) if old else {"not_before": p["start_date"], "due": (date.fromisoformat(p["start_date"]) + timedelta(days=p["days"]-1)).isoformat()}
            merged.update(spec)
            validated = parsed(WorkSpec, merged)
            capacities = self._project_capacities(p)
            for rid, units in validated.resources.items():
                if rid not in capacities or units > capacities[rid]:
                    raise DomainError("INVALID_ARGUMENT", "Unknown resource or requested units exceed its capacity.")
            oid = self._member_in_group(p["group_id"], owner) if owner else target["owner_id"] if target else actor
            if oid not in self.plan_audience(p):
                raise DomainError("FORBIDDEN", "The work owner must be in this plan's audience.")
            if target and oid != target["owner_id"]:
                self._project_lead(p, actor)
            for dep in validated.depends_on:
                row = self._item(dep)
                if not row or row["plan_id"] != p["id"] or row["shape"] != "task" or row["archived"]:
                    raise DomainError("INVALID_ARGUMENT", "Dependencies must be live task ids in this project.")
            if not target:
                if not title or not title.strip():
                    raise DomainError("INVALID_ARGUMENT", "New work needs a title.")
                target = self._insert_item(p, "task", "milestone" if validated.duration_minutes == 0 else "task", title.strip(), oid, actor, status="todo")
            iid = target["id"]
            self.db.run("INSERT INTO project_work(item_id,plan_id,spec_json) VALUES(?,?,?) "
                        "ON CONFLICT(item_id) DO UPDATE SET spec_json=excluded.spec_json,version=project_work.version+1", iid, p["id"], validated.model_dump_json())
            self.db.run("UPDATE items SET label=?, owner_id=?, due=?, version=version+1, updated_at=? WHERE id=?",
                        title or target["label"], oid, validated.due.isoformat(), self.ts(), iid)
            # Use the existing cycle-checked dependency graph; replace only this
            # task's waits_on edges. Task completion uses the same graph.
            self.db.run("DELETE FROM links WHERE item_id=? AND kind='waits_on'", iid)
            for dep in validated.depends_on:
                self.link_items(actor, iid, dep, plan=p["id"])
            self._project_touch(p, actor, "project.work_updated", f"{self.name(actor)} updated {title or target['label']}; its schedule still needs confirmation.")
            return {"outcome": "updated" if old else "created", "item": iid, "version": self._plan_row(p["id"])["aggregate_version"], "spec": validated.model_dump(mode="json")}
        return self.db.tx(run)

    def manage_recurrence(self, actor: str, plan: str, action: str, series: str | None = None, title: str | None = None,
                          owner: str | None = None, rule: dict | None = None, spec: dict | None = None) -> dict:
        self._rate_limit(actor)
        def run():
            nonlocal series
            p = self._project(actor, plan)
            self._project_lead(p, actor)
            if action == "create":
                validated = parsed(RecurrenceSpec, rule or {})
                if not title:
                    raise DomainError("INVALID_ARGUMENT", "A recurring task needs a title.")
                oid = self._member_in_group(p["group_id"], owner) if owner else actor
                if oid not in self.plan_audience(p):
                    raise DomainError("FORBIDDEN", "The series owner must see this project.")
                series = new_id("series")
                payload = {"title": title, "owner": oid, "rule": validated.model_dump(mode="json"), "spec": spec or {}}
                self.db.run("INSERT INTO project_series VALUES(?,?,?,1)", series, p["id"], json.dumps(payload))
            elif action != "expand" or not series:
                raise DomainError("INVALID_ARGUMENT", "Use action=create or expand with a series id. Edit a single occurrence with upsert_work.")
            row = self.db.get("SELECT * FROM project_series WHERE id=? AND plan_id=?", series, p["id"])
            if not row:
                raise DomainError("NOT_FOUND", "No such series in this project.")
            payload = json.loads(row["rule_json"])
            validated = parsed(RecurrenceSpec, payload["rule"])
            start = date.fromisoformat(p["start_date"])
            try:
                dates = occurrences(validated, start, start + timedelta(days=p["days"]-1))
            except ValueError as err:
                raise DomainError("INVALID_ARGUMENT", str(err)) from err
            if len(dates) > 200:
                raise DomainError("INVALID_ARGUMENT", "Limit 200 occurrences per expansion; shorten the planning window.")
            made = []
            for d in dates:
                recurrence_id = d.isoformat()
                if self.db.get("SELECT 1 FROM project_work WHERE series_id=? AND recurrence_id=?", series, recurrence_id):
                    continue
                data = dict(payload["spec"], not_before=recurrence_id, due=recurrence_id)
                result = self.upsert_work(actor, p["id"], data, title=f"{payload['title']} · {recurrence_id}", owner=payload["owner"])
                self.db.run("UPDATE project_work SET series_id=?,recurrence_id=? WHERE item_id=?", series, recurrence_id, result["item"])
                made.append(result["item"])
            return {"outcome": "expanded", "series": series, "created": made, "count": len(made),
                    "message": "Each occurrence is independent; edit its item id for an exception. Expanding again preserves existing edits."}
        return self.db.tx(run)

    def _project_capacities(self, p) -> dict[str, int]:
        return {r["id"]: r["capacity"] for r in self.db.all("SELECT * FROM workspace_resources WHERE group_id=?", p["group_id"])}

    def _project_work(self, p) -> list[dict]:
        rows = self.db.all("SELECT w.*,i.label,i.owner_id,i.status,i.archived,i.version AS item_version FROM project_work w "
                           "JOIN items i ON i.id=w.item_id WHERE w.plan_id=? ORDER BY i.created_at,i.id", p["id"])
        done = {r["item_id"] for r in rows if r["status"] == "done"}
        result = []
        start, end = date.fromisoformat(p["start_date"]), date.fromisoformat(p["start_date"]) + timedelta(days=p["days"]-1)
        for row in rows:
            spec = json.loads(row["spec_json"])
            deps = [d["depends_on_id"] for d in self.db.all("SELECT depends_on_id FROM links WHERE item_id=? AND kind='waits_on' ORDER BY depends_on_id", row["item_id"])]
            spec["depends_on"] = [d for d in deps if d not in done]
            if row["series_id"] and (date.fromisoformat(spec["due"]) < start or date.fromisoformat(spec["not_before"]) > end):
                continue
            if row["archived"] or row["status"] == "done":
                continue
            result.append({"id": row["item_id"], "title": row["label"], "owner_id": row["owner_id"], "owner": self.name(row["owner_id"]),
                           "status": row["status"], "spec": spec, "version": row["version"], "item_version": row["item_version"],
                           "series_id": row["series_id"], "recurrence_id": row["recurrence_id"]})
        return result

    def _project_inputs(self, p) -> dict:
        config = self._project_config(p).model_dump(mode="json")
        # Include all task versions, including completed/archived work, because
        # completing a predecessor changes which edges still need scheduling.
        versions = self.db.all("SELECT id,version,owner_id,status,archived FROM items WHERE plan_id=? ORDER BY id", p["id"])
        memberships = [(m, self._group_role(p["group_id"], m)) for m in self.plan_audience(p)]
        return {"plan": p["id"], "start": p["start_date"], "days": p["days"], "timezone": p["timezone"], "config": config,
                "work": self._project_work(p), "versions": versions, "memberships": memberships,
                "resources": self.db.all("SELECT * FROM workspace_resources WHERE group_id=? ORDER BY id", p["group_id"]),
                "roles": self.db.all("SELECT * FROM workspace_roles WHERE group_id=? ORDER BY member_id,role", p["group_id"])}

    def _project_reservations(self, p):
        return self.db.all("SELECT resource_id,start_utc,end_utc,units FROM resource_reservations WHERE plan_id<>?", p["id"])

    def repair_project(self, actor: str, plan: str, budget_s: float = 3.0) -> dict:
        self._rate_limit(actor)
        if not 0.05 <= budget_s <= 10:
            raise DomainError("INVALID_ARGUMENT", "Solver budget must be 0.05..10 seconds.")
        def capture():
            p = self._project(actor, plan)
            self._project_lead(p, actor)
            inputs = self._project_inputs(p)
            cfg = self._project_config_row(p)
            baseline = self.db.get("SELECT * FROM project_revisions WHERE id=?", cfg["committed_revision"] or cfg["current_revision"])
            return p, inputs, self._project_reservations(p), json.loads(baseline["snapshot_json"])["schedule"] if baseline else []
        p, inputs, reservations, baseline = self.db.tx(capture)
        cfg = ProjectConfig.model_validate(inputs["config"])
        capacities = {r["id"]: r["capacity"] for r in inputs["resources"]}
        result = solve(inputs["work"], cfg, p["timezone"], p["start_date"], p["days"], capacities, reservations, baseline, budget_s)
        if result["status"] != "feasible":
            return {"outcome": result["status"], "proof": result, "message": result.get("message", "No validated schedule found within this window and budget; no commitments changed.")}
        def publish():
            fresh = self._project(actor, p["id"])
            self._project_lead(fresh, actor)
            if digest(inputs) != digest(self._project_inputs(fresh)):
                raise DomainError("STALE_INPUT", "Project inputs changed during scheduling. Nothing was published; run repair again.")
            current = self._project_config_row(fresh)
            if current["current_revision"]:
                self.db.run("UPDATE project_revisions SET status='superseded' WHERE id=? AND status<>'committed'", current["current_revision"])
            revision = new_id("project_rev")
            public_config = cfg.model_dump(mode="json", exclude={"private_calendars"})
            snapshot = {"plan_id": p["id"], "stage": cfg.stage, "schedule": result["schedule"], "work": inputs["work"], "config": public_config,
                        "window_start": p["start_date"], "days": p["days"], "timezone": p["timezone"],
                        "cost_minor": sum(w["spec"]["cost_minor"] for w in inputs["work"])}
            proof = {k: v for k, v in result.items() if k != "schedule"}
            self.db.run("INSERT INTO project_revisions VALUES(?,?,'candidate',?,?,?,?,?,?)", revision, p["id"], json.dumps(snapshot),
                        digest(inputs), digest(snapshot), json.dumps(proof), actor, iso_now())
            self.db.run("UPDATE project_config SET current_revision=? WHERE plan_id=?", revision, p["id"])
            self._bump(p["id"], "PROPOSED", "Resource schedule prepared; organizer must request commitments and authorization.")
            self._audit(actor, "project.repaired", "plan", p["id"], fresh, "Prepared a validated resource schedule.", payload=proof)
            changed = [s["item_id"] for s in result["schedule"] if next((b for b in baseline if b["item_id"]==s["item_id"] and b==s), None) is None]
            return {"outcome": "proposed", "revision": revision, "changed": changed, "proof": proof,
                    "message": "Prepared a proposal. Ask request_confirmations; each owner commits only to their work and stage approvers authorize the stage on their own pages."}
        return self.db.tx(publish)

    def _project_revision(self, p):
        cfg = self._project_config_row(p)
        return self.db.get("SELECT * FROM project_revisions WHERE id=?", cfg["current_revision"]) if cfg and cfg["current_revision"] else None

    def _project_scope_fingerprint(self, p, member: str, payload: dict) -> str:
        cfg = self._project_config(p)
        private_calendar = cfg.private_calendars.get(member, cfg.calendar).model_dump(mode="json")
        return digest({"member": member, "membership": self._group_role(p["group_id"], member), "audience": self.plan_audience(p),
                       "calendar": private_calendar, "role": self.db.all("SELECT role,version FROM workspace_roles WHERE group_id=? AND member_id=? ORDER BY role", p["group_id"], member),
                       "resources": [(r, self.db.get("SELECT capacity,version FROM workspace_resources WHERE id=?", r)) for r in sorted(payload.get("resources", {}))]})

    def _project_scope_state(self, scope) -> tuple[str, str | None]:
        # The latest answer for this exact scope wins, including a refusal or
        # revocation. Never look past a later refusal to reuse an older approval.
        receipt = self.db.get("SELECT * FROM project_receipts WHERE member_id=? AND digest=? AND fingerprint=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                              scope["member_id"], scope["digest"], scope["fingerprint"])
        if not receipt:
            return "pending", None
        if receipt["expires_at"] < iso_now() or scope["expires_at"] < iso_now():
            return "expired", receipt["id"]
        p = self.db.get("SELECT p.* FROM plans p JOIN project_revisions r ON r.plan_id=p.id WHERE r.id=?", scope["revision_id"])
        if scope["fingerprint"] != self._project_scope_fingerprint(p, scope["member_id"], json.loads(scope["payload_json"])):
            return "stale", receipt["id"]
        if receipt["decision"] != "approve":
            return ("declined" if receipt["decision"] == "decline" else "revoked"), receipt["id"]
        return ("approved" if receipt["scope_id"] == scope["id"] else "carried_forward"), receipt["id"]

    def _project_send(self, actor: str, p) -> dict:
        def run():
            p0 = self._project(actor, p["id"])
            self._project_lead(p0, actor)
            rev = self._project_revision(p0)
            if not rev or rev["status"] not in ("candidate", "awaiting"):
                raise DomainError("INVALID_ARGUMENT", "Prepare a new project schedule first with repair_project.")
            if rev["input_digest"] != digest(self._project_inputs(p0)):
                raise DomainError("STALE_INPUT", "The work changed; prepare a fresh schedule before asking people.")
            cfg = self._project_config(p0)
            if not cfg.approvers:
                raise DomainError("INVALID_ARGUMENT", "Configure at least one authorized workspace stage approver.")
            snapshot = json.loads(rev["snapshot_json"])
            errors = validate_schedule(snapshot["schedule"], self._project_work(p0), cfg, p0["timezone"], p0["start_date"], p0["days"],
                                       self._project_capacities(p0), self._project_reservations(p0))
            if errors:
                return {"outcome": "blocked", "message": "The stage is no longer schedulable. Prepare another repair.", "problems": errors}
            scopes = self.db.all("SELECT * FROM project_scopes WHERE revision_id=?", rev["id"])
            if not scopes:
                expiry = (now() + timedelta(days=cfg.approval_days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                work = {w["id"]: w for w in snapshot["work"]}
                payloads = []
                schedule = {s["item_id"]: s for s in snapshot["schedule"]}
                for item in snapshot["schedule"]:
                    w = work[item["item_id"]]
                    payload = {"plan_id": p0["id"], "kind": "commitment", "item_id": w["id"], "recurrence_id": w["recurrence_id"],
                               "schedule": item, "spec": w["spec"], "resources": item["resources"], "stage": cfg.stage,
                               "dependency_ends": {d: schedule[d]["end"] for d in w["spec"]["depends_on"]}}
                    payloads.append((item["owner_id"], "commitment", w["id"], payload))
                for member in cfg.approvers:
                    if member not in self.plan_audience(p0) or not self._workspace_role(p0["group_id"], member, "approver"):
                        raise DomainError("FORBIDDEN", "Stage approvers changed; update the project policy.")
                    payloads.append((member, "authorization", None, {"plan_id": p0["id"], "kind": "authorization", "snapshot_digest": rev["digest"],
                                    "stage": cfg.stage, "cost_minor": snapshot["cost_minor"], "schedule": snapshot["schedule"]}))
                for member, kind, item_id, payload in payloads:
                    self.db.run("INSERT INTO project_scopes VALUES(?,?,?,?,?,?,?,?,?)", new_id("pscope"), rev["id"], member, kind, item_id,
                                json.dumps(payload), digest(payload), self._project_scope_fingerprint(p0, member, payload), expiry)
            self.db.run("UPDATE project_revisions SET status='awaiting' WHERE id=?", rev["id"])
            self._bump(p0["id"], "AWAITING_CONFIRMATIONS", "Work owners give commitments; authorized approvers separately authorize the stage.")
            self._notify(list(dict.fromkeys([s["owner_id"] for s in snapshot["schedule"]] + cfg.approvers)), p0, "project_confirm", "Please review your project commitments or stage authorization on your own page.")
            self._audit(actor, "project.confirmations_requested", "plan", p0["id"], p0, "Requested scoped commitments and separate stage authorization.")
            return {"outcome": "requested", "revision": rev["id"], "confirmations": self._project_confirmations(p0, rev)}
        return self.db.tx(run)

    def _project_confirmations(self, p, rev) -> list[dict]:
        return [{"scope_id": s["id"], "person": self.name(s["member_id"]), "kind": s["kind"], "item": s["item_id"],
                 "status": self._project_scope_state(s)[0]} for s in self.db.all("SELECT * FROM project_scopes WHERE revision_id=? ORDER BY member_id,kind,item_id", rev["id"])]

    def commit_project(self, actor: str, plan: str) -> dict:
        self._rate_limit(actor)
        def run():
            p = self._project(actor, plan)
            self._project_lead(p, actor)
            rev = self._project_revision(p)
            if rev and rev["status"] == "committed":
                checks = self._project_confirmations(p, rev)
                if p["status"] == "COMMITTED" and rev["input_digest"] == digest(self._project_inputs(p)) and checks and all(s["status"] in ("approved", "carried_forward") for s in checks):
                    return {"outcome": "already_committed", "revision": rev["id"]}
                return {"outcome": "blocked", "message": "The committed stage changed or a commitment was withdrawn. Prepare a repair."}
            if not rev or rev["status"] != "awaiting":
                return {"outcome": "blocked", "message": "Request scoped commitments and stage authorization first."}
            if rev["input_digest"] != digest(self._project_inputs(p)):
                return {"outcome": "blocked", "message": "The project changed; prepare a new schedule and confirmations."}
            checks = self._project_confirmations(p, rev)
            if not checks or any(s["status"] not in ("approved", "carried_forward") for s in checks):
                return {"outcome": "blocked", "message": "Some owner commitments or stage authorizations are still missing.", "confirmations": checks}
            cfg = self._project_config(p)
            if any(m not in self.plan_audience(p) or not self._workspace_role(p["group_id"], m, "approver") for m in cfg.approvers):
                return {"outcome": "blocked", "message": "Stage approver permissions changed."}
            snapshot = json.loads(rev["snapshot_json"])
            errors = validate_schedule(snapshot["schedule"], self._project_work(p), cfg, p["timezone"], p["start_date"], p["days"],
                                       self._project_capacities(p), self._project_reservations(p))
            if errors:
                return {"outcome": "blocked", "message": "Capacity changed before commit. Repair against the current reservations.", "problems": errors}
            # BEGIN IMMEDIATE covers the recheck and all replacements, across
            # processes as well as threads. The losing project must repair.
            self.db.run("DELETE FROM resource_reservations WHERE plan_id=?", p["id"])
            for item in snapshot["schedule"]:
                if item["start"] == item["end"]:
                    continue
                demand = dict(item["resources"], **{f"person:{item['owner_id']}": 1})
                for rid, units in demand.items():
                    self.db.run("INSERT INTO resource_reservations VALUES(?,?,?,?,?,?,?,?)", new_id("reservation"), p["id"], rev["id"], item["item_id"], rid,
                                item["start"], item["end"], units)
            self.db.run("UPDATE project_revisions SET status='committed' WHERE id=?", rev["id"])
            self.db.run("UPDATE project_config SET committed_revision=? WHERE plan_id=?", rev["id"], p["id"])
            self._bump(p["id"], "COMMITTED", "Validated commitments and stage authorization; resources reserved atomically.")
            self._audit(actor, "project.committed", "plan", p["id"], p, "Committed the stage schedule and reserved resources.", payload={"revision": rev["id"], "digest": rev["digest"]})
            return {"outcome": "committed", "revision": rev["id"], "message": "Stage committed. External spending or deployment is not executed by this local reservation."}
        return self.db.tx(run)

    def get_project(self, actor: str, plan: str, workstream: str | None = None, limit: int = 25, offset: int = 0) -> dict:
        if not 1 <= limit <= 100 or offset < 0:
            raise DomainError("INVALID_ARGUMENT", "limit is 1..100; offset is non-negative.")
        def run():
            p = self._project(actor, plan)
            cfg = self._project_config(p)
            work = self._project_work(p)
            visible_work = [w for w in work if workstream is None or w["spec"]["workstream"] == workstream]
            page = visible_work[offset:offset+limit]
            rev = self._project_revision(p)
            snapshot = json.loads(rev["snapshot_json"]) if rev else None
            fresh = bool(rev and rev["input_digest"] == digest(self._project_inputs(p)))
            checks = self._project_confirmations(p, rev) if rev else []
            resources = self.db.all("SELECT id,name,capacity FROM workspace_resources WHERE group_id=? ORDER BY name", p["group_id"])
            return {"plan": p["id"], "title": p["title"], "version": p["aggregate_version"], "stage": cfg.stage,
                    "window_start": p["start_date"], "days": p["days"], "timezone": p["timezone"], "currency": p["currency"],
                    "config": cfg.model_dump(mode="json", exclude={"private_calendars"}), "resources": resources,
                    "your_calendar": cfg.private_calendars.get(actor, cfg.calendar).model_dump(mode="json"),
                    "work": page, "total": len(visible_work), "next_offset": offset+limit if offset+limit < len(visible_work) else None,
                    "workstreams": sorted({w["spec"]["workstream"] for w in work}),
                    "confirmation_totals": {"required": len(checks), "satisfied": sum(c["status"] in ("approved", "carried_forward") for c in checks)},
                    "series": [{"id": s["id"], "title": json.loads(s["rule_json"])["title"]} for s in self.db.all("SELECT * FROM project_series WHERE plan_id=?", p["id"])],
                    "revision": {"id": rev["id"], "status": rev["status"], "fresh": fresh, "proof": json.loads(rev["solver_json"]),
                                 "cost_minor": snapshot["cost_minor"], "schedule": [s for s in snapshot["schedule"] if s["item_id"] in {w["id"] for w in page}],
                                 "confirmations": [c for c in checks if c["item"] is None or c["item"] in {w["id"] for w in page}]} if rev else None}
        return self.db.tx(run)

    def _plan_view(self, scope, plan):
        view = super()._plan_view(scope, plan)
        if self._is_project(plan):
            config = self._project_config(plan)
            rev = self._project_revision(plan)
            work = self._project_work(plan)
            # Plan summaries must not dump hundreds of tasks into an LLM turn.
            view.tasks = view.tasks[:12]
            view.project = {"stage": config.stage, "open_work": len(work), "workstreams": sorted({w["spec"]["workstream"] for w in work}),
                            "revision": rev["id"] if rev else None, "status": rev["status"] if rev else "draft",
                            "next": "get_project pages work/schedule; repair_project prepares a proposal; request_confirmations asks humans; commit_plan reserves resources."}
            checks = self._project_confirmations(plan, rev) if rev else []
            view.project["pending_by_person"] = {m: sum(c["person"] == self.name(m) and c["status"] not in ("approved", "carried_forward") for c in checks)
                                                 for m in self.plan_audience(plan)}
            view.next_step = view.project["next"]
            if rev and rev["input_digest"] != digest(self._project_inputs(plan)):
                view.project["status"] = "stale"
                view.status = "DEGRADED" if self._project_config_row(plan)["committed_revision"] else "DRAFT"
                view.status_reason = "Project inputs changed; prepare a fresh resource schedule."
        return view

    def _todos(self, scope, plans):
        from .views import TodoView
        todos = super()._todos(scope, plans)
        for p in plans:
            if not self._is_project(p):
                continue
            rev = self._project_revision(p)
            fresh = bool(rev and rev["input_digest"] == digest(self._project_inputs(p)))
            if fresh and rev["status"] == "awaiting":
                checks = self._project_confirmations(p, rev)
                mine = [c for c in checks if c["person"] == scope.name and c["status"] not in ("approved", "carried_forward")]
                if mine:
                    todos.append(TodoView(kind="confirm",plan=p["title"],text=f"Review {len(mine)} project work commitment(s) or stage authorization(s).",where="your GroupFlow page"))
                if checks and all(c["status"] in ("approved", "carried_forward") for c in checks) and self.is_organizer(p,scope.member_id):
                    todos.append(TodoView(kind="commit",plan=p["title"],text="All project scopes are satisfied; the stage can be committed after capacity recheck.",where="ask Alexa"))
            elif (not fresh or p["status"] == "DEGRADED") and self.is_organizer(p,scope.member_id):
                todos.append(TodoView(kind="repair",plan=p["title"],text="Prepare a fresh project resource schedule.",where="ask Alexa"))
        return todos

    def request_confirmations(self, actor_id, plan=None, **kwargs):
        p = self.resolve_plan(self.scope_for(actor_id), plan)
        if self._is_project(p):
            self._rate_limit(actor_id)
            self._check_version(p, kwargs.get("expected_version"))
            return self._project_send(actor_id, p)
        return super().request_confirmations(actor_id, plan=plan, **kwargs)

    def commit_plan(self, actor_id, plan=None, **kwargs):
        p = self.resolve_plan(self.scope_for(actor_id), plan)
        if self._is_project(p):
            self._check_version(p, kwargs.get("expected_version"))
            if kwargs.get("candidate") and (not self._project_revision(p) or self._project_revision(p)["id"] != kwargs["candidate"]):
                raise DomainError("STALE", "That project candidate is no longer current.")
            return self.commit_project(actor_id, p["id"])
        return super().commit_plan(actor_id, plan=plan, **kwargs)

    def human_inbox(self, member) -> dict:
        def run():
            result = super(Collaboration, self).human_inbox(member)
            projects = self.human_project_inbox(member)
            result.update(project_items=projects["items"], project_plans=projects["projects"])
            return result
        return self.db.tx(run)

    def human_project_inbox(self, member) -> dict:
        def run():
            scope = self.scope_for(member.id)
            items = []
            projects = []
            for p in scope.plans.values():
                if not self._is_project(p):
                    continue
                cfg = self._project_config(p)
                projects.append({"id": p["id"], "title": p["title"], "workspace": p["group_id"], "timezone": p["timezone"], "currency": p["currency"],
                                 "admin": self._workspace_admin(p["group_id"], member.id),
                                 "calendar": cfg.private_calendars.get(member.id, cfg.calendar).model_dump(mode="json"),
                                 "members": [{"id": m, "name": self.name(m)} for m in self.plan_audience(p)]})
                current = self._project_revision(p)
                committed = self.db.get("SELECT * FROM project_revisions WHERE id=?", self._project_config_row(p)["committed_revision"])
                revisions = {r["id"]: r for r in (committed, current) if r and r["status"] in ("awaiting", "committed")}
                for rev in revisions.values():
                    fresh = rev["input_digest"] == digest(self._project_inputs(p))
                    for s in self.db.all("SELECT * FROM project_scopes WHERE revision_id=? AND member_id=? ORDER BY kind,item_id", rev["id"], member.id):
                        state, receipt = self._project_scope_state(s)
                        items.append({"id": s["id"], "plan": p["title"], "plan_id": p["id"], "kind": s["kind"], "status": state,
                                      "payload": json.loads(s["payload_json"]), "expires_at": s["expires_at"], "receipt": receipt, "revision": rev["id"],
                                      "can_answer": bool(fresh and current and rev["id"] == current["id"] and rev["status"] == "awaiting"),
                                      "can_revoke": bool(receipt and state not in ("declined", "revoked")), "current": bool(current and current["id"]==rev["id"])})
            return {"items": items, "projects": projects, "member": {"id": member.id, "name": member.name}}
        return self.db.tx(run)

    def _human_project_scope(self, member, scope_id: str, answering: bool = True):
        s = self.db.get("SELECT * FROM project_scopes WHERE id=? AND member_id=?", scope_id, member.id)
        if not s:
            raise DomainError("FORBIDDEN", "This scope is not yours.")
        rev = self.db.get("SELECT * FROM project_revisions WHERE id=?", s["revision_id"])
        p = self._project(member.id, rev["plan_id"])
        current = self._project_revision(p)
        if answering:
            if not current or current["id"] != rev["id"] or rev["status"] != "awaiting" or rev["input_digest"] != digest(self._project_inputs(p)):
                raise DomainError("STALE", "This project schedule changed; reload the page.")
            if s["expires_at"] < iso_now() or s["fingerprint"] != self._project_scope_fingerprint(p, member.id, json.loads(s["payload_json"])):
                raise DomainError("STALE", "Your conditions, membership or approval validity changed.")
        elif not ((current and current["id"] == rev["id"]) or self._project_config_row(p)["committed_revision"] == rev["id"]):
            raise DomainError("STALE", "This historical scope is no longer current or committed.")
        if s["kind"] == "authorization" and not self._workspace_role(p["group_id"], member.id, "approver"):
            raise DomainError("FORBIDDEN", "Stage authorization requires a workspace approver role.")
        return p, s

    def human_project_challenge(self, member, scope_id: str) -> dict:
        def run():
            _, s = self._human_project_scope(member, scope_id)
            nonce, cid = new_secret(16), new_id("pch")
            expiry = (now() + timedelta(minutes=10)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            self.db.run("INSERT INTO project_challenges VALUES(?,?,?,?,?,?,?,0)", cid, s["id"], member.id, s["digest"], s["fingerprint"], hash_secret(nonce), expiry)
            return {"challenge_id": cid, "nonce": nonce, "digest": s["digest"], "expires_at": expiry, "payload": json.loads(s["payload_json"])}
        return self.db.tx(run)

    def human_project_decide(self, member, challenge_id: str, nonce: str, decision: str) -> dict:
        if decision not in ("approve", "decline"):
            raise DomainError("INVALID_ARGUMENT", "Use approve or decline.")
        def run():
            ch = self.db.get("SELECT * FROM project_challenges WHERE id=? AND member_id=?", challenge_id, member.id)
            if not ch or hash_secret(nonce) != ch["nonce_hash"]:
                raise DomainError("FORBIDDEN", "Invalid confirmation challenge.")
            if ch["used"]:
                raise DomainError("CHALLENGE_USED", "This challenge was already used.")
            if ch["expires_at"] < iso_now():
                raise DomainError("CHALLENGE_EXPIRED", "Open this scope again to get a fresh challenge.")
            p, s = self._human_project_scope(member, ch["scope_id"])
            if ch["digest"] != s["digest"] or ch["fingerprint"] != s["fingerprint"]:
                raise DomainError("STALE", "The confirmation content changed.")
            self.db.run("UPDATE project_challenges SET used=1 WHERE id=?", ch["id"])
            receipt = new_id("prcpt")
            self.db.run("INSERT INTO project_receipts VALUES(?,?,?,?,?,?,?,?)", receipt, s["id"], member.id, s["digest"], s["fingerprint"], decision, iso_now(), s["expires_at"])
            checks = self._project_confirmations(p, self._project_revision(p))
            ready = bool(checks) and all(c["status"] in ("approved", "carried_forward") for c in checks)
            blocked = any(c["status"] in ("declined", "revoked") for c in checks)
            self._bump(p["id"], "READY_TO_COMMIT" if ready else "BLOCKED" if blocked else "AWAITING_CONFIRMATIONS",
                       "All project commitments and stage authorizations are satisfied." if ready else "Waiting for project work commitments and stage authorization.")
            self._audit(member.id, "project.human_answer", "plan", p["id"], p, f"{member.name} answered their {s['kind']} scope.", private_to=member.id)
            return {"outcome": decision, "receipt": receipt}
        return self.db.tx(run)

    def human_project_revoke(self, member, scope_id: str) -> dict:
        def run():
            p, s = self._human_project_scope(member, scope_id, answering=False)
            self.db.run("INSERT INTO project_receipts VALUES(?,?,?,?,?,?,?,?)", new_id("prcpt"), s["id"], member.id, s["digest"], s["fingerprint"], "revoke", iso_now(), s["expires_at"])
            self._bump(p["id"], "DEGRADED", "A person withdrew their project commitment/authorization; organize a repair.")
            self._audit(member.id, "project.revoked", "plan", p["id"], p, "A person withdrew their own project commitment or authorization.")
            return {"outcome": "revoked"}
        return self.db.tx(run)

    def human_project_calendar(self, member, plan: str, calendar: dict) -> dict:
        def run():
            p = self._project(member.id, plan)
            cfg = self._project_config(p)
            cfg.private_calendars[member.id] = parsed(Calendar, calendar)
            self.db.run("UPDATE project_config SET config_json=? WHERE plan_id=?", cfg.model_dump_json(), p["id"])
            self._project_touch(p, member.id, "project.calendar_changed", "A member updated their scheduling availability; review the schedule.")
            return {"outcome": "updated", "message": "Your private availability was saved; other people only see scheduling effects."}
        return self.db.tx(run)

    def human_workspace_role(self, member, group: str, target: str, role: str, grant: bool) -> dict:
        def run():
            if not self._workspace_admin(group, member.id):
                raise DomainError("FORBIDDEN", "Only a workspace admin can grant or revoke workspace roles.")
            if role not in ("lead", "approver"):
                raise DomainError("INVALID_ARGUMENT", "Role must be lead or approver.")
            current = self._group_role(group, target)
            if not current or current["status"] != "active":
                raise DomainError("INVALID_ARGUMENT", "Target must be an active workspace member.")
            if grant:
                self.db.run("INSERT INTO workspace_roles VALUES(?,?,?,1) ON CONFLICT(group_id,member_id,role) DO UPDATE SET version=version+1", group, target, role)
            else:
                self.db.run("DELETE FROM workspace_roles WHERE group_id=? AND member_id=? AND role=?", group, target, role)
            self._audit(member.id, "workspace.role_changed", "group", group, None, f"Workspace {role} role {'granted' if grant else 'revoked'} for {self.name(target)}.", group_id=group)
            return {"outcome": "updated"}
        return self.db.tx(run)
