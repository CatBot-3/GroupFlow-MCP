# Information that helps the assistant act

GroupFlow v0.5 focuses its product demonstration on four friends repairing a Friday dinner. The family, trip, study, household and project worlds remain tests of shared MCP structures: ownership, visibility, typed items, conditions, revisions, confirmation scopes and persistent state.

## Why a semantic memory map

A database ER diagram helps a developer inspect storage. A judge first needs to see which information reaches a particular assistant session and what the tools do with it. The default visualization therefore maps an actual identity-filtered `get_context` response, rather than fetching all tables from an admin endpoint.

The map shows a speaker, their groups, visible plans, returned items and activities, plan versions, public confirmation state, and their own private conditions. Selecting a category reveals individual records, their source field and relationships. Multiple versions of one activity are separate versioned records, not extra activities in the evening. The server has already applied group and space permissions before returning these records; missing nodes do not reveal hidden records or counts.

The current graph is a semantic projection, not a complete database export. It intentionally excludes credentials, private limits belonging to others, raw approval nonces, and admin-only data. Ordinary MCP calls still expose the same 21 tools; visualization adds no tool to approve for someone.

## Two observations, kept separate

| View | Evidence | Meaning |
| --- | --- | --- |
| Visible memory | Fresh, read-only MCP panel response with `mark_read=false` | What this identity can read now; may be paged or summarized |
| This speaker's last turn | Recorded host briefing, model-selected calls, server results, and final reply | What was returned during that turn, and whether it was actually sent to a subsequent model request |

Each call records `sent_to_model`. A call made on the last available model step can return a result without another model request ever consuming it. The UI marks this case explicitly. The delivered content is the same compact value placed in the tool message, including an error wrapper on refusals.

These are observable inputs and outputs, **not hidden model reasoning**, and do not prove which individual field causally influenced the model. A transport-success response can contain an application-level blocked outcome. The UI makes that distinction rather than declaring every HTTP success an action success.

Switching identity clears the rendered snapshot while its new request loads. A trace from another group or one containing a no-longer-visible plan is not replayed. Reset and forget clear the relevant recorded evidence. Failed MCP reads never fall back to the unfiltered inspector. The local simulator's selectable personas are demo identities, not production authentication.

## Confirmation effort as an observable result

The main screen reports distinct changed activity IDs, valid carried-forward confirmations and remaining answers from current MCP state. Committed plans retain a compact `agreement_summary`; the result does not disappear when the candidate becomes active. A venue and price change to the same dinner count as one changed activity.

`scripts/compare_confirmation_policies.py` executes the same synthetic repair in two independent in-memory instances. One deliberately disables only carry-forward. Both use the actual solver, human-scope engine and commit gate, and must share an identical candidate digest. In the dinner fixture, full reconfirmation needs 16 new human receipts; scoped reuse needs four and preserves twelve. This is 75% fewer confirmation requests for that fixture, not a measured user time saving or competitor benchmark.

The route solver's lexicographic repair criteria and finite-domain proof still apply. Structured projects use changed starts, moved minutes and priority-weighted completion; they do not claim to globally minimize the number of renewed human scopes.

## Verification

- Unit/integration checks exercise hidden plans, private conditions, pagination omissions, actual delivered model content, exhausted model budgets, reset/forget, and removed visibility.
- The original 108 scenarios remain unchanged in scope and separate from eight new structure-transfer cases under `eval/holdout`. These were fixed before their first model run. Once used to diagnose changes, they are regression cases rather than unseen cases.
- Unsupported expectation keys fail loading instead of silently yielding a passing scenario.
- Enterprise lifecycle runs now cover several people, a dependency chain, own effort changes, repair, scoped work/stage requests, a false Slack consent claim, real human HTTP answers with replay rejection, commit, cleared model history, another edit, carried scopes, a decline, a blocked commit and later recommit.
- Release reports include source hashes, a source-stability check at completion and P95 measured over individual model turns. Independent full repetitions are reported separately and can be pooled only when their source hash and evaluation settings match.

Browser validation and paid-model results are recorded separately from deterministic tests. Simulated approvals are never described as an independent human user study.
