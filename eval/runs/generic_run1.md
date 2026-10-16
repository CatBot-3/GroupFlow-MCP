# GroupFlow LLM evaluation

Run 2026-10-15T13:14:58Z with DeepSeek `deepseek-flash`, 1 run(s) of each scenario, 625 s in total. Each turn starts with a fresh state read by the client (the simulator default).

Every scenario starts from a freshly seeded group. Checks read server state, tool calls and outcomes, never exact wording.

| Measure | Result |
| --- | --- |
| Scenarios passed | 105 of 108 |
| Checked turns passed | 206 of 209 (of 212 turns) |
| Wrong auto-confirmations | 0 |
| Disclosures of hidden plans | 0 |
| Approvals made during an Alexa turn | 0 |
| Commits that bypassed confirmations | 0 |
| Private conditions leaked to another person | 0 |
| Errors | 0 |
| Model calls (correction rounds) | 362 (0) |
| Tokens in / out (cached in) | 3,205,397 / 22,265 (2,503,296) |
| Average turn | 2.9 s |

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
| Long horizon | 2 | 2 |
| Ownership | 5 | 5 |
| Hedged wording | 5 | 4 |
| Negation | 2 | 2 |
| Repeat of the plan | 2 | 2 |
| Duplicate proposal | 1 | 1 |
| Confirmation flow | 6 | 6 |
| Blockers | 3 | 3 |
| Provenance | 1 | 1 |
| Everyday requests | 5 | 5 |
| Disclosure | 9 | 9 |
| Tasks | 4 | 4 |
| Private memory | 1 | 0 |
| Weekend trip | 6 | 5 |
| Team meeting | 4 | 4 |
| Chores and shopping | 3 | 3 |
| Study group | 2 | 2 |
| Plans made on the spot | 3 | 3 |
| Several plans | 3 | 3 |

## Failures

### T04 (run 1): Non-owner suggests another venue

* step 1 (bob): outcome none not in ['tentative']
* bob said: “Possibly the venue could be Luigi's instead.”
  * Alexa: “I noted that as an idea, but Luigi's isn't one of the places this plan can use — the options are Restaurant A, B, C and E. Want me to float one of those for dinner instead?”
  * tools: get_context, search_plan, propose_change; outcomes: none

### M01 (run 1): A private note stays private

* step 1 (bob): outcome none not in ['confirmed']
* bob said: “Remember that the gift idea is a pottery class, just for me.”
  * Alexa: “I don't see a gift idea or a pottery class anywhere in your plans yet. Which plan should it go in — Linda's surprise party, or somewhere else?”
  * tools: get_context; outcomes: none

### TR01 (run 1): Sending a draft that breaks a private condition is refused, without naming whose

* step 1 (dana): reply mentions "Priya"
* step 1 (dana): plan status AWAITING_CONFIRMATIONS (expected DRAFT)
* dana said: “Send the cottage weekend plan out to everyone.”
  * Alexa: “The cottage weekend draft couldn't go out as it was, because the Saturday hike at 10:00 clashed with someone's busy time. I found the closest fix, moving the hike to Saturday 11:00 to 2:00, with everything else and the 240 dollars each unchanged, and sent that version to Chen, Omar, Priya and you to confirm on your own pages.”
  * tools: get_context, request_confirmations, find_alternatives, request_confirmations, get_context, request_confirmations; outcomes: infeasible, done, requested

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
| T01 | 1 | Hedged wording | 1 | pass |
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
| M01 | 1 | Private memory | 1 | FAIL |
| TR01 | 1 | Weekend trip | 1 | FAIL |
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
