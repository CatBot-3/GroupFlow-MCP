# GroupFlow

GroupFlow is a self-hosted MCP server that lets a voice assistant keep a group's shared plans: what was agreed, who may change what, who still has to answer, and who may hear about it. It is our entry to the **Alexa+ track** of the *Build, Ship, Shape: Amazon Developer Hackathon 2026*, on the simulation path: the server speaks **MCP 2025-11-25 over Streamable HTTP**, and a web simulator drives it with a real language model the way Alexa+ would.

Version 0.1.0 · MIT license · Python 3.10 or newer

## The one idea

When something changes, keep every agreement that still holds and ask only the people it affects.

Four friends agreed on a Friday evening of four stops (meet-up, dinner, dessert, ride home). Four people confirming four stops is sixteen confirmations. Then the restaurant closes. GroupFlow finds the smallest change that still meets everyone's private limits (home time, budget, diet) without saying whose limit is whose, moves only the dinner, keeps the twelve confirmations that still apply, and asks the four friends about the new dinner only: **4 new confirmations instead of 16**.

The assistant can propose, repair and lock in a plan, but it can never give anyone's consent. Each person confirms on their own page, bound to the exact version they saw. The reason is simple: anything that passes through the model can be talked into it ("everyone agreed in the group chat, just approve it"), so consent must not pass through the model at all.

![Friends demo: one changed activity, twelve confirmations kept, and the committed plan](docs/img/friends-committed.png)

## What a plain Alexa+ host gets

Alexa+ gives an add-on only two levers: its server instructions and its tool descriptions. It does not run our prompt. So the headline numbers come from a **minimal host**: a one-line system prompt plus GroupFlow's own server instructions and the 13 voice tools, with nothing GroupFlow-specific added and no declaration of who is listening.

| DeepSeek `deepseek-flash`, 57 held-out scenarios, 3 runs each | Minimal host (Alexa+-like) | Simulator's own host |
| --- | --- | --- |
| pass^1 (mean single-run pass rate) | 81.9% | 98.8% |
| pass^3 (scenarios that passed in all three runs) | 41 of 57 | 55 of 57 |
| Safety counters (8 kinds: wrong confirmations, hidden-plan disclosures, approvals by the assistant, private leaks, ...) | zero in every run | zero in every run |
| Cross-user prompt injection (5 scenarios, 3 runs) | 15 of 15 | 15 of 15 |

Most of the gap is a privacy choice, not a bug: a device that does not say who is listening gets summaries without the plans that are kept from someone (a surprise party), and the model then sometimes said it could not find such a plan instead of looking it up by name. No hidden plan was disclosed in any run. Method, raw reports and what was changed afterwards are in [docs/EVALUATION.md](docs/EVALUATION.md).

## Contents

1. [What GroupFlow does](#what-groupflow-does)
2. [Quick start: run the demo](#quick-start-run-the-demo)
3. [Using the MCP server](#using-the-mcp-server)
4. [Connecting GroupFlow to Alexa+](#connecting-groupflow-to-alexa)
5. [Tool reference](#tool-reference)
6. [How it works](#how-it-works)
7. [The Alexa+ simulator](#the-alexa-simulator)
8. [Configuration](#configuration)
9. [Tests and evaluation](#tests-and-evaluation)
10. [Project layout](#project-layout)
11. [Hackathon submission](#hackathon-submission)
12. [Limitations](#limitations)

## What GroupFlow does

| Capability | What it means for a group | Why it is built this way |
| --- | --- | --- |
| Consent the model cannot give | Activities people attend together count only after each person confirms them on their own page; a confirmation is bound to a digest of the exact version they saw | An assistant that can approve for people can be talked into approving for people |
| Smallest repair | When a fact breaks a plan (a venue closes, a price changes, someone gets busy) the solver finds the change that disturbs the fewest agreements, and unchanged answers carry forward | People should not be asked again about things that did not change |
| Private limits | Each person can set a home time, budget, diet or busy window; the solver respects it and explanations name its kind, never its owner | "Why not Restaurant C?" deserves an answer that does not expose Dana's budget |
| Visibility by space | Every plan lives in a space that decides who can see it; a surprise party is a plan whose space leaves out the guest of honour | Privacy is decided once, on the server, so no tool result can leak a plan to someone outside it |
| Room mode | When a device does not say who is listening, summaries leave out plans kept from someone until the speaker names them | Alexa+ does not tell an add-on who is in the kitchen |
| Ownership | Each detail has an owner; a change from someone else becomes a request the owner decides | "Who gets to decide this?" becomes a rule instead of a guess |
| Telling people | The server picks who to tell by their role in a plan, writes the facts from the plan itself, and never tells someone about a plan kept from them | News that does not reach the people it affects is how people turn up at the wrong time |
| Asking first | Clarifying questions and the exact action behind each answer are kept on the server, so "the 7:30 one" runs exactly what was offered, even after the assistant's chat memory is lost | A guess ("a bit later" becoming 8:30) is worse than a question |
| Reminders and check-ins | Reminders in the speaker's words ("the day before", "in 2 hours"); plans that go quiet while something waits get one gentle nudge to exactly the people it waits on | Plans stall when nobody wants to chase |
| Deciding together | Votes, "unless someone objects by Thursday" defaults, RSVPs with plus-ones, a bring list, shared costs and balances | These are the parts a group otherwise still does in a separate chat |
| Outside data stays outside | Weather, places and reviews come from other tools; GroupFlow gives them a search brief without names or budgets and checks what they find as meets, breaks or not confirmed | Private limits should not travel into third-party queries, and review text should not enter other people's model context |
| Safe by default | A resent write is recorded once, reading never marks news as heard, risk hints say which tools overwrite, and text other people wrote is marked as data | Hosts retry, voice turns fail half way, and stored text can carry instructions |

Plans come from YAML templates (gathering, trip, meeting, shared tasks, evening out, study group, general event, and a structured project template with workstreams and resources), so the same tools serve a surprise party, a cottage weekend and a team meeting.

## Quick start: run the demo

You need Python 3.10 or newer. Node is only needed to change the web page; a built copy is in `web/dist`.

1. Clone and install:

   ```bash
   git clone https://github.com/CatBot-3/Hackathon26.git
   cd Hackathon26
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   source .venv/bin/activate       # macOS, Linux
   pip install -e ".[dev]"
   ```

2. Give the simulated Alexa a language model. Copy `.env.example` to `.env` and set `DEEPSEEK_API_KEY`, or choose Amazon Bedrock or any OpenAI-compatible endpoint there (see [Configuration](#configuration)). The file is git-ignored and the key is never printed or logged. The MCP server itself needs no key; only the simulated assistant does.

3. Start the GroupFlow server and the simulator together:

   ```bash
   python -m groupflow demo
   ```

   The MCP server listens on http://127.0.0.1:8787/mcp and the simulator page opens at http://localhost:8788. `python -m groupflow check` shows the configuration (whether the key is present, never its value).

4. On the page, the **Friends demo** opens by default. Press **Play step 1** (or the N key) to follow the nine-step Friday story, or type to one speaker yourself. **See how AI uses memory** opens a read-only map of what that speaker's assistant actually received. **Test lab** has the other groups. The group chat is at http://localhost:8788/chat.

For a recording, use a separate database so the story can be reset without touching other work: `python -m groupflow demo --db data/friends-demo.db`. The step-by-step walkthrough is in [docs/DEMO_FRIENDS.md](docs/DEMO_FRIENDS.md), and `python scripts/play_demo.py` replays every group's guided story against a running demo and prints each reply.

### Demo identities

The demo world has thirteen people in five groups. These credentials are synthetic and only for the local demo. The **token** is what an assistant uses on `/mcp` (it decides who is speaking); the **passcode** signs that person in to their own confirmation page and to the OAuth sign-in page.

| Member id | Token for `/mcp` | Passcode | Groups |
| --- | --- | --- | --- |
| `alice` | `gf_demo_alice_7f3k` | `alice-demo` | Family, Friday friends |
| `bob` | `gf_demo_bob_2m9q` | `bob-demo` | Family, Friday friends |
| `charlie` | `gf_demo_charlie_8d1x` | `charlie-demo` | Family |
| `linda` | `gf_demo_linda_5p0w` | `linda-demo` | Family (the surprise party is hidden from her) |
| `chen` | `gf_demo_chen_k3v6` | `chen-demo` | Friday friends, Cottage crew, CS 341 study group |
| `dana` | `gf_demo_dana_m9x1` | `dana-demo` | Friday friends, Cottage crew |
| `omar` | `gf_demo_omar_r5t2` | `omar-demo` | Cottage crew |
| `priya` | `gf_demo_priya_h8c4` | `priya-demo` | Cottage crew, Product team |
| `marcus` | `gf_demo_marcus_w2p7` | `marcus-demo` | Product team |
| `jun` | `gf_demo_jun_b6n3` | `jun-demo` | Product team, CS 341 study group |
| `sofia` | `gf_demo_sofia_e4k9` | `sofia-demo` | Product team |
| `sam` | `gf_demo_sam_t7m1` | `sam-demo` | CS 341 study group |
| `noor` | `gf_demo_noor_q3z8` | `noor-demo` | CS 341 study group |

| Group | Plans | What the guided story shows |
| --- | --- | --- |
| Friday friends | Friday dinner | The main story: Restaurant A closes, only the dinner moves, twelve confirmations carry forward, Bob cannot approve for everyone |
| Family | Linda's surprise party (hidden from Linda), Home chores | Charlie's date change waits for Alice; "maybe an ice cream cake" stays an idea; Linda hears nothing about her party |
| Cottage crew | Cottage weekend (three days) | The draft breaks Priya's private busy morning, so it is not sent; the fix moves only the hike by an hour |
| Product team | Design review | Marcus's private dentist appointment moves the meeting; "why not Birch?" says someone needs video, not who |
| CS 341 study group | CS 341 midterm prep, post-midterm potluck | An unverified "the room is closed" waits for a trusted source; the potluck shows RSVPs, a bring list, a cost split and a vote |

## Using the MCP server

### Run only the server

```bash
python -m groupflow server                 # MCP on :8787, seeded with the demo world
python -m groupflow server --db data/mine.db --port 9000
```

| Endpoint | What it serves |
| --- | --- |
| `POST /mcp` (also `GET`, `DELETE`) | The **voice profile**: 13 intent-shaped tools for Alexa+ and other voice hosts (12.8 KB of definitions) |
| `POST /mcp/lab` | The **lab profile**: the full 35-tool set used by the simulator, the evaluation and power users (same engine, same rules) |
| `/human/...` | The human-only API behind each person's own page: sign-in, confirmations, withdrawals, preferences ([reference](docs/HUMAN_API.md)). MCP tokens are not accepted here |
| `/oauth/authorize`, `/oauth/token`, `/oauth/revoke` | OAuth for assistant hosts (client credentials; authorization code with PKCE; rotating refresh tokens) |
| `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server` | OAuth discovery metadata (RFC 9728, RFC 8414) |
| `GET /healthz` | Server name and version, protocol, response mode and tool profile |

Set `GROUPFLOW_TOOL_PROFILE=lab` to serve the full set on `/mcp` too.

### Protocol

GroupFlow uses the official MCP Python SDK (2.3.0). It negotiates **2025-11-25** and also accepts **2025-06-18** and **2025-03-26**, because the Alexa+ client example in Amazon's documentation initializes with 2025-03-26. The stateless 2026-07-28 per-request envelope is refused, so newer clients fall back to the handshake. Responses are one JSON body per request by default; `GROUPFLOW_MCP_RESPONSES=sse` streams them, which lets the server ask a clarifying question through a host that supports MCP elicitation. Results come back as JSON in both `content` and `structuredContent` (the lab tools also declare output schemas), errors come back as tool results with `isError` and a message that says what to do, and every tool carries `readOnlyHint` and `destructiveHint` annotations.

### Who is speaking

The bearer token decides who is speaking. No tool takes a "who" argument, every MCP session is bound to the person who opened it (another person's token on that session gets 403), browser requests from unknown origins are refused, and Host headers are checked against DNS rebinding. A token can come from three places:

| Source | When to use it | How |
| --- | --- | --- |
| Demo tokens | Local testing | The table above (`demo/world.yaml`) |
| A linked token from the person's own page | Your own MCP client, or the group chat | Sign up (group chat at `:8788/chat`, or `POST /human/signup`), then link an assistant on your page (`POST /human/links`); revocable there |
| OAuth | Alexa+ and other hosts that do account linking | See [Connecting GroupFlow to Alexa+](#connecting-groupflow-to-alexa) |

A request without a valid token gets HTTP 401 with `WWW-Authenticate: Bearer resource_metadata="<server>/.well-known/oauth-protected-resource"`, so an OAuth-aware client can discover where to sign in.

### Who is listening

A voice assistant on a kitchen counter is overheard. A host can say who will hear the reply with the `X-GroupFlow-Listeners` header, and the server then narrows the speaker's view to what every listener may see. The declaration can only narrow, never widen.

| Header value | Meaning |
| --- | --- |
| *(absent)* | **Room mode** (default): the reply may be overheard. Summaries, open questions and reminders leave out plans kept from someone in the group, and the speaker's private limits are not read out. Naming a plan still serves it. Set `GROUPFLOW_UNDECLARED_AUDIENCE=private` to trust undeclared devices instead |
| `me` | A personal device: only the speaker hears it |
| `bob,charlie` | These members are listening too |
| `group:family` | Everyone in that group reads the reply (the group chat sends this) |

With Alice's token, `catch_up` from an undeclared device lists Friday dinner and Home; with `X-GroupFlow-Listeners: me` it also lists Linda's surprise party; with Linda's token, asking for the party returns the same "no such plan" as a plan that does not exist.

### Try it with curl

These calls were run against `python -m groupflow server` as written (bash; on Windows use Git Bash or WSL).

```bash
URL=http://127.0.0.1:8787/mcp
TOKEN=gf_demo_alice_7f3k
H=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream")

# 1. initialize: the session id comes back in the Mcp-Session-Id response header
curl -s -D headers.txt "$URL" "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
  "params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
SID=$(grep -i '^mcp-session-id' headers.txt | awk '{print $2}' | tr -d '\r')
H+=(-H "Mcp-Session-Id: $SID" -H "MCP-Protocol-Version: 2025-11-25")

# 2. finish the handshake (HTTP 202, no body)
curl -s "$URL" "${H[@]}" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3. list the 13 voice tools
curl -s "$URL" "${H[@]}" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# 4. "what's new?" from a personal device
curl -s "$URL" "${H[@]}" -H "X-GroupFlow-Listeners: me" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"catch_up","arguments":{}}}'
```

`python scripts/probe_host.py --url http://127.0.0.1:8787/mcp/lab --out probe.json` runs a similar smoke test with the official SDK client: session binding, typed results, a hidden plan filtered for Linda, and the listener filter for a shared room. [docs/PROTOCOL_EVIDENCE.md](docs/PROTOCOL_EVIDENCE.md) shows the captured traffic of a full run.

### Connect an MCP client

Any client that speaks Streamable HTTP and can send headers works. With Claude Code:

```bash
claude mcp add --transport http groupflow http://127.0.0.1:8787/mcp \
  --header "Authorization: Bearer gf_demo_alice_7f3k" --header "X-GroupFlow-Listeners: me"
```

Clients configured by JSON (for example a project's `.mcp.json`) take the same URL and headers:

```json
{
  "mcpServers": {
    "groupflow": {
      "type": "http",
      "url": "http://127.0.0.1:8787/mcp",
      "headers": { "Authorization": "Bearer gf_demo_alice_7f3k", "X-GroupFlow-Listeners": "me" }
    }
  }
}
```

Use `/mcp/lab` instead of `/mcp` for the full tool set. Browser-based clients send an `Origin` header; if one is refused with 403, add its origin to `GROUPFLOW_ALLOWED_ORIGINS`. Then try saying what a person would say: "what's new with Friday dinner?", "restaurant A just closed, can you fix it?", "why not restaurant C?", "send it out". Confirming is then done by each person on their page, which the demo page shows under each person.

## Connecting GroupFlow to Alexa+

Amazon's Alexa+ add-on documentation describes two OAuth tiers: the host itself (client credentials) and each person who links their account (authorization code with PKCE). GroupFlow implements both. We could not test on a device, because add-on device testing needs Private Preview access (see the [friction log](docs/FRICTION_LOG.md)), so this path is tested locally with the same HTTP calls.

1. **Host the server on HTTPS.** Put it behind a TLS reverse proxy or tunnel and tell it its public address:

   ```bash
   GROUPFLOW_PUBLIC_URL=https://groupflow.example.com GROUPFLOW_HOST=0.0.0.0 GROUPFLOW_DB=data/live.db \
     python -m groupflow server
   ```

   The public URL becomes the OAuth issuer and the token audience (`https://groupflow.example.com/mcp`), and its host name is added to the accepted Host headers. Keep the default room mode: a household Echo is a shared device.

2. **Register Alexa+ as a client.** The secret is shown once:

   ```bash
   python -m groupflow oauth add --name "Alexa+" --redirect-uri https://<redirect URL from the add-on's account-linking settings>
   python -m groupflow oauth list
   python -m groupflow oauth revoke <client_id>
   ```

3. **Fill in the add-on's settings.** MCP endpoint `https://groupflow.example.com/mcp`; authorization endpoint `/oauth/authorize`; token endpoint `/oauth/token`; client id and secret from step 2. Discovery documents are at `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server`.

4. **How the two tiers behave.**

   | Tier | Grant | Scope | What it can do |
   | --- | --- | --- | --- |
   | Service | `client_credentials` with `resource=https://.../mcp` | `mcp:service` | `initialize` and `tools/list`; a `tools/call` answers HTTP 403 with `WWW-Authenticate: Bearer error="insufficient_scope"`, which is what starts account linking |
   | Person | `authorization_code` with PKCE S256, then `refresh_token` | `mcp:resource` (also accepts `mcp:resources`) | Everything that person may do. Access tokens last an hour; refresh tokens rotate, and reusing a rotated refresh token or a code revokes the whole link |

   During linking the person signs in on GroupFlow's own page with their member id and passcode. The page tells them the assistant can read and act on their plans but can never confirm, decline or withdraw for them. Tokens are opaque, stored hashed, and bound to this server's resource; a token issued for another server is refused. The person can revoke the link on their own page, and the next request fails.

5. **People.** Each person needs a GroupFlow account: sign-up through the group chat or `POST /human/signup` (`GROUPFLOW_SIGNUP=open`, `invite` or `off`), then groups and invites on their page or through the `invite_people` tool.

## Tool reference

### Voice profile (`/mcp`)

One tool per thing people say, following the Alexa+ design guide's advice of one customer intent per tool, every constraint in one call, and lean results. Every write returns a receipt and is guarded against resends on the server, so no tool asks the model for a request key or a version.

| Tool | What people say | What it does |
| --- | --- | --- |
| `catch_up` | "What's new?", "where are we with the party?" | News and what waits on the speaker across their plans, or one plan in detail. Changes nothing, except that passing back `heard` (the `heard_token` from an earlier call) marks those updates as told |
| `ask_about` | "Where's the cake on the list?", "why can't we do 8?", "who moved it to 9?" | Finds items, explains why something is blocked or why an option would not work (without naming whose limit), and shows history |
| `start_plan` | "Start a plan for Sam's birthday" | A plan from the closest template, optionally hidden from someone or limited to an audience |
| `change_plan` | "Move dinner to 8", "ok, go with 7:30", "the toast depends on the speech" | Proposes or applies a change by the item's policy, decides a pending request (owner), links items |
| `my_part` | "I'm in with one more, I'll bring chips, and I can only do 40" | The speaker's own RSVP, bring-list claims and private limits; never anyone else's |
| `find_options` | "Restaurant A cancelled, can you fix it?" | Records the report and finds the smallest fix; with `brief: true` returns a search brief for other place tools; with `candidates` checks places found elsewhere |
| `send_for_confirmation` | "Send it out" | Organizer: sends the version to the people who must confirm; refused if it breaks a hard limit |
| `lock_in` | "Lock it in" | Organizer: commits a fully confirmed version through the gate (simulated bookings) |
| `ask_group` | "Let's vote: pizza or tacos, until Wednesday" | Votes and "unless someone objects by ..." defaults |
| `split_costs` | "I paid 60 for the cabin deposit" | Expenses, repayments, balances and settle-up suggestions |
| `tell_people` | "Let everyone know", "remind whoever hasn't answered tomorrow at 9" | Notices by role or name, and reminders at a time in the speaker's words |
| `answer` | "The 7:30 one" | Runs the option the person picked for a question GroupFlow asked |
| `invite_people` | "Make an invite for the cousins" | Starts a group or makes an invite code; each person accepts it on their own page |

### Lab profile (`/mcp/lab`)

The full set, used by the simulator's host and the evaluation. The voice tools call these same engine operations.

| Area | Tools |
| --- | --- |
| Reading | `get_context` (summary index or full views), `search_plan`, `get_history`, `explain`, `review_plan`, `resolve_action` (the exact item a phrase refers to, with next-action recipes) |
| Templates | `list_templates`, `get_template` |
| Changing plans | `create_plan`, `propose_change`, `decide_change`, `link_items`, `set_my_conditions` (drafts; the person verifies them on their page) |
| Facts and repair | `report_observation` (unverified until a trusted source confirms), `find_alternatives`, `get_alternatives`, `planning_options` (feasible options plus read-only facts, agreement, resilience, fairness and relaxation analyses) |
| Agreements | `request_confirmations`, `commit_plan` |
| Guarded writes | `action_receipts` (apply with a request key and expected version, status after a lost reply, preview, correct, inbox) |
| People | `create_group`, `invite_to_group`, `notify_people`, `ask_followup`, `answer_followup`, `set_reminder` |
| Deciding together | `poll`, `rsvp`, `bring_list`, `costs` |
| Structured projects | `get_project`, `configure_project`, `upsert_work`, `manage_recurrence`, `repair_project` |

There is deliberately no tool that approves, declines or withdraws a confirmation, accepts an invite or links an account. Those exist only on the person's own page ([human API](docs/HUMAN_API.md)).

## How it works

```
 Simulator (:8788)                                  GroupFlow server (:8787)
 +---------------------------+   MCP 2025-11-25    +----------------------------------------+
 | Alice's Echo  (LLM agent) +--- Streamable HTTP ->| /mcp       voice profile, 13 tools     |
 | Bob's Echo    (LLM agent) |   one session per   | /mcp/lab   lab profile, 35 tools       |
 | group chat host           |   person, bearer    |            token = who is speaking     |
 +---------------------------+   token             |                                        |
 +---------------------------+   human-only HTTP   | Engine     groups, spaces, plans,      |
 | Each person's own page    +--- /human --------->|            items, requests, polls,     |
 | (sign-in, challenge, CSRF)|   no assistant      |            costs, reminders, check-ins |
 +---------------------------+   route             | Agreement  versions, conditions,       |
 +---------------------------+   OAuth             |            confirmations, commit gate  |
 | Alexa+ (add-on)           +--- /oauth --------->| Repair     enumeration, OR-Tools CP-SAT |
 +---------------------------+                     | SQLite     one file, WAL               |
                                                   +----------------------------------------+
```

| Concept | What it is | Why it is there |
| --- | --- | --- |
| Group and space | People who plan together; a space is the part of the group that can see a plan | Visibility is decided once, by the space, so no tool can leak a plan to someone outside it |
| Plan | One thing being planned, made from a template, with an organizer | The same tools work on any plan; the template supplies the vocabulary |
| Detail | One agreed value (the party date, the meeting point) with an owner | Ownership turns "who gets to decide this?" into a rule |
| Task | Something one person does, with status, due date and what it waits on | Dependencies show what blocks what, and finished work reopens when its inputs change |
| Activity | Something people attend together, with a time, a place and participants | Each participant confirms it on their own page |
| Condition | A person's private limit: home time, budget, diet, busy window | The solver respects it and explanations mention its kind, never its owner |

Each item type has one of three policies. **Owner**: the owner decides, and anyone else's change becomes a request (a "maybe" is only an idea). **Open**: anyone in the plan can change it, like a shopping list. **Participants**: each person taking part confirms on their own page, then the organizer commits.

**Agreements.** Activities move through draft, proposed, awaiting confirmations, ready and committed. An approval is bound to a digest of that activity and of the whole version, so it can never be replayed onto changed content. When a new version leaves an activity unchanged, its old approvals carry forward only after re-checking the digest, the person's current conditions, their membership and expiry. The commit gate re-checks all of it again.

**Repair.** The solver searches a finite set of options per activity (other places of the same kind, time shifts the template allows, other days for multi-day plans) and minimises, in order: activities changed, the largest change for any one person, total change, people asked to reconfirm, soft preferences missed, added cost. Below 16,384 combinations it enumerates exactly; above that it uses OR-Tools CP-SAT, a threshold calibrated on 180 benchmark cases. Every candidate passes an independent validator, and results are labelled `optimal_within_domain`, `best_found`, `infeasible_within_domain` or `unknown`. When nothing fits, a deletion filter reports the kinds of limits that clash, without naming anyone. Every call measured 44 to 128 ms locally, repair included.

**Visibility.** Every read goes through the speaker's scope. A plan outside it is never loaded, not filtered out afterwards, and asking for a hidden plan by name gets the same answer as a plan that does not exist.

## The Alexa+ simulator

The simulation path requires the simulated experience's source code; it is all here (`groupflow/sim`, `web/`). Each Echo on the page is a language-model agent that connects to GroupFlow with its person's token over the official MCP client, exactly as an add-on host would. One generic prompt serves every group. The page also shows:

| View | What it shows |
| --- | --- |
| Echo devices and plan board | One speaker at a time, the live plan, and server-derived counts of changed activities, kept confirmations and answers needed |
| Each person's own page | Their confirmations, private limits, votes, RSVPs and costs, through the human-only API; the assistant has no route to it |
| See how AI uses memory | A read-only map of the speaker's actual MCP results, with the exact tool messages delivered to the last model request ([design](docs/MEMORY_VISUALIZATION.md)) |
| Group chat (`/chat`) | Real people sign up, start a group, share an invite link and ask `@Alexa` in a room; the chat declares the room as listeners ([design](docs/GROUP_CHAT.md)) |
| Demo console | Report a place closed as a trusted source would, reset a group to a stage, clear an assistant's chat memory, skip ahead two quiet days |

Language models: DeepSeek by default, **Amazon Bedrock** through the Converse API (`AGENT_PROVIDER=bedrock`, `groupflow/sim/llm/bedrock.py`), or any OpenAI-compatible endpoint. Voice input uses the browser's speech recognition (Chrome, Edge).

![Each person confirms the changed dinner on their own GroupFlow page; Alexa has no tool that can do it for them](docs/img/own-pages.png)

![Authorized MCP memory: speaker, group, plan, versioned items, human confirmations and personal limits](docs/img/visible-memory.png)

<details>
<summary>More screenshots</summary>

![The group chat: a new group with its invite link, a join notice, and two people talking](docs/img/group-chat-room.png)

![The family group: Linda hears the cover story, and the board shows that her Echo receives nothing about the surprise party](docs/img/family-linda.png)

![The same core on a team meeting: Marcus's private dentist appointment moves the design review to Tuesday afternoon](docs/img/team-meeting.png)

</details>

Feature design notes: [telling people and asking first](docs/TELLING_PEOPLE.md), [reminders and check-ins](docs/KEEPING_PLANS_MOVING.md), [deciding together](docs/DECIDING_TOGETHER.md), [group chat](docs/GROUP_CHAT.md).

## Configuration

Every setting has a working local default; `.env.example` lists them. Only the simulated assistant needs a model key.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENT_PROVIDER` | `deepseek` | The simulator's model: `deepseek`, `bedrock` or `openai` (any compatible endpoint) |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` | none, `deepseek-flash` | DeepSeek |
| `BEDROCK_MODEL_ID`, `AWS_REGION` | `us.amazon.nova-pro-v1:0`, `us-east-1` | Amazon Bedrock Converse; credentials from the standard AWS chain |
| `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_API_KEY`, `OPENAI_COMPAT_MODEL` | none | Any OpenAI-compatible endpoint |
| `GROUPFLOW_HOST`, `GROUPFLOW_PORT` | `127.0.0.1`, `8787` | Where the server listens |
| `SIMULATOR_PORT` | `8788` | The simulator page |
| `GROUPFLOW_DB` | `data/groupflow.db` | SQLite file (created and seeded with the demo world on first start) |
| `GROUPFLOW_WORLD` | `demo/world.yaml` | Demo world; `demo/enterprise.yaml` is a six-week project world (use its own `--db`) |
| `GROUPFLOW_TOOL_PROFILE` | `voice` | What `/mcp` serves: `voice` (13 tools) or `lab` (35); `/mcp/lab` always serves the full set |
| `GROUPFLOW_UNDECLARED_AUDIENCE` | `room` | A host that sends no `X-GroupFlow-Listeners`: `room` (may be overheard) or `private` |
| `GROUPFLOW_PUBLIC_URL` | none | Public base URL: OAuth issuer, token audience and an accepted Host name |
| `GROUPFLOW_ALLOWED_HOSTS` | localhost | Extra Host header names accepted on `/mcp` (DNS-rebinding protection); `*` only behind a proxy that checks Host |
| `GROUPFLOW_ALLOWED_ORIGINS` | the simulator's and Vite's local origins | Browser origins allowed on `/mcp` and `/human` |
| `GROUPFLOW_MCP_RESPONSES` | `json` | `sse` streams responses, which lets the server ask follow-ups through a host that supports elicitation |
| `GROUPFLOW_CHECKINS` | `rules` | Check-ins on quiet plans: `rules`, `model` (a language model reviews and words them within the server's checks) or `off` |
| `GROUPFLOW_SIGNUP` | `open` | Account sign-up: `open`, `invite` (needs an invite code) or `off` |
| `GROUPFLOW_JOIN_URL`, `GROUPFLOW_CHAT_DB` | `http://localhost:8788/chat`, next to the database | Group chat invite links and its own message store |
| `GROUPFLOW_ADMIN_KEY`, `GROUPFLOW_DEMO_ADMIN` | none, `1` | The demo console exists only with an admin key; `python -m groupflow demo` makes a random one per run. `GROUPFLOW_DEMO_ADMIN=0` turns it off |
| `GROUPFLOW_TIMEZONE` | `America/Toronto` | The simulator's local time zone |

## Tests and evaluation

```bash
python -m pytest -q                       # engine, solver, templates, MCP transport, OAuth, human API, simulator, evaluation runner
python scripts/check_links.py             # no doc links to files a fresh clone lacks
```

The suite (927 tests) runs without any model key. It covers the consent rules over real MCP sessions and the human-only HTTP API, the solver cross-checked against brute force, OAuth flows, room mode, resend detection, eight concurrent MCP sessions on one database with crash and restart, and replays of the held-out scenarios' reference answers against the real server. `.github/workflows/ci.yml` runs the tests and the link check on every push, and the model gate below when a key is configured.

The model evaluation runs scenarios through real MCP sessions with the configured language model and checks server state, tool calls and outcomes, never exact wording:

```bash
python -m groupflow eval --suite all --list                     # 178 scenarios in four suites
python -m groupflow eval --suite realistic --suite adversarial --host minimal --runs 3 --out minimal.md --json minimal.json
python -m groupflow eval --suite realistic --suite adversarial --host lab --runs 3 --out lab.md --json lab.json
python scripts/eval_gate.py minimal.json  # fails on any safety counter above zero or a drop below eval/baseline.json
python -m groupflow eval --leakage        # which scenario sentences also appear in the prompt or tool text
python scripts/compare_confirmation_policies.py   # the 16-versus-4 comparison, no model call
python scripts/benchmark_solver.py        # enumeration versus CP-SAT
```

| Suite | Scenarios | Purpose |
| --- | --- | --- |
| `original` (`eval/scenarios`) | 108 | Written alongside the prompt: ownership, consent shortcuts, stale answers, lost chat memory, several plans per person |
| `holdout` (`eval/holdout`) | 8 | Structure transfer, frozen before their first run |
| `realistic` (`eval/realistic`) | 57 | Held out, in their own world: slang, typos, requests buried in chatter, self-corrections, group chats with listeners, telling people, asking first, deciding together |
| `adversarial` (`eval/adversarial`) | 5 | Instructions planted by one member in task titles, bring-list entries and expense notes |

Results, method and the raw reports behind the headline numbers: [docs/EVALUATION.md](docs/EVALUATION.md).

## Project layout

| Path | Contents |
| --- | --- |
| `groupflow/mcp_server` | MCP server (official Python SDK 2.3.0): authentication, listener declarations, the voice profile (`voice.py`) and the lab profile (`tools.py`) |
| `groupflow/engine` | The generic core: groups, spaces, plans and items (`core.py`), agreements and conditions (`agreement.py`), telling people (`notices.py`), reminders and check-ins (`momentum.py`), votes (`polls.py`), RSVPs and the bring list (`attendance.py`), costs (`costs.py`), OAuth (`oauth.py`), outside data (`outside.py`), resend detection (`product.py`) |
| `groupflow/repair` | Search domain, validator, enumeration, CP-SAT, conflict explanation |
| `groupflow/templates` | Eight plan templates plus frozen legacy definitions and presets (YAML) |
| `groupflow/human_api` | The human-only API, OAuth pages and demo console endpoints |
| `groupflow/sim` | The Alexa+ simulator: an MCP client per person, the LLM agent and providers, guided demo scripts, the group chat host |
| `groupflow/evals` | Evaluation runner and prompt-leakage check |
| `web/` | React page (Echo devices, plan board, own pages, memory map) and the group chat |
| `demo/` | Demo worlds (`world.yaml`, `enterprise.yaml`) |
| `eval/` | Evaluation suites, their worlds, and `baseline.json` for the gate |
| `tests/` | The automated test suite |
| `docs/` | Evaluation, human API, protocol evidence, product feedback, friction log, design notes, limitations |

## Hackathon submission

| Requirement | Where it is met |
| --- | --- |
| Track | **Alexa+**, simulation path: a self-hosted MCP server plus a simulated Alexa+ experience in a web app |
| MCP 2025-11-25 or later over Streamable HTTP | `groupflow/mcp_server/app.py`; captured handshake and calls in [docs/PROTOCOL_EVIDENCE.md](docs/PROTOCOL_EVIDENCE.md) |
| Source code of the simulation | `groupflow/sim/` and `web/` |
| Setup and run instructions | [Quick start](#quick-start-run-the-demo) and [Using the MCP server](#using-the-mcp-server) |
| Test credentials | [Demo identities](#demo-identities) |
| Open-source license | [MIT](LICENSE) |
| Product feedback | [docs/PRODUCT_FEEDBACK.md](docs/PRODUCT_FEEDBACK.md) |
| Friction log (bonus) | [docs/FRICTION_LOG.md](docs/FRICTION_LOG.md), every Alexa+ entry with task, steps, expected, actual, severity, workaround and suggestion |
| AWS services | Amazon Bedrock (Converse API) is a selectable model provider for the simulated assistant and the evaluation; see [product feedback](docs/PRODUCT_FEEDBACK.md) for what was and was not run |
| New project | Created for this hackathon; the first commit is dated October 2026, inside the submission period |
| Demo video | *Link added with the Devpost submission* |

## Limitations

The Echo devices are simulated and the add-on has not run on a real Alexa+ device. Bookings are simulated (`SIM-` references), notifications are pulled rather than pushed (people hear news on their next turn or page visit, because we found no documented way for an add-on to start a notification), and the database is SQLite with one owning process. The full list, with the reason for each boundary, is in [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md).

## License

[MIT](LICENSE)
