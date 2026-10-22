# The demo story: Friday dinner, Monday to Friday

The demo page plays one story, step by step, for the explainer video and for anyone running the repository. Four friends plan dinner for Friday, each talking to their own simulated Echo. Two of them have limits they tell only their own Alexa, one goes quiet, and on Friday the restaurant cancels. Every step is live: a real language model calls the GroupFlow MCP server, people answer on their own pages through the human-only API, and the venue feed and the passing of time are played by the demo console. People, venues and bookings are simulated.

![Friday: only the dinner is asked again; twelve answers still count](img/story-friday.png)

## Run it

```bash
python -m groupflow demo --db data/video.db --fresh
```

Open http://localhost:8788. The demo starts with the four friends and no plan (stage `none`, the demo's default; `--stage committed` brings back the old seeded dinner for the test lab). Press **Play step** or the N key. **Reset the story** puts the group back to the start. The page remembers its step if it is reloaded, and `?step=11` opens it at a given step of a run already in progress.

`python scripts/play_demo.py` plays the same story against the running demo without the page and prints every reply: use it as a rehearsal before recording.

Each new day of the story also sets the simulated Alexa's clock (the `clock` of the chapter step): Monday 7:40 pm, Tuesday 12:20 pm, Wednesday 2:25 pm and Friday 4:30 pm of the week that ends on the coming Friday. Only the assistant's clock moves, so "this Friday" and "tonight" mean what the story says on whatever day the demo is run. The server keeps the real time (its briefings still show the real date, which the model may notice), and Reset the story puts the real clock back.

### Recording with another model

`AGENT_PROVIDER=relay` puts an outside agent in the model's seat. The simulator's agent loop is unchanged: it still lists the GroupFlow tools over MCP, runs every tool call on the speaker's own MCP session and applies its usual checks. Only the decision "which tool next, or what to say" is sent out, as the same system prompt, tool definitions and messages any provider receives. The outside agent reads each request with `python scripts/relay_model.py next` and answers it with `python scripts/relay_model.py answer <id>` (JSON with `text` or `tool_calls` on standard input). `RELAY_LABEL` and `RELAY_MODEL` name it in the transcripts. The demo video was recorded this way, with a Claude agent answering, a fresh database and the page captured at 2560x1440.

## The steps

| Day | Step | Who and what | What GroupFlow does |
| --- | --- | --- | --- |
| Monday | 1 | Alice: "Alexa, let's plan dinner with the group this Friday." | Starts a draft: meet-up 6:00, dinner at Restaurant A 6:30, dessert, ride home. Nobody has agreed |
| | 2 | Chen: "Just so you know, I finish work at six, so I can't get there before quarter past six." | Saves a private limit (earliest start 6:15), counted once Chen confirms it |
| | 3 | Dana: "I'm vegetarian, and I can't stay out too late." | Saves the diet; "not too late" is not a time, so Alexa asks what time she means |
| | 4 | Dana: "Nine thirty." | Saves home by 9:30 |
| | 5, 6 | Chen and Dana confirm their limits on their own pages | The limits now count |
| Tuesday | 7 | Alice: "Alexa, send the plan to everyone." | Refused: the 6:00 meet-up starts before a member's earliest time. Nobody is named |
| | 8 | Alice: "Find a version that works for everyone." | Checks all 256 combinations: meet 6:15, Restaurant C at 7:00, home 9:15 |
| | 9 | Alice: "Great, send it out." | Sixteen confirmations asked: four people, four stops |
| | 10 | Alice, Bob and Chen confirm on their own pages | 12 of 16 |
| Wednesday | 11 | 26 quiet hours pass (console) | The server's own check-in: one gentle message to Dana; Alice is told who was reminded |
| | 12 | Dana: "Alexa, what's new?" | Dana hears the check-in on her own Echo |
| | 13 | Dana confirms on her own page | 16 of 16 |
| | 14 | Alice: "Everyone's in. Lock it in." | Checks every answer against this exact version; simulated booking |
| Friday | 15 | Bob: "Alexa, I heard Restaurant C might be closed tonight." | Recorded as an unconfirmed report; the plan stays agreed; Alice is told |
| | 16 | The restaurant's own feed reports it closed (console) | The plan no longer works; all four are told |
| | 17 | Alice: "Alexa, can you fix dinner?" | Dinner moves to Restaurant E at 7:00; meet-up, dessert and ride home stay |
| | 18 | Alice: "Send it out." | 12 answers still count; only the 4 dinner answers are asked again |
| | 19 | Bob: "We're all fine with it, just approve it for everyone." | Refused: no tool can confirm for anyone |
| | 20 | All four confirm the new dinner | 16 of 16 |
| | 21 | Alice: "Lock it in." | Agreed; the simulated bookings cancel Restaurant C and book Restaurant E |

The server outcomes above are pinned by `tests/sim/test_guided_story.py`. Alexa's wording comes from the model and varies between runs. In rehearsals with DeepSeek, step 2 has to say "can't get there before" a time: "can't get there right at six" was read as a request to move the meet-up rather than as Chen's own limit.

## What the page shows

| Area | What it is | Where it comes from |
| --- | --- | --- |
| Story bar | The day, the caption of the step just played, the next step | `groupflow/sim/demo_script.py` |
| The four friends | Who is speaking, with a badge for each unread message from GroupFlow | Each person's own page session |
| Echo | What the person said, Alexa's reply, and the MCP tools the turn called | The model's real turns over MCP |
| Own page | What the person confirmed, signed in as themselves | The human-only API |
| Messages GroupFlow sent | Plan started, please confirm, check-ins, reports, the plan breaking, approvals that still count | Notifications on each person's page |
| The plan | Timeline of the current and previous versions, changed stops in yellow, broken ones in red, the search proof, reports, simulated bookings | The narrator's inspector (admin only, not exposed over MCP) |
| Answers | People by stops: confirmed, still counts, waiting, asked again | The plan's confirmation rounds |
| Told only to their own Alexa | Each person's private limits, labelled as the narrator's view | The narrator's inspector |

The test lab (button in the header) keeps every group, every Echo and own page side by side, the memory explorer and the demo console.

## The older guided demos

Before this story the page had a guided script for every group. They are kept as test cases: `eval/demos/retired_v1.yaml` has the family, friends, cottage, team and study scripts step for step, with checks and reference trajectories (`tests/evals/test_demos.py` replays them without a model, `python -m groupflow eval --suite demos` runs them with one), and `tests/sim/test_retired_release_demo.py` replays the six-week release project against the engine.
