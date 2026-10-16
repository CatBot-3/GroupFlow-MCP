# Friction log

Problems we hit while building GroupFlow, in the order we hit them, with what fixed each one. We kept it because several of these will bite anyone building an MCP add-on with an LLM in front of it.

## MCP server and client (official Python SDK 2.3.0)

1. **The v2 SDK renamed the server.** `FastMCP` examples from v1 do not apply; v2 has `MCPServer` in `mcp.server.mcpserver`, a middleware chain with the signature `async (ctx, call_next)`, and raw dict results for `initialize` and `tools/list`. Filtering tools per member meant editing `result["tools"]` as a dict inside middleware. Most search results still show v1 code.
2. **Pinning the protocol version.** The v2 client in `auto` mode first tries the newer per-request envelope (2026-07-28) before falling back to the 2025-11-25 handshake. To serve only 2025-11-25, our middleware answers modern-envelope requests with JSON-RPC error -32601, which makes the client fall back. It works, but there is no documented server switch for "handshake only".
3. **Mounting under a path.** Mounting the SDK's Starlette app at `/mcp` inside FastAPI turned `POST /mcp` into a redirect to `/mcp/`, which MCP clients do not follow. We replaced the mount with a tiny ASGI dispatcher that sends `/mcp` to the SDK app and everything else to FastAPI.
4. **Async context ownership.** An SDK client session entered in one asyncio task and closed in another fails with an anyio cancel-scope error. A web backend handles each request in a different task, so each member's session now lives inside its own long-lived task, and requests talk to it.
5. **Async event hooks.** The SDK uses `httpx2`; on an async client a response hook must be an `async def`, otherwise every request fails with "object NoneType can't be used in 'await' expression".
6. **Identity per request.** Bearer identity has to reach tool code. We put the member on the ASGI scope in an auth wrapper and read it in tools through the request context, and bound each `mcp-session-id` to the member who opened it so a session cannot be borrowed with another token.

## LLM behaviour (DeepSeek, OpenAI-compatible API)

7. **Thinking mode is on by default** on DeepSeek, which adds seconds per turn. We send it disabled; in thinking mode the reasoning text must be sent back on assistant messages that carry tool calls, or the next call fails.
8. **Hedges confirmed as decisions.** "How about 30 guests?" was recorded as a firm change. Listing hedge words in the tool description and the server's initialize instructions fixed it.
9. **Negations stored as values.** "We're not getting an ice cream cake" became the cake value "not ice cream cake". The tool description now says a negative statement is never a value.
10. **The model judged permissions itself.** It refused to mark a task done because it guessed the speaker was not the owner. The prompt now says to make the call and let the server decide, then explain its answer.
11. **"Noted" without a tool call.** The model sometimes said it had recorded something it never sent. The prompt forbids claiming a change unless a tool call in that turn made it; the evaluation counts these.

## Data and platform

12. **A worker race on Windows.** Notifications go through an outbox that a background worker delivers. On a slower Windows run, Bob read his state before the worker delivered "P2 is now the agreed plan". Reading a member's notifications now first delivers that member's queue.
13. **OR-Tools crashed the process on Windows.** CP-SAT raised an access violation inside `solve`. The venv was built from Anaconda's Python, which ships msvcp140.dll 14.29 next to `python.exe`; OR-Tools wheels need 14.40 or newer, and Windows loads the copy next to the executable first. GroupFlow now loads the system copy of the runtime before importing OR-Tools, and falls back to exact enumeration if no new enough copy exists.
14. **A stale Python registration.** The Windows `py` launcher listed Python 3.14 at a path that no longer existed, so `py -3.14 -m venv` failed with an unreadable message. Using an installed interpreter by full path worked.
15. **SQLite and concurrency.** Python's `sqlite3` defaults to implicit transactions. We use autocommit mode with explicit `BEGIN IMMEDIATE`, WAL, and nested transactions that join the outer one, so a commit and a decision racing on the same plan version cannot both win.

## Turning two demo engines into one generic core

The first Python build had a household engine for the surprise party and a separate agreement engine for the dinner, each with its own tools. Merging them into one core with templates surfaced problems that neither engine had on its own.

16. **Loose matching across plans.** With several plans per person, a fuzzy match on item names turned "party.venue" into the party date (both start with "party"). Fuzzy matching is now skipped for dotted references, and every word must hit before a fuzzy match counts.
17. **History that remembered too much.** When the party date changed, the Home plan's cover story was flagged, and the history entry for that flag was filed under the Home plan, which Linda can see; it named "Party date & time". Our own leak test caught it. History entries caused by another plan now live with the more private of the two plans.
18. **Error messages that echo guesses.** A "no such plan" error repeated the title the model had guessed, which could confirm a hidden plan's name to someone who guessed right. Errors for plans the speaker cannot see now read exactly like errors for plans that do not exist.
19. **A guess that became a new item.** Asked to mark "the decorations task" done with a made-up id, the server created a new task called that id. An unknown item with nothing but a status is now refused; only an explicit new type, title or value adds an item.
20. **Evaluation state leaking between scenarios.** Each scenario reset only its own group, so a scenario in the family group could start with Bob's open confirmations for Friday dinner left over from an earlier dinner scenario, and the model talked about those instead. Every scenario now starts from the whole world as seeded.
21. **Repairing and sending in one breath.** Asked to "send the cottage plan out", the model found that the draft broke a condition, ran the search for alternatives itself and sent the repaired version, so the organizer never heard what changed before everyone was asked to confirm it. The refusal message and the prompt now say that the organizer decides whether to send a repaired version.
22. **The Windows event loop closed its own listening socket.** When the screenshot browser was killed while the page held an open event stream, accept() failed with WinError 64, and asyncio's proactor loop responds to that by closing the listening socket: the simulator kept running but refused every new connection. The servers now run on the selector event loop on Windows, and the same abrupt disconnects no longer matter.
