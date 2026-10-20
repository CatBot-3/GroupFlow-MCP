"""Additive receipts, personal delivery preferences and calendar publication."""
from groupflow.core.db import register_schema

register_schema('product_v1', """
CREATE TABLE IF NOT EXISTS action_receipts (
 id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
 operation TEXT NOT NULL, request_key TEXT, request_hash TEXT, audience TEXT NOT NULL,
 plan_ids_json TEXT NOT NULL, receipt_json TEXT NOT NULL, result_json TEXT NOT NULL,
 undo_json TEXT, corrected_by TEXT, created_at TEXT NOT NULL,
 UNIQUE(actor_id, request_key)
);
CREATE TABLE IF NOT EXISTS delivery_preferences (
 member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE, plan_id TEXT NOT NULL,
 muted INTEGER NOT NULL DEFAULT 0, digest INTEGER NOT NULL DEFAULT 0,
 quiet_start INTEGER, quiet_end INTEGER, timezone TEXT NOT NULL DEFAULT 'America/Toronto',
 PRIMARY KEY(member_id, plan_id)
);
CREATE TABLE IF NOT EXISTS calendar_links (
 id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS calendar_events (
 member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE, plan_id TEXT NOT NULL,
 activity_id TEXT NOT NULL, uid TEXT NOT NULL, sequence INTEGER NOT NULL,
 fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL, changed_at TEXT NOT NULL,
 PRIMARY KEY(member_id, plan_id, activity_id)
);
""")
