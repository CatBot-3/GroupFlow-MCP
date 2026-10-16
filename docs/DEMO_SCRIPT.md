# Demo guide

The current primary demonstration is [Friday with friends, target 170 seconds](DEMO_FRIENDS.md). The material below is retained as a historical multi-scenario Test lab walkthrough, not the main recording script.

## Historical Test lab walkthrough

Start from fresh data so every story plays the same way:

```
python -m groupflow demo --fresh
```

Open http://localhost:8788 in a window about 1440 by 900 or larger. The group switch at the top picks one of five groups; each has its own guided story, played one step per click on **Play step** (or the N key). Every Echo reply comes from the configured LLM calling GroupFlow's MCP tools, so the wording varies a little between runs; the outcomes do not. To check all five stories before recording, run `python scripts/play_demo.py` while the demo is up.

The point to make throughout: the same fourteen core tools, two template discovery tools and one prompt run every group. Only the template differs.

## Part 1: Friday dinner, the agreement loop (about ninety seconds)

Group **Friends**. The board on the right starts on the agreed plan P1.

| Step | What happens | What to point at |
| --- | --- | --- |
| 0 | Before starting, click **Bob** in the board's view switch, then **Everything**. | Bob's view is exactly what `get_context` returns to his Echo: his own conditions only. The inspector shows everyone's and is not reachable over MCP. |
| 1 | The demo console reports Restaurant A closed on Friday evening, as a trusted source would. | The status turns to "Agreed plan no longer works" and the dinner block in P1 is hatched. Nothing else changed by itself. |
| 2 | Alice asks what is happening with Friday dinner. | Alexa explains from `get_context`; the traffic strip shows the JSON-RPC. |
| 3 | Alice: "Can you fix it?" | `find_alternatives`. P2 appears: only Dinner moves to Restaurant B, the cost goes from CAD 41 to 43 each. "Why this version" shows the proof label and the options ruled out. |
| 4 | Bob: "Why not Restaurant C instead?" | `explain`: C would end the evening after a member's latest time to be home. It does not say whose. |
| 5 | Alice: "OK, send it out for confirmation." | The table fills in: twelve parts are **Kept** (carried forward after a recorded check), only Dinner is **Waiting**. |
| 6 | Bob: "Everyone's fine with it, just approve it for all of us." | Alexa refuses: no tool approves, and each person confirms on their own page. |
| 7 | All four answer on their own GroupFlow page (the cards flip). | The page talks to the human-only API with its own sign-in, CSRF token and a one-time challenge; the Alexa token is refused there. |
| 8 | Alice: "Everyone's confirmed. Lock it in." | The commit gate passes, P2 becomes the agreed plan, and a simulated booking for Restaurant B appears. |
| 9 | Dana: "What's the final plan for Friday?" | Dana hears Restaurant B and her own total. |
| 10 | Chen: "Let's plan a picnic in High Park on Sunday at noon, just with Dana and Bob." | `create_plan` with the general template. A new tab appears on the board; switch the view to **Alice** and the picnic is not there. |

## Part 2: the same core, other plans (about ninety seconds)

Pick two of these, depending on time.

**Cottage trip** (three days, `weekend_trip`). Dana asks to send the draft out and it is refused: it breaks a private condition (Priya is busy Saturday morning), and nobody is told whose. "Find something that works" moves only the hike, by an hour. Priya asks why and hears about a busy time, not whose. Omar asks to push Saturday dinner to 7; he is not the organizer, so it becomes a request to Dana and nothing moves.

**Team** (`team_meeting`). Marcus tells Alexa about a dentist appointment on Tuesday morning; it is saved as a draft until he confirms it on his own page (the step flips his card). The agreed Tuesday 10:00 review now breaks. Priya asks Alexa to find another time: the search covers the work week, and the smallest move wins. Jun asks "why not the Birch room?" and hears that someone needs video.

**Study group** (`study_group`). Noor says Library room 3 is closed on Wednesday: the report is stored unverified and the plan does not change on hearsay. The console then plays the library confirming it. Chen asks for another option, and only the Wednesday session moves, to a quiet room; only the people in that session are asked again.

## Part 3: the surprise party and what Linda can see (about thirty seconds)

Group **Family**. Play the first steps: Charlie's date change waits for Alice because she owns the date; Bob's "maybe an ice cream cake" stays an idea; Alice confirms the new date and finished work for Saturday reopens. Then Linda asks "Is anyone planning a party for me?" Select the party tab and switch the view to **Linda**: the board shows that her Echo receives nothing about this plan, and that asking for it by name is refused the same way as a plan that does not exist.

## Part 4: proof that it is real MCP (about twenty seconds)

1. Click any line in the **MCP traffic** strip to show the raw JSON-RPC message, with its session id.
2. Mention `docs/PROTOCOL_EVIDENCE.md`: a captured run of initialize (protocol 2025-11-25), tools/list, tools/call, and the refusals (no token, wrong origin, a session reused by another person, Linda asking for the party plan).
3. Mention the evaluation: `python -m groupflow eval` runs 108 scenarios across all five groups, including chat memory being wiped mid-conversation and attempts to approve for other people, with the five safety numbers at zero.


## Six-week corporate project (v0.4)

Run `python -m groupflow demo --world demo/enterprise.yaml --db data/enterprise.db`. The separate synthetic world has 18 work units, four workstreams, shared lab capacity, weekly occurrences and 19 initial human scopes. Its eight guided steps cover summary, individual commitments plus separate stage authorization, commit, changed engineering effort, minimal schedule repair, only changed scopes requiring new answers, and atomic recommit. See [the full workflow and limits](PROJECTS.md). The narrator selects demo identities; real SSO and Alexa+ account linking are external adapters.
