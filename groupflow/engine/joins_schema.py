"""Additive table: a join asked for by voice ("join Alice's group, the code is ..."), waiting for the person's own confirmation."""
from groupflow.core.db import register_schema

register_schema("joins_v1", """
CREATE TABLE IF NOT EXISTS join_requests (
  id TEXT PRIMARY KEY,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  invite_id TEXT NOT NULL REFERENCES group_invites(id) ON DELETE CASCADE,
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('pending', 'joined', 'declined', 'expired')),
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS join_requests_member ON join_requests(member_id, status);
""")
