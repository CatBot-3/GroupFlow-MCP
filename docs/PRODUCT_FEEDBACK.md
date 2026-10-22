# Product feedback

The submission asks, for every tool, API and SDK we used: what we used it for, what worked, what needs work, how onboarding felt, and whether we would build with it again. Part 1 answers that. Part 2 lists the features we would ask Amazon for, with how urgent each is. The detailed, reproducible friction entries are in the [friction log](FRICTION_LOG.md).

## Part 1. Tools, APIs and SDKs

### Alexa+ add-ons (MCP toolkit documentation)

| | |
| --- | --- |
| **Used for** | The target host. Its design guide shaped the 13 voice tools (one customer intent per tool, every constraint in one call, lean results), and its authentication and account-linking pages shaped GroupFlow's two OAuth tiers (client credentials for the service, authorization code with PKCE and refresh tokens for each person) |
| **What worked** | The design guide's advice was concrete and measurably right for us: folding 35 overlapping tools into 13 voice tools cut the tool definitions from 81 KB to about 13 KB. The two-tier sign-in model is clear about what a service token may do before a person links |
| **What needs work** | The documented client initializes with protocol 2025-03-26 while the hackathon requires 2025-11-25; `WWW-Authenticate` is listed as unsupported although MCP authorization discovery relies on it; the person scope appears as both `mcp:resource` and `mcp:resources`; nothing tells an add-on who is speaking or who else is in the room; the 500 ms requirement does not say what is measured; and it is not documented which server fields (instructions, annotations, output schemas) reach the model. Each is a friction log entry with steps and a suggestion |
| **Onboarding** | Reading was easy; testing was not. Add-on testing on a device sits behind Private Preview access, so we built a simulator, and an Alexa+-like client that adds nothing of ours, to measure what a plain host would get |
| **Build again?** | Yes. Voice is where group plans actually get mentioned ("what's new with Friday?"), and the add-on model fits a server that keeps the rules itself. A self-serve test path would change the most for us |

### Model Context Protocol 2025-11-25 and the official Python SDK 2.3.0

| | |
| --- | --- |
| **Used for** | The GroupFlow server (`MCPServer`, Streamable HTTP, sessions, middleware, elicitation) and the simulator's client, one session per person |
| **What worked** | Streamable HTTP with sessions was straightforward to secure per request (bearer identity, session bound to its member, Origin and Host checks). Tool annotations, structured results and `isError` let us return errors that tell the model what to do next. Elicitation over streamed responses lets the server ask a clarifying question through the host |
| **What needs work** | The v2 SDK renamed the server (`FastMCP` to `MCPServer`) and most examples found online are still v1. There is no server switch for "handshake era only"; we answer the 2026-07-28 envelope with -32601 so clients fall back. Mounting under a path redirected `POST /mcp` to `/mcp/`, which clients do not follow. Advertised Pydantic schemas are verbose (titles, `anyOf` with null for every optional field) |
| **Onboarding** | Slower than it should be, mainly because of the v1/v2 mismatch in examples; the specification itself was the reliable reference |
| **Build again?** | Yes. One server served Alexa+-style voice hosts, a group chat host and the evaluation harness without changes |

### Amazon Bedrock (Converse API, through boto3)

| | |
| --- | --- |
| **Used for** | A selectable model provider for the simulated assistant, the evaluation harness and the optional check-in reviewer (`AGENT_PROVIDER=bedrock`, default model `us.amazon.nova-pro-v1:0`; `groupflow/sim/llm/bedrock.py`) |
| **What worked** | The Converse API's message and tool-use shapes map cleanly onto the provider interface the simulator uses for every model, so the integration is a small adapter with lazy imports (the demo starts without AWS set up) |
| **What needs work** | We could not run it: no AWS credentials were available on the machine that ran the evaluation, so all reported model results use DeepSeek. Running the same two evaluation commands with Nova and Claude on Bedrock is the next measurement we want, because those are the model families behind Alexa+ |
| **Onboarding** | The code side was quick; access (credentials and model enablement) is the step we did not complete in time |
| **Build again?** | Yes, and it is the provider we would use for a real Alexa+ deployment |

### DeepSeek API (`deepseek-flash`, OpenAI-compatible)

| | |
| --- | --- |
| **Used for** | The simulated assistant during development and every reported model evaluation |
| **What worked** | Cheap and fast enough to run every suite three times; provider-side caching served most input tokens on repeated runs (7.1 of 9.0 million in one full run) |
| **What needs work** | Thinking mode is on by default and adds seconds per turn, so we send it disabled. A run that exhausts the balance fails with HTTP 402 mid-suite; our runner now stops on 401, 402 and 403 and marks the report incomplete instead of recording failures |
| **Onboarding** | Minutes, through the OpenAI-compatible API |
| **Build again?** | Yes, for development and evaluation |

### Google OR-Tools (CP-SAT)

| | |
| --- | --- |
| **Used for** | Plan repair above 16,384 combinations, and the interval scheduler for structured projects |
| **What worked** | Large repair domains stay under 100 ms where exact enumeration takes seconds ([benchmark](EVALUATION.md#solver-benchmark)), and interval variables made the project scheduler short |
| **What needs work** | On Windows it crashed with Anaconda's Python, which ships an older Visual C++ runtime next to `python.exe`; GroupFlow now loads the system runtime first and falls back to enumeration |
| **Onboarding** | Good documentation; the Windows runtime conflict cost the most time |
| **Build again?** | Yes |

### Other open-source building blocks

FastAPI, Starlette and uvicorn (the HTTP server), SQLite (storage, in WAL mode with explicit `BEGIN IMMEDIATE`), Pydantic, httpx, PyYAML, React and Vite (the web page). All worked as expected. The one surprise was on Windows: with asyncio's default proactor loop, one abruptly closed browser connection made the server stop accepting new ones, so the servers use the selector loop there.

## Part 2. Feature requests for Alexa+ add-ons

### 1. A stable identity for the person speaking (critical)

Group planning only works if the server knows who is talking. GroupFlow takes identity from a per-person bearer token and binds each MCP session to that person. On a shared Echo, that requires Alexa+ to pass a stable, per-person identity (from voice profiles) to the MCP server, and account linking per person rather than per Amazon account. Without it, every household member is the same caller and ownership rules cannot be enforced.

**Ask:** pass a per-person subject (or a per-person linked token) on every MCP request, plus a shared-device flag, and document what happens when the speaker is not recognised.

### 2. Confirmations the model cannot give (important)

GroupFlow's central rule is that the assistant can never agree for a person. Approvals go through a separate page with a one-time challenge bound to the exact version, because anything that goes through the model can be talked into it ("everyone agreed, just approve it"). MCP elicitation asks the user a question, but the answer still flows through the client the model drives.

**Ask:** an out-of-band confirmation channel: the server requests a confirmation, Alexa+ shows it in the Alexa app (or reads it with an explicit voice confirmation step the model does not handle), and the server receives a signed result tied to the person and to a digest of what they saw.

### 3. Telling a specific person something (important)

When a plan breaks (a venue closes, someone withdraws), the affected people should hear it. GroupFlow's `tell_people` tool picks recipients by their role in a plan (organizers, participants, owners, whoever's answer is pending), never past who can see the plan, and writes each notice from the plan's own state. But it can only keep the notice until the person next talks to Alexa: we found no documented way for an MCP add-on to start a conversation or raise a notification.

**Ask:** a way for an MCP server to send a notification to a specific linked person, rate-limited and opt-in, delivered by Alexa as a notification rather than by the model. GroupFlow already produces exactly the payload this needs: one recipient, one plan, a short text written for that person.

### 4. Clear protocol and capability support (important)

**Ask:** document which MCP protocol versions Alexa+ negotiates, whether it reads `outputSchema` and `structuredContent`, whether it caches `tools/list` per account or per person, and whether it honours tool annotations such as `readOnlyHint` and `destructiveHint`. GroupFlow's first build returned different tools to different members; the current build gives every member the same tools (13 on `/mcp`, 36 on `/mcp/lab`) and filters the returned data instead, partly because we could not tell how a client would cache a per-person tool list.

### 5. What an elicitation answer means (important)

The MCP toolkit documentation says Alexa+ first tries to gather missing details from context and then uses MCP elicitation to ask the customer. GroupFlow uses elicitation for clarifying questions ("7:00 or 7:30?") and runs the chosen option as the speaker, with every permission check, so an answer can do nothing the speaker could not ask for anyway. It deliberately does not use elicitation for consent, because if the client may fill an elicitation from context, an elicited "yes" is not proof that the person said it.

**Ask:** document when Alexa+ asks the customer and when it answers an elicitation itself, which response modes and form shapes it supports, how it reads a choice list aloud, and what a decline or a timeout returns. A flag in the elicitation result saying "answered by the customer" would let servers use it for more than clarifications.

### 6. A multi-person test console (nice to have)

Testing GroupFlow meant building a simulator with thirteen people in five groups, each a separate MCP client with its own token, driven by a language model. Ownership, privacy and confirmation rules only show up with several people, and the hardest cases came from people who belong to several groups at once.

**Ask:** a developer console that can talk to an add-on as several voice profiles in one household, shows the MCP traffic per profile, and can replay a conversation.

### 7. Guidance and an evaluation harness for agent-facing tool design (nice to have)

The biggest quality gains came from tool descriptions and server instructions, not code: telling the model which words are hedges, that a negation is never a value, that it must not claim a change unless a tool call made it, and that the server, not the model, decides permissions.

**Ask:** published guidance on how Alexa+ uses server `instructions` and tool descriptions (length limits, precedence over its own prompt), and an evaluation harness for add-on developers.
