# Known limitations

GroupFlow is a hackathon build. These are the boundaries we know about, so nobody mistakes the demo for more than it is.

## Scope

1. **The Echo devices are simulated.** The page drives a real LLM (DeepSeek by default, Amazon Bedrock or any OpenAI-compatible endpoint as options) through real MCP sessions, but it is not Alexa+ itself. The server is built to be added to Alexa+ as a remote MCP server; that connection was not available to us for testing.
2. **Bookings are simulated.** After a commit, GroupFlow records booking receipts with idempotency keys and a `SIM-` reference for the activity kinds a template marks as bookable. No restaurant, room or ride service is contacted.
3. **Verified outside facts come from the demo console.** In a product, a trusted connector (a venue's booking system, a room calendar) would report that a place became unavailable. A member telling Alexa is stored as an unverified report and does not change the plan.
4. **Notifications are pulled, not pushed.** People hear news, reminders and check-ins the next time they talk to Alexa or open their page (the group chat shows them at once in their private thread).
5. **Templates are written by hand.** A new plan type within the generic item and policy model needs a YAML template (item types, policies, conditions, an optional catalog of places). New semantics such as structured project scheduling also require implementation and dedicated tools. The short directory and `get_template` discover new YAML without changing tool descriptions; restart after editing templates because the registry is cached. Alexa can create plans from those templates, but cannot invent new template definitions.

## Security

6. **Demo credentials.** Bearer tokens and page passcodes for the thirteen demo people are in `demo/world.yaml`, and the demo page fills the passcode in for you. People who sign up in the group chat choose their own passcode (stored with salted scrypt) and link an assistant on their own page, which issues that host a revocable token: a simulation of account linking, not OAuth. A real deployment needs OAuth account linking for the MCP token and a real sign-in (with recovery) for the confirmation page.
7. **The demo console and reset endpoints** are protected by a shared admin key meant for a local demo only. Turn them off with `GROUPFLOW_DEMO_ADMIN=0`.
8. **Rate limiting** of agent writes is per person and in memory.

## Planning and repair

9. **The search domain is finite by design.** Each activity can keep its option, move to another place of the same kind, or shift by the offsets its template lists. `optimal_within_domain` is optimal among those options only. Auto routing switches to CP-SAT at 16,384 combinations for fixed-order domains. Drop/reorder options use enumeration; the proof shows the actual backend. This is a locally calibrated heuristic, and easy sparse repairs can still favour enumeration well above it. See [benchmark and timeout handling](SOLVER_BENCHMARK.md).
10. **Travel times come from a fixed zone table** in each template, not a maps service.
11. **One time zone per group.** Legacy activity plans use minute offsets from the first local day. Structured projects convert each local workday separately to UTC and test DST transitions.
12. **Conflict explanations** name the kinds of conditions that clash (a latest time, a budget, a diet, a busy window) and never whose; in very small groups people may still be able to guess.
13. **Places outside a catalog are free text.** Plans made from `general_event` accept any place name, so GroupFlow cannot check opening hours or prices for them.

## Agent behaviour

14. **The LLM can still phrase things imperfectly.** The server decides every outcome (who may change what, what counts as confirmed, what anyone may see), so a wrong sentence cannot authorize a server-side commit. The model can still guess or imply a private condition incorrectly; the evaluation measures such output separately from actual server data exposure. It can still misread which plan someone means when the same words fit two of their plans, or answer without calling a tool; the evaluation report lists every failure we measured.
15. **Chat history is short** (six turns per person) and lives in the simulator's memory. The plans, approvals and conditions live on the server and survive restarts. Each turn refreshes a compact index and the focused plan; switching groups clears chat and focus. More plan details are read on demand, so the model may need an extra tool round. Current and historical paid-model evaluations are reported separately in `EVALUATION.md`; raw failed runs are preserved.
16. **Voice input** uses the browser's speech recognition, which works in Chrome and Edge.

## Platform

17. **SQLite, one process.** The worker that runs searches for alternatives runs inside the server process. The data access layer is in one module, so PostgreSQL could replace SQLite later.
18. **OR-Tools on Windows** needs the Microsoft Visual C++ runtime 14.40 or newer. Some Python distributions (Anaconda, for one) ship an older copy next to `python.exe`; GroupFlow loads the system copy first, and if none is new enough route repair uses exact enumeration instead. Project scheduling returns an explicit unavailable result because that model requires interval CP-SAT.
19. **Windows event loop.** The servers run on asyncio's selector event loop on Windows, because with the default proactor loop one abruptly closed browser connection can stop the server from accepting new ones (see the friction log). The selector loop is limited to about 500 open sockets, far above what the demo uses.

## Group chat (v0.6)

20. **A demonstration host.** The group chat keeps messages and the tokens people linked to it unencrypted in its own SQLite file next to the GroupFlow database. It delivers live events only while the page is open; there is no email, SMS or push.
21. **Listeners are declared by the host.** `X-GroupFlow-Listeners` narrows the speaker's scope to what every listener may see and can never widen it, but the server cannot check who is really reading a chat or standing near a device. A host that forgets the header answers as on a personal device.
22. **Membership changes.** Joining or leaving a group changes who a plan is for, so a version out for confirmation must be sent again (unchanged answers carry forward). A person who leaves stops seeing the group's shared plans at once, but activities that list them as a participant still need the organizer to update who is coming. Only organizers create invites.
23. **The realistic held-out suite has not been scored with a paid model yet.** Its 30 scenarios are validated deterministically (each is solvable and each fails under trivial policies); run it with a configured provider before quoting a pass rate.

## Telling people and asking first (v0.7)

24. **Notices are pulled.** `notify_people` keeps each notice on the server until the person next talks to their assistant (it is in their `get_context` updates); the group chat posts it to their private thread at once. Nothing reaches a phone or an Echo by itself.
25. **The note check is a phrase check.** A note that names a plan kept from a recipient (its title or a place or text value) is refused, but a paraphrase can pass. The facts in a notice cannot leak, because recipients are limited to people who can see the plan.
26. **Elicitation is opt-in and untested on Alexa+.** It needs `GROUPFLOW_MCP_RESPONSES=sse` and a host that declares form elicitation; it is tested over real MCP with the official SDK client only. It is never used for consent.
27. **Offers and answers are heuristics.** The server offers to tell the others only after decisions on details. An answer without a question id is matched against the newest pending questions by label, time, position, yes or no, and shared words; an answer that matches nothing is refused rather than guessed. The vague-time guard covers time changes whose words name no time, day, date or amount; vagueness about other things is left to the assistant.
28. **The follow-up suite has not been scored with a paid model.** Its ten scenarios are validated like the realistic suite, and they were written in the same change as the feature.

## Reminders and check-ins (v0.8)

29. **Reminders and check-ins are pulled too.** They wait on the server as notifications and reach the person on their next turn with Alexa, on their GroupFlow page, or in their private thread in the group chat. A real deployment would hand them to a push channel (Alexa notifications, if an add-on is allowed to send them, or SMS/email); we found no documented way for an Alexa+ MCP add-on to do that.
30. **Times are read by rules, in English.** `set_reminder` understands exact times, delays and times relative to the plan's date; anything else is refused and asked about rather than guessed. "Next Friday" means the coming Friday, which some people would not; the reply reads the resolved date back. A plan's own date is its first activity or a date detail whose name says so (date, time, when, day); a household plan has none.
31. **Quiet hours belong to the group, not the person.** Check-ins go out between 8 am and 10 pm in the group's time zone. Reminders go out at the time they were set for, whatever the hour.
32. **What counts as stuck is a fixed list.** Pending answers, open suggestions, the organizer's next step, overdue tasks with a due date the server can read, and unset details or unstarted tasks close to the date. A plan can be stuck in ways this list does not see (an unanswered question in a group chat, say). Tasks without a readable due date are never overdue.
33. **The model reviewer is untested with a real model.** Its guardrails are tested with scripted, including hostile, answers; whether a given model judges "stuck" well, or words nudges that people actually answer, has not been measured. The text check on its messages is the same phrase check as for notes, now against every plan member's view, so a paraphrase can still pass; the model is only given the plan's own facts, which every recipient may see.
34. **The demo's skip-ahead does not move the clock.** It runs the worker's pass as if the time had passed and records rounds at the real time, so it shows the sequence of messages, not the exact wording of elapsed times across several presses.

## Visualization and final validation boundaries (v0.5)

The memory visualization maps identity-filtered MCP results, not all database records or hidden model reasoning. A fresh panel snapshot is separate from the recorded last model turn; results are marked as delivered only after a subsequent model request consumes them. Missing nodes can reflect pagination or summary limits. The simulator's selectable personas are test identities, not production account linking.

Personal task summaries include live visible dependency blockers, and plan summaries include canonical group IDs. The host binds the actual request text to plan creation. The server refuses omitted audience for explicit “just/only with” wording; this bounded check does not parse every possible natural-language privacy intent. Another host must bind authentic request text itself.

The final paid-model source completed one full run at 107/108. Two additional repetitions ended with DeepSeek HTTP 402 (insufficient balance); those are incomplete experiments even though the old runner visited every scenario. Original reports are preserved. The evaluator now stops immediately on provider 401/402/403, marks the report incomplete and exits unsuccessfully. No account top-up was performed. New chats need a funded/configured provider; saved plans and the read-only graph remain inspectable.

The remaining full-run failure inferred a different Saturday and opened an owner-review request; the agreed date did not change. Another partial run confused two plans' venues and was refused. Eight additional structure cases repeated three times scored 23/24; one generic denial triggered a fixed broad phrase-ban assertion. Raw scores were not rewritten.

No independent participant study has been conducted. A fixed formative protocol is provided in [USER_TEST_PROTOCOL.md](USER_TEST_PROTOCOL.md); scripted human answers and API tests are not user research.

## Structured-project boundaries (v0.4)

See [PROJECTS.md](PROJECTS.md) for the complete implemented contract. A work unit is one contiguous 15-minute-grid block up to eight hours; split multi-day work. Limits: 400 open units per window, 200 occurrences per expansion, a 1–366-day window. The default three-second budget may time out on larger models; a validated feasible result is not always proven optimal. Project confirmations expire separately from the planning window. Recurrence is an explicit DAILY/WEEKLY subset, not a full RRULE parser. Workspace roles, resource reservations and scoped human gates are local implementations; SSO/SCIM, external calendars, real payment/deployment and real Alexa+ account linking are not production integrations. Migration is additive only from the recognized legacy schema; unsupported files are preserved and refused.
