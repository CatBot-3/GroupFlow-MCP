# Telling people and asking first

Two things go wrong in group plans even when every decision is recorded correctly. The news does not reach the people it affects: the organizer moves the party to 9 and two of the four people still turn up at 8. And an assistant fills a gap with a guess: "can we push it back a bit" becomes 8:30 because the model had to pick something. GroupFlow gives the MCP server three tools for this, `notify_people`, `ask_followup` and `answer_followup` (in the voice profile they are `tell_people` and `answer`), and keeps the important decisions on the server rather than in the prompt, for the same reason as the rest of GroupFlow: a model can be talked into things, and a rule in code cannot.

## Telling people: `notify_people`

The speaker says who should hear it, by role or by name, and what it is about. The server decides everything else.

| Audience | Who that is, computed from the plan at that moment |
| --- | --- |
| `everyone` (default) | Everyone who can see the plan |
| `organizers` | The plan's organizer and group organizers who can see it |
| `participants` | The people taking part in the activities it is about (or in any activity of the plan); in a plan without activities, everyone in it |
| `owners` | The owners of the items it is about and of the work that depends on them; without items, everyone who owns something in the plan |
| `needs_answer` | People whose answer is pending: a page confirmation in an open round, or a decision on an open request |
| a name | That person, if they can see the plan; otherwise the call is refused (`NOT_FOUND`), so the guest of honour cannot be named into a surprise |

The assistant maps everyday phrasings onto the levels ("whoever's in charge" is `organizers`, "whoever hasn't confirmed" is `needs_answer`), the server also accepts a few synonyms (hosts, attendees, pending), and one call may combine several, such as `["organizers", "Chen"]`.

Why the server and not the model writes the message: each notice states the facts from the plan's own state ("Venue is "Lucky Star karaoke" (Bob suggested ..., waiting for Alice)"), so a model's paraphrase can never put a wrong time in someone else's ear. The speaker's own words can ride along as a short `note`. Each recipient also gets a line for what they have to do, if anything: "Your answer is needed on your GroupFlow page" for a pending confirmation, "It is waiting for your decision; tell Alexa yes or no" for an open request, "Check that your part still works with this" for the owner of dependent work. A bare reminder with no note ("poke whoever still hasn't confirmed") is accepted exactly when every recipient has such a line.

The rules that keep it safe and quiet:

| Rule | Why |
| --- | --- |
| Only people who can see the plan are ever told | Visibility is decided by the plan's space, as everywhere else. A hidden plan's guest is not a possible recipient. |
| A note that mentions a plan kept from a recipient is refused (`PRIVATE_NOTE`), and nobody is told | "Tell the family the surprise party is at Rosa's" must not reach Linda through the family plan either. The check compares the note with the titles and place and text values of plans in the recipient's own group that they cannot see, ignoring words that also appear in plans they can see. It is a phrase check, so it is a safety net, not a guarantee. |
| People in the current conversation are skipped for a role | In a group chat or on a shared Echo they just heard it. A named person is always told. |
| The same unread notice is not sent twice within 30 minutes; six updates per plan per speaker per 10 minutes | Repeating "tell everyone" should not spam anyone. |
| Telling people changes nothing in the plan, and is never anyone's agreement | Consent still happens only on each person's own page. The result says so, so the model can too. |

Delivery is pull, not push: a notice waits on the server and reaches the person on their next turn with Alexa (it is in their `get_context` updates) or, in the group chat, appears at once in their private thread with Alexa. We found no documented way for an Alexa+ MCP add-on to start a conversation or push a notification, so the server keeps the notice until the person's own assistant reads it, and the chat shows what a push would look like.

## "Want me to tell the others?": the server's offer

When a decision takes effect on a detail (an owner sets the venue, accepts a suggestion, amends one), the result carries `suggested_followup`: a question such as `Want me to let Bob know that Venue is now "Lucky Star karaoke"?`, with the exact `notify_people` call behind its yes. The people listed are those who can see the plan, minus the speaker, minus whoever the change itself already reached (the person whose suggestion was accepted, the owners of work that depends on the item, which the change flagged for them), minus anyone listening. When nobody is left, there is no offer. Asking is the assistant's choice; the server only makes the offer cheap and exact.

Three details make it pleasant rather than nagging. A second decision in the same plan soon after widens the same offer (same id), so one "yes" tells everyone about both changes. A "no" stops offers in that plan for 30 minutes. An offer the assistant may never have asked expires after 10 minutes, a question it did ask after 30.

## Asking first: `ask_followup` and `answer_followup`

When the speaker is vague about something the assistant would otherwise have to invent, the assistant asks, and the server keeps the question together with the exact call behind each answer:

```
kofi:  can we do the 60th a little later? kwame's shift runs late that day
Alexa: ask_followup("How much later, 7:00 or 7:30?",
         7:00 -> propose_change(sixty.date, "Sat, Nov 28 7:00 pm"),
         7:30 -> propose_change(sixty.date, "Sat, Nov 28 7:30 pm"))
       "Sure. How much later, 7:00 or 7:30?"
kofi:  half 7 should do it
Alexa: answer_followup("7:30")  -> the server runs the 7:30 change, as Kofi, with every permission check
       "Done, it now starts at 7:30."
```

Why keep the question on the server: the answer then runs exactly what was offered, not the model's second attempt at rebuilding the call, and it survives the assistant losing its chat memory or the person answering from another device. The briefing lists waiting questions as `open_questions` (newest first, marked `asked` when the assistant asked them and not when they are server offers), and `answer_followup` without an id matches the person's words against the newest question they answer, so "7:30" answers the time question even if an offer to tell people came after it. Words are matched by label ("7:30 pm" from "7:30" or "7"), position ("the second one"), yes and no in their casual forms ("yep do it", "nah"), and shared words. Only the person who was asked can answer, and in a shared conversation only questions about plans everyone listening can see are listed.

Options can run `notify_people`, `propose_change`, `decide_change`, `request_confirmations`, `find_alternatives`, `commit_plan`, `create_plan`, `report_observation` or `set_my_conditions`. Each runs as if the speaker had called it then, so a follow-up cannot do anything the speaker could not. When the action records the speaker's words, the question and their answer are added to them (`can we do the 60th a little later [asked: "How much later, 7:00 or 7:30?" answered: "7:30"]`), so the history shows how a vague request became a concrete one.

The server asks for clarification itself in two places:

| Situation | What the server does |
| --- | --- |
| A time change whose words name no time, day, date or amount ("a bit later", "sometime next week", "move it up") | Refuses with `VAGUE_TIME` and changes nothing; the error tells the assistant to ask, with concrete options. "Half an hour later", "push it to 8", "the 28th" and "tomorrow" pass. A number inside a name ("her 60th") does not count as a time. |
| A request that could mean several plans | The `AMBIGUOUS_PLAN` error of `propose_change` and `notify_people` carries a ready "Which plan is that for: Home, Friday dinner or ...?" follow-up whose options replay the same call with each plan. |

## The host can ask directly: MCP elicitation

Alexa+ supports MCP elicitation: a server can ask the customer for missing information during a tool call. GroupFlow uses it when it can. Start the server with `GROUPFLOW_MCP_RESPONSES=sse` (streamed responses; the default `json` sends one JSON body per request and cannot carry a request from the server). Then, if the host declared form elicitation, `ask_followup` asks the person through the host with a single-choice form whose choices are the option labels, runs the chosen option at once, and returns the answered result (`asked_by_host: true`). If the person declines or closes it, nothing runs and the question is dropped. If the host cannot elicit, the question comes back for the assistant to ask in words. Both paths are covered by tests over real MCP with the official SDK client (`tests/mcp/test_followups.py`); neither has been tried on a real Alexa+ device.

## In the group chat

What `notify_people` sends appears immediately in each recipient's private thread with Alexa, marked as an update from that plan, written by the server for that person. When Alexa's reply asks a question the server holds, the person who was asked sees quick-answer buttons under it; tapping one simply says that answer in the conversation, so the model and the server handle it exactly like a typed reply.

## How it is evaluated

`eval/realistic/followups_v1.yaml` adds ten held-out scenarios in the realistic world: heads-ups to named people about a surprise, "poke whoever hasn't confirmed", "tell whoever's in charge", telling one person about someone else's change, the offer accepted and declined, two vague times (one answered after Alexa's memory was wiped), a vague day, and a group chat where the people who still have to confirm are reading along. New checks: `notified` and `not_notified` (who received a notice during the turn) and `asks_first` (the reply asks, and nothing changed). Every notice written during any turn is also scanned like a reply: the hidden plan's text must not reach the people it is kept from, and nobody's private limits may reach anyone else. A negative control proves that scan counts a surprise sent to its guest even if the server's own guard failed. The reference trajectories pass and a silent or record-everything policy fails each scenario; no paid model has been run on them yet.

## Limits

The note check matches phrases, so a paraphrase of a secret ("the thing on the 28th at the Ghanaian place") can pass it; the plan's own facts can never leak because recipients are limited to the plan's audience. Offers are made after decisions on details, not after task or activity changes (activities already have their own confirmation round and notices). The simulated host keeps only text between turns, which is why answers without an id are matched against the newest questions. Notices are delivered when the person next talks to their assistant or opens the chat; there is no push to a phone or an Echo.
