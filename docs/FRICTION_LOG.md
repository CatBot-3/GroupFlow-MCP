# Friction log

What slowed us down, written so someone else can reproduce it and the owner can fix it. Every entry follows the format the hackathon asks for: the task we attempted, the steps we took, what we expected and what actually happened, a severity rating, the workaround we used and a suggestion. Part 1 is the Alexa+ add-on platform and its documentation, the entries we most want Amazon to see. Part 2 is the other tools and SDKs we built on. Part 3 keeps the shorter engineering notes from the build, most of them our own bugs, because several will bite anyone building an MCP add-on with a language model in front of it.

**Severity.** High: blocks a requirement, a connection or a running server. Medium: costs hours or forces a workaround in our code. Low: an annoyance, or a cost in size or speed rather than correctness.

| Entry | Area | Severity |
| --- | --- | --- |
| F1. The documented client initializes with an older protocol version | Alexa+ add-ons | High |
| F2. WWW-Authenticate listed as unsupported | Alexa+ add-ons | Medium |
| F3. Two spellings of the person-level scope | Alexa+ add-ons | Low |
| F4. No signal for who is speaking or listening on a shared device | Alexa+ add-ons | High |
| F5. Device testing needs Private Preview access | Alexa+ add-ons | High |
| F6. The 500 ms requirement does not say what is measured | Alexa+ add-ons | Low |
| F7. "A tool returns 401/403" is ambiguous for account linking | Alexa+ add-ons | Medium |
| F8. Not documented what the model sees of a server | Alexa+ add-ons | Medium |
| F9. The v2 Python SDK renamed the server; most examples are v1 | MCP Python SDK 2.3.0 | Medium |
| F10. No server switch to stay in the handshake protocol era | MCP Python SDK 2.3.0 | Low |
| F11. Mounting the SDK app under a path redirects `POST /mcp` | MCP Python SDK 2.3.0 | Medium |
| F12. Elicitation silently needs streamed responses | MCP Python SDK 2.3.0 | Medium |
| F13. Advertised tool schemas are verbose | MCP Python SDK 2.3.0 | Low |
| F14. Thinking on by default, and balance errors in the middle of a run | DeepSeek API | Medium |
| F15. OR-Tools crashed on Windows under Anaconda's Python | Google OR-Tools | High |

## Part 1. Alexa+ add-on friction

### F1. The documented client initializes with an older protocol version than the rules require

* **Task:** Serve the MCP version the hackathon requires (2025-11-25) to the client Alexa+ actually runs.
* **Steps:** Read the MCP toolkit client lifecycle page; compare its initialize example with the rules.
* **Expected:** The documented Alexa+ client sends `protocolVersion: "2025-11-25"`, or the docs say which versions it negotiates.
* **Actual:** The example initializes with `2025-03-26`. A server that accepted only 2025-11-25 would fail the handshake.
* **Severity:** High (a strict server cannot connect).
* **Workaround:** GroupFlow prefers 2025-11-25 and also accepts 2025-06-18 and 2025-03-26; a test negotiates all three.
* **Suggestion:** State the supported protocol versions in one place and keep the client example current.

### F2. WWW-Authenticate is listed as unsupported, but MCP authorization uses it for discovery

* **Task:** Let Alexa+ find GroupFlow's authorization server.
* **Steps:** Implement MCP 2025-11-25 authorization (protected resource metadata, RFC 9728), then read the Alexa+ authentication page.
* **Expected:** Alexa+ reads `WWW-Authenticate: Bearer resource_metadata=...` on a 401, as the MCP spec describes, or the docs say where else it learns the endpoints.
* **Actual:** The header is listed as unsupported, and the docs do not say how the authorization server is discovered instead.
* **Severity:** Medium (account linking may need manual configuration we cannot see).
* **Workaround:** GroupFlow sends the header and also serves `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server`, so either path works.
* **Suggestion:** Document the discovery path Alexa+ uses (console fields or metadata URLs).

### F3. Two spellings of the person-level scope

* **Task:** Accept the scope Alexa+ requests for a linked person.
* **Steps:** Search the authentication and account-linking pages for the scope name.
* **Expected:** One scope name.
* **Actual:** Both `mcp:resource` and `mcp:resources` appear.
* **Severity:** Low (a strict server refuses one of them).
* **Workaround:** GroupFlow accepts both for person tokens, and only `mcp:service` for client credentials.
* **Suggestion:** Pick one spelling and list the exact scopes per tier.

### F4. No signal says who is speaking or who will hear the reply on a shared device

* **Task:** Keep a surprise party out of a family kitchen.
* **Steps:** Look for any request field, header or token claim that identifies the speaker on a household Echo, or who else is present.
* **Expected:** A recognized-speaker or shared-device indication, so a server can choose what is safe to say aloud.
* **Actual:** One OAuth token per linked account and nothing about the room.
* **Severity:** High for any add-on with private data.
* **Workaround:** A shared-device default: when a host does not say who is listening, GroupFlow leaves plans hidden from someone out of summaries and serves them only when named; a host can declare a personal device with `X-GroupFlow-Listeners: me`. Measured cost: in the evaluation of the Alexa+-like client, most failures were requests about a plan the summary had left out (see EVALUATION.md).
* **Suggestion:** Pass a shared-device flag (and, when known, the recognized speaker) to add-ons.

### F5. Device testing needs Private Preview access

* **Task:** Test GroupFlow on a real Echo before submission.
* **Steps:** Follow the development stages page.
* **Expected:** A self-serve test path for hackathon entrants.
* **Actual:** Add-on testing on devices sits behind Private Preview access.
* **Severity:** High for the production track (the rules ask to show the server running on the device).
* **Workaround:** We entered on the simulation path, and built an Alexa+-like client that adds nothing GroupFlow-specific, so the measured numbers describe what a plain host gets.
* **Suggestion:** Give hackathon participants preview access or a hosted test client.

### F6. The 500 ms requirement does not say what is measured

* **Task:** Prove GroupFlow meets the latency requirement.
* **Steps:** Read supported capabilities; time every tool locally.
* **Expected:** A definition: server processing or network round trip, cold or warm session, which percentile.
* **Actual:** "Under 500 ms" with no method.
* **Severity:** Low.
* **Workaround:** We report local server time per call (44 to 128 ms, repair search included).
* **Suggestion:** Define the measurement and the percentile.

### F7. "A tool returns 401/403" is ambiguous for account linking

* **Task:** Make the service tier (client credentials) trigger linking when a person is needed.
* **Steps:** Read account linking; implement the service tier.
* **Expected:** It is clear whether Alexa+ wants an HTTP status on `tools/call` or an MCP tool result with an error.
* **Actual:** The wording allows both.
* **Severity:** Medium.
* **Workaround:** With a service-only token GroupFlow answers `tools/call` with HTTP 403 and `WWW-Authenticate: Bearer error="insufficient_scope"`, while `initialize` and `tools/list` work.
* **Suggestion:** Show one request and response that triggers linking.

### F8. It is not documented what the model sees of a server

* **Task:** Decide where to put guidance: server instructions, tool descriptions or both.
* **Steps:** Search the design guide for `instructions`, `annotations`, `outputSchema`.
* **Expected:** Whether initialize instructions reach the model, and whether readOnlyHint and destructiveHint change confirmation behaviour.
* **Actual:** Not stated.
* **Severity:** Medium (it decides how much an add-on can rely on).
* **Workaround:** The voice tools keep all essential rules on the server and in short tool descriptions; the server instructions are about 1,700 characters, and every write is guarded on the server regardless of hints.
* **Suggestion:** Document which server fields Alexa+ passes to its model and how it uses tool annotations.

## Part 2. Friction with the other tools we used

### F9. The v2 Python SDK renamed the server; most examples are v1

* **Task:** Build the server on the official MCP Python SDK 2.3.0.
* **Steps:** Follow the examples found online for a Streamable HTTP server with middleware.
* **Expected:** Examples that run on the current release.
* **Actual:** v2 renamed `FastMCP` to `MCPServer` (in `mcp.server.mcpserver`), changed middleware to `async (ctx, call_next)` and returns raw dicts for `initialize` and `tools/list`. Most search results and tutorials still show v1 code, which does not import.
* **Severity:** Medium (hours lost; no risk once known).
* **Workaround:** We read the v2 source and the specification instead of the examples.
* **Suggestion:** A v1 to v2 migration page, and a version banner on v1 examples.

### F10. No server switch to stay in the handshake protocol era

* **Task:** Serve MCP 2025-11-25, the handshake-era version the rules require.
* **Steps:** Connect the SDK's v2 client, in `auto` mode, to the server.
* **Expected:** A server option that declares its supported protocol versions, so clients go straight to the handshake.
* **Actual:** The client first tries the newer per-request envelope (2026-07-28) and falls back only after an error; the server has no documented switch.
* **Severity:** Low.
* **Workaround:** Our middleware answers modern-envelope requests with JSON-RPC error -32601, which makes the client fall back; a test covers it.
* **Suggestion:** A documented server setting for the supported protocol versions.

### F11. Mounting the SDK app under a path redirects `POST /mcp`

* **Task:** Serve `/mcp` (voice tools) and `/mcp/lab` (all tools) from one FastAPI app.
* **Steps:** Mount the SDK's Starlette app at `/mcp`.
* **Expected:** `POST /mcp` reaches the MCP endpoint.
* **Actual:** The mount redirects `POST /mcp` to `/mcp/`, which MCP clients do not follow, so connections fail without a clear error.
* **Severity:** Medium.
* **Workaround:** A small ASGI dispatcher routes `/mcp` and `/mcp/lab` itself.
* **Suggestion:** Document mounting under a prefix, or accept the path with and without the trailing slash.

### F12. Elicitation silently needs streamed responses

* **Task:** Ask a clarifying question in the middle of a tool call ("7:00 or 7:30?") with MCP elicitation.
* **Steps:** Serve one JSON body per request, then call elicitation from a tool.
* **Expected:** Either it works, or the SDK says elicitation needs streamed (SSE) responses.
* **Actual:** With one JSON body per request a tool cannot send `elicitation/create` mid-call, and nothing says so.
* **Severity:** Medium.
* **Workaround:** The response mode is a setting (`GROUPFLOW_MCP_RESPONSES=sse` streams), and our `ask_followup` falls back to a spoken question.
* **Suggestion:** Document the dependency, or raise a clear error when elicitation is attempted in JSON mode.

### F13. Advertised tool schemas are verbose

* **Task:** Keep the tool definitions small for a voice host.
* **Steps:** Measure `tools/list` with the Pydantic-derived input schemas.
* **Expected:** Compact JSON Schema.
* **Actual:** Every optional field carries a title, `anyOf` with null and `default: null`; with 35 tools the definitions came to 81 KB.
* **Severity:** Low (size and speed, not correctness).
* **Workaround:** The voice tools advertise a simplified schema while validation still uses the full signature; with 13 lean tools that is about 13 KB.
* **Suggestion:** An SDK option for compact advertised schemas.

### F14. Thinking on by default, and balance errors in the middle of a run

* **Task:** Run the evaluation suites with DeepSeek `deepseek-flash` through its OpenAI-compatible API.
* **Steps:** Run all scenarios three times in one go.
* **Expected:** Fast turns, and a clear stop when the account runs out.
* **Actual:** Thinking mode is on by default and adds seconds per turn. When the balance ran out, requests failed with HTTP 402 in the middle of a suite, which at first looked like scenario failures.
* **Severity:** Medium (wrong numbers if unnoticed).
* **Workaround:** We send thinking disabled, and our runner stops on 401, 402 and 403 and marks the report incomplete.
* **Suggestion:** Document the default prominently, and send a low-balance warning before requests start failing.

### F15. OR-Tools crashed on Windows under Anaconda's Python

* **Task:** Use CP-SAT for large repair searches on the Windows development machine.
* **Steps:** Import OR-Tools under Anaconda's Python and run a search.
* **Expected:** The same behaviour as on Linux.
* **Actual:** The process crashed: Anaconda ships an older Visual C++ runtime (`msvcp140.dll`) next to `python.exe`, and it is loaded before the system's.
* **Severity:** High on affected machines (the process dies).
* **Workaround:** GroupFlow loads the system runtime first, and falls back to exact enumeration when CP-SAT is unavailable.
* **Suggestion:** Check the runtime version at import and raise a readable error that names the conflicting DLL.

## Part 3. Engineering notes from the build

Shorter notes, in the order we hit them, with what fixed each one. Entries that are friction with a tool are written out in full in Part 2.

### MCP server and client (official Python SDK 2.3.0)

1. **Async context ownership.** An SDK client session entered in one asyncio task and closed in another fails with an anyio cancel-scope error, so each member's session lives inside its own long-lived task.
2. **Async event hooks.** The SDK uses `httpx2`; on an async client a response hook must be an `async def`.
3. **Identity per request.** The member goes on the ASGI scope in an auth wrapper and tools read it through the request context; each `mcp-session-id` is bound to the member who opened it.

### LLM behaviour (DeepSeek, OpenAI-compatible API)

4. **Hedges confirmed as decisions; negations stored as values; permissions guessed by the model; "noted" without a tool call.** Each was fixed in tool descriptions and server rules, and the evaluation counts the last one.
5. **Host help hides weaknesses.** The simulator's long prompt, forced state read and reply re-check raised scores that a plain host would not get. The Alexa+-like client, and counting scenarios that pass all three runs, now measure that gap (EVALUATION.md).

### Data and platform

6. **A worker race on Windows,** fixed by delivering a member's outbox before reading their notifications.
7. **A stale Python registration** in the Windows `py` launcher; using an interpreter by full path worked.
8. **SQLite and concurrency:** autocommit with explicit `BEGIN IMMEDIATE`, WAL and nested transactions that join the outer one.
9. **Tests that needed local files.** One test read a frozen case file from the ignored `eval/runs/` folder, so a fresh clone failed it; the file is now a tracked fixture and tests write evidence to a temporary folder unless `GROUPFLOW_KEEP_EVIDENCE=1`.

### Turning two demo engines into one generic core

10. **Loose matching across plans** turned "party.venue" into the party date; dotted references skip fuzzy matching.
11. **History that remembered too much:** a flag caused by a hidden plan was filed where the guest of honour could read it; such entries now live with the more private plan.
12. **Error messages that echo guesses** could confirm a hidden plan's name; they now read like errors for plans that do not exist.
13. **A guess that became a new item;** an unknown item with only a status is refused.
14. **Evaluation state leaking between scenarios;** every scenario starts from the whole world as seeded.
15. **Repairing and sending in one breath;** the organizer decides whether to send a repaired version.
