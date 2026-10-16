"""Additive v0.7 table: follow-up questions, held by the server with the exact action behind each answer.

suggested: the server offered it in a tool result (e.g. "want me to tell the others?"); the assistant may or may not ask it.
open: the assistant asked it (ask_followup), so a bare "yes" from the speaker answers it.
"""
from groupflow.core.db import register_schema

register_schema("followups_v1", """
CREATE TABLE IF NOT EXISTS followups (
  id TEXT PRIMARY KEY,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  plan_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('offer', 'clarify', 'ask')),
  question TEXT NOT NULL,
  options_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('suggested', 'open', 'answered', 'cancelled')),
  answer TEXT,
  result_json TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  answered_at TEXT
);
CREATE INDEX IF NOT EXISTS followups_member ON followups(member_id, status, plan_id);
""")
