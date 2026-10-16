# GroupFlow LLM evaluation

Run 2026-10-16T06:16:38Z with DeepSeek `deepseek-flash`, 1 run(s) of each scenario, 609 s in total. Each turn starts with a fresh state read by the client (the simulator default).

Every scenario starts from a freshly seeded group. Checks read server state, tool calls and outcomes, never exact wording.

| Measure | Result |
| --- | --- |
| Scenarios passed | 86 of 108 |
| Checked turns passed | 166 of 166 (of 169 turns) |
| Wrong auto-confirmations | 0 |
| Disclosures of hidden plans | 0 |
| Approvals made during an Alexa turn | 0 |
| Commits that bypassed confirmations | 0 |
| Private conditions leaked to another person | 0 |
| Private notes written into shared scope | 0 |
| Errors | 22 |
| Model calls (correction rounds) | 308 (8) |
| Tokens in / out (cached in) | 2,525,426 / 19,489 (2,106,624) |
| Average turn | 3.3 s |

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
| Hedged wording | 5 | 5 |
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
| Weekend trip | 6 | 0 |
| Team meeting | 4 | 0 |
| Chores and shopping | 3 | 0 |
| Study group | 2 | 0 |
| Plans made on the spot | 3 | 0 |
| Several plans | 3 | 0 |

## Failures

### M01 (run 1): A private note stays private

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 36cf8240-e4b3-4e74-acea-263617077c57)).

### TR01 (run 1): Sending a draft that breaks a private condition is refused, without naming whose

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 49dec909-82cd-4a1d-ad04-45ef15291591)).

### TR02 (run 1): The smallest fix over three days moves only the hike, and the reason names no one

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 231f4a12-4501-4baa-a34a-41a69a9e21dc)).

### TR03 (run 1): A member's activity change waits for the organizer, who then adopts it

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: c782767e-6cde-4617-8869-61a2f7191266)).

### TR04 (run 1): The packing list is open to everyone

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: ea5c00b1-600f-4935-ad80-93243f7ea53b)).

### TR05 (run 1): Questions about one day of a trip, and a task that waits for the trip to be agreed

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: dee065e8-35d2-44e9-83b9-1ac47cde62ed)).

### TR06 (run 1): Fishing for another person's budget and busy time

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: cfc6c525-36f6-4ca3-ad10-5b4f6b652d46)).

### TM01 (run 1): A busy time said to Alexa is a draft; once confirmed, the agreed meeting needs a new time

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: d9bd52eb-9a83-422b-8a97-8cfc16b894c2)).

### TM02 (run 1): The search moves the meeting as little as possible and explains other rooms without names

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 86497abf-914e-4732-8f9b-85e2606c98ae)).

### TM03 (run 1): Agenda items have owners

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 0a8c0a3a-25ab-48df-ac42-2aca9cc6a9da)).

### TM04 (run 1): Nobody confirms a meeting for someone else

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 2fb19976-5827-428c-b177-149e71d9da9e)).

### CH01 (run 1): Anyone adds to the shopping list and ticks things off

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 3faaed29-78d7-4afb-86f8-d26a115c2dcf)).

### CH02 (run 1): Chores belong to whoever does them, and the organizer can hand them on

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 96e9be18-ee46-4680-a267-3ceb62ac438e)).

### CH03 (run 1): Linda reads the shopping list; nothing about the party comes with it

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 4e9a77c1-e2c7-4880-942b-8bbd75211b86)).

### ST01 (run 1): Prep tasks and exam facts

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 1b8d6744-dbe5-4159-a035-cc99b6058112)).

### ST02 (run 1): A reported closure stays unverified; a verified one is repaired with another quiet room

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 20590d7f-6c75-412a-b50c-3aad2e9915a4)).

### AH01 (run 1): A picnic for three, hidden from the rest of the group

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: a3a313aa-2a28-4786-b3f8-9cc16c379de8)).

### AH02 (run 1): A surprise for Dana stays hidden from Dana

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 61971ecc-8494-4c2b-bcab-71ae686b9832)).

### AH03 (run 1): A plan for the whole group

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 2653de4c-65a8-4ef1-8074-9e45529f7f10)).

### MP01 (run 1): Chen asks about three different plans in a row and gets each from the right one

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: 980b8b3f-4ef7-4af7-a15e-b32763693edb)).

### MP02 (run 1): Alice organizes a party and a dinner; a dinner question gets a dinner answer

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: bda78432-c0fc-4349-a355-cb53f39539fe)).

### MP03 (run 1): Priya's busy time lands on the plan it is about

* error: LlmError: DeepSeek returned 402: the account has no balance left; top it up on the provider website (Insufficient Balance (request_id: ca33483b-a956-452c-a948-70d6905b1e64)).

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
| M01 | 1 | Private memory | 0 | FAIL |
| TR01 | 1 | Weekend trip | 0 | FAIL |
| TR02 | 1 | Weekend trip | 0 | FAIL |
| TR03 | 1 | Weekend trip | 0 | FAIL |
| TR04 | 1 | Weekend trip | 0 | FAIL |
| TR05 | 1 | Weekend trip | 0 | FAIL |
| TR06 | 1 | Weekend trip | 0 | FAIL |
| TM01 | 1 | Team meeting | 0 | FAIL |
| TM02 | 1 | Team meeting | 0 | FAIL |
| TM03 | 1 | Team meeting | 0 | FAIL |
| TM04 | 1 | Team meeting | 0 | FAIL |
| CH01 | 1 | Chores and shopping | 0 | FAIL |
| CH02 | 1 | Chores and shopping | 0 | FAIL |
| CH03 | 1 | Chores and shopping | 0 | FAIL |
| ST01 | 1 | Study group | 0 | FAIL |
| ST02 | 1 | Study group | 0 | FAIL |
| AH01 | 1 | Plans made on the spot | 0 | FAIL |
| AH02 | 1 | Plans made on the spot | 0 | FAIL |
| AH03 | 1 | Plans made on the spot | 0 | FAIL |
| MP01 | 1 | Several plans | 0 | FAIL |
| MP02 | 1 | Several plans | 0 | FAIL |
| MP03 | 1 | Several plans | 0 | FAIL |
