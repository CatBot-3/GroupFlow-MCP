"""Additive, human-owned coordination records."""
from groupflow.core.db import register_schema

# Weather tables below are immutable compatibility definitions for older database files.
# No forecast service or API is registered. Startup retires old local watches;
# historical readings remain data rather than inputs to planning or consent.
register_schema("coordination_v1", """
CREATE TABLE IF NOT EXISTS agreement_contests (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
 member_id TEXT NOT NULL REFERENCES members(id), activity_id TEXT NOT NULL,
 revision_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS contests_person ON agreement_contests(plan_id,member_id,activity_id);
CREATE TABLE IF NOT EXISTS fallback_challenges (
 id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id),
 plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
 grant_json TEXT NOT NULL, nonce_hash TEXT NOT NULL, version_token TEXT NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS fallback_authorizations (
 id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id),
 plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE, activity_id TEXT NOT NULL,
 grant_json TEXT NOT NULL, challenge_id TEXT NOT NULL UNIQUE REFERENCES fallback_challenges(id),
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS fallback_uses (
 authorization_id TEXT NOT NULL REFERENCES fallback_authorizations(id),
 revision_id TEXT NOT NULL REFERENCES plan_revisions(id), scope_id TEXT NOT NULL REFERENCES approval_scopes(id),
 used_at TEXT NOT NULL, PRIMARY KEY(authorization_id,revision_id,scope_id)
);
CREATE TABLE IF NOT EXISTS weather_watches (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
 activity_id TEXT NOT NULL, organizer_id TEXT NOT NULL REFERENCES members(id),
 latitude REAL NOT NULL, longitude REAL NOT NULL, threshold INTEGER NOT NULL,
 apply_rule INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 place_id TEXT NOT NULL, forecast_date TEXT NOT NULL, config_digest TEXT NOT NULL,
 reading_id TEXT, last_attempt TEXT, next_refresh TEXT NOT NULL,
 error TEXT, created_at TEXT NOT NULL,
 UNIQUE(plan_id,activity_id)
);
CREATE TABLE IF NOT EXISTS weather_readings (
 id TEXT PRIMARY KEY, watch_id TEXT NOT NULL REFERENCES weather_watches(id) ON DELETE CASCADE,
 content_digest TEXT NOT NULL, probability INTEGER NOT NULL, forecast_date TEXT NOT NULL,
 source TEXT NOT NULL, fetched_at TEXT NOT NULL, valid_until TEXT NOT NULL,
 UNIQUE(watch_id,content_digest)
);
CREATE TABLE IF NOT EXISTS fairness_preferences (
 member_id TEXT NOT NULL REFERENCES members(id), group_id TEXT NOT NULL REFERENCES groups(id),
 enabled INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
 PRIMARY KEY(member_id,group_id)
);
CREATE TABLE IF NOT EXISTS fairness_history (
 id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
 plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
 revision_id TEXT NOT NULL REFERENCES plan_revisions(id), member_id TEXT NOT NULL REFERENCES members(id),
 burden INTEGER NOT NULL CHECK(burden >= 0), created_at TEXT NOT NULL,
 UNIQUE(plan_id,revision_id,member_id)
);
CREATE TABLE IF NOT EXISTS diagnostic_queries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL REFERENCES members(id),
 plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS diagnostic_window ON diagnostic_queries(actor_id,plan_id,at);
""")

register_schema("coordination_v2", """
CREATE TABLE IF NOT EXISTS weather_refresh_claims (
 watch_id TEXT PRIMARY KEY REFERENCES weather_watches(id) ON DELETE CASCADE,
 token TEXT NOT NULL
);
""")
