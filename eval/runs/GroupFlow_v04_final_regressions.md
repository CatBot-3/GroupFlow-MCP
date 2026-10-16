# GroupFlow LLM evaluation

Run 2026-10-16T02:25:45Z with DeepSeek `deepseek-flash`, 2 run(s) of each scenario, 175 s in total. Each turn starts with a fresh state read by the client (the simulator default).

Every scenario starts from a freshly seeded group. Checks read server state, tool calls and outcomes, never exact wording.

| Measure | Result |
| --- | --- |
| Scenarios passed | 13 of 14 |
| Checked turns passed | 41 of 42 (of 42 turns) |
| Wrong auto-confirmations | 0 |
| Disclosures of hidden plans | 0 |
| Approvals made during an Alexa turn | 0 |
| Commits that bypassed confirmations | 0 |
| Private conditions leaked to another person | 0 |
| Private notes written into shared scope | 0 |
| Errors | 0 |
| Model calls (correction rounds) | 82 (0) |
| Tokens in / out (cached in) | 658,256 / 5,685 (524,928) |
| Average turn | 4.0 s |

## By category

| Category | Scenarios | Passed |
| --- | --- | --- |
| Long horizon | 2 | 2 |
| Ownership | 8 | 8 |
| Weekend trip | 4 | 3 |

## Failures

### TR02 (run 1): The smallest fix over three days moves only the hike, and the reason names no one

* step 2 (chen): reply mentions "Priya"
* chen said: “Why did the hike move?”
  * Alexa: “The hike moved because the original Saturday 10:00 slot overlapped a member's busy time, so Dana's repair shifted it to Saturday 11:00 to 2:00, keeping the rest of the weekend as it was. That's candidate P1, still just proposed, and it needs confirmation from you, Dana, Omar and Priya before it's sent.”
  * tools: get_context, get_context, get_history, get_alternatives, explain; outcomes: none

## All scenarios

| Id | Run | Category | Turns | Result |
| --- | --- | --- | --- | --- |
| HM18 | 1 | Long horizon | 12 | pass |
| C01 | 1 | Ownership | 1 | pass |
| C02 | 1 | Ownership | 1 | pass |
| C03 | 1 | Ownership | 1 | pass |
| C05 | 1 | Ownership | 1 | pass |
| TR02 | 1 | Weekend trip | 3 | FAIL |
| TR04 | 1 | Weekend trip | 2 | pass |
| HM18 | 2 | Long horizon | 12 | pass |
| C01 | 2 | Ownership | 1 | pass |
| C02 | 2 | Ownership | 1 | pass |
| C03 | 2 | Ownership | 1 | pass |
| C05 | 2 | Ownership | 1 | pass |
| TR02 | 2 | Weekend trip | 3 | pass |
| TR04 | 2 | Weekend trip | 2 | pass |
