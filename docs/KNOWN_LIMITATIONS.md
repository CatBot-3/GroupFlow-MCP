# Known limitations

GroupFlow is a hackathon build. These are the boundaries we know about, each with the reason it is there, so nobody mistakes the demo for more than it is.

## Scope

1. **The Echo devices are simulated.** The simulator drives a real language model (DeepSeek by default; Amazon Bedrock or any OpenAI-compatible endpoint as options) through real MCP sessions, but it is not Alexa+. The server implements what Amazon documents for add-ons (Streamable HTTP, the two OAuth tiers, account linking), but add-on testing on a device needs Private Preview access, so it has never run on one.
2. **Bookings are simulated.** After a commit, GroupFlow records booking receipts with idempotency keys and a `SIM-` reference for the activity kinds a template marks as bookable. No restaurant, room or ride service is contacted.
3. **Verified facts come from the demo console.** A closed venue or a changed price becomes verified only when a trusted source reports it; in the demo that source is the console. A member telling the assistant is stored as an unverified report and changes nothing until confirmed.
4. **Notifications are pulled, not pushed.** Notices, reminders and check-ins wait on the server until the person next talks to their assistant or opens their page; the group chat shows them at once in the person's private thread. We found no documented way for an Alexa+ add-on to start a notification.
5. **The person's own page ships with the web app.** `python -m groupflow server` alone serves the human-only API and the OAuth sign-in pages; the questions people tap are part of the simulator's web app (`python -m groupflow demo`), which calls that API. In the demo they appear on each person's Echo screen. Alexa+ documents no way for an add-on to show a confirmation that its model cannot answer, so on a real Alexa+ device this page would have to open somewhere else, such as the person's phone, until Amazon adds one (our request is in [PRODUCT_FEEDBACK.md](PRODUCT_FEEDBACK.md)).
6. **Templates are written by hand.** A new kind of plan within the item and policy model needs a YAML template; new semantics (such as structured project scheduling) need code and their own tools. The assistant can create plans from templates but cannot invent new ones. Restart the server after editing templates.

## Identity and security

7. **Demo credentials are public.** Tokens and passcodes for the thirteen demo people are in `demo/world.yaml`. People who sign up choose their own passcode (stored with salted scrypt); there is no password recovery and no second factor.
8. **OAuth is implemented, not certified.** Client credentials, authorization code with PKCE S256, rotating refresh tokens, revocation and discovery metadata are tested locally with real HTTP calls, not with Alexa+. Tokens are opaque and stored hashed.
9. **The demo console is for local use.** It exists only when an admin key is configured (`python -m groupflow demo` makes a random one per run), and `GROUPFLOW_DEMO_ADMIN=0` removes it.
10. **Rate limits** on agent writes are per person and kept in memory.

## Privacy

11. **Listeners are declared by the host.** `X-GroupFlow-Listeners` narrows what the speaker's assistant may say to what every listener may see, and can never widen it, but the server cannot check who is really in the room. A host that declares nothing is treated as a shared device that others may overhear.
12. **The shared-device default has a cost.** Leaving plans hidden from someone out of summaries means the assistant can fail to find such a plan when the person refers to it without naming it ("that hall is way too big"). This was the largest source of failures for the Alexa+-like client in the evaluation. The simulator page has no switch for a shared kitchen device yet; its API accepts `device: "kitchen"`.
13. **Explanations name kinds, not people.** A conflict explanation says "someone's latest time" or "a budget", never whose. In very small groups people can still guess, and dietary tags shown for a potluck can identify someone in a plan of three.
14. **The note check is a phrase check.** A notice or check-in text that names a plan kept from a recipient is refused, but a paraphrase can pass. The facts in a notice cannot leak, because recipients are limited to people who can see the plan.

## Planning and repair

15. **The search domain is finite by design.** Each activity can keep its option, move to another place of the same kind, or shift by the offsets its template lists. `optimal_within_domain` means optimal among those options only. The switch to CP-SAT at 16,384 combinations is a locally calibrated heuristic ([benchmark](EVALUATION.md#solver-benchmark)).
16. **Travel times come from a fixed zone table** in each template, not a maps service.
17. **One time zone per group.** Daylight-saving transitions are validated at entry, search, confirmation and commit; a repeated hour needs an explicit choice of occurrence.
18. **Outside places are briefed and checked, not adopted.** `find_options` can produce a search brief for other place tools and check places they found against everyone's limits (meets, breaks, not confirmed by the venue), but a plan with a fixed catalog cannot yet adopt an outside place with its source and expiry bound into the approval digest. Plans made from `general_event` accept any place name as free text, without hours or prices.
19. **Weather belongs to the host.** GroupFlow has no forecast provider. The instructions ask the assistant to use a connected weather tool when weather matters and to continue without it otherwise; no such tool was connected in testing.

## The assistant's behaviour

20. **The model can still phrase things imperfectly.** The server decides every outcome, so a wrong sentence cannot authorize a commit or produce consent, but the model can still misread which plan someone means when the same words fit two of their plans, ask instead of acting, or answer without calling a tool. Every measured failure is in the [evaluation reports](evidence/README.md).
21. **Chat history is short** (six turns per person) and lives in the simulator. Plans, approvals and conditions live on the server and survive restarts and lost chat memory.
22. **Times are read by rules, in English.** Reminders understand exact times, delays and times relative to the plan's date; anything vague is asked about rather than guessed. "Next Friday" means the coming Friday, and the reply reads the resolved date back.
23. **What counts as a stalled plan is a fixed list:** pending answers, open suggestions, the organizer's next step, overdue tasks with a readable due date, and unset details close to the date. Check-ins go out between 8 am and 10 pm in the group's time zone. The optional model reviewer of check-ins is tested only with scripted (including hostile) answers.
24. **Voice input** uses the browser's speech recognition, which works in Chrome and Edge.

## Deciding together

25. **Votes are open, not secret.** Everyone in the plan sees who chose what, because the decider needs the tally. A vote never settles an activity, since each person confirms an activity on their own page.
26. **Deadlines are heard late.** A vote or default that closes at night is applied at its time, but people hear the result on their next turn, page visit or chat thread.
27. **Equal splits only, one currency per plan.** Shares are by heads (a guest counts with whoever brings them). Splits by amount or percentage and currency conversion are not supported; nothing is paid through GroupFlow.
28. **Plus-ones are a number.** Guests are not people in GroupFlow, so they have no RSVP, conditions or page.
29. **Bring-list matching is by words.** "the chips" and "some chips" are one entry; "crisps" is a new one.

## Group chat

30. **A demonstration host.** The group chat keeps messages and the tokens people linked to it unencrypted in its own SQLite file next to the GroupFlow database, and delivers live events only while the page is open; there is no email, SMS or push.
31. **Membership changes reopen confirmations.** Joining or leaving a group changes who a plan is for, so a version out for confirmation must be sent again (answers about unchanged stops still count). Only organizers create invites.

## Structured projects

32. **Bounded by design.** A work unit is one contiguous block of up to eight hours on a 15-minute grid; at most 400 open units per window, 200 occurrences per expansion, and a window of 1 to 366 days. Recurrence supports daily and weekly rules only. The default three-second budget may time out on larger models, in which case a validated feasible result is returned without a proof of optimality.

## Platform

33. **SQLite with one owning process.** The worker that runs repair searches lives inside the server process; startup requeues interrupted jobs under that assumption, and several live processes would need worker leases first. Searches run one at a time. The data access layer is one module, so PostgreSQL could replace SQLite later.
34. **OR-Tools on Windows** needs the Microsoft Visual C++ runtime 14.40 or newer. Some Python distributions (Anaconda, for one) ship an older copy; GroupFlow loads the system copy first, and if none is new enough, route repair uses exact enumeration and project scheduling reports that it is unavailable.
35. **Windows event loop.** The servers use asyncio's selector loop on Windows, because with the default proactor loop one abruptly closed browser connection can stop the server from accepting new ones. The selector loop allows about 500 open sockets, far above what the demo uses.
