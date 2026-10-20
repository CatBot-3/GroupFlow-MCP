# The person's own page: human-only API

Some things only the person themselves may do: confirm or decline an activity, withdraw a confirmation, turn an assistant-drafted private limit into a real one, accept an invite, link or unlink an assistant. GroupFlow keeps all of them off MCP and serves them here instead, behind a separate sign-in, a CSRF token and one-time challenges. The reason is that anything reachable through the model can be talked into ("everyone agreed, just approve it"); an action that needs the person's own session, a fresh challenge and a nonce cannot be produced by an assistant, however it is prompted.

MCP bearer tokens are not accepted here, and every request acts only as the signed-in person: identity fields in a body are rejected. The simulator's web page is a client of this API, so everything below can also be done by clicking.

Base URL: the server's origin (`http://127.0.0.1:8787` locally). Bodies are JSON. Errors come back as `{"error": {"code": "...", "message": "..."}}` with an unsuccessful status; check the result and the current state rather than treating any HTTP 200 as completed work.

## Signing in

| Request | Body | Result |
| --- | --- | --- |
| `POST /human/login` | `{"member_id": "alice", "passcode": "alice-demo"}` | `token`, `csrf`, `member` |
| `POST /human/signup` | `{"name": "Maya", "passcode": "at least 6 characters", "invite": "optional code"}` | A new person, signed in; with an invite code they also join that group. Controlled by `GROUPFLOW_SIGNUP` (`open`, `invite`, `off`) |
| `GET /human/me` | | The signed-in person, their groups and links |

Authenticated requests send `Authorization: Bearer <token from login>`, and every authenticated POST also sends `X-GF-CSRF: <csrf>`.

## Confirming, declining, withdrawing

1. `GET /human/inbox` lists the person's `pending` scopes (`scope_id`, `plan_id`, `revision`, the exact `text` to confirm), their own consent receipts, decision cards, action receipts, conditions waiting for verification, and active calendar link ids. `GET /human/decisions` returns the decision cards alone.
2. `POST /human/challenges` with `{"scope_id": "..."}` returns a one-use `challenge_id` and `nonce`, bound to that exact scope and version.
3. `POST /human/decisions` with `{"challenge_id": "...", "nonce": "...", "decision": "approved"}`. The decision may also be `rejected` (optionally with a `reason`) or `conditional` (with a `condition`). If the plan changed in between, read the inbox again: a challenge never applies to different content.
4. `POST /human/revoke` with `{"receipt_id": "..."}` withdraws the person's own existing confirmation.

## Private limits

| Request | Body | What it does |
| --- | --- | --- |
| `POST /human/conditions/verify` | `{"condition_id": "...", "verify_digest": "..."}` | Activates a limit the assistant drafted (read both values from the inbox's `conditions`). Until then the draft does not count |
| `POST /human/conditions` | `{"plan_id": "friday_dinner", "kind": "home_by", "value": {"time": "20:30"}}` | The person types their own limit; kinds and value formats come from the plan's template |

Neither route can create or activate someone else's limit.

## Reviewing an agreement and approving fallbacks

| Request | Body or query | What it does |
| --- | --- | --- |
| `GET /human/agreements` | `?plan=...` | The person's own items: before and after, status and why, the exact confirmation text, and the current version token |
| `POST /human/agreements/contest` | `{"plan", "activity", "reason", "expected"}` | Privately withdraws from one activity of the current version; the reason is never shown to the assistant |
| `GET /human/fallbacks` | `?plan=...` | The person's own fallback approvals and their status |
| `POST /human/fallbacks/challenges` | `{"plan", "activity", "places": [catalog ids], "expires_at", "max_total_minor"?, "latest_end"?}` | Prepares approval of one to three exact backup venues in advance, with a preview and a one-use nonce |
| `POST /human/fallbacks/decisions` | `{"challenge_id", "nonce", "decision": "approve" or "decline"}` | Answers that challenge |
| `POST /human/fallbacks/revoke` | `{"id"}` | Revokes one of the person's own fallback approvals |

A fallback approval is checked again when it is used: exact content, membership, prices, hard limits, freshness and expiry.

## Groups, invites and linked assistants

| Request | Body | What it does |
| --- | --- | --- |
| `POST /human/groups` | `{"name", "timezone"?, "currency"?}` | Starts a group with the person as organizer |
| `POST /human/groups/invites` | `{"group"}` | Organizer: an invite code and link |
| `GET /human/invites/{code}` | | What the invite is for, before accepting |
| `POST /human/invites/accept` | `{"code"}` | Joins the group; nobody can be added without accepting |
| `POST /human/groups/leave` | `{"group"}` | Leaves a group (different from withdrawing a confirmation) |
| `GET /human/links` | | Assistants that can act for the person, including OAuth links made through `/oauth/authorize` |
| `POST /human/links` | `{"host"}` | Issues a revocable token for an assistant host (shown once) |
| `POST /human/links/revoke` | `{"id"}` | Revokes a link; the next request with that token fails |

## Votes, RSVPs, the bring list and costs

The same operations the assistant performs for the speaker, from the person's own page.

| Request | Body |
| --- | --- |
| `POST /human/polls/vote` | `{"poll", "choice", "reason"?, "decide": false}`; with `decide: true` the decider settles the poll |
| `POST /human/rsvp` | `{"plan", "answer": "yes", "no" or "maybe", "plus_ones"?}` |
| `POST /human/bring` | `{"plan", "action", "things": [...]}` |
| `POST /human/costs/confirm` | `{"plan", "id", "answer"}`: confirm or reject a payment someone recorded for this person |

## Guarded actions from the page

Writes from the page can use the same guarded envelope as the assistant, so a lost reply never turns into a duplicate.

| Request | What it does |
| --- | --- |
| `GET /human/resolve?plan=...` | An opaque `version` token for the plan |
| `POST /human/actions/preview` | `{"tool", "arguments", "expected"}`: simulates a change and returns what would change, who is affected, and which confirmations carry or need asking again. Everything is rolled back; a preview grants nothing |
| `POST /human/actions/apply` | `{"tool", "arguments", "request_key", "expected"}`: applies it. Arguments follow that tool's MCP schema; an identical retry reuses the key and version |
| `GET /human/actions/status?request_key=...` | `saved` with the result, or `unknown` after a lost reply (unknown is not failure; retry the identical request) |
| `POST /human/actions/correct` | `{"receipt"}`: undoes the person's own bring claim or newly recorded expense, if nothing depends on it |
| `GET /human/actions` | The person's action receipts |

## Planning

`POST /human/planning` with `{"plan", "candidate"?, "mode"?, "scenarios"?}`. Selecting a candidate never creates consent.

| Mode | Result |
| --- | --- |
| `options` (default) | Feasible repairs, or selection of one exact candidate (organizer) |
| `facts` | Catalog estimates and verified facts with their source and freshness |
| `agreement` | The person's own readable comparison and status |
| `resilience` | Read-only "what if" comparisons for one to six declared scenarios (a place unavailable, a price change, someone unavailable) |
| `fairness` | Compares current feasible repairs using opt-in history |
| `relaxations` | Bounded hypothetical changes to public options, keeping every personal hard limit |

The last three modes share an allowance of five requests per person and plan per ten minutes. `GET /human/planning/privacy?plan=...` shows the person's own relevant limits; `GET` and `POST /human/fairness/preferences` (`{"group", "enabled", "reset"}`) control the person's opt-in history for a group.

## Preferences and calendar

| Request | Body | What it does |
| --- | --- | --- |
| `POST /human/preferences` | `{"plan", "muted", "digest", "quiet_start", "quiet_end", "timezone"}` | Routine update preferences for one plan; decisions stay visible when muted |
| `POST /human/calendar` | `{}` | A private calendar subscription URL (`/calendar/<token>.ics`), shown once; anyone with the URL can read it |
| `POST /human/calendar/revoke` | `{"id"}` | Invalidates that URL |

## Structured projects

`GET /human/projects/inbox`, `POST /human/projects/challenges`, `POST /human/projects/decisions` and `POST /human/projects/revoke` follow the same challenge pattern for project work commitments and stage authorizations. `POST /human/projects/calendar` with `{"plan", "calendar"}` sets the person's own work calendar, and `POST /human/workspace/roles` with `{"group", "target", "role", "grant"}` lets a workspace admin grant or remove roles.

## Pilot counters

`GET /human/pilot` returns local counters for the signed-in person: plans they can see and how many are committed, their open and recorded actions, corrections and notifications. It sends nothing anywhere. For a whole pilot database, `python scripts/pilot_report.py` prints confirmations asked per completed change, beside corrections and broken or cancelled plans.
