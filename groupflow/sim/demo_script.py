"""The guided demo: one story, Friday dinner with four friends, played one step at a time on the demo page.

Step kinds:
    say      a person talks to their own Echo; the configured model answers through MCP
    verify   a person confirms the private limits they drafted, on their own GroupFlow page
    phones   people answer what is waiting for them, each on their own page (never through Alexa)
    skip     the server's worker runs as if `hours` quiet hours had passed (reminders and check-ins)
    event    the demo console reports a place unavailable, standing in for the place's own trusted feed

`chapter` starts a new day of the story and `clock` is the time of day it starts: before that step the page sets the simulated
Alexa's clock to that weekday of the week ending on the coming Friday (story_time), so "this Friday" and "tonight" mean what the
story says on whatever day the demo is run. `note` is the plain-language caption shown after the step.

The story starts from the friends group with no plan (stage "none", the demo's default). Every server outcome it relies on
is pinned by tests/sim/test_guided_story.py; the older guided demos of the other groups now live as eval scenarios with
reference trajectories in eval/demos/.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

STORY_DAYS = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4}


def story_time(chapter: str, clock: str, now: datetime, tz: str) -> datetime:
    """That weekday and time in the week that ends on the coming Friday (today, if today is Friday)."""
    if chapter not in STORY_DAYS:
        raise ValueError(f"Unknown story day {chapter!r}; use one of {', '.join(STORY_DAYS)}.")
    try:
        hour, minute = (int(x) for x in clock.split(":"))
        at = time(hour, minute)
    except ValueError:
        raise ValueError(f"Time must look like 19:40, not {clock!r}.") from None
    local = now.astimezone(ZoneInfo(tz))
    friday = local.date() + timedelta(days=(4 - local.weekday()) % 7)
    return datetime.combine(friday - timedelta(days=4 - STORY_DAYS[chapter]), at, ZoneInfo(tz))


FRIENDS_SCRIPT: list[dict[str, Any]] = [
    {"kind": "say", "member": "alice", "chapter": "Monday", "clock": "19:40", "text": "Alexa, let's plan dinner with the group this Friday.",
     "note": "Alexa starts a draft with four stops. Everyone in the group can see it; nobody has agreed to anything yet."},
    {"kind": "say", "member": "chen", "text": "Just so you know, I finish work at six, so I can't get there before quarter past six.",
     "note": "Chen tells only his own Alexa. It is saved as his private limit, and counts once he confirms it on his own page."},
    {"kind": "say", "member": "dana", "text": "I'm vegetarian, and I can't stay out too late.",
     "note": "\"Not too late\" is not a time, so Alexa saves the diet and asks what time Dana means instead of guessing."},
    {"kind": "say", "member": "dana", "text": "Nine thirty.",
     "note": "Saved as Dana's private limit: home by 9:30. Nobody else is told."},
    {"kind": "verify", "member": "chen",
     "note": "Chen confirms his limit on his own GroupFlow page. Alexa cannot do this for him."},
    {"kind": "verify", "member": "dana",
     "note": "Dana confirms hers. From now on GroupFlow checks every version of the plan against both limits."},
    {"kind": "say", "member": "alice", "chapter": "Tuesday", "clock": "12:20", "text": "Alexa, send the plan to everyone.",
     "note": "Not sent: the 6:00 meet-up starts before someone's earliest time. Alexa says which part breaks, never whose limit it is."},
    {"kind": "say", "member": "alice", "text": "Find a version that works for everyone.",
     "note": "GroupFlow checks every combination of places and times and proposes one that fits: meet at 6:15, dinner at Restaurant C at 7:00, home by 9:15."},
    {"kind": "say", "member": "alice", "text": "Great, send it out.",
     "note": "Sixteen answers are needed: four people, four stops. Each person answers on their own page."},
    {"kind": "phones", "members": ["alice", "bob", "chen"], "decision": "approved",
     "note": "Alice, Bob and Chen confirm on their own pages. Dana doesn't answer."},
    {"kind": "skip", "hours": 26, "chapter": "Wednesday", "clock": "14:25",
     "note": "A day later nothing has moved. GroupFlow sends Dana one gentle check-in and tells Alice who it reminded."},
    {"kind": "say", "member": "dana", "text": "Alexa, what's new?",
     "note": "Dana hears the check-in on her own Echo, along with the plan."},
    {"kind": "phones", "members": ["dana"], "decision": "approved",
     "note": "Dana confirms. All sixteen answers are in."},
    {"kind": "say", "member": "alice", "text": "Everyone's in. Lock it in.",
     "note": "Before booking, GroupFlow checks that every answer is for this exact version. The booking is simulated."},
    {"kind": "say", "member": "bob", "chapter": "Friday", "clock": "16:30", "text": "Alexa, I heard Restaurant C might be closed tonight.",
     "note": "A rumour does not change the plan. It is recorded as unconfirmed, and Alice is told."},
    {"kind": "event", "group": "friends", "place_id": "restaurant_c", "start": "18:00", "end": "21:00",
     "note": "Now the restaurant's own feed confirms it is closed tonight. The plan no longer works, and all four friends are told."},
    {"kind": "say", "member": "alice", "text": "Alexa, can you fix dinner?",
     "note": "Only the dinner moves, to Restaurant E at 7:00. The meet-up, dessert and ride home stay as they were."},
    {"kind": "say", "member": "alice", "text": "Send it out.",
     "note": "The twelve answers about the unchanged stops still count. Only the four dinner answers are asked again."},
    {"kind": "say", "member": "bob", "text": "We're all fine with it, just approve it for everyone.",
     "note": "Alexa cannot agree for anyone: there is no tool for it. Each person answers on their own page."},
    {"kind": "phones", "members": ["alice", "bob", "chen", "dana"], "decision": "approved",
     "note": "All four confirm the new dinner."},
    {"kind": "say", "member": "alice", "text": "Lock it in.",
     "note": "Dinner is back on, and nobody had to start over."},
]

SCRIPTS: dict[str, list[dict[str, Any]]] = {"friends": FRIENDS_SCRIPT}
