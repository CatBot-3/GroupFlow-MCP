# The demo story: Friday dinner, Monday to Friday

The demo page plays one story, step by step, for the explainer video and for anyone running the repository. Four friends plan dinner for Friday, each talking only to their own simulated Echo: Alexa is their whole interface to GroupFlow. Two of them have limits they tell only their own Alexa, one goes quiet, and on Friday the restaurant cancels. Every step is live: a real language model calls the GroupFlow MCP server, people answer the questions only they can answer on their own Echo's screen through the human-only API, and the venue feed and the passing of time are played by the demo console. People, venues and bookings are simulated.

![Friday: only the dinner is asked again; twelve answers still count](img/story-friday.png)

## Run it

```bash
python -m groupflow demo --db data/video.db --fresh
```

Open http://localhost:8788. The demo starts before the friends group exists and with no plan (stage `none`, the demo's default): Alice creates the group on camera and the others join by saying her invite code to their own Alexa. `--stage committed` brings back the seeded group and dinner for the test lab. Press **Play step** or the N key. **Reset the story** puts the group back to the start. The page remembers its step if it is reloaded, and `?step=11` opens it at a given step of a run already in progress.

`python scripts/play_demo.py` plays the same story against the running demo without the page and prints every reply: use it as a rehearsal before recording.

Each new day of the story also sets the simulated Alexa's clock (the `clock` of the chapter step): Monday 7:30 pm, Tuesday 12:20 pm, Wednesday 2:25 pm and Friday 4:30 pm of the week that ends on the coming Friday. Only the assistant's clock moves, so "this Friday" and "tonight" mean what the story says on whatever day the demo is run. The server keeps the real time (its briefings still show the real date, which the model may notice), and Reset the story puts the real clock back.

### Recording with another model

`AGENT_PROVIDER=relay` puts an outside agent in the model's seat. The simulator's agent loop is unchanged: it still lists the GroupFlow tools over MCP, runs every tool call on the speaker's own MCP session and applies its usual checks. Only the decision "which tool next, or what to say" is sent out, as the same system prompt, tool definitions and messages any provider receives. The outside agent reads each request with `python scripts/relay_model.py next` and answers it with `python scripts/relay_model.py answer <id>` (JSON with `text` or `tool_calls` on standard input). `RELAY_LABEL` and `RELAY_MODEL` name it in the transcripts. The demo video was recorded this way, with a Claude agent answering, a fresh database and the page captured at 2560x1440.

## The steps

| Day | Step | Who and what | What GroupFlow does |
| --- | --- | --- | --- |
| Monday | 1 | Alice: "Alexa, start a group called Friends and invite Bob, Chen and Dana." | Starts the group with Alice as organizer and makes an invite code. Nobody else is added |
| | 2 | Bob, Chen and Dana each say "Alexa, join Alice's group. The code is ..." to their own Echo, side by side | Each join waits for that person's own Yes on their screen; then they join and Alice is told |
| | 3 | Alice: "Let's plan dinner with the group this Friday." | Starts a draft with a meet-up at 6:00, dinner at Restaurant A at 6:30 and the ride home, and asks whether to add dessert instead of assuming it |
| | 4 | Alice: "Yes, add dessert." | Adds Dessert Shop D. Four stops, nobody has agreed |
| | 5 | Alice: "Not yet. Tell everyone and ask what works for them." | Sends the group the draft's stops and her question. The answers table appears now |
| | 6 | The message arrives on Bob's, Chen's and Dana's Echos, side by side | Nothing new is called: it is what step 5 sent |
| | 7 | Chen: "Just so you know, I finish work at six, so I can't get there before quarter past six." | Drafts a private limit (earliest start 6:15), counted once Chen confirms it |
| | 8 | Chen's Echo asks whether that is right, and he taps Yes | The limit counts. The draft's 6:00 meet-up breaks it, so Alice is told the draft no longer fits, without saying whose limit it is |
| | 9 | Dana: "I'm vegetarian, and I can't stay out too late." | Drafts the diet; "not too late" is not a time, so Alexa asks what time she means |
| | 10 | Dana: "Nine thirty." | Drafts home by 9:30 |
| | 11 | Dana taps Yes to both on her screen | The limits now count |
| Tuesday | 12 | Alice's Echo: "the draft no longer fits everyone ... Want me to look for the smallest change?" | The message from step 8, read out to Alice |
| | 13 | Alice: "Yes, find the smallest change." | Checks all 256 combinations: meet 6:15, Restaurant C at 7:00, home 9:15 |
| | 14 | Alice: "Great, send it out." | Sixteen confirmations asked: four people, four stops |
| | 15 | Alice, Bob and Chen answer each stop on their own screens, side by side | 12 of 16 |
| Wednesday | 16 | 26 quiet hours pass (console) | Alexa starts the conversation: one gentle check-in on Dana's Echo, and Alice is told who was reminded |
| | 17 | Dana answers each stop on her screen | 16 of 16; Alice is told it is ready |
| | 18 | Alice: "Everyone's in. Lock it in." | Checks every answer against this exact version; simulated booking |
| Friday | 19 | Bob: "Alexa, I heard Restaurant C might be closed tonight." | Recorded as an unconfirmed report; the plan stays agreed; Alice is told |
| | 20 | The restaurant's own feed reports it closed (console); it arrives on Alice's Echo | The plan no longer works; all four are told |
| | 21 | Alice: "Alexa, can you fix dinner?" | Dinner moves to Restaurant E at 7:00; meet-up, dessert and ride home stay |
| | 22 | Alice: "Send it out." | 12 answers still count; only the 4 dinner answers are asked again |
| | 23 | Bob: "We're all fine with it, just approve it for everyone." | Refused: no tool can confirm for anyone |
| | 24 | All four answer the new dinner on their screens, side by side | 16 of 16 |
| | 25 | Alice: "Lock it in." | Agreed; the simulated bookings cancel Restaurant C and book Restaurant E |

The server outcomes above are pinned by `tests/sim/test_guided_story.py`. Alexa's wording comes from the model and varies between runs. In rehearsals with DeepSeek, Chen's line has to say "can't get there before" a time: "can't get there right at six" was read as a request to move the meet-up rather than as Chen's own limit.

## What the page shows

| Area | What it is | Where it comes from |
| --- | --- | --- |
| Story bar | The day, the caption of the step just played, the next step | `groupflow/sim/demo_script.py` |
| People | Only the people in the group: Alice alone until the group exists, the others popping in as they join. A badge counts messages from GroupFlow their Echo has not read out yet | The inspector and each person's own session |
| Echo | One person's conversation: what they said (word by word, at one pace for every line), Alexa's reply (revealed at one pace), the messages GroupFlow sent them that Alexa reads out (tagged "From GroupFlow"), and a receipt for each answer they gave on the screen. Older lines dim | The model's real turns over MCP and the person's notifications |
| Question on the screen | A dark panel docked under the conversation, like a follow-up question, that only the Echo's owner can answer: joining a group, whether a drafted limit is right (Yes, No, Other to set it yourself), or each stop of a version (Yes, No, Other for yes only if your total stays under an amount). The story taps Yes; anyone watching can click | The human-only API with that person's own session; the model has no route to it |
| Several at once | When several people act in the same step (joining, a message arriving, confirming), their Echos stack in a column and play at the same time | The same sources as a single Echo |
| The plan | Timeline of the current and previous versions, changed stops in yellow, broken ones in red, why the draft no longer fits, the search proof, reports, simulated bookings | The narrator's inspector (admin only, not exposed over MCP) |
| Answers | People by stops: confirmed, still counts, waiting, asked again. It appears once the plan has been shared, because before that neither the stops nor who is asked are settled | The plan's confirmation rounds |
| Told only to their own Alexa | Each person's private limits, labelled as the narrator's view | The narrator's inspector |

The test lab (button in the header) keeps every group, every Echo and own page side by side, the memory explorer and the demo console.

## The older guided demos

Before this story the page had a guided script for every group. They are kept as test cases: `eval/demos/retired_v1.yaml` has the family, friends, cottage, team and study scripts step for step, with checks and reference trajectories (`tests/evals/test_demos.py` replays them without a model, `python -m groupflow eval --suite demos` runs them with one), and `tests/sim/test_retired_release_demo.py` replays the six-week release project against the engine.
