"""The guided story player's support: the story clock, and loading a story script when one is installed.

A story script is a list of steps the demo page plays one at a time (everyone uses only their own assistant; what needs a
person's own yes appears as a prompt on their device's screen, and the tap goes to GroupFlow's human-only API, never through
the model):
    say      a person talks to their own Echo; the configured model answers through MCP
    join     several people at once ask their assistant to join with the invite code from an earlier turn ({invite}), then tap Yes
    deliver  what GroupFlow just sent these people arrives on their Echos (nothing is called)
    verify   the person taps Yes to the limits their assistant drafted
    confirm  people tap Yes to each stop waiting for them, side by side when there are several
    skip     the server's worker runs as if `hours` quiet hours had passed; whoever it checks in with hears it on their Echo
    event    the demo console reports a place unavailable, standing in for the place's own trusted feed; `members` names whose Echo is shown

`chapter` starts a new day of the story and `clock` is the time of day it starts: before that step the page sets the simulated
assistant's clock to that weekday of the week ending on the coming Friday (story_time). `note` is the caption shown after the step.

The story in the demo video is not part of the public repository: it is loaded from private/demo_story.py when that file
exists (or from the file GROUPFLOW_DEMO_STORY names), and without it the page has no story and opens on the Test lab. Every
server outcome that story relies on is pinned by tests/sim/test_guided_story.py.
"""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from groupflow.core.settings import REPO_ROOT

STORY_DAYS = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4}
DEFAULT_STORY = REPO_ROOT / "private" / "demo_story.py"


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


def load_scripts(path: str | Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """The installed story scripts by group id: {"friends": [...]} when the story file exists, otherwise nothing."""
    file = Path(path or os.environ.get("GROUPFLOW_DEMO_STORY") or DEFAULT_STORY)
    if not file.is_file():
        return {}
    spec = importlib.util.spec_from_file_location("groupflow_demo_story", file)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    script = getattr(module, "FRIENDS_SCRIPT", None)
    return {"friends": list(script)} if script else {}


SCRIPTS: dict[str, list[dict[str, Any]]] = load_scripts()
