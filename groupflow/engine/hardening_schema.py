"""Additive tables: heard-update tracking, repeated-write detection and OAuth sign-in. Earlier tables stay byte-stable."""
from groupflow.core.db import register_schema

register_schema("hardening_v1", """
CREATE TABLE IF NOT EXISTS update_deliveries (
 notification_id TEXT PRIMARY KEY, member_id TEXT NOT NULL, deliveries INTEGER NOT NULL DEFAULT 0,
 first_at TEXT NOT NULL, last_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS heard_tokens (
 token TEXT PRIMARY KEY, member_id TEXT NOT NULL, ids_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS heard_tokens_member ON heard_tokens(member_id);
CREATE TABLE IF NOT EXISTS recent_writes (
 actor_id TEXT NOT NULL, request_hash TEXT NOT NULL, receipt_id TEXT NOT NULL, wall_at REAL NOT NULL,
 PRIMARY KEY(actor_id, request_hash)
);
CREATE TABLE IF NOT EXISTS oauth_clients (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, secret_hash TEXT, redirect_uris_json TEXT NOT NULL,
 grant_types_json TEXT NOT NULL, scopes_json TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS oauth_grants (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES oauth_clients(id), member_id TEXT REFERENCES members(id) ON DELETE CASCADE,
 scope TEXT NOT NULL, resource TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS oauth_grants_member ON oauth_grants(member_id);
CREATE TABLE IF NOT EXISTS oauth_codes (
 code_hash TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE, redirect_uri TEXT NOT NULL,
 challenge TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
 token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
 grant_id TEXT REFERENCES oauth_grants(id) ON DELETE CASCADE, client_id TEXT NOT NULL, member_id TEXT,
 scope TEXT NOT NULL, resource TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT, replaced_by TEXT
);
CREATE INDEX IF NOT EXISTS oauth_tokens_grant ON oauth_tokens(grant_id);
""")
