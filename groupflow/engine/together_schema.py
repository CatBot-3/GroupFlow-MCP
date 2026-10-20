"""Additive tables: polls and decide-by defaults, RSVPs and a bring list, and shared costs."""
from groupflow.core.db import register_schema

register_schema("together_v1", """
CREATE TABLE IF NOT EXISTS polls (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  item_id TEXT REFERENCES items(id) ON DELETE SET NULL,
  base_fact_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('choice', 'objection')),
  question TEXT NOT NULL,
  options_json TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES members(id),
  decider_id TEXT NOT NULL REFERENCES members(id),
  closes_at TEXT,
  closes_text TEXT,
  fallback TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'decided', 'cancelled')),
  decided_option TEXT,
  decided_by TEXT,
  decided_how TEXT,
  result TEXT,
  created_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE INDEX IF NOT EXISTS polls_plan ON polls(plan_id, status);
CREATE INDEX IF NOT EXISTS polls_due ON polls(status, closes_at);
CREATE TABLE IF NOT EXISTS poll_votes (
  poll_id TEXT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  choice TEXT NOT NULL,
  reason TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (poll_id, member_id)
);
CREATE TABLE IF NOT EXISTS rsvps (
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  answer TEXT NOT NULL CHECK (answer IN ('yes', 'no', 'maybe')),
  plus_ones INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (plan_id, member_id)
);
CREATE TABLE IF NOT EXISTS bring_items (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  claimed_by TEXT REFERENCES members(id) ON DELETE SET NULL,
  added_by TEXT NOT NULL REFERENCES members(id),
  status TEXT NOT NULL CHECK (status IN ('open', 'removed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bring_plan ON bring_items(plan_id, status);
CREATE TABLE IF NOT EXISTS expenses (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  payer_id TEXT NOT NULL REFERENCES members(id),
  amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
  currency TEXT NOT NULL,
  description TEXT NOT NULL,
  shares_json TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES members(id),
  status TEXT NOT NULL CHECK (status IN ('active', 'pending', 'removed')),
  created_at TEXT NOT NULL,
  confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS expenses_plan ON expenses(plan_id, status);
CREATE TABLE IF NOT EXISTS settlements (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  from_id TEXT NOT NULL REFERENCES members(id),
  to_id TEXT NOT NULL REFERENCES members(id),
  amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
  currency TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES members(id),
  status TEXT NOT NULL CHECK (status IN ('active', 'pending', 'removed')),
  created_at TEXT NOT NULL,
  confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS settlements_plan ON settlements(plan_id, status);
""")
