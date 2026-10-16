# GroupFlow improvement plan

Written 2026-10-16 against commit `3b7dbd0` (v0.5).

**Status (v0.6, same day).** Two parts of this plan are now implemented on this branch. From item 3: a realistic held-out evaluation suite in its own world, with zero sentence overlap with the prompt, reference trajectories and negative controls ([evaluation](EVALUATION.md)); it has not been scored with a paid model yet. From item 4 (and the listener part of item 12): a group chat that people can sign up to, with groups, invite links, simulated account linking and a server-side listener restriction for shared conversations ([group chat](GROUP_CHAT.md)). The other items are unchanged. The local suite passes (362 tests in about two minutes), so this plan is about direction and missing pieces, not about broken code. Time to implement is deliberately ignored; the order still reflects what matters most for an Alexa+ submission and for real use.

## Where the project stands

GroupFlow's core is that the server, not the model, decides who may change what, what counts as consent, and who can see what. Approvals are bound to a digest of exactly what a person saw, the repair solver keeps unchanged confirmations instead of asking everyone again, and hidden plans are never loaded for people outside their space. That core is implemented and tested (version digests, carry-forward checks, CP-SAT with an independent validator, a separate human-only API), so it is the part to build on rather than rework.

The gaps are in what surrounds that core. Three problems dominate:

1. **It has never run on Alexa+, and several of its guarantees depend on the simulator rather than on the server.** The simulated Echo has a 5,800-character system prompt, an automatic "you claimed an action you didn't take" correction round, and binds the real utterance into `quote`. A real Alexa+ host provides none of these. The 107/108 evaluation score measures the simulator plus the server, not what Alexa+ users would get.
2. **A real group cannot use it.** Groups, members and credentials exist only in `demo/world.yaml`. There is no way to create a group, invite someone, or reach a person who does not happen to talk to their Echo. A product whose whole value is coordinating several people has no channel to reach those people.
3. **The story is spread thin.** Five groups, eight templates, legacy templates, presets, and a full enterprise project scheduler with recurrence and workspace roles. The consumer demo carries 21 tools, and the remaining model failures are all of the same kind: the model hesitating between plans. More surface area makes that worse, and judges have less time to find the core idea.

The plan below is ordered by those three problems, followed by voice quality, security, code health and submission packaging.

## Summary

| # | Improvement | Why it matters | Size |
| --- | --- | --- | --- |
| 1 | Deploy publicly and connect to Alexa+ with OAuth account linking | Without it, the Alexa+ track entry is a simulation of Alexa+ | L |
| 2 | Move guardrails from the simulator prompt into the server | Alexa+ owns the system prompt; only server behavior transfers | M |
| 3 | Evaluate honestly: no test literals in prompts, real held-out set, bare-host mode, Amazon models | Current score partly measures memorized examples | M |
| 4 | Create groups and invite people by voice; link-based guests | No real group can exist today | M |
| 5 | Deliver notifications (push, SMS/email, Alexa notifications) | The repair loop stalls until each person happens to speak | M |
| 6 | One-tap confirmation links instead of passcode pages | The consent step is the highest-friction moment in the flow | M |
| 7 | Plan from scratch: "find a night next week that works for all of us" | The most common group request is unsupported; only repair is | L |
| 8 | Real facts: calendar free/busy, real venues, hand-off booking to Alexa+ | Conditions are typed by hand and bookings are fake | L |
| 9 | Day-of features: reminders, "running late", leave-by times | Groups need the plan most on the day itself | S–M |
| 10 | Server-written speakable summaries and fewer round trips | Latency and wording errors are the voice UX | M |
| 11 | Resolve ambiguity on the server (which plan, which person, ASR noise) | All remaining eval failures are ambiguity hesitation | M |
| 12 | Shared-device identity: recognized, unrecognized and guest speakers | An Echo in a kitchen is used by several people | M |
| 13 | Separate the enterprise project module from the Alexa+ submission | Fewer tools, one story | S |
| 14 | Security hardening for anything beyond localhost | Unsalted SHA-256 passcodes, default admin key, unbounded session map | S–M |
| 15 | Code and repository health | 1,800-line modules, mixed-language docs, 270k lines of eval output | M |
| 16 | Submission packaging: pitch-first README, video, hosted demo | Judges spend minutes, not hours | S |

## Phase 1: make it real on Alexa+

### 1. Public deployment, OAuth and account linking

**What is there now.** `groupflow/mcp_server/app.py` hardcodes `allowed_hosts` to localhost variants, so even with `GROUPFLOW_HOST=0.0.0.0` behind a public domain, the SDK's DNS-rebinding check rejects every request whose `Host` header is the real domain. Identity is a static bearer token per person from `demo/world.yaml`. The 401 response carries `WWW-Authenticate: Bearer realm="groupflow"` but no `resource_metadata`, so a spec-compliant MCP client cannot discover how to authorize.

**What to change.**

1. Make allowed hosts and origins configuration, not code, and deploy behind TLS (a single container on App Runner, ECS or Fly is enough). Add a `Dockerfile` and a one-command deploy.
2. Implement MCP authorization as the 2025-11-25 spec describes it: publish OAuth Protected Resource Metadata (RFC 9728) at `/.well-known/oauth-protected-resource`, point it at an authorization server (Cognito or Login with Amazon is the natural choice for an Alexa+ entry), validate access tokens on `/mcp` (audience, expiry, scopes), and include `resource_metadata` in the 401 challenge.
3. Map the token subject to a GroupFlow member through a `linked_accounts` table instead of the `members.token_hash` column, so one person can link several devices and the demo tokens become one adapter among others.
4. Register the server with Alexa+ through whatever the hackathon's Alexa+ integration path is, and record a real end-to-end run in `docs/PROTOCOL_EVIDENCE.md` next to the simulator capture. Check the actual Alexa+ documentation for how it negotiates protocol versions, caches `tools/list`, and treats tool annotations, and adapt rather than assume.

**Why.** This is the line between "an MCP server that could be added to Alexa+" and "an Alexa+ experience". `docs/PRODUCT_FEEDBACK.md` already lists what the team would ask Amazon for; running against the real host is what turns those asks into evidence rather than predictions.

**Done when** a person links their account in the Alexa app, asks their real Echo "what's happening Friday", and the request arrives at `/mcp` with a validated token that maps to their member id.

### 2. Move the guardrails into the server

**What is there now.** Much of the model's good behavior is taught by `groupflow/sim/agent.py`: the long system prompt, the `claims_without_action` regex with its correction round, the `PREFETCH` of `get_context` before every turn, and binding the speaker's real words into `quote`. `docs/KNOWN_LIMITATIONS.md` says it directly: "Another host must bind authentic request text itself." In Alexa+, none of that exists. Only the server's `instructions` string (one paragraph), tool descriptions, and tool results reach the model.

**What to change.**

1. **Treat the server instructions and tool results as the only prompt.** Move each rule that matters from the simulator prompt into one of three places: the server `instructions`, the relevant tool description, or, best of all, the tool result itself. A refused `request_confirmations` result can say "Tell the organizer what breaks and offer a repair; do not call find_alternatives until they ask." The model reads that exactly when it is relevant, whatever host it runs in.
2. **Add a `say` field to every write result**: one privacy-safe sentence, written by the server, describing the outcome ("Your idea to start at 7 is saved as a suggestion; Alice decides the time."). This replaces the regex-based "did you really do that" check with something the model can quote, and it puts the exact outcome wording under the server's control.
3. **Make privacy the default instead of relying on `quote`.** The audience check for "just with Dana and Bob" only works when the host passes the real utterance. Make the server safe without it: a new plan starts visible only to its creator until the creator adds people, or `create_plan` requires an explicit `audience` choice (`whole_group`, `named`, `only_me`). An omitted value then fails safe rather than leaking.
4. **Make `get_context` read-only.** It is annotated `read_only=False` because it marks notifications as heard. A host that asks the user before non-read-only calls would add a confirmation prompt to every turn. Default `mark_read` to false and add a small `mark_heard` tool, or mark notifications heard only when the server-written `say` text that contains them is returned.

**Why.** The safety claims ("the model cannot give anyone's consent") already hold on the server. The behavior claims (hedges recorded as tentative, negations not stored, organizer told before sending a repair) do not, because they live in a prompt that Alexa+ will not use. Moving them makes the evaluation describe the product.

**Done when** the evaluation suite passes at a similar rate with the simulator in "bare host" mode (item 3), meaning a short generic assistant prompt, no prefetch, no correction round, and no `quote` binding.

### 3. An evaluation that measures the product

**What is there now.** The simulator prompt contains the evaluation's own sentences or near copies: "ice cream cake", "Dana can only stay until 8, set that for her", "Chen can't come, take him off", "I have a dentist appointment Tuesday 9 to noon", "Remember the gift idea, just for me". The same literals appear in tool descriptions (`propose_change`, `set_my_conditions`). Those exact lines are scenarios in `eval/scenarios/family_single.yaml` and `dinner_adversarial.yaml`. The paid-model runs all use DeepSeek. The eight-case holdout became regression data once it was used for diagnosis, as `docs/EVALUATION.md` acknowledges.

**What to change.**

1. Replace every example in the prompt and tool descriptions that matches a scenario with an abstract rule or a differently worded example. Keep a CI check that fails when a scenario line appears in prompt or description text.
2. Build a real held-out set: generate paraphrases, new names, new venues and new domains (a book club, a kids' playdate, a moving day) from the scenario intents, freeze it, and run it only for release numbers.
3. Add a `--host bare` mode to `groupflow eval` (item 2) and report both modes side by side.
4. Run on Amazon models through the existing Bedrock provider (Nova, and Claude on Bedrock) in addition to DeepSeek. That is closer to what Alexa+ uses and shows the server design holds across models.
5. Add voice-shaped inputs: ASR-style errors ("Chen" heard as "Jen", "for" vs "four"), disfluencies ("uh, actually, make it, no, seven"), and very short follow-ups ("yeah do that").
6. Run the participant protocol in `docs/USER_TEST_PROTOCOL.md` with three to five real people, even informally. One observed person confused by "carried forward" is worth more to the design than another 108 synthetic runs.

**Why.** A score of 107/108 on scenarios whose sentences are in the prompt says the model can follow examples it was shown. Judges who read the prompt will notice. A lower but honest held-out number in bare-host mode is more convincing, and it tells the team where the server still needs to carry more of the load.

## Phase 2: make it useful to a real group

### 4. Groups, invitations and people without an Echo

**What is there now.** No tool or API creates a group, adds or removes a member, or changes roles in a consumer group. All of it comes from YAML at seed time.

**What to change.**

1. Add `create_group`, `invite_member` (by phone or email, producing a link) and `leave_group` to the MCP tools, and an accept-invite flow on the human page. Membership changes go through the same consent principle: joining is something only the invited person does, on their own page.
2. Let a member exist with no Alexa at all. They receive links, confirm on the web page and can state conditions there. In practice most groups will have one Echo owner and several friends on phones.
3. Handle leaving cleanly: a person who leaves loses visibility immediately, their confirmations on open versions are withdrawn, and their private conditions stop counting.

**Why.** The first thing a real user says is "make a group with my sister and two friends". Today that sentence cannot succeed. The person-without-an-Echo case decides whether the product works at all for a typical friend group.

### 5. Notifications that actually reach people

**What is there now.** `_notify` in `groupflow/engine/agreement.py` writes to an outbox, and the worker delivers it to an in-app queue that is read the next time the person talks to Alexa. `docs/KNOWN_LIMITATIONS.md` item 4 says notifications are pulled, not pushed.

**What to change.** Add delivery adapters behind the outbox: email and SMS for the confirmation link, web push for the page, and Alexa notifications (the yellow ring) if the Alexa+ integration exposes proactive notifications to MCP servers. Add per-person preferences (quiet hours, which kinds of news) and rate limits, and keep the content of a notification as privacy-filtered as `get_context` already is.

**Why.** The repair loop the demo celebrates (restaurant closes, repair, four confirmations, commit) only completes if the four people find out. Without push delivery, the organizer has to message everyone in another app, which removes the reason to use GroupFlow. The outbox already exists, so this is plumbing on top of a sound design.

### 6. One-tap confirmation

**What is there now.** A person signs in with a member id and passcode, requests a challenge, and answers it. The demo page fills the passcode in.

**What to change.**

1. Send a signed, single-use magic link per confirmation scope. Opening it shows exactly what changed (old against new, the person's own cost and times) with Confirm and Decline buttons. The link is bound to the scope digest, so the existing replay protection stays.
2. Design an optional voice path for later: if Alexa+ can provide a host-level, voice-ID-backed confirmation that the model cannot trigger by itself, accept its signed result as a receipt type. Keep the web link as the baseline. This is the "out-of-band confirmation channel" already requested in `docs/PRODUCT_FEEDBACK.md`.
3. Show the change as a short diff, because "Dinner moves from Restaurant A to Restaurant B, +CAD 2 each, same time" is what people decide on.

**Why.** Requiring confirmation outside the model is the project's central safety idea, and it is also the step most likely to make people give up. A passcode screen on a phone loses many of them. A one-tap link keeps the guarantee and removes most of the friction.

### 7. Plan from scratch, not only repair

**What is there now.** `find_alternatives` requires a plan that already has activities (`"has no activities to rearrange"`) and searches around them. Choosing a date in the first place has no tool support.

**What to change.** Add a `suggest_times` (or `find_options`) tool that takes a duration, a window ("next week, evenings") and participants, and ranks slots against everyone's verified conditions without revealing whose condition rules a slot out. Reuse the existing domain, validator and conflict explanation. Optionally offer a quick poll: send the top three slots to everyone's page and let the organizer pick once answers arrive.

**Why.** "When can we all do dinner?" is the most common group-planning question there is, and it is exactly where private limits matter most. The solver and the privacy-preserving explanation already exist; pointing them at an empty plan turns a repair engine into a planning assistant.

### 8. Real facts instead of hand-typed ones

**What is there now.** Venue catalogs, opening hours, prices and a travel-time zone table live in template YAML. Busy windows are typed by hand. A venue closing comes from the demo console. Bookings are simulated `SIM-` receipts.

**What to change.**

1. **Calendar free/busy import.** With the person's consent on their own page, read free/busy (Google, Outlook or the calendar Alexa already knows) and turn it into `busy` conditions that only that person can see. This removes the most tedious step and makes "find a night" (item 7) accurate.
2. **Venues from a real source.** A places API for hours, price level and tags, with the YAML catalog as an offline fallback.
3. **Booking as a hand-off.** After commit, GroupFlow should not book anything itself. It should hand Alexa+ a precise booking intent ("table for 4 at Restaurant B, Friday 19:00"), and Alexa+ can complete it through its own booking integrations, then report the confirmation back as a verified fact. This positions GroupFlow as the consent layer that composes with Alexa+, rather than a competitor to it.
4. **Verified facts from a connector.** Venue availability changes arrive from the booking or places source instead of the demo console; member reports stay unverified exactly as they are now.

**Why.** Every one of these replaces a demo-only shortcut with the thing a real user would expect, and item 3 is a strong Alexa+ story: Alexa already books things, GroupFlow makes sure the group agreed before it does.

### 9. Day-of features

Add reminders before a committed activity, a "leave by 18:40 to reach Restaurant B" hint using the travel table, and a "running late" or "can't make it" flow that tells the group and, if needed, starts a repair for the remaining people. The `cannot_attend` observation and the participants policy already exist; the missing parts are scheduling and delivery (item 5).

**Why.** A plan is used most on the day it happens. These are also natural voice moments ("Alexa, tell the group I'm ten minutes late"), so they show Alexa+ in its best setting.

## Phase 3: voice quality

### 10. Speakable output and latency

**What is there now.** Mean turn latency is 3.2 s and P95 is 6.1 s with DeepSeek, before speech synthesis. Every turn starts with a `get_context` prefetch, and turns average about two model calls each.

**What to change.** Return the server-written `say` sentence (item 2) so simple turns need one tool call and almost no generation. Cache the summary context per person and invalidate it on change events. Return `structuredContent` with an `outputSchema` alongside a short text block, so the model does not need to read large JSON. Set a latency budget per turn type (for example under 2 s for a status question) and track it in the evaluation report.

**Why.** On a voice device, a six-second pause feels broken. Fewer rounds and shorter generated text also reduce the chance of a wrong sentence, which is the remaining class of error.

### 11. Resolve ambiguity on the server

**What is there now.** The remaining evaluation failures are the model asking "which plan?" or choosing the wrong plan when a person is in several groups ("the cake", "the venue"). The fixes so far are prompt sentences.

**What to change.** Let item-taking tools accept a loose reference without `plan`, have the server rank candidates (recent focus, recency of mention, item type match, whether the speaker owns or participates), and act on a clear winner while returning `resolved_to` so the model can say which plan it used. Return a short candidate list only when the ranking is genuinely tied. Add fuzzy matching of member names against the speaker's groups for ASR errors.

**Why.** The server already knows everything needed to resolve "the cake" for Bob, and it applies the same rule every time. The model guessing differently on each run is what produces the flaky failures.

### 12. Shared devices and unrecognized speakers

Define what happens when Alexa cannot tell who is speaking on a kitchen Echo: read-only access to plans visible to every household member, no writes, and no private conditions. Recognized speakers get their own scope. Store the device as a separate identity from the person so "Alice's Echo" and "the kitchen Echo" behave correctly. This also covers the surprise-party case where the guest of honour talks to the same device.

**Why.** Per-person identity on shared devices is the first item in `docs/PRODUCT_FEEDBACK.md`. The server should have a defined, safe behavior for the unrecognized case rather than depending on the host to always know.

## Phase 4: focus, security and code health

### 13. Separate the enterprise project module

Move `get_project`, `configure_project`, `upsert_work`, `manage_recurrence` and `repair_project` to a separate MCP endpoint (for example `/mcp/projects`) or behind a feature flag, and keep the consumer server at about fifteen tools. Drop the legacy templates from the default directory. Keep the module in the repository as a second product direction.

**Why.** Each extra tool is another option the model can pick wrongly, and the consumer evaluation already shows plan-choice hesitation. For judges, a six-week enterprise release schedule is a different product from four friends repairing dinner; showing both makes neither memorable.

### 14. Security hardening

1. Hash passcodes with a salted, slow KDF (argon2 or scrypt) instead of the unsalted SHA-256 in `groupflow/core/util.py:hash_secret`. SHA-256 is fine for random tokens, not for human-chosen secrets.
2. Refuse to start with the default `gf_demo_admin` key or with debug routes when the host is not localhost.
3. Bound the in-memory `McpAuth.sessions` map, expire entries with the MCP session, and move rate limits into the database so they survive restarts and multiple processes.
4. Revisit small-group inference: an explanation that says "a latest-time limit" in a group of three where only one person ever sets such limits still identifies them. Use a minimum group size for kind-level explanations and fall back to "it doesn't work for everyone" below it.
5. Add per-person data export and deletion, which any real deployment needs.

### 15. Code and repository health

1. Split `groupflow/engine/agreement.py` (1,804 lines) and `groupflow/engine/core.py` (1,464 lines) by responsibility: revisions and digests, human confirmations, repair jobs, notifications.
2. Translate `docs/CONTEXT_DESIGN.md`, `docs/PROJECTS.md` and the completion reports to English, or keep both languages, since judges will read English.
3. Move `eval/runs/` (about 236,000 of the repository's 297,000 tracked lines) and `docs/GroupFlow_v04_changes.patch` to release attachments, keeping only a summary table in the repository.
4. Prepare SQLite to PostgreSQL migration behind the existing data access layer, so the server can run more than one process once it is public.
5. Fix small inconsistencies, for example "five safety numbers" in `docs/DEMO_SCRIPT.md` against the six counters elsewhere.

### 16. Submission packaging

Rewrite the README so the first screen is a two-sentence pitch, a 90-second video or GIF of the friends story, and a link to a hosted demo that needs no API key. Move the detailed evaluation caveats, which are valuable and honest, into `docs/EVALUATION.md` and link them. End with "what we would build next" taken from this plan, since a credible roadmap is part of what judges assess.

## Suggested order

If only a few items get done, do them in this order, because each one makes the next more convincing:

1. Item 2 and item 3 together: server-side guardrails and a bare-host evaluation. They are mostly moving text and adding one evaluation mode, and they make every later claim believable.
2. Item 1: real Alexa+ connection. It is the track's premise.
3. Items 4, 5 and 6: groups, notifications and one-tap confirmation. Together they make the friends story work for real people.
4. Item 13 and item 16: narrow the scope and repackage the submission.
5. Items 7 and 8: planning from scratch, calendars and the booking hand-off, which turn a repair demo into a planning assistant.

## Open questions to settle first

1. **What does the hackathon's Alexa+ integration actually support?** Protocol versions, OAuth flow, whether `structuredContent` and tool annotations are honored, and whether MCP servers can send notifications. Items 1, 5, 6 and 10 depend on the answers, so read the official integration documentation before designing them in detail.
2. **Who is the target user?** Friend groups and families point to items 4 to 9; teams point back to the project module. Picking one shapes the README, the demo and the default tool set.
3. **Is booking in scope?** If Alexa+ booking hand-off is not available to third-party servers, keep bookings simulated and say so, and put the effort into calendars and notifications instead.

## Next round (after v0.6)

Building the group chat, the held-out suite and the casual-speech prompt exposed a second set of gaps. In order:

1. **Score every suite with real models, including Amazon's.** Re-run the original suite (its recorded scores predate the casual-speech prompt), then the holdout and realistic suites, three runs each, with DeepSeek and with Amazon Nova through the existing Bedrock provider. Report suites separately. Why: nothing about the new prompt or the realistic suite has met a model yet, and showing that the same server rules hold under two different models is the strongest evidence that safety lives in the server, not in one model's habits.
2. **Spoken input, not only typed casualness.** Alexa+ hears speech: transcripts have no punctuation or emoji, carry fillers and false starts ("um so like move it to uh friday"), say numbers as words ("seven thirty", "quarter to eight") and mishear names ("Ines" as "Inez"). Generate a spoken variant of the realistic suite with a deterministic transform, and later a true round trip through text-to-speech and speech recognition. A useful question to ask first: which server lookups compare strings exactly? Member names are one, so a misheard name fails before the model can recover; phonetic matching of names and spoken-number parsing are the matching server fixes.
3. **Server-written replies (`say`) and a bare-host run.** This round improved a host prompt that Alexa+ will never see; only the server's instructions, tool descriptions and results reach its model. Returning one plain outcome sentence with each write, and evaluating with a minimal host prompt, measures what Alexa+ users would actually get (item 2 above, still open).
4. **A false-action rate for group chats.** In a room the main risk flips from missing a request to acting on chatter. Measure the share of turns that wrote something nobody asked for, over a few hundred chatter and reaction messages; the `nothing_changed` expectation and the record-everything control already exist. Once that rate is known and low, consider a suggestion mode where Alexa notices "anyone free sat?" and offers to start a plan, but writes nothing until asked.
5. **Rooms that hear news.** A room learns something only when someone asks Alexa. Post one short message to a room when a plan everyone there can see changes (a venue closes, a version goes out, it becomes agreed, someone joins), read through the room's listener-restricted session so a room never hears hidden news. The repair loop is GroupFlow's central story, and a room is where judges can watch it happen live.
6. **Everyday dates and times.** The date-guard bug the realistic suite found is a sign of where the system is brittle. Add a table-driven test of the server's parsing for "the 28th", "this sat", "next next fri", "tmrw night", "half 7", "after work" and "in two weeks", and decide for each whether the server resolves it or asks; asking is a correct answer for "after work".
7. **Nicknames and family words.** People say "mum", "my sister", "Raf". The server resolves exact names and the group's role labels, which are written from one point of view ("Mum", "Daughter"): "mum" works, but Kofi saying "my sister" finds nobody, and a loose match that picked the wrong person would send a request to the wrong owner, which matters more in a consent system than a missed request. Let each person add nicknames on their own page, and resolve relation words relative to the speaker.
8. **Show it live.** Deploy with `GROUPFLOW_SIGNUP=invite` and record the family-room moment on two phones: Linda is in the chat, someone asks about Saturday, Alexa answers without the surprise. Twenty seconds of that explains the listener restriction better than any document.
