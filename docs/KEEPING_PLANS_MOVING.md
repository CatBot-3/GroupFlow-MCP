# Reminders and keeping plans moving (v0.8)

Two more things go wrong in group plans after every decision is recorded correctly. People forget at the moment it matters: the cake is agreed, and nobody remembers to pick it up. And plans stall: three of five people never answer, a suggestion waits for a decision nobody wants to make, the organizer never sends the draft. Usually nobody is refusing anything; answering just feels like committing, and reminding a friend twice feels pushy. Version 0.8 gives the MCP server `set_reminder` and `review_plan`, and a check-in that the server runs by itself on plans that went quiet while something waits. As in the rest of GroupFlow, the decisions that matter (who is reminded, about what, how often) are made in code on the server, not by the model.

## Reminders: `set_reminder`

The speaker says when, who and what about. The time is resolved by the server, in the group's time zone:

| Said | Means |
| --- | --- |
| "tomorrow at 9", "Friday 6pm", "Oct 20", "the 22nd", "tonight" | That moment; a day without a time means 9 am, "tonight" 7 pm |
| "in 2 hours", "in half an hour", "in a couple of days" | From now |
| "at 5", "6:30" | Today if still ahead, otherwise tomorrow |
| "1 day before", "2 hours before the party", "the evening before", "the morning of" | Counted from the plan's own date (or from the item the reminder is about, such as the book club entry) |
| "later", "sometime next week", "this weekend", "in a bit" | Refused with `WHEN_UNCLEAR`, together with a server-held question: "When should the reminder go off: this evening at 7, tomorrow at 9am, the day before at 9am or the morning of?" Each option carries the exact call, so "the day before" sets it. |

Who gets it uses the same roles as `notify_people` (everyone, organizers, participants, owners, needs_answer, or names), and the default is the speaker. The important difference is when the roles are worked out: **when the reminder goes off, not when it is set.** "Remind whoever hasn't confirmed, the day before" reaches the people still missing on that day, not the five who were missing when it was said; someone who has left the plan by then gets nothing; and the facts in the message are read from the plan at that moment, so a reminder about the cake says the cake it is now. That late binding is the thing a phone alarm or a plain device reminder cannot do, and it is why reminders belong on the planning server.

| Rule | Why |
| --- | --- |
| Recipients can only be people who can see the plan, checked when it is set and again when it goes off | Visibility is decided by the plan's space; naming the guest of honour into a surprise is refused (`NOT_FOUND`). |
| A note that mentions a plan kept from anyone who can see this plan is refused (`PRIVATE_NOTE`), even for a reminder only for the speaker | The reminder lives in this plan, and its text can be read out wherever the plan is (a family room, a kitchen Echo), so "remind me to buy candles for the surprise party" belongs on the party plan, not the family's Home plan. The same phrase check as `notify_people`; it runs again when the reminder fires, because who can see the plan may have changed. |
| A time that has passed, or more than a year ahead, is refused; at most 20 waiting reminders per person | No silent rescheduling, no unbounded queues. |
| Only the person who set a reminder can cancel it (`cancel=<id>`); `get_context` lists theirs as `your_reminders` | A reminder is the speaker's own. |
| A reminder on a cancelled plan, or whose creator can no longer see the plan, is dropped | It would otherwise speak for someone who left. |

Delivery works like notices. The worker looks for due reminders every second and queues a notification of kind `reminder`; the person hears it on their next turn with Alexa (it is in their `get_context` updates), sees it on their GroupFlow page, and in the group chat it appears in their private thread with Alexa. We found no documented way for an Alexa+ MCP add-on to start a conversation or push to an Echo, so this is pull delivery with the chat showing what a push would look like.

## When is a plan stuck?

A check-in needs two things at once. The plan has been **quiet**: nothing has changed in it for 48 hours, or 24 when the plan's date is less than a week away. Telling people, reminders, check-ins and questions are not progress, so they never reset the quiet time. And something specific is **waiting**, worked out from the plan's own state:

| What waits | On whom | What they are asked |
| --- | --- | --- |
| Answers on a version sent out for confirmation | Everyone whose answer is still pending | Confirm or decline on their own page; "a no helps just as much as a yes" |
| A suggestion waiting for a decision | The owner it was sent to | Tell Alexa yes or no |
| Locking in a version everyone confirmed | The organizer | Ask Alexa to lock it in |
| A fix for a plan that no longer works | The organizer | Ask Alexa for alternatives |
| A draft with activities that was never sent | The organizer | Send it out when it looks right |
| A task past its due date | Its owner | Say it is done, or when it will be |
| A detail still not set, with the date a week away or less | Its owner | Decide it |

Tasks nobody has started with the date three days away or less are **loose ends**, not blockers: some of them are meant for the day itself ("drive Linda to the party"), and code cannot tell which. The server's rules only list them for the organizer; a reviewing model (below) may judge which owners to remind.

A plan whose date has passed (six hours after it starts, or after its last day) is never chased. A household plan has no single date, so a book club entry in it is not treated as "the day". The organizer can change the quiet time or turn check-ins off with `review_plan(check_ins="24h" | "48h" | "off")`.

## What a check-in sends

The worker looks every minute, only between 8 am and 10 pm in the group's time zone, and runs one round per quiet stretch:

1. Each person the plan waits on gets **one message** for everything they are waited on for, worded gently and with an easy way to say no.
2. The organizer gets a summary: what is waiting on whom, who was reminded, and any loose ends ("Not started yet, with the day 2 days away: "Share the design file before the meeting" (Marcus)"). If the organizer was also reminded, it is one message.
3. After **two** reminders about the same thing, a person is not reminded again. The organizer is told once ("Bob, Chen and Dana have not answered after 2 reminders about answers on the sent plan (P1). You could check with them directly, or ask Alexa for alternatives."), and then the server says nothing more about it. Reminding the organizer about their own step simply stops after two.

So the most anyone hears about one stuck thing is two gentle messages, and the organizer hears once that it needs a human. Real progress (an answer, a decision, a change) restarts the quiet time, and the round count starts over for anything new.

Why rules first: the detection is a handful of queries over state the server already keeps, it can be tested exactly (`tests/engine/test_momentum.py`), and it decides the two things that must never be wrong, who is contacted and how often. A check-in that reached the guest of honour, or that nagged someone ten times, would cost more trust than a stalled plan.

## A reviewing model, inside a fence

With `GROUPFLOW_CHECKINS=model` the worker asks a language model (the configured `AGENT_PROVIDER`) to review each round before anything is sent. It gets the plan's facts as JSON: status, how long it has been quiet, how far away the day is, the blockers and loose ends with names, the last few events, who was already reminded twice, and the candidates it may remind, each with the server's own draft. The prompt asks it to think it through privately (is this quiet normal for this plan, or is it stuck? is anyone likely avoiding a hard conversation, a decision nobody wants to own, a vote nobody wants to cast, that a low-pressure message would make easier?) and to answer with one JSON object: stuck or not, a one-sentence reason, at most one message per person, and a line for the organizer.

What the model can do: decide the quiet is fine (nobody is contacted; it is asked again after the next quiet stretch), choose whom to remind among the people the server listed, including owners of loose ends, and word each message. What it cannot do, enforced in `apply_review`:

| Check | Result if it fails |
| --- | --- |
| The recipient is one of the listed candidates | Ignored; a model can never add anyone |
| One message per person | Later ones are ignored |
| At most 300 characters | The server's own wording is sent instead |
| It mentions nothing its reader cannot see: checked against what every person in the plan can see and the reader cannot (another plan, a hidden detail) | The server's own wording is sent instead |
| The model answers at all, in time, with a usable JSON object | The rules decide the round |

The model was only given this plan's facts, all visible to the plan's members, so the text check is the second lock, not the only one. Who was reminded is always added to the organizer's summary by the server, so a summary cannot claim reminders that did not happen. The model's reason is kept in the audit log, not shown to members.

Why split it this way: deciding whether a quiet plan is a problem is judgement (a dinner waiting a week for a date is fine; a party in two days with no invitations sent is not), and wording a nudge so that a hesitant friend actually answers is language. Both are what a model is good at. Recipients, frequency and privacy are not judgement, so they stay in code.

## `review_plan`: "where are we with this?"

Anyone in a plan can ask what it is waiting on: "Friday dinner has been quiet for 33 hours. Waiting on: answers on the sent plan (P1) from Alice, you, Chen and Dana. The next check-in will remind them." It also lists loose ends, says when check-ins are off, and once everyone has had their two reminders says that no more go out automatically. The organizer also gets the names of people who did not answer after two reminders. This is the same report the worker uses, so what Alexa says and what the check-in does always agree.

## Trying it

In the demo console, **Skip ahead two quiet days** runs what the worker would do if 48 quiet hours had passed: reminders due by then go out, and plans stuck waiting on someone get their check-in, whatever the local hour. Rounds are recorded at the real time, so pressing it again plays the next quiet stretch: the second reminder, then the one message to the organizer, then nothing. With the group chat open (http://localhost:8788/chat), the messages appear in each person's private thread with Alexa within seconds; on each person's page they are labelled Reminder or Check-in. For example, reset the friends group to the `awaiting` stage and skip ahead: Alice, Bob, Chen and Dana are each asked for their answer, and Alice also hears who was reminded.

## Tests

| File | What it covers |
| --- | --- |
| `tests/engine/test_momentum.py` | Spoken times, late-bound recipients, facts read at fire time, cancel and leave, privacy of recipients and notes, stuck only after the quiet time, two reminders then one escalation then silence, organizer settings, loose ends, finished plans, and every check on a model's decision |
| `tests/engine/test_checkins.py` | The worker's pass with the rules and with a scripted model, a failing or rambling model falling back to the rules, and the skip-ahead sequence |
| `tests/mcp/test_reminders.py` | Both tools over real MCP: a vague time answered through the server's question, `your_reminders`, cancelling, organizer-only check-in settings, a hidden plan staying hidden |
| `tests/sim/test_chat.py` | Reminders and check-ins posted into the private thread exactly once, through the person's own session, and the console's skip-ahead end to end |

The model reviewer has been tested with scripted answers only, including hostile ones; it has not been scored with a paid model.
