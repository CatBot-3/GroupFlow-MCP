# Known limitations

GroupFlow is a hackathon build. These are the boundaries we know about, so nobody mistakes the demo for more than it is.

## Scope

1. **The Echo devices are simulated.** The page drives a real LLM (DeepSeek by default, Amazon Bedrock or any OpenAI-compatible endpoint as options) through real MCP sessions, but it is not Alexa+ itself. The server is built to be added to Alexa+ as a remote MCP server; that connection was not available to us for testing.
2. **Bookings are simulated.** After a commit, GroupFlow records booking receipts with idempotency keys and a `SIM-` reference for the activity kinds a template marks as bookable. No restaurant, room or ride service is contacted.
3. **Verified outside facts come from the demo console.** In a product, a trusted connector (a venue's booking system, a room calendar) would report that a place became unavailable. A member telling Alexa is stored as an unverified report and does not change the plan.
4. **Notifications are pulled, not pushed.** People hear news the next time they talk to Alexa or open their page.
5. **Templates are written by hand.** A new plan type within the generic item and policy model needs a YAML template (item types, policies, conditions, an optional catalog of places). New semantics such as structured project scheduling also require implementation and dedicated tools. The short directory and `get_template` discover new YAML without changing tool descriptions; restart after editing templates because the registry is cached. Alexa can create plans from those templates, but cannot invent new template definitions.

## Security

6. **Demo credentials.** Bearer tokens and page passcodes for the thirteen demo people are in `demo/world.yaml`, and the demo page fills the passcode in for you. A real deployment needs per-person account linking (OAuth) for the MCP token and a real sign-in for the confirmation page.
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

## Visualization and final validation boundaries (v0.5)

The memory visualization maps identity-filtered MCP results, not all database records or hidden model reasoning. A fresh panel snapshot is separate from the recorded last model turn; results are marked as delivered only after a subsequent model request consumes them. Missing nodes can reflect pagination or summary limits. The simulator's selectable personas are test identities, not production account linking.

Personal task summaries include live visible dependency blockers, and plan summaries include canonical group IDs. The host binds the actual request text to plan creation. The server refuses omitted audience for explicit “just/only with” wording; this bounded check does not parse every possible natural-language privacy intent. Another host must bind authentic request text itself.

The final paid-model source completed one full run at 107/108. Two additional repetitions ended with DeepSeek HTTP 402 (insufficient balance); those are incomplete experiments even though the old runner visited every scenario. Original reports are preserved. The evaluator now stops immediately on provider 401/402/403, marks the report incomplete and exits unsuccessfully. No account top-up was performed. New chats need a funded/configured provider; saved plans and the read-only graph remain inspectable.

The remaining full-run failure inferred a different Saturday and opened an owner-review request; the agreed date did not change. Another partial run confused two plans' venues and was refused. Eight additional structure cases repeated three times scored 23/24; one generic denial triggered a fixed broad phrase-ban assertion. Raw scores were not rewritten.

No independent participant study has been conducted. A fixed formative protocol is provided in [USER_TEST_PROTOCOL.md](USER_TEST_PROTOCOL.md); scripted human answers and API tests are not user research.

## Structured-project boundaries (v0.4)

See [PROJECTS.md](PROJECTS.md) for the complete implemented contract. A work unit is one contiguous 15-minute-grid block up to eight hours; split multi-day work. Limits: 400 open units per window, 200 occurrences per expansion, a 1–366-day window. The default three-second budget may time out on larger models; a validated feasible result is not always proven optimal. Project confirmations expire separately from the planning window. Recurrence is an explicit DAILY/WEEKLY subset, not a full RRULE parser. Workspace roles, resource reservations and scoped human gates are local implementations; SSO/SCIM, external calendars, real payment/deployment and real Alexa+ account linking are not production integrations. Migration is additive only from the recognized legacy schema; unsupported files are preserved and refused.
