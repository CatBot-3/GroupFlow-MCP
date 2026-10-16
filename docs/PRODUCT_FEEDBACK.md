# Product feedback for Alexa+ and MCP add-ons

What we would ask for after building a group-planning add-on. Each point comes from something GroupFlow had to work around.

## 1. A stable identity for the person speaking

Group planning only works if the server knows who is talking. GroupFlow takes identity from a per-person bearer token and binds each MCP session to that person. On a shared Echo, that requires Alexa+ to pass a stable, per-person identity (from voice profiles) to the MCP server, and account linking per person rather than per Amazon account. Without it, every household member is the same caller and ownership rules cannot be enforced.

**Ask:** pass a per-person subject (or a per-person linked token) on every MCP request, and document what happens when the speaker is not recognised.

## 2. Confirmations the model cannot give

GroupFlow's central rule is that the agent can never agree for a person. Approvals go through a separate page with a one-time challenge bound to the exact version, because anything that goes through the model can be talked into it ("everyone agreed, just approve it"). MCP elicitation asks the user a question, but the answer still flows through the client the model drives.

**Ask:** an out-of-band confirmation channel: the server requests a confirmation, Alexa+ shows it in the Alexa app (or reads it with an explicit voice confirmation step that the model does not handle), and the server receives a signed result tied to the person and to a digest of what they saw.

## 3. Telling a specific person something

When a plan breaks (a venue closes, someone withdraws), the affected people should hear it. Since v0.7 GroupFlow has a `notify_people` tool that picks recipients by their role in a plan (organizers, participants, owners, whoever's answer is pending), never past who can see the plan, and writes each notice from the plan's own state. But it can only keep the notice until the person next talks to Alexa: we found no documented way for an MCP add-on to start a conversation or raise a notification.

**Ask:** a way for an MCP server to send a notification to a specific linked person, rate-limited and opt-in, delivered by Alexa as a notification rather than by the model. GroupFlow already produces exactly the payload this needs: one recipient, one plan, a short text written for that person.

## 4. Clear protocol and capability support

We had to choose between the 2025-11-25 handshake and the newer per-request envelope, and between plain text and structured results.

**Ask:** document which MCP protocol versions Alexa+ negotiates, whether it reads `outputSchema` and `structuredContent`, whether it caches `tools/list` per account or per person, and whether it honours tool annotations such as `readOnlyHint` and `destructiveHint`. GroupFlow's first build returned different tools to different members; the generic build gives everyone the same fourteen tools and filters the data instead, partly because we could not tell how a client would cache a per-person tool list.

## 5. A multi-person test console

Testing GroupFlow meant building a simulator with thirteen people in five groups, each a separate MCP client with its own token, driven by an LLM. Ownership, privacy and confirmation rules only show up with several people, and the hardest cases came from people who belong to several groups at once.

**Ask:** a developer console that can talk to an add-on as several voice profiles in one household, shows the MCP traffic per profile, and can replay a conversation.

## 6. Guidance for agent-facing tool design

The biggest quality gains came from tool descriptions and server instructions, not code: telling the model which words are hedges, that a negation is never a value, that it must not claim a change unless a tool call made it, and that the server, not the model, decides permissions.

**Ask:** published guidance on how Alexa+ uses server `instructions` and tool descriptions (length limits, precedence over its own prompt), and an evaluation harness for add-on developers.

## 7. What an elicitation answer means

The MCP toolkit documentation says Alexa+ first tries to gather missing details from context and then uses MCP elicitation to ask the customer. GroupFlow 0.7 uses elicitation for clarifying questions ("7:00 or 7:30?") and runs the chosen option as the speaker, with every permission check, so an answer can do nothing the speaker could not ask for anyway. It deliberately does not use elicitation for consent, because if the client may fill an elicitation from context, an elicited "yes" is not proof that the person said it.

**Ask:** document when Alexa+ asks the customer and when it answers an elicitation itself, which response modes and form shapes it supports (single choice from an enum, free text), how it reads a choice list aloud, and what a decline or a timeout returns. A flag in the elicitation result saying "answered by the customer" would let servers use it for more than clarifications.
