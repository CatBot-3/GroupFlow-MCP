# The demo story: Friday dinner, Monday to Friday

This is the story in the demo video. Four friends plan dinner for Friday, each talking only to their own simulated Echo: Alexa is their whole interface to GroupFlow. Two of them have limits they tell only their own Alexa, one goes quiet, and on Friday the restaurant cancels. Every step is live: a real language model calls the GroupFlow MCP server, people answer the questions only they can answer on their own Echo's screen through the human-only API, and the venue's notice and the passing of time are played by the demo console. People, venues and bookings are simulated.

![Friday: only the dinner is asked again; twelve answers still count](img/story-friday.png)

## Replay it

To try GroupFlow freely, use the sandbox described in the [README](../README.md#run-it). To replay this exact story instead:

```bash
python -m groupflow demo --db data/story.db --fresh
```

Open http://localhost:8788 and press **Play step 1** (or the N key) to play one step at a time. The story starts before the friends group exists and with no plan: Alice creates the group and the others join by saying her invite code to their own Alexa. **Reset the story** puts everything back to the start, the page keeps its step if it is reloaded, and `?step=11` opens it at a given step of a run already in progress. `python scripts/play_demo.py` plays the same story against the running demo without the page and prints every reply.

Each new day of the story sets the simulated Alexa's clock: Monday 7:30 pm, Tuesday 12:20 pm, Wednesday 2:25 pm and Friday 4:30 pm of the week that ends on the coming Friday. Only the assistant's clock moves, so "this Friday" and "tonight" mean what the story says on whatever day it is played. The server keeps the real time, and Reset the story puts the real clock back.

Every server outcome in the story is checked by `tests/sim/test_guided_story.py`. Alexa's wording comes from the model and varies between runs. In rehearsals with DeepSeek, Chen's line had to say "can't get there before" a time: "can't get there right at six" was read as a request to move the meet-up rather than as Chen's own limit.

## The older guided demos

Before this story the page had a guided script for every group of the demo world. They are kept as tests: `eval/demos/retired_v1.yaml` has the family, friends, cottage, team and study scripts step by step, with checks and reference answers (`tests/evals/test_demos.py` replays them without a model, `python -m groupflow eval --suite demos` runs them with one), and `tests/sim/test_retired_release_demo.py` replays a six-week project demo against the engine.
