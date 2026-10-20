"""Additive project-collaboration tables. The original event/agreement schema stays byte-stable."""
from groupflow.core.db import register_schema

register_schema("collaboration_v1", """
CREATE TABLE IF NOT EXISTS plan_extensions (
  plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
  template_json TEXT NOT NULL,
  template_version TEXT NOT NULL,
  preset TEXT,
  lifecycle TEXT NOT NULL CHECK(lifecycle IN ('bounded','ongoing')),
  planning_window_days INTEGER NOT NULL CHECK(planning_window_days BETWEEN 1 AND 366)
);
CREATE TABLE IF NOT EXISTS project_config (
  plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
  config_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  current_revision TEXT,
  committed_revision TEXT
);
CREATE TABLE IF NOT EXISTS project_work (
  item_id TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  spec_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  series_id TEXT,
  recurrence_id TEXT,
  UNIQUE(series_id, recurrence_id)
);
CREATE INDEX IF NOT EXISTS project_work_plan ON project_work(plan_id);
CREATE TABLE IF NOT EXISTS project_series (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  rule_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS workspace_roles (
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('lead','approver')),
  version INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(group_id,member_id,role)
);
CREATE TABLE IF NOT EXISTS workspace_resources (
  id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  capacity INTEGER NOT NULL CHECK(capacity BETWEEN 1 AND 1000),
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(group_id,name)
);
CREATE TABLE IF NOT EXISTS project_revisions (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK(status IN ('candidate','awaiting','committed','superseded')),
  snapshot_json TEXT NOT NULL,
  input_digest TEXT NOT NULL,
  digest TEXT NOT NULL,
  solver_json TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES members(id),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS project_revisions_plan ON project_revisions(plan_id,created_at);
CREATE TABLE IF NOT EXISTS project_scopes (
  id TEXT PRIMARY KEY,
  revision_id TEXT NOT NULL REFERENCES project_revisions(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id),
  kind TEXT NOT NULL CHECK(kind IN ('commitment','authorization')),
  item_id TEXT REFERENCES items(id),
  payload_json TEXT NOT NULL,
  digest TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  UNIQUE(revision_id,member_id,kind,item_id)
);
CREATE TABLE IF NOT EXISTS project_challenges (
  id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES project_scopes(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id),
  digest TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  nonce_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0,1))
);
CREATE TABLE IF NOT EXISTS project_receipts (
  id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES project_scopes(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id),
  digest TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('approve','decline','revoke')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_reservations (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES project_revisions(id),
  item_id TEXT NOT NULL REFERENCES items(id),
  resource_id TEXT NOT NULL,
  start_utc TEXT NOT NULL,
  end_utc TEXT NOT NULL,
  units INTEGER NOT NULL CHECK(units > 0)
);
CREATE INDEX IF NOT EXISTS resource_reservations_time ON resource_reservations(resource_id,start_utc,end_utc);
""")
