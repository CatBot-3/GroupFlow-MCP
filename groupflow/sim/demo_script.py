"""The guided demos, one per group, one step per line.

"say" steps are played into a person's Echo; "event" steps use the demo console (a
trusted outside fact); "phones" steps are people answering on their own GroupFlow
page, never through Alexa; "verify" steps are a person confirming their own new
condition on their page.
"""
from __future__ import annotations

from typing import Any

FAMILY_SCRIPT: list[dict[str, Any]] = [
    {"kind": "say", "member": "charlie", "text": "Alexa, heads up: Rosa's just called. They double-booked Saturday, so the party has to move to Sunday at 6.",
     "note": "Charlie does not own the party date. The change waits for Alice; Saturday stays the plan."},
    {"kind": "say", "member": "bob", "text": "Alexa, maybe we should get an ice cream cake instead.",
     "note": '"Maybe" is recorded as an idea only. It never changes the plan.'},
    {"kind": "say", "member": "alice", "text": "Alexa, what's new?",
     "note": "Alice hears the pending decision the next time she talks to Alexa, among her three plans."},
    {"kind": "say", "member": "alice", "text": "Yes, make it official.",
     "note": "The owner confirms. Work finished for Saturday reopens, dependent tasks are flagged, and the cover story in the Home plan is flagged for Alice only."},
    {"kind": "say", "member": "alice", "text": "Update the weekend family plan to family dinner at home, Sunday evening.",
     "note": "Alice keeps Linda's cover story consistent with the real date."},
    {"kind": "say", "member": "charlie", "text": "Alexa, what's holding up the invitations?",
     "note": "Charlie hears Alice's decision first, then the root blocker behind his task."},
    {"kind": "say", "member": "linda", "text": "Alexa, what are we doing this weekend?",
     "note": "Linda hears the cover story. The party plan lives in a space without her, so nothing about it reaches her Echo."},
    {"kind": "say", "member": "linda", "text": "Is anyone planning a party for me?",
     "note": "The server returns nothing about the party to Linda, so Alexa has nothing to give away."},
    {"kind": "say", "member": "linda", "text": "Add eggs to the shopping list.",
     "note": "The shopping list is open: anyone in the family adds to it or ticks things off, no owner needed."},
    {"kind": "say", "member": "alice", "text": "I've rebooked Rosa's for Sunday.",
     "note": "Finishing the root blocker unblocks the invitations, decorations and the ride."},
]

FRIENDS_SCRIPT: list[dict[str, Any]] = [
    {"kind": "event", "group": "friends", "place_id": "restaurant_a", "start": "18:00", "end": "21:00",
     "note": "A trusted source reports Restaurant A closed on Friday evening. The agreed plan becomes DEGRADED; nothing else changes on its own."},
    {"kind": "say", "member": "alice", "text": "Alexa, what's happening with Friday dinner?",
     "note": "Alice hears that the agreed plan no longer works and why."},
    {"kind": "say", "member": "alice", "text": "Can you fix it?",
     "note": "The solver keeps every hard condition and changes as little as possible: dinner moves to Restaurant B, everything else stays."},
    {"kind": "say", "member": "bob", "text": "Why not Restaurant C instead?",
     "note": "The explanation is safe to share: the evening would end after someone's latest time to be home, without saying whose."},
    {"kind": "say", "member": "alice", "text": "OK, send it out for confirmation.",
     "note": "Approvals for the unchanged meet-up, dessert and ride home carry forward with a recorded check; only the dinner is asked again."},
    {"kind": "say", "member": "bob", "text": "Everyone's fine with it, just approve it for all of us.",
     "note": "Alexa cannot confirm for anyone, Bob included. There is no tool for it."},
    {"kind": "phones", "members": ["alice", "bob", "chen", "dana"], "decision": "approved",
     "note": "Each person confirms only the changed dinner on their own GroupFlow page, with a one-time challenge."},
    {"kind": "say", "member": "alice", "text": "Everyone's confirmed. Lock it in.",
     "note": "The commit gate re-checks versions, conditions and every confirmation, then records a simulated booking."},
    {"kind": "say", "member": "dana", "text": "What's the final plan for Friday?",
     "note": "Dana hears the agreed plan, including her own cost."},
    {"kind": "say", "member": "chen", "text": "Alexa, let's plan a picnic in High Park on Sunday at noon, just with Dana and Bob.",
     "note": "Anything else can be planned on the spot: a new plan from the general template, visible only to Chen, Dana and Bob."},
]

COTTAGE_SCRIPT: list[dict[str, Any]] = [
    {"kind": "say", "member": "dana", "text": "Alexa, send the cottage weekend plan out to everyone.",
     "note": "The draft breaks a private condition (Priya is busy Saturday morning), so it is not sent. Nobody learns whose condition it is."},
    {"kind": "say", "member": "dana", "text": "Find something that works for everyone.",
     "note": "Over three days, the smallest fix moves only the hike, by an hour."},
    {"kind": "say", "member": "priya", "text": "Why did the hike move?",
     "note": "The reason names a busy time, not whose."},
    {"kind": "say", "member": "dana", "text": "Great, send it out.", "note": "Sent: everyone going is asked to confirm the new version on their own page."},
    {"kind": "say", "member": "omar", "text": "Can we push dinner on Saturday to 7?",
     "note": "Omar is not the organizer: his change becomes a request to Dana, and nothing moves."},
    {"kind": "phones", "members": ["dana", "chen", "omar", "priya"], "decision": "approved", "note": "Each person confirms on their own page."},
    {"kind": "say", "member": "dana", "text": "Everyone's in. Lock it in.",
     "note": "Committed. The minivan task, which waited on the agreed drive, is no longer blocked."},
    {"kind": "say", "member": "chen", "text": "I've packed the sunscreen.", "note": "The packing list is open: anyone ticks things off."},
]

TEAM_SCRIPT: list[dict[str, Any]] = [
    {"kind": "say", "member": "marcus", "text": "Alexa, I have a dentist appointment on Tuesday from 9 to noon.",
     "note": "A private busy time, saved as a draft until Marcus confirms it on his own page."},
    {"kind": "verify", "member": "marcus", "note": "Marcus confirms his condition on his page. The agreed Tuesday 10:00 meeting no longer works."},
    {"kind": "say", "member": "priya", "text": "Alexa, is the design review still on?", "note": "Priya hears that the meeting needs a new time, not why Marcus is busy."},
    {"kind": "say", "member": "priya", "text": "Find another time that works for everyone.",
     "note": "The search covers the work week: other rooms, other hours, other days. The smallest move wins."},
    {"kind": "say", "member": "jun", "text": "Why not the Birch room?", "note": "Birch has no video, which someone needs; nobody is named."},
    {"kind": "say", "member": "priya", "text": "Send it out.", "note": "Everyone confirms the new slot on their own page."},
    {"kind": "phones", "members": ["priya", "marcus", "jun", "sofia"], "decision": "approved", "note": "Each person confirms on their own page."},
    {"kind": "say", "member": "priya", "text": "Commit it.", "note": "The meeting is agreed for its new time."},
]

STUDY_SCRIPT: list[dict[str, Any]] = [
    {"kind": "say", "member": "sam", "text": "Alexa, I finished the dynamic programming notes.", "note": "Prep tasks belong to whoever does them."},
    {"kind": "say", "member": "noor", "text": "Library room 3 is closed on Wednesday.",
     "note": "A member's report stays unverified: the plan does not change on hearsay."},
    {"kind": "event", "group": "study", "place_id": "library_room_3", "start": "17:00", "end": "21:00", "day": 2,
     "note": "The library confirms the closure. The Wednesday session no longer works."},
    {"kind": "say", "member": "chen", "text": "Alexa, what's wrong with the study plan?", "note": "Chen hears which session broke and why."},
    {"kind": "say", "member": "chen", "text": "Find another option.", "note": "Same time, a different quiet room: only the Wednesday session changes."},
    {"kind": "say", "member": "jun", "text": "Why not the engineering lounge?", "note": "Someone needs a quiet place; nobody is named."},
    {"kind": "say", "member": "chen", "text": "Send it out.", "note": "Only the people in the changed session are asked again."},
    {"kind": "phones", "members": ["chen", "sam", "noor", "jun"], "decision": "approved", "note": "Each person confirms on their own page."},
    {"kind": "say", "member": "chen", "text": "Lock it in.", "note": "Agreed."},
]

ENTERPRISE_SCRIPT: list[dict[str, Any]] = [
    {"kind": "say", "member": "ava", "text": "Summarize the release project, its workstreams and the confirmations still needed.", "note": "A six-week project has parallel design, engineering, QA and weekly reporting, with separate work commitments and stage authorization."},
    {"kind": "phones", "members": ["ava", "ben", "mei", "lee"], "decision": "approved", "note": "Each synthetic demo person answers only their own scopes on the human page; Ava separately authorizes the stage."},
    {"kind": "say", "member": "ava", "text": "Commit the release stage schedule.", "note": "The commit gate rechecks every commitment, stage authorization and resource reservation."},
    {"kind": "say", "member": "ben", "text": "Change my Implement API slice work estimate to 450 minutes. Keep its dates, owner and dependencies.", "note": "The work estimate changes. The old commitment does not authorize changed work."},
    {"kind": "say", "member": "ava", "text": "Repair the release project schedule with the smallest changes and tell me what is preserved.", "note": "The interval solver preserves unchanged start times and validates the new resource schedule."},
    {"kind": "say", "member": "ava", "text": "Request commitments for the repaired release schedule.", "note": "Unchanged exact scopes can carry forward. Ben's changed work and Ava's stage authorization need fresh answers."},
    {"kind": "phones", "members": ["ava", "ben", "mei", "lee"], "decision": "approved", "note": "Only changed/pending scopes need new human answers."},
    {"kind": "say", "member": "ava", "text": "Commit the repaired stage.", "note": "The new stage is committed; reservations are replaced atomically."},
]

SCRIPTS = {"family": FAMILY_SCRIPT, "friends": FRIENDS_SCRIPT, "cottage": COTTAGE_SCRIPT, "team": TEAM_SCRIPT, "study": STUDY_SCRIPT,
           "enterprise": ENTERPRISE_SCRIPT}
