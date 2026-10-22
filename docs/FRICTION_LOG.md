# Friction log

Two parts. First, friction with the Alexa+ add-on platform and its documentation, written in the format the rules ask for (task, steps, expected, actual, severity, workaround, suggestion); these are the entries we would most like Amazon to see. Second, the engineering notes we kept while building, most of them our own bugs, because several will bite anyone building an MCP add-on with a language model in front of it.

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

## Part 2. Engineering notes from the build

These are in the order we hit them, with what fixed each one.

### MCP server and client (official Python SDK 2.3.0)

1. **The v2 SDK renamed the server.** `FastMCP` examples from v1 do not apply; v2 has `MCPServer` in `mcp.server.mcpserver`, a middleware chain with the signature `async (ctx, call_next)`, and raw dict results for `initialize` and `tools/list`. Most search results still show v1 code.
2. **Pinning the protocol era.** The v2 client in `auto` mode first tries the newer per-request envelope (2026-07-28) before falling back to the handshake. Our middleware answers modern-envelope requests with JSON-RPC error -32601, which makes the client fall back. There is no documented server switch for "handshake only".
3. **Mounting under a path.** Mounting the SDK's Starlette app at `/mcp` inside FastAPI turned `POST /mcp` into a redirect to `/mcp/`, which MCP clients do not follow. A tiny ASGI dispatcher now routes `/mcp` and `/mcp/lab` itself.
4. **Async context ownership.** An SDK client session entered in one asyncio task and closed in another fails with an anyio cancel-scope error, so each member's session lives inside its own long-lived task.
5. **Async event hooks.** The SDK uses `httpx2`; on an async client a response hook must be an `async def`.
6. **Identity per request.** The member goes on the ASGI scope in an auth wrapper and tools read it through the request context; each `mcp-session-id` is bound to the member who opened it.
7. **Elicitation needs a streamed response.** With one JSON body per request a tool cannot send `elicitation/create` mid-call, so the response mode is a setting and `ask_followup` falls back to a spoken question.
8. **Advertised schemas are verbose.** Pydantic schemas carry titles, `anyOf` with null and `default: null` for every optional field; for the voice tools we simplify the advertised schema (validation still uses the full signature), which with lean descriptions brought 13 tools to about 13 KB.

### LLM behaviour (DeepSeek, OpenAI-compatible API)

9. **Thinking mode is on by default** on DeepSeek, which adds seconds per turn; we send it disabled.
10. **Hedges confirmed as decisions; negations stored as values; permissions guessed by the model; "noted" without a tool call.** Each was fixed in tool descriptions and server rules, and the evaluation counts the last one.
11. **Host help hides weaknesses.** The simulator's long prompt, forced state read and reply re-check raised scores that a plain host would not get. The Alexa+-like client, and counting scenarios that pass all three runs, now measure that gap (EVALUATION.md).

### Data and platform

12. **A worker race on Windows,** fixed by delivering a member's outbox before reading their notifications.
13. **OR-Tools crashed on Windows** because Anaconda's Python ships an older msvcp140.dll; GroupFlow loads the system runtime first and falls back to exact enumeration.
14. **A stale Python registration** in the Windows `py` launcher; using an interpreter by full path worked.
15. **SQLite and concurrency:** autocommit with explicit `BEGIN IMMEDIATE`, WAL and nested transactions that join the outer one.
16. **The Windows event loop closed its own listening socket** after an abrupt disconnect (WinError 64); servers run on the selector loop on Windows.
17. **Tests that needed local files.** One test read a frozen case file from the ignored `eval/runs/` folder, so a fresh clone failed it; the file is now a tracked fixture and tests write evidence to a temporary folder unless `GROUPFLOW_KEEP_EVIDENCE=1`.

### Turning two demo engines into one generic core

18. **Loose matching across plans** turned "party.venue" into the party date; dotted references skip fuzzy matching.
19. **History that remembered too much:** a flag caused by a hidden plan was filed where the guest of honour could read it; such entries now live with the more private plan.
20. **Error messages that echo guesses** could confirm a hidden plan's name; they now read like errors for plans that do not exist.
21. **A guess that became a new item;** an unknown item with only a status is refused.
22. **Evaluation state leaking between scenarios;** every scenario starts from the whole world as seeded.
23. **Repairing and sending in one breath;** the organizer decides whether to send a repaired version.
