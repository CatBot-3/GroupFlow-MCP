"""GroupFlow tables. Money is integer minor units; timestamps are UTC ISO strings.

Groups hold people; spaces inside a group decide who can see a plan (a
surprise party lives in a space that leaves the guest of honour out). A plan is
made from a template and holds items: details and tasks are rows here with
their history in `facts`; activities live in immutable plan revisions, because
everyone taking part must confirm the exact version before it counts.
"""
from groupflow.core.db import register_schema

register_schema("engine", """
CREATE TABLE IF NOT EXISTS groups (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  timezone TEXT NOT NULL,
  currency TEXT NOT NULL,
  description TEXT
);
CREATE TABLE IF NOT EXISTS group_members (
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('organizer', 'member')),
  label TEXT,
  confirmation_mode TEXT NOT NULL DEFAULT 'scoped' CHECK (confirmation_mode IN ('scoped', 'whole_plan')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'left')),
  membership_version INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (group_id, member_id)
);
CREATE TABLE IF NOT EXISTS spaces (
  id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('shared', 'private')),
  description TEXT
);
CREATE TABLE IF NOT EXISTS space_members (
  space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  PRIMARY KEY (space_id, member_id)
);
CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL REFERENCES groups(id),
  space_id TEXT NOT NULL REFERENCES spaces(id),
  template TEXT NOT NULL,
  title TEXT NOT NULL,
  start_date TEXT NOT NULL,
  days INTEGER NOT NULL DEFAULT 1,
  timezone TEXT NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'DRAFT', 'PROPOSED', 'AWAITING_CONFIRMATIONS', 'READY_TO_COMMIT', 'COMMITTED',
                                          'DEGRADED', 'REPAIRING', 'BLOCKED', 'NEEDS_ATTENTION', 'CANCELLED')),
  status_reason TEXT,
  organizer_id TEXT NOT NULL REFERENCES members(id),
  aggregate_version INTEGER NOT NULL DEFAULT 1,
  draft_revision_id TEXT,
  candidate_revision_id TEXT,
  active_revision_id TEXT,
  catalog_json TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL REFERENCES members(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  shape TEXT NOT NULL CHECK (shape IN ('detail', 'task', 'activity')),
  type TEXT NOT NULL,
  label TEXT NOT NULL,
  owner_id TEXT NOT NULL REFERENCES members(id),
  policy TEXT NOT NULL CHECK (policy IN ('owner', 'participants', 'open')),
  value_kind TEXT NOT NULL DEFAULT 'text',
  aliases TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 0,
  current_fact_id TEXT,
  status TEXT,
  due TEXT,
  archived INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL REFERENCES members(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_plan ON items(plan_id, shape);
CREATE TABLE IF NOT EXISTS utterances (
  id TEXT PRIMARY KEY,
  member_id TEXT NOT NULL REFERENCES members(id),
  space_id TEXT NOT NULL REFERENCES spaces(id),
  text TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  value TEXT NOT NULL,
  norm TEXT NOT NULL,
  certainty TEXT NOT NULL CHECK (certainty IN ('tentative', 'committed')),
  status TEXT NOT NULL CHECK (status IN ('confirmed', 'superseded', 'pending_review', 'tentative', 'rejected', 'corroborating')),
  proposed_by TEXT NOT NULL REFERENCES members(id),
  utterance_id TEXT REFERENCES utterances(id),
  created_at TEXT NOT NULL,
  decided_by TEXT REFERENCES members(id),
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS facts_item ON facts(item_id, created_at);
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  item_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('value', 'task', 'activity')),
  fact_id TEXT REFERENCES facts(id),
  change_json TEXT NOT NULL DEFAULT '{}',
  summary TEXT NOT NULL,
  certainty TEXT NOT NULL CHECK (certainty IN ('tentative', 'committed')),
  assignee_id TEXT NOT NULL REFERENCES members(id),
  requested_by TEXT NOT NULL REFERENCES members(id),
  utterance_id TEXT REFERENCES utterances(id),
  base_version INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'accepted', 'rejected', 'amended', 'stale')),
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  resolved_by TEXT REFERENCES members(id),
  note TEXT
);
CREATE INDEX IF NOT EXISTS requests_open ON requests(item_id, status);
CREATE TABLE IF NOT EXISTS links (
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  depends_on_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('waits_on', 'watches')),
  PRIMARY KEY (item_id, depends_on_id),
  CHECK (item_id <> depends_on_id)
);
CREATE TABLE IF NOT EXISTS flags (
  id TEXT PRIMARY KEY,
  target_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  cause_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  space_id TEXT NOT NULL REFERENCES spaces(id),
  owner_id TEXT NOT NULL REFERENCES members(id),
  message TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'cleared')),
  created_at TEXT NOT NULL,
  cleared_at TEXT,
  cleared_by TEXT REFERENCES members(id)
);
CREATE INDEX IF NOT EXISTS flags_owner ON flags(owner_id, status);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY,
  member_id TEXT NOT NULL REFERENCES members(id),
  plan_id TEXT,
  space_id TEXT,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  read_at TEXT
);
CREATE INDEX IF NOT EXISTS notifications_member ON notifications(member_id, read_at);
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor_id TEXT,
  group_id TEXT,
  plan_id TEXT,
  space_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  detail TEXT NOT NULL,
  before_value TEXT,
  after_value TEXT,
  utterance_id TEXT,
  private_to TEXT,
  aggregate_version INTEGER,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_target ON audit(target_type, target_id, seq);
CREATE INDEX IF NOT EXISTS audit_plan ON audit(plan_id, seq);

CREATE TABLE IF NOT EXISTS plan_revisions (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  parent_id TEXT,
  label TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('draft', 'candidate', 'committed', 'superseded', 'discarded')),
  source TEXT NOT NULL CHECK (source IN ('draft', 'proposal', 'edit', 'repair')),
  snapshot_json TEXT NOT NULL,
  content_digest TEXT NOT NULL,
  input_versions TEXT NOT NULL DEFAULT '{}',
  repair_job_id TEXT,
  alternate INTEGER NOT NULL DEFAULT 0,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conditions (
  id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL REFERENCES groups(id),
  plan_id TEXT REFERENCES plans(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL REFERENCES members(id),
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  strength TEXT NOT NULL CHECK (strength IN ('hard', 'soft')),
  value_json TEXT NOT NULL,
  visibility TEXT NOT NULL CHECK (visibility IN ('private', 'group')),
  status TEXT NOT NULL CHECK (status IN ('unverified', 'active', 'retired')),
  version INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL CHECK (source IN ('agent', 'human', 'seed')),
  created_at TEXT NOT NULL,
  verified_at TEXT,
  retired_at TEXT
);
CREATE INDEX IF NOT EXISTS conditions_owner ON conditions(plan_id, owner_id, status);
CREATE TABLE IF NOT EXISTS confirmation_rounds (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES plan_revisions(id),
  status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'superseded')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE TABLE IF NOT EXISTS approval_scopes (
  id TEXT PRIMARY KEY,
  revision_id TEXT NOT NULL REFERENCES plan_revisions(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id),
  scope_kind TEXT NOT NULL CHECK (scope_kind IN ('activity', 'whole_plan')),
  activity_id TEXT NOT NULL DEFAULT '',
  canonical_json TEXT NOT NULL,
  scope_digest TEXT NOT NULL,
  UNIQUE (revision_id, member_id, scope_kind, activity_id)
);
CREATE TABLE IF NOT EXISTS approval_receipts (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES plan_revisions(id),
  scope_id TEXT NOT NULL REFERENCES approval_scopes(id),
  member_id TEXT NOT NULL REFERENCES members(id),
  decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'conditional')),
  condition_json TEXT,
  reason TEXT,
  scope_digest TEXT NOT NULL,
  candidate_digest TEXT NOT NULL,
  challenge_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('valid', 'revoked', 'expired', 'invalidated')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS receipts_scope ON approval_receipts(scope_id, status);
CREATE TABLE IF NOT EXISTS approval_carry_forwards (
  id TEXT PRIMARY KEY,
  receipt_id TEXT NOT NULL REFERENCES approval_receipts(id),
  target_revision_id TEXT NOT NULL REFERENCES plan_revisions(id),
  scope_id TEXT NOT NULL REFERENCES approval_scopes(id),
  dependency_fingerprint TEXT NOT NULL,
  check_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (receipt_id, target_revision_id)
);
CREATE TABLE IF NOT EXISTS challenges (
  id TEXT PRIMARY KEY,
  member_id TEXT NOT NULL REFERENCES members(id),
  revision_id TEXT NOT NULL REFERENCES plan_revisions(id),
  scope_id TEXT NOT NULL REFERENCES approval_scopes(id),
  scope_digest TEXT NOT NULL,
  candidate_digest TEXT NOT NULL,
  nonce_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
CREATE TABLE IF NOT EXISTS human_sessions (
  token_hash TEXT PRIMARY KEY,
  member_id TEXT NOT NULL REFERENCES members(id),
  csrf_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL REFERENCES groups(id),
  plan_id TEXT,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  verification TEXT NOT NULL CHECK (verification IN ('unverified', 'verified', 'rejected')),
  source TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  actor_id TEXT,
  received_at TEXT NOT NULL,
  applied_at TEXT,
  UNIQUE (source, source_event_id)
);
CREATE TABLE IF NOT EXISTS repair_jobs (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  base_revision_id TEXT NOT NULL REFERENCES plan_revisions(id),
  input_digest TEXT NOT NULL,
  input_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
  method TEXT NOT NULL,
  solver_budget_ms INTEGER NOT NULL,
  result_status TEXT,
  result_json TEXT,
  error TEXT,
  requested_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS repair_candidates (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES repair_jobs(id) ON DELETE CASCADE,
  rank INTEGER NOT NULL,
  revision_id TEXT NOT NULL REFERENCES plan_revisions(id),
  alternate INTEGER NOT NULL,
  objective_json TEXT NOT NULL,
  burden_json TEXT NOT NULL,
  diff_json TEXT NOT NULL,
  reconfirm_json TEXT NOT NULL,
  explanation_json TEXT NOT NULL,
  proof_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES plan_revisions(id),
  activity_id TEXT NOT NULL,
  action TEXT NOT NULL,
  action_digest TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'unknown', 'compensation_required', 'cancelled')),
  provider_reference TEXT,
  simulated INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
  member_id TEXT NOT NULL,
  key TEXT NOT NULL,
  tool TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (member_id, key)
);
""")
