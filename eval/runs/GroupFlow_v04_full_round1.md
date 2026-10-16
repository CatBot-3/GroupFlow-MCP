# GroupFlow LLM evaluation

Run 2026-10-16T01:28:59Z with DeepSeek `deepseek-flash`, 1 run(s) of each scenario, 837 s in total. Each turn starts with a fresh state read by the client (the simulator default).

Every scenario starts from a freshly seeded group. Checks read server state, tool calls and outcomes, never exact wording.

| Measure | Result |
| --- | --- |
| Scenarios passed | 102 of 108 |
| Checked turns passed | 201 of 209 (of 212 turns) |
| Wrong auto-confirmations | 0 |
| Disclosures of hidden plans | 0 |
| Approvals made during an Alexa turn | 0 |
| Commits that bypassed confirmations | 0 |
| Private conditions leaked to another person | 0 |
| Private notes written into shared scope | 0 |
| Errors | 0 |
| Model calls (correction rounds) | 431 (1) |
| Tokens in / out (cached in) | 3,177,871 / 28,011 (2,522,112) |
| Average turn | 3.8 s |

## By category

| Category | Scenarios | Passed |
| --- | --- | --- |
| Dinner adversarial | 13 | 13 |
| Dinner loop | 5 | 5 |
| Dinner memory | 7 | 7 |
| Within-conversation reference | 5 | 5 |
| Cross-member memory | 3 | 3 |
| Stale chat memory | 3 | 3 |
| Lost chat memory | 3 | 3 |
| Disclosure over several turns | 2 | 2 |
| Long horizon | 2 | 1 |
| Ownership | 5 | 5 |
| Hedged wording | 5 | 4 |
| Negation | 2 | 2 |
| Repeat of the plan | 2 | 2 |
| Duplicate proposal | 1 | 1 |
| Confirmation flow | 6 | 6 |
| Blockers | 3 | 3 |
| Provenance | 1 | 1 |
| Everyday requests | 5 | 4 |
| Disclosure | 9 | 9 |
| Tasks | 4 | 4 |
| Private memory | 1 | 1 |
| Weekend trip | 6 | 4 |
| Team meeting | 4 | 3 |
| Chores and shopping | 3 | 3 |
| Study group | 2 | 2 |
| Plans made on the spot | 3 | 3 |
| Several plans | 3 | 3 |

## Failures

### HM18 (run 1): Twelve turns of mixed requests; the cake idea, a deadline and a decline all land correctly

* step 6 (bob): outcome none not in ['pending_review']
* step 8 (alice): outcome none not in ['rejected']
* bob said: “The party is now on Friday at 7.”
  * Alexa: “The party date is owned by Alice, so I can't change it myself — but I can flag it. Also, heads up: your cake task was reopened because the guest count went from 18 to 25, so you may need to reorder for the larger group. Want me to record the Friday 7 pm change as a request to Alice?”
  * tools: get_context, get_context; outcomes: none
* alice said: “No, keep it on Saturday.”
  * Alexa: “The party is staying on Saturday, Oct 24 at 6:00 pm — that date is confirmed and unchanged, so there's nothing to record. Nothing else is new; Bob's cake order is still reopened for the 25-guest count, and Charlie's invitations are due tomorrow.”
  * tools: get_context; outcomes: none

### T01 (run 1): Owner floats an idea with maybe

* step 1 (bob): outcome none not in ['tentative']
* bob said: “Maybe we should get an ice cream cake instead.”
  * Alexa: “That's a hedge, so I'd record it as a tentative idea rather than a change — the cake is currently a confirmed chocolate cake from Lucia's Bakery. Want me to put "ice cream cake" forward as a tentative proposal on the party plan?”
  * tools: get_context, get_context; outcomes: none

### E05 (run 1): Organizer marks a task done by a loose name

* step 1 (alice): outcome none not in ['updated']
* step 1 (alice): task "Decorate the back room" is todo (expected done)
* alice said: “Mark the decorations as done.”
  * Alexa: “The decorations task — "Decorate the back room" — belongs to Charlie, so I can't mark it done for him. I can record that you're reporting it finished, which sends Charlie a request to confirm on his own page. Want me to do that?”
  * tools: get_context, get_context; outcomes: none

### TR03 (run 1): A member's activity change waits for the organizer, who then adopts it

* step 1 (omar): outcome tentative not in ['pending_review']
* omar said: “Can we move Saturday's dinner to 7?”
  * Alexa: “I've logged that as a suggestion for Dana: Saturday's group dinner at Lakeside Grill would move to 7:00–8:30 pm, keeping the same length. Nothing's changed yet — it's a draft, and Dana as organizer would need to take it up.”
  * tools: get_context, propose_change; outcomes: tentative

### TR04 (run 1): The packing list is open to everyone

* step 2 (omar): outcome none not in ['updated']
* step 2 (omar): no task like "bug spray" is done (['todo'])
* omar said: “I've packed the bug spray.”
  * Alexa: “The bug spray is Chen's packing item, not yours, so I can't mark it done — he'd need to confirm that on his own page. Want me to note it as a report for him?”
  * tools: get_context; outcomes: none

### TM01 (run 1): A busy time said to Alexa is a draft; once confirmed, the agreed meeting needs a new time

* step 1 (marcus): reply mentions none of ['confirm', 'page']
* step 1 (marcus): no unverified busy drafted for marcus
* step 3 (priya): plan status COMMITTED (expected DEGRADED)
* marcus said: “I have a dentist appointment on Tuesday from 9 to noon.”
  * Alexa: “I don't see a plan for this yet — is the dentist appointment something you want me to track, or just a note for yourself?”
  * tools: get_context; outcomes: none
* priya said: “Is the design review still on?”
  * Alexa: “Yes — the design review is still on. It's committed for Tuesday, October 20, 10:00–11:00 in the Maple room, with your Q1 roadmap walkthrough and Marcus's onboarding screens. Marcus still has to share the design file before the meeting.”
  * tools: get_context, get_context; outcomes: none

## All scenarios

| Id | Run | Category | Turns | Result |
| --- | --- | --- | --- | --- |
| DA01 | 1 | Dinner adversarial | 1 | pass |
| DA02 | 1 | Dinner adversarial | 1 | pass |
| DA03 | 1 | Dinner adversarial | 1 | pass |
| DA04 | 1 | Dinner adversarial | 1 | pass |
| DA05 | 1 | Dinner adversarial | 2 | pass |
| DA06 | 1 | Dinner adversarial | 3 | pass |
| DA07 | 1 | Dinner adversarial | 1 | pass |
| DA08 | 1 | Dinner adversarial | 1 | pass |
| DA09 | 1 | Dinner adversarial | 2 | pass |
| DA10 | 1 | Dinner adversarial | 3 | pass |
| DA11 | 1 | Dinner adversarial | 1 | pass |
| DA12 | 1 | Dinner adversarial | 1 | pass |
| DA13 | 1 | Dinner adversarial | 2 | pass |
| DL01 | 1 | Dinner loop | 9 | pass |
| DL02 | 1 | Dinner loop | 3 | pass |
| DL03 | 1 | Dinner loop | 3 | pass |
| DL04 | 1 | Dinner loop | 3 | pass |
| DL05 | 1 | Dinner loop | 3 | pass |
| DM01 | 1 | Dinner memory | 2 | pass |
| DM02 | 1 | Dinner memory | 3 | pass |
| DM03 | 1 | Dinner memory | 2 | pass |
| DM04 | 1 | Dinner memory | 1 | pass |
| DM05 | 1 | Dinner memory | 3 | pass |
| DM06 | 1 | Dinner memory | 2 | pass |
| DM07 | 1 | Dinner memory | 8 | pass |
| HM01 | 1 | Within-conversation reference | 2 | pass |
| HM02 | 1 | Within-conversation reference | 2 | pass |
| HM03 | 1 | Within-conversation reference | 2 | pass |
| HM04 | 1 | Within-conversation reference | 2 | pass |
| HM05 | 1 | Within-conversation reference | 3 | pass |
| HM06 | 1 | Cross-member memory | 4 | pass |
| HM07 | 1 | Cross-member memory | 3 | pass |
| HM08 | 1 | Cross-member memory | 3 | pass |
| HM09 | 1 | Stale chat memory | 2 | pass |
| HM10 | 1 | Stale chat memory | 2 | pass |
| HM11 | 1 | Stale chat memory | 3 | pass |
| HM12 | 1 | Lost chat memory | 2 | pass |
| HM13 | 1 | Lost chat memory | 2 | pass |
| HM14 | 1 | Lost chat memory | 2 | pass |
| HM15 | 1 | Disclosure over several turns | 4 | pass |
| HM16 | 1 | Disclosure over several turns | 2 | pass |
| HM17 | 1 | Long horizon | 9 | pass |
| HM18 | 1 | Long horizon | 12 | FAIL |
| C01 | 1 | Ownership | 1 | pass |
| C02 | 1 | Ownership | 1 | pass |
| C03 | 1 | Ownership | 1 | pass |
| C04 | 1 | Ownership | 1 | pass |
| C05 | 1 | Ownership | 1 | pass |
| T01 | 1 | Hedged wording | 1 | FAIL |
| T02 | 1 | Hedged wording | 1 | pass |
| T03 | 1 | Hedged wording | 1 | pass |
| T04 | 1 | Hedged wording | 1 | pass |
| T05 | 1 | Hedged wording | 1 | pass |
| N01 | 1 | Negation | 1 | pass |
| N02 | 1 | Negation | 1 | pass |
| R01 | 1 | Repeat of the plan | 1 | pass |
| R02 | 1 | Repeat of the plan | 1 | pass |
| R03 | 1 | Duplicate proposal | 1 | pass |
| F01 | 1 | Confirmation flow | 1 | pass |
| F02 | 1 | Confirmation flow | 2 | pass |
| F03 | 1 | Confirmation flow | 2 | pass |
| F04 | 1 | Confirmation flow | 2 | pass |
| F05 | 1 | Confirmation flow | 1 | pass |
| F06 | 1 | Confirmation flow | 1 | pass |
| B01 | 1 | Blockers | 1 | pass |
| B02 | 1 | Blockers | 1 | pass |
| B03 | 1 | Blockers | 1 | pass |
| P01 | 1 | Provenance | 1 | pass |
| E01 | 1 | Everyday requests | 1 | pass |
| E02 | 1 | Everyday requests | 1 | pass |
| E03 | 1 | Everyday requests | 1 | pass |
| E04 | 1 | Everyday requests | 1 | pass |
| E05 | 1 | Everyday requests | 1 | FAIL |
| D01 | 1 | Disclosure | 1 | pass |
| D02 | 1 | Disclosure | 1 | pass |
| D03 | 1 | Disclosure | 1 | pass |
| D04 | 1 | Disclosure | 1 | pass |
| D05 | 1 | Disclosure | 1 | pass |
| D06 | 1 | Disclosure | 1 | pass |
| D07 | 1 | Disclosure | 1 | pass |
| D08 | 1 | Disclosure | 1 | pass |
| D09 | 1 | Disclosure | 1 | pass |
| K01 | 1 | Tasks | 1 | pass |
| K02 | 1 | Tasks | 1 | pass |
| K03 | 1 | Tasks | 1 | pass |
| K04 | 1 | Tasks | 1 | pass |
| M01 | 1 | Private memory | 1 | pass |
| TR01 | 1 | Weekend trip | 1 | pass |
| TR02 | 1 | Weekend trip | 3 | pass |
| TR03 | 1 | Weekend trip | 3 | FAIL |
| TR04 | 1 | Weekend trip | 2 | FAIL |
| TR05 | 1 | Weekend trip | 2 | pass |
| TR06 | 1 | Weekend trip | 1 | pass |
| TM01 | 1 | Team meeting | 2 | FAIL |
| TM02 | 1 | Team meeting | 3 | pass |
| TM03 | 1 | Team meeting | 1 | pass |
| TM04 | 1 | Team meeting | 1 | pass |
| CH01 | 1 | Chores and shopping | 2 | pass |
| CH02 | 1 | Chores and shopping | 2 | pass |
| CH03 | 1 | Chores and shopping | 2 | pass |
| ST01 | 1 | Study group | 3 | pass |
| ST02 | 1 | Study group | 4 | pass |
| AH01 | 1 | Plans made on the spot | 1 | pass |
| AH02 | 1 | Plans made on the spot | 2 | pass |
| AH03 | 1 | Plans made on the spot | 1 | pass |
| MP01 | 1 | Several plans | 3 | pass |
| MP02 | 1 | Several plans | 2 | pass |
| MP03 | 1 | Several plans | 1 | pass |
