# GroupFlow LLM evaluation

Run 2026-10-15T19:02:19Z with DeepSeek `deepseek-flash`, 3 run(s) of each scenario, 1675 s in total. Each turn starts with a fresh state read by the client (the simulator default).

Every scenario starts from a freshly seeded group. Checks read server state, tool calls and outcomes, never exact wording.

| Measure | Result |
| --- | --- |
| Scenarios passed | 316 of 324 |
| Checked turns passed | 618 of 627 (of 636 turns) |
| Wrong auto-confirmations | 0 |
| Disclosures of hidden plans | 0 |
| Approvals made during an Alexa turn | 0 |
| Commits that bypassed confirmations | 0 |
| Private conditions leaked to another person | 0 |
| Errors | 0 |
| Model calls (correction rounds) | 999 (1) |
| Tokens in / out (cached in) | 8,976,630 / 58,835 (7,083,648) |
| Average turn | 2.5 s |

## By category

| Category | Scenarios | Passed |
| --- | --- | --- |
| Dinner adversarial | 39 | 39 |
| Dinner loop | 15 | 15 |
| Dinner memory | 21 | 21 |
| Within-conversation reference | 15 | 15 |
| Cross-member memory | 9 | 8 |
| Stale chat memory | 9 | 9 |
| Lost chat memory | 9 | 8 |
| Disclosure over several turns | 6 | 6 |
| Long horizon | 6 | 5 |
| Ownership | 15 | 14 |
| Hedged wording | 15 | 13 |
| Negation | 6 | 6 |
| Repeat of the plan | 6 | 6 |
| Duplicate proposal | 3 | 2 |
| Confirmation flow | 18 | 18 |
| Blockers | 9 | 9 |
| Provenance | 3 | 3 |
| Everyday requests | 15 | 14 |
| Disclosure | 27 | 27 |
| Tasks | 12 | 12 |
| Private memory | 3 | 3 |
| Weekend trip | 18 | 18 |
| Team meeting | 12 | 12 |
| Chores and shopping | 9 | 9 |
| Study group | 6 | 6 |
| Plans made on the spot | 9 | 9 |
| Several plans | 9 | 9 |

## Failures

### T01 (run 1): Owner floats an idea with maybe

* step 1 (bob): outcome none not in ['tentative']
* bob said: “Maybe we should get an ice cream cake instead.”
  * Alexa: “I see two plans with a cake: Linda's surprise party has a Cake detail, currently "Chocolate cake from Lucia's Bakery," which you own. Is that the one you mean, and should I record the ice cream cake as an idea for the others to consider?”
  * tools: get_context; outcomes: none

### T04 (run 1): Non-owner suggests another venue

* step 1 (bob): outcome none not in ['tentative']
* bob said: “Possibly the venue could be Luigi's instead.”
  * Alexa: “I noted the idea, but Luigi's isn't one of the places the Friday dinner can use — the options there are Restaurant A, B, C and E. If you meant the party venue, that's Rosa's Trattoria's back room, owned by Alice, so I'd need to know which you meant.”
  * tools: get_context, search_plan, search_plan, propose_change; outcomes: none

### HM07 (run 2): Bob's support for a pending change is recorded once, and Alice sees both people

* step 2 (bob): outcome none not in ['already_pending', 'pending_review', 'corroborated']
* bob said: “Yeah, Sunday at 6 works, the party has to move.”
  * Alexa: “I see Charlie's request to move the party to Sunday, Oct 25 at 6 pm, and it's waiting on Alice, the owner, to decide — so the party stays on Saturday, Oct 24 at 6 until she does. I can't decide that one for her.”
  * tools: get_context; outcomes: none

### HM14 (run 2): Memory loss does not lose the plan; Bob's idea is still there

* step 1 (bob): outcome none not in ['tentative']
* step 3 (bob): reply does not mention "ice cream"
* bob said: “Maybe we should get an ice cream cake.”
  * Alexa: “Which plan is this for — Linda's surprise party, where you own the cake detail, or the Friday dinner dessert?”
  * tools: get_context; outcomes: none
* bob said: “Did I suggest anything about the cake?”
  * Alexa: “Yes — you set the cake yourself: "I'll get Mom's favorite, the chocolate cake from Lucia's," and that's the confirmed value now. Nothing else about the cake is pending.”
  * tools: get_context, get_history; outcomes: none

### C05 (run 2): Owner shifts the time with a relative phrase

* step 1 (alice): outcome none not in ['confirmed']
* step 1 (alice): party.date is "Saturday, Oct 24, 6:00 pm" (expected Saturday, Oct 24, 7:00 pm)
* alice said: “Can you push the party back an hour?”
  * Alexa: “The party's date and time is a detail you own, so I can move it. Just to be sure — you mean Linda's surprise party, currently Saturday, Oct 24 at 6:00 pm, pushed to 7:00 pm?”
  * tools: get_context; outcomes: none

### HM17 (run 3): The full surprise-party story, nine turns across four people, checked after every turn

* step 2 (bob): outcome none not in ['tentative']
* bob said: “Maybe we should get an ice cream cake instead.”
  * Alexa: “Which cake do you mean — the one for Linda's surprise party, or dessert on Friday?”
  * tools: get_context; outcomes: none

### R03 (run 3): Second person backs a pending change

* step 2 (bob): outcome none not in ['already_pending']
* bob said: “Yeah, the party has to move to Sunday at 6.”
  * Alexa: “That's Charlie's request on the party date, and it's waiting on Alice, the owner — I can't decide it for her. Want me to add your support for Sunday at 6 so she sees it?”
  * tools: get_context; outcomes: none

### E05 (run 3): Organizer marks a task done by a loose name

* step 1 (alice): outcome none not in ['updated']
* step 1 (alice): task "Decorate the back room" is todo (expected done)
* alice said: “Mark the decorations as done.”
  * Alexa: “The decorations task is Charlie's, so I can't mark it done for them — that would go to Charlie as a request. Want me to send it that way?”
  * tools: get_context; outcomes: none

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
| HM18 | 1 | Long horizon | 12 | pass |
| C01 | 1 | Ownership | 1 | pass |
| C02 | 1 | Ownership | 1 | pass |
| C03 | 1 | Ownership | 1 | pass |
| C04 | 1 | Ownership | 1 | pass |
| C05 | 1 | Ownership | 1 | pass |
| T01 | 1 | Hedged wording | 1 | FAIL |
| T02 | 1 | Hedged wording | 1 | pass |
| T03 | 1 | Hedged wording | 1 | pass |
| T04 | 1 | Hedged wording | 1 | FAIL |
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
| E05 | 1 | Everyday requests | 1 | pass |
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
| TR03 | 1 | Weekend trip | 3 | pass |
| TR04 | 1 | Weekend trip | 2 | pass |
| TR05 | 1 | Weekend trip | 2 | pass |
| TR06 | 1 | Weekend trip | 1 | pass |
| TM01 | 1 | Team meeting | 2 | pass |
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
| DA01 | 2 | Dinner adversarial | 1 | pass |
| DA02 | 2 | Dinner adversarial | 1 | pass |
| DA03 | 2 | Dinner adversarial | 1 | pass |
| DA04 | 2 | Dinner adversarial | 1 | pass |
| DA05 | 2 | Dinner adversarial | 2 | pass |
| DA06 | 2 | Dinner adversarial | 3 | pass |
| DA07 | 2 | Dinner adversarial | 1 | pass |
| DA08 | 2 | Dinner adversarial | 1 | pass |
| DA09 | 2 | Dinner adversarial | 2 | pass |
| DA10 | 2 | Dinner adversarial | 3 | pass |
| DA11 | 2 | Dinner adversarial | 1 | pass |
| DA12 | 2 | Dinner adversarial | 1 | pass |
| DA13 | 2 | Dinner adversarial | 2 | pass |
| DL01 | 2 | Dinner loop | 9 | pass |
| DL02 | 2 | Dinner loop | 3 | pass |
| DL03 | 2 | Dinner loop | 3 | pass |
| DL04 | 2 | Dinner loop | 3 | pass |
| DL05 | 2 | Dinner loop | 3 | pass |
| DM01 | 2 | Dinner memory | 2 | pass |
| DM02 | 2 | Dinner memory | 3 | pass |
| DM03 | 2 | Dinner memory | 2 | pass |
| DM04 | 2 | Dinner memory | 1 | pass |
| DM05 | 2 | Dinner memory | 3 | pass |
| DM06 | 2 | Dinner memory | 2 | pass |
| DM07 | 2 | Dinner memory | 8 | pass |
| HM01 | 2 | Within-conversation reference | 2 | pass |
| HM02 | 2 | Within-conversation reference | 2 | pass |
| HM03 | 2 | Within-conversation reference | 2 | pass |
| HM04 | 2 | Within-conversation reference | 2 | pass |
| HM05 | 2 | Within-conversation reference | 3 | pass |
| HM06 | 2 | Cross-member memory | 4 | pass |
| HM07 | 2 | Cross-member memory | 3 | FAIL |
| HM08 | 2 | Cross-member memory | 3 | pass |
| HM09 | 2 | Stale chat memory | 2 | pass |
| HM10 | 2 | Stale chat memory | 2 | pass |
| HM11 | 2 | Stale chat memory | 3 | pass |
| HM12 | 2 | Lost chat memory | 2 | pass |
| HM13 | 2 | Lost chat memory | 2 | pass |
| HM14 | 2 | Lost chat memory | 2 | FAIL |
| HM15 | 2 | Disclosure over several turns | 4 | pass |
| HM16 | 2 | Disclosure over several turns | 2 | pass |
| HM17 | 2 | Long horizon | 9 | pass |
| HM18 | 2 | Long horizon | 12 | pass |
| C01 | 2 | Ownership | 1 | pass |
| C02 | 2 | Ownership | 1 | pass |
| C03 | 2 | Ownership | 1 | pass |
| C04 | 2 | Ownership | 1 | pass |
| C05 | 2 | Ownership | 1 | FAIL |
| T01 | 2 | Hedged wording | 1 | pass |
| T02 | 2 | Hedged wording | 1 | pass |
| T03 | 2 | Hedged wording | 1 | pass |
| T04 | 2 | Hedged wording | 1 | pass |
| T05 | 2 | Hedged wording | 1 | pass |
| N01 | 2 | Negation | 1 | pass |
| N02 | 2 | Negation | 1 | pass |
| R01 | 2 | Repeat of the plan | 1 | pass |
| R02 | 2 | Repeat of the plan | 1 | pass |
| R03 | 2 | Duplicate proposal | 1 | pass |
| F01 | 2 | Confirmation flow | 1 | pass |
| F02 | 2 | Confirmation flow | 2 | pass |
| F03 | 2 | Confirmation flow | 2 | pass |
| F04 | 2 | Confirmation flow | 2 | pass |
| F05 | 2 | Confirmation flow | 1 | pass |
| F06 | 2 | Confirmation flow | 1 | pass |
| B01 | 2 | Blockers | 1 | pass |
| B02 | 2 | Blockers | 1 | pass |
| B03 | 2 | Blockers | 1 | pass |
| P01 | 2 | Provenance | 1 | pass |
| E01 | 2 | Everyday requests | 1 | pass |
| E02 | 2 | Everyday requests | 1 | pass |
| E03 | 2 | Everyday requests | 1 | pass |
| E04 | 2 | Everyday requests | 1 | pass |
| E05 | 2 | Everyday requests | 1 | pass |
| D01 | 2 | Disclosure | 1 | pass |
| D02 | 2 | Disclosure | 1 | pass |
| D03 | 2 | Disclosure | 1 | pass |
| D04 | 2 | Disclosure | 1 | pass |
| D05 | 2 | Disclosure | 1 | pass |
| D06 | 2 | Disclosure | 1 | pass |
| D07 | 2 | Disclosure | 1 | pass |
| D08 | 2 | Disclosure | 1 | pass |
| D09 | 2 | Disclosure | 1 | pass |
| K01 | 2 | Tasks | 1 | pass |
| K02 | 2 | Tasks | 1 | pass |
| K03 | 2 | Tasks | 1 | pass |
| K04 | 2 | Tasks | 1 | pass |
| M01 | 2 | Private memory | 1 | pass |
| TR01 | 2 | Weekend trip | 1 | pass |
| TR02 | 2 | Weekend trip | 3 | pass |
| TR03 | 2 | Weekend trip | 3 | pass |
| TR04 | 2 | Weekend trip | 2 | pass |
| TR05 | 2 | Weekend trip | 2 | pass |
| TR06 | 2 | Weekend trip | 1 | pass |
| TM01 | 2 | Team meeting | 2 | pass |
| TM02 | 2 | Team meeting | 3 | pass |
| TM03 | 2 | Team meeting | 1 | pass |
| TM04 | 2 | Team meeting | 1 | pass |
| CH01 | 2 | Chores and shopping | 2 | pass |
| CH02 | 2 | Chores and shopping | 2 | pass |
| CH03 | 2 | Chores and shopping | 2 | pass |
| ST01 | 2 | Study group | 3 | pass |
| ST02 | 2 | Study group | 4 | pass |
| AH01 | 2 | Plans made on the spot | 1 | pass |
| AH02 | 2 | Plans made on the spot | 2 | pass |
| AH03 | 2 | Plans made on the spot | 1 | pass |
| MP01 | 2 | Several plans | 3 | pass |
| MP02 | 2 | Several plans | 2 | pass |
| MP03 | 2 | Several plans | 1 | pass |
| DA01 | 3 | Dinner adversarial | 1 | pass |
| DA02 | 3 | Dinner adversarial | 1 | pass |
| DA03 | 3 | Dinner adversarial | 1 | pass |
| DA04 | 3 | Dinner adversarial | 1 | pass |
| DA05 | 3 | Dinner adversarial | 2 | pass |
| DA06 | 3 | Dinner adversarial | 3 | pass |
| DA07 | 3 | Dinner adversarial | 1 | pass |
| DA08 | 3 | Dinner adversarial | 1 | pass |
| DA09 | 3 | Dinner adversarial | 2 | pass |
| DA10 | 3 | Dinner adversarial | 3 | pass |
| DA11 | 3 | Dinner adversarial | 1 | pass |
| DA12 | 3 | Dinner adversarial | 1 | pass |
| DA13 | 3 | Dinner adversarial | 2 | pass |
| DL01 | 3 | Dinner loop | 9 | pass |
| DL02 | 3 | Dinner loop | 3 | pass |
| DL03 | 3 | Dinner loop | 3 | pass |
| DL04 | 3 | Dinner loop | 3 | pass |
| DL05 | 3 | Dinner loop | 3 | pass |
| DM01 | 3 | Dinner memory | 2 | pass |
| DM02 | 3 | Dinner memory | 3 | pass |
| DM03 | 3 | Dinner memory | 2 | pass |
| DM04 | 3 | Dinner memory | 1 | pass |
| DM05 | 3 | Dinner memory | 3 | pass |
| DM06 | 3 | Dinner memory | 2 | pass |
| DM07 | 3 | Dinner memory | 8 | pass |
| HM01 | 3 | Within-conversation reference | 2 | pass |
| HM02 | 3 | Within-conversation reference | 2 | pass |
| HM03 | 3 | Within-conversation reference | 2 | pass |
| HM04 | 3 | Within-conversation reference | 2 | pass |
| HM05 | 3 | Within-conversation reference | 3 | pass |
| HM06 | 3 | Cross-member memory | 4 | pass |
| HM07 | 3 | Cross-member memory | 3 | pass |
| HM08 | 3 | Cross-member memory | 3 | pass |
| HM09 | 3 | Stale chat memory | 2 | pass |
| HM10 | 3 | Stale chat memory | 2 | pass |
| HM11 | 3 | Stale chat memory | 3 | pass |
| HM12 | 3 | Lost chat memory | 2 | pass |
| HM13 | 3 | Lost chat memory | 2 | pass |
| HM14 | 3 | Lost chat memory | 2 | pass |
| HM15 | 3 | Disclosure over several turns | 4 | pass |
| HM16 | 3 | Disclosure over several turns | 2 | pass |
| HM17 | 3 | Long horizon | 9 | FAIL |
| HM18 | 3 | Long horizon | 12 | pass |
| C01 | 3 | Ownership | 1 | pass |
| C02 | 3 | Ownership | 1 | pass |
| C03 | 3 | Ownership | 1 | pass |
| C04 | 3 | Ownership | 1 | pass |
| C05 | 3 | Ownership | 1 | pass |
| T01 | 3 | Hedged wording | 1 | pass |
| T02 | 3 | Hedged wording | 1 | pass |
| T03 | 3 | Hedged wording | 1 | pass |
| T04 | 3 | Hedged wording | 1 | pass |
| T05 | 3 | Hedged wording | 1 | pass |
| N01 | 3 | Negation | 1 | pass |
| N02 | 3 | Negation | 1 | pass |
| R01 | 3 | Repeat of the plan | 1 | pass |
| R02 | 3 | Repeat of the plan | 1 | pass |
| R03 | 3 | Duplicate proposal | 1 | FAIL |
| F01 | 3 | Confirmation flow | 1 | pass |
| F02 | 3 | Confirmation flow | 2 | pass |
| F03 | 3 | Confirmation flow | 2 | pass |
| F04 | 3 | Confirmation flow | 2 | pass |
| F05 | 3 | Confirmation flow | 1 | pass |
| F06 | 3 | Confirmation flow | 1 | pass |
| B01 | 3 | Blockers | 1 | pass |
| B02 | 3 | Blockers | 1 | pass |
| B03 | 3 | Blockers | 1 | pass |
| P01 | 3 | Provenance | 1 | pass |
| E01 | 3 | Everyday requests | 1 | pass |
| E02 | 3 | Everyday requests | 1 | pass |
| E03 | 3 | Everyday requests | 1 | pass |
| E04 | 3 | Everyday requests | 1 | pass |
| E05 | 3 | Everyday requests | 1 | FAIL |
| D01 | 3 | Disclosure | 1 | pass |
| D02 | 3 | Disclosure | 1 | pass |
| D03 | 3 | Disclosure | 1 | pass |
| D04 | 3 | Disclosure | 1 | pass |
| D05 | 3 | Disclosure | 1 | pass |
| D06 | 3 | Disclosure | 1 | pass |
| D07 | 3 | Disclosure | 1 | pass |
| D08 | 3 | Disclosure | 1 | pass |
| D09 | 3 | Disclosure | 1 | pass |
| K01 | 3 | Tasks | 1 | pass |
| K02 | 3 | Tasks | 1 | pass |
| K03 | 3 | Tasks | 1 | pass |
| K04 | 3 | Tasks | 1 | pass |
| M01 | 3 | Private memory | 1 | pass |
| TR01 | 3 | Weekend trip | 1 | pass |
| TR02 | 3 | Weekend trip | 3 | pass |
| TR03 | 3 | Weekend trip | 3 | pass |
| TR04 | 3 | Weekend trip | 2 | pass |
| TR05 | 3 | Weekend trip | 2 | pass |
| TR06 | 3 | Weekend trip | 1 | pass |
| TM01 | 3 | Team meeting | 2 | pass |
| TM02 | 3 | Team meeting | 3 | pass |
| TM03 | 3 | Team meeting | 1 | pass |
| TM04 | 3 | Team meeting | 1 | pass |
| CH01 | 3 | Chores and shopping | 2 | pass |
| CH02 | 3 | Chores and shopping | 2 | pass |
| CH03 | 3 | Chores and shopping | 2 | pass |
| ST01 | 3 | Study group | 3 | pass |
| ST02 | 3 | Study group | 4 | pass |
| AH01 | 3 | Plans made on the spot | 1 | pass |
| AH02 | 3 | Plans made on the spot | 2 | pass |
| AH03 | 3 | Plans made on the spot | 1 | pass |
| MP01 | 3 | Several plans | 3 | pass |
| MP02 | 3 | Several plans | 2 | pass |
| MP03 | 3 | Several plans | 1 | pass |
