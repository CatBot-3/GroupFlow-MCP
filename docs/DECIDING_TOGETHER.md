# Deciding things together

This covers the four things a group would otherwise still do outside GroupFlow: choosing among options with many people, letting silence become an answer, keeping track of who paid for what, and knowing who is coming and who brings what. Each one is an MCP tool (`poll`, `rsvp`, `bring_list`, `costs`; in the voice profile `ask_group`, `my_part` and `split_costs`) on the same generic core, with the same principle as the rest of the server: the model words things, the server decides who may do what, who hears it and what counts.

The hard part of every feature here was the same question in a new costume: **whose word is this?** A vote is a person's preference, not their consent. A deadline turns silence into a decision only if everyone was told in advance and the decision was the decider's to make anyway. An RSVP is a promise about where you will be, so only you can give it. A payment is a claim about money, so the person who handled the money confirms it. Each rule below is one answer to that question, enforced in code.

## Votes: `poll`

| Said | What the server does |
| --- | --- |
| "Let's vote on food: pizza, tacos or everyone cooks, until Wednesday night" | `start` with 2 to 8 options and an optional closing time; everyone who can see the plan is told |
| "Tacos!", "the first one", "no preference" | `vote`: the speaker's pick, matched to the options with the same matcher as follow-up answers; "no preference" is a real answer and counts as having voted |
| "OK, tacos it is" (by whoever decides) | `decide`: the decider picks; if the vote is about a detail, the value is written there; everyone is told |

A vote never changes anything by itself. Who decides follows the plan's own rules, so a vote cannot be used to route around an owner: a vote about a detail is decided by that detail's owner (or, for an open detail anyone may set, by whoever started the vote), and a vote about nothing in particular is decided by the organizer. Votes are open inside the plan (the plan view says who chose what and who has not answered), because a group choosing a restaurant is not a secret ballot and the decider needs to see the tally to decide.

| Rule | Why |
| --- | --- |
| A vote about an activity is refused (`CONSENT_REQUIRED`) | An activity counts only when each person taking part confirms it on their own page. A majority of "yes" would let three people commit the fourth; the organizer can still start a plain vote, then change the plan and send it out for confirmation. |
| At most 5 open votes per plan, one per detail | Check-ins ask non-voters; ten open votes would mean ten reminders. |
| The question and options go through the hidden-plan phrase check (`PRIVATE_NOTE`) | A vote in the family's Home plan that names Rosa's back room would tell Linda about her surprise. |
| The decider is told when everyone else has answered, and a check-in asks people who have not | Silence is the usual failure; "no preference" is an easy way out. |

## Decide-by deadlines with a default

"If nobody objects by Thursday we go with Rosa's" is the cleanest cure for a stall, because silence becomes an answer everyone was told about in advance. It is also the easiest feature here to make unsafe, so the server draws the line:

1. **Only the decider can set one.** Starting a vote with `closes` and `fallback`, or a proposal (one option) settled unless someone objects, is refused for anyone except the person who could decide the question anyway: the detail's owner, or the organizer. Others can start a plain vote, which is only a suggestion.
2. **Never for activities.** A deadline cannot replace each person's confirmation, for the same reason a vote cannot.
3. **Everyone is told the rule when it starts**, with the exact time ("unless someone objects by Thu, Oct 22, 6:00 pm, Venue will be Rosa's Trattoria, main room"). A vague closing time ("soon") is refused with a server-held question offering concrete times, like reminders.
4. **One objection stops the default.** An objection (with its reason, if given) goes to the decider at once; at the deadline nothing is applied, and the decider is asked.
5. **A value that changed meanwhile is never overwritten.** The vote remembers which value of the detail it was started against; if someone set a new value before the deadline, the default is not applied and the decider is asked instead.

At the deadline (the worker checks every second, with reminders), a choice vote with `fallback: "leading"` takes the option with the most votes, and with a named fallback takes the leader or, on a tie, the named option. A tie with `"leading"`, or a vote with no fallback, closes and goes to the decider, and a check-in asks them if they stay quiet. The result, and why ("nobody objected by the deadline", "it led when votes closed: Tacos 3, Pizza 1"), is told to everyone in the plan.

## RSVPs and the bring list: `rsvp`, `bring_list`

An RSVP is the speaker's own yes, no or maybe, with how many guests they bring (0 to 10). Only the speaker can answer for themselves, because it is a promise about where they will be; the connection fixes the speaker, so there is no argument for whom. The headcount is computed from the answers (yes plus their guests; maybes shown separately) instead of being typed by anyone. A plan's "Guest count" detail stays its owner's to set, because it may include guests who are not in GroupFlow.

An RSVP is not consent to anything. It confirms no activity (those stay on each person's page), which is also why the page offers it on dated plans without activities (a household's running list has nothing to come to), and on activity plans only once someone has started using it.

Dietary needs are deliberately not part of an RSVP. They stay private conditions (`set_my_conditions`, confirmed on the person's own page), and the RSVP view shows only the needs to cater for, as tags without names, from confirmed conditions of people who said yes or maybe. A potluck host needs to know someone is vegetarian; nobody needs to know who.

The bring list is a potluck list. Anyone in the plan adds what is needed; each person claims what they bring; nobody can claim for someone else or take over someone else's claim ("Kofi is already bringing the ice; only he can hand it over"). "I'll bring a salad" adds and claims in one step. Saying no to the plan frees what you were bringing and tells the others it needs someone. A shared word is not the same thing: "birthday candles" does not claim the "birthday banner". Trips keep their packing list, which already says who brings what, so `bring_list` is refused there with a pointer to it rather than splitting one list into two.

Check-ins learned two new waits. With the plan's date a week away or less, people who have not answered whether they are coming are waited on; with the date three days away or less, things nobody has claimed are loose ends, listed for the organizer ("Nobody is bringing ice yet, with the day 2 days away").

## Shared costs: `costs`

Each expense has a payer, an amount in the plan's currency, what it was for, and who it is split among. Without a list it is split among everyone in the plan except people who said they are not coming, with a guest counted in the share of whoever brings them; a list can name people, "me" or "everyone". Shares are equal and add up exactly (the leftover cents go to the first people in a fixed order). Balances come from the confirmed entries only, and the settle-up suggestions pay the largest debt to the largest credit first, which needs at most one payment fewer than the number of people.

| Recorded | By | Counts |
| --- | --- | --- |
| An expense | Its payer ("I paid 60 for the cake") | At once; the people sharing it are told their share |
| An expense | Someone else ("Bob paid for the pizza") | Once the payer confirms it; until then it waits and shows as waiting |
| A repayment | Its receiver ("Carol paid me back 15") | At once |
| A repayment | Anyone else ("I paid Alice back") | Once the receiver confirms they got it; the amount defaults to what the balances say |

The rule is one sentence: **whoever handled the money confirms it.** The payer knows whether they paid; the receiver knows whether the money arrived. It also closes the two obvious abuses, recording a debt against someone else and marking your own debt as paid, without asking anyone to referee.

Nothing is paid through GroupFlow; it keeps the list, works out the balances and reminds people. Settling up is a check-in after the day: for up to 30 days after the plan has happened (for plans without a date, three days after the last expense), the people who owe are reminded once a quiet stretch has passed, each with their own amount and to whom ("By the list you owe Alice CAD 45.32"), and payments waiting for someone's word are a wait at any time. A plan that is over still waits on nothing else, so this is the only thing a finished plan is ever chased for.

Removing an expense is for its payer, the person who recorded it, or the organizer; the people sharing it are told and see their new balance. Descriptions go through the hidden-plan phrase check, and a plan's costs are visible only to the people who can see the plan, so the family's household plan never shows the surprise party's room deposit.

## Where people answer

Everything above works by voice, as the speaker, through the MCP tools. Each person's own GroupFlow page also has a "Your say" section: open votes with their options (and the decider's buttons for the decider), "Coming?" with a guest count, the bring list with "I'll bring it", and payments recorded for them with "Yes, right" or "No". These call the same engine methods as the tools, behind the person's own login and CSRF token (`/human/polls/vote`, `/human/rsvp`, `/human/bring`, `/human/costs/confirm`); the page adds nothing Alexa could not do, it is just the place to answer without speaking and the place a check-in points to. The plan board shows votes with their tally, who is coming, the bring list and the costs as the selected viewer sees them. In the group chat, notices of these kinds reach each person's private thread like reminders do.

## In the demo world

| Plan | What is seeded |
| --- | --- |
| Linda's surprise party | Bob paid CAD 64 for the cake and Alice CAD 100 for the room deposit (Charlie owes CAD 54.66); Charlie brings balloons, Bob the candles, the streamers and banner are unclaimed; a music vote tied one each, set to take the leader on Thursday, so it goes back to Alice |
| Cottage weekend | Dana proposes 4:00 pm at her place as the meeting point unless someone objects in three days; Dana paid CAD 240 for the minivan; Omar recorded CAD 86.40 of groceries Priya paid for, waiting for Priya |
| Post-midterm potluck (new, study group) | Noor, Chen and Sam (with a guest) are coming, Jun is a maybe; Sam's vegetarian diet shows as a need without his name; dessert and samosas are claimed, drinks, chips and plates are not; Noor's CAD 42.50 for drinks is split five ways by heads; "After dinner?" has one vote |

## How it is tested

`tests/engine/test_together.py` (29 tests) states each rule as a test: who decides, the activity refusal, the deadline cases (no objection, one objection, a value changed meanwhile, the leader, a tie with and without a named default), a vague deadline asked rather than guessed, the hidden-plan check, RSVPs only for oneself, dietary tags without names, claims that cannot be taken over, money that adds up exactly, the confirmation rules for expenses and repayments, and settling up after the day but not before or a month later. `tests/mcp/test_together.py` runs a vote, an RSVP, a claim and a confirmation over real MCP and over the page's HTTP API, including the refusals.

`eval/realistic/together_v1.yaml` is a held-out suite of 17 scenarios in the realistic world: a vote started with a closing time, one-word and "whatever" answers, a grumble that must not count as a vote, the decider deciding from the tally, a default set by the owner of the 60th's venue that its guest of honour hears nothing about, an objection with its reason, a deadline on bouldering that must be refused, RSVPs with a cousin and a maybe, a yes with a diet that must become a private draft, a claim, a claim someone else already holds, an expense split four ways, someone else's payment, a repayment, and a mother asking whether she owes anything while her children's deposit for her party sits in a plan she cannot see. Every reference trajectory passes with all safety counters at zero, a model that only says "Okay." fails every scenario that needs an action, a model that writes everything down fails the one where nothing may change, and no sentence shares a five-word sequence with the prompt corpus. No paid model has run it.

## Limits

Votes are open, not secret; a group that needs a secret ballot should not use this. Only equal splits (by heads, with guests) are supported, not amounts or percentages per person, and one currency per plan. Nobody can dispute an expense except by asking its payer, recorder or the organizer to remove it. RSVP plus-ones are a number, not named guests. Delivery is still pull-only (the next turn, the page, the chat thread), so a deadline that passes overnight is heard about in the morning. See [known limitations](KNOWN_LIMITATIONS.md).
