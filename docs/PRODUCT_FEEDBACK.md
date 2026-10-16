# Product feedback for Alexa+ and MCP add-ons

What we would ask for after building a group-planning add-on. Each point comes from something GroupFlow had to work around.

## 1. A stable identity for the person speaking

Group planning only works if the server knows who is talking. GroupFlow takes identity from a per-person bearer token and binds each MCP session to that person. On a shared Echo, that requires Alexa+ to pass a stable, per-person identity (from voice profiles) to the MCP server, and account linking per person rather than per Amazon account. Without it, every household member is the same caller and ownership rules cannot be enforced.

**Ask:** pass a per-person subject (or a per-person linked token) on every MCP request, and document what happens when the speaker is not recognised.

## 2. Confirmations the model cannot give

GroupFlow's central rule is that the agent can never agree for a person. Approvals go through a separate page with a one-time challenge bound to the exact version, because anything that goes through the model can be talked into it ("everyone agreed, just approve it"). MCP elicitation asks the user a question, but the answer still flows through the client the model drives.

**Ask:** an out-of-band confirmation channel: the server requests a confirmation, Alexa+ shows it in the Alexa app (or reads it with an explicit voice confirmation step that the model does not handle), and the server receives a signed result tied to the person and to a digest of what they saw.

## 3. Telling a specific person something

When a plan breaks (a venue closes, someone withdraws), the affected people should hear it. GroupFlow can only queue notifications and deliver them the next time someone talks to Alexa.

**Ask:** a way for an MCP server to send a notification to a specific linked person, rate-limited and opt-in, delivered by Alexa as a notification rather than by the model.

## 4. Clear protocol and capability support

We had to choose between the 2025-11-25 handshake and the newer per-request envelope, and between plain text and structured results.

**Ask:** document which MCP protocol versions Alexa+ negotiates, whether it reads `outputSchema` and `structuredContent`, whether it caches `tools/list` per account or per person, and whether it honours tool annotations such as `readOnlyHint` and `destructiveHint`. GroupFlow's first build returned different tools to different members; the generic build gives everyone the same fourteen tools and filters the data instead, partly because we could not tell how a client would cache a per-person tool list.

## 5. A multi-person test console

Testing GroupFlow meant building a simulator with thirteen people in five groups, each a separate MCP client with its own token, driven by an LLM. Ownership, privacy and confirmation rules only show up with several people, and the hardest cases came from people who belong to several groups at once.

**Ask:** a developer console that can talk to an add-on as several voice profiles in one household, shows the MCP traffic per profile, and can replay a conversation.

## 6. Guidance for agent-facing tool design

The biggest quality gains came from tool descriptions and server instructions, not code: telling the model which words are hedges, that a negation is never a value, that it must not claim a change unless a tool call made it, and that the server, not the model, decides permissions.

**Ask:** published guidance on how Alexa+ uses server `instructions` and tool descriptions (length limits, precedence over its own prompt), and an evaluation harness for add-on developers.
