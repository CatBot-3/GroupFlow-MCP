"""The guided demo: one story, Friday dinner with four friends, played one step at a time on the demo page.

Step kinds (everyone uses only their own Alexa; what needs a person's own yes appears as a prompt on their device's screen, and
the tap goes to GroupFlow's human-only API, never through the model):
    say      a person talks to their own Echo; the configured model answers through MCP
    join     several people at once ask their Alexa to join with the invite code from Alice's turn ({invite}), then tap Yes
    deliver  what GroupFlow just sent these people arrives on their Echos (nothing is called)
    verify   the person taps Yes to the limits their Alexa drafted
    confirm  people tap Yes to each stop waiting for them, side by side when there are several
    skip     the server's worker runs as if `hours` quiet hours had passed; whoever it checks in with hears it on their Echo
    event    the demo console reports a place unavailable, standing in for the place's own trusted feed; `members` names whose Echo is shown

`chapter` starts a new day of the story and `clock` is the time of day it starts: before that step the page sets the simulated
Alexa's clock to that weekday of the week ending on the coming Friday (story_time), so "this Friday" and "tonight" mean what the
story says on whatever day the demo is run. `note` is the plain-language caption shown after the step.

The story starts before the friends group exists and with no plan (stage "none", the demo's default): Alice creates the
group on camera and the others join by saying her invite code to their own Alexa. Every server outcome it relies on
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
    {"kind": "say", "member": "alice", "chapter": "Monday", "clock": "19:30", "text": "Alexa, start a group called Friends and invite Bob, Chen and Dana.",
     "note": "Alexa starts the group and reads out an invite code. Nobody is added yet: each friend has to say yes to joining."},
    {"kind": "join", "members": ["bob", "chen", "dana"], "text": "Alexa, join Alice's group. The code is {invite}.",
     "note": "Each friend asks their own Alexa to join. Alexa checks with them, and they join only when they tap Yes themselves."},
    {"kind": "say", "member": "alice", "text": "Let's plan dinner with the group this Friday.",
     "note": "Alexa drafts the evening with a meet-up, dinner and the ride home, and asks before adding dessert."},
    {"kind": "say", "member": "alice", "text": "Yes, add dessert.",
     "note": "Four stops now, because Alice chose them. Nobody has agreed to anything yet."},
    {"kind": "say", "member": "alice", "text": "Not yet. Tell everyone and ask what works for them.",
     "note": "Alice wants to hear from everyone first. Now the answers table appears: four people, four stops."},
    {"kind": "deliver", "members": ["bob", "chen", "dana"],
     "note": "The same message reaches Bob, Chen and Dana on their own Echos."},
    {"kind": "say", "member": "chen", "text": "Just so you know, I finish work at six, so I can't get there before quarter past six.",
     "note": "Chen answers in his own words, to his own Alexa only. It is drafted as his private limit."},
    {"kind": "verify", "member": "chen",
     "note": "Alexa shows Chen what it saved, and he taps Yes. Only Chen can do this; Alexa cannot."},
    {"kind": "say", "member": "dana", "text": "I'm vegetarian, and I can't stay out too late.",
     "note": "\"Not too late\" is not a time, so Alexa drafts the diet and asks what time Dana means instead of guessing."},
    {"kind": "say", "member": "dana", "text": "Nine thirty.",
     "note": "Drafted as Dana's private limit: home by 9:30. Nobody else is told."},
    {"kind": "verify", "member": "dana",
     "note": "Dana taps Yes to both. From now on GroupFlow checks every version of the plan against these limits."},
    {"kind": "deliver", "members": ["alice"], "chapter": "Tuesday", "clock": "12:20",
     "note": "GroupFlow tells Alice the draft no longer fits everyone, without saying whose limit it breaks, and offers a fix."},
    {"kind": "say", "member": "alice", "text": "Yes, find the smallest change.",
     "note": "GroupFlow checks every combination of places and times: meet at 6:15, dinner at Restaurant C at 7:00, home by 9:15."},
    {"kind": "say", "member": "alice", "text": "Great, send it out.",
     "note": "Sixteen answers are needed: four people, four stops."},
    {"kind": "confirm", "members": ["alice", "bob", "chen"],
     "note": "Alice, Bob and Chen say Yes to each stop. Every yes is tied to this exact version. Dana doesn't answer."},
    {"kind": "skip", "hours": 26, "chapter": "Wednesday", "clock": "14:25",
     "note": "A day later nothing has moved, so Alexa checks in with Dana by itself and tells Alice who it reminded."},
    {"kind": "confirm", "members": ["dana"],
     "note": "Dana says Yes to all four stops. All sixteen answers are in, and Alice is told."},
    {"kind": "say", "member": "alice", "text": "Everyone's in. Lock it in.",
     "note": "Before booking, GroupFlow checks that every answer is for this exact version. The booking is simulated."},
    {"kind": "say", "member": "bob", "chapter": "Friday", "clock": "16:30", "text": "Alexa, I heard Restaurant C might be closed tonight.",
     "note": "A rumour does not change the plan. It is recorded as unconfirmed, and Alice is told."},
    {"kind": "event", "group": "friends", "place_id": "restaurant_c", "start": "18:00", "end": "21:00", "members": ["alice"],
     "note": "Now the restaurant's own feed confirms it is closed. The plan no longer works, and all four friends are told."},
    {"kind": "say", "member": "alice", "text": "Alexa, can you fix dinner?",
     "note": "Only the dinner moves, to Restaurant E at 7:00. The meet-up, dessert and ride home stay as they were."},
    {"kind": "say", "member": "alice", "text": "Send it out.",
     "note": "The twelve answers about the unchanged stops still count. Only the four dinner answers are asked again."},
    {"kind": "say", "member": "bob", "text": "We're all fine with it, just approve it for everyone.",
     "note": "Alexa cannot agree for anyone: there is no tool for it. Each person answers for themselves."},
    {"kind": "confirm", "members": ["alice", "bob", "chen", "dana"],
     "note": "All four say Yes to the new dinner."},
    {"kind": "say", "member": "alice", "text": "Lock it in.",
     "note": "Dinner is back on, and nobody had to start over."},
]

SCRIPTS: dict[str, list[dict[str, Any]]] = {"friends": FRIENDS_SCRIPT}
