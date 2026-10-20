"""Additive tables: reminders at a chosen time, per-plan check-in settings, and the record of check-in reminders already sent."""
from groupflow.core.db import register_schema

register_schema("momentum_v1", """
CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  created_by TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  audience_json TEXT NOT NULL,
  about_json TEXT NOT NULL,
  note TEXT,
  when_text TEXT NOT NULL,
  due_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('scheduled', 'sent', 'cancelled')),
  created_at TEXT NOT NULL,
  sent_at TEXT,
  result TEXT
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status, due_at);
CREATE TABLE IF NOT EXISTS plan_checkins (
  plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
  quiet_hours INTEGER NOT NULL,
  updated_by TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nudges (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  blocker TEXT NOT NULL,
  text TEXT NOT NULL,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS nudges_plan ON nudges(plan_id, member_id, blocker);
""")
