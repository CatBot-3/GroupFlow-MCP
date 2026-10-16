"""Plan templates: everything domain-specific about a kind of plan.

GroupFlow's engine knows three shapes of item and three consent policies; a
template says which item types a kind of plan has, which shape and policy
each one uses, which personal conditions people may set, which places the
plans can use (the catalog), how far a repair may move things, and which
items a new plan starts with. Adding a kind of plan means adding a YAML file
here; no code changes.

Shapes:
  detail    one agreed value (the party date, the cake, the guest count)
  task      something one person does, with a status and an optional due date
  activity  something people attend together: when, where, who, what it costs

Policies:
  owner         the item's owner decides; anyone else's change waits for them
                (the owner may decide by voice, through Alexa)
  participants  every person taking part must confirm on their own page before
                a change counts; the organizer then commits it
  open          anyone who can see the plan may change it directly
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

HERE = Path(__file__).resolve().parent

Shape = Literal["detail", "task", "activity"]
Policy = Literal["owner", "participants", "open"]
ValueKind = Literal["datetime", "place", "count", "text"]
Check = Literal["latest_end", "earliest_start", "max_total_cost", "needs", "unavailable_window", "must_attend", "prefer_keep"]

CHECK_HINTS: dict[str, str] = {
    "latest_end": '{"time": "21:00"} — nothing they attend may end after this time of day',
    "earliest_start": '{"time": "09:00"} — nothing they attend may start before this time of day',
    "max_total_cost": '{"amount": 45} — their total share, in the plan currency',
    "needs": '{"tags": ["vegetarian"]} — places must have these tags',
    "unavailable_window": '{"day": "Tue", "from": "13:00", "to": "15:00"} — they are busy then (day optional)',
    "must_attend": '{"activity": "dinner"} — everyone must attend this activity',
    "prefer_keep": '{"activity": "dessert"} — a soft wish to keep this activity unchanged',
}


class ItemType(BaseModel):
    name: str
    label: str
    shape: Shape
    policy: Policy
    value_kind: ValueKind = "text"
    place_kinds: list[str] = Field(default_factory=list, description="Activities: catalog place kinds this activity can use.")
    default_minutes: int = 60
    description: str = ""
    intent_terms: list[str] = Field(default_factory=list, description="Explicit list phrases that select this item type, e.g. packing list.")

    @model_validator(mode="after")
    def _policy_fits_shape(self) -> "ItemType":
        if self.shape == "activity" and self.policy != "participants":
            raise ValueError(f"item type {self.name}: activities use the participants policy")
        if self.shape != "activity" and self.policy == "participants":
            raise ValueError(f"item type {self.name}: only activities use the participants policy")
        return self


class ConditionType(BaseModel):
    name: str = Field(description="What people and Alexa call it, e.g. home_by or diet.")
    check: Check
    label: str
    applies_to: list[str] = Field(default_factory=list, description="needs: place kinds the tags apply to; empty means every place.")
    tags: list[str] = Field(default_factory=list, description="needs: tags the catalog knows, e.g. vegetarian.")
    soft: bool = False

    @property
    def hint(self) -> str:
        return CHECK_HINTS[self.check]


class Place(BaseModel):
    id: str
    name: str
    kind: str
    zone: str = "local"
    open: str = "00:00"
    close: str = "23:59"
    capacity: int = 99
    price: float = 0.0
    tags: list[str] = Field(default_factory=list)
    start_slots: list[str] | None = None
    aliases: list[str] = Field(default_factory=list)

    @property
    def price_minor(self) -> int:
        return int(round(self.price * 100))


class Catalog(BaseModel):
    version: str = "v1"
    places: list[Place] = Field(default_factory=list)
    travel_minutes: list[tuple[str, str, int]] = Field(default_factory=list)
    default_travel: int | None = Field(default=None, description="Minutes between zones with no listed route; None means unreachable.")


class Search(BaseModel):
    time_shifts: list[int] = Field(default_factory=lambda: [15, 30, -15, 45], description="Minutes a repair may move an activity.")
    max_options: int = 4


class StarterDetail(BaseModel):
    key: str
    label: str
    type: str = "detail"
    value_kind: ValueKind = "text"
    value: str | None = None
    aliases: list[str] = Field(default_factory=list)


class StarterTask(BaseModel):
    title: str
    type: str = "task"


class StarterActivity(BaseModel):
    key: str
    title: str
    type: str = "activity"
    day: int = 0
    start: str
    end: str
    place: str | None = None


class Starter(BaseModel):
    details: list[StarterDetail] = Field(default_factory=list)
    tasks: list[StarterTask] = Field(default_factory=list)
    activities: list[StarterActivity] = Field(default_factory=list)


class Template(BaseModel):
    id: str
    name: str
    description: str
    version: str = "1"
    days: int = Field(default=1, ge=1, le=366)
    lifecycle: Literal["bounded", "ongoing"] = "bounded"
    hidden_from_guest: bool = Field(default=False, description="Plans of this kind are usually kept from someone (a surprise).")
    item_types: list[ItemType]
    conditions: list[ConditionType] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=lambda: ["place_unavailable", "price_changed", "cannot_attend"])
    catalog: Catalog = Field(default_factory=Catalog)
    search: Search = Field(default_factory=Search)
    starter: Starter = Field(default_factory=Starter)
    booking_kinds: list[str] = Field(default_factory=list, description="Place kinds that get a (simulated) booking when a plan is committed.")

    @field_validator("item_types")
    @classmethod
    def _unique(cls, v: list[ItemType]) -> list[ItemType]:
        names = [t.name for t in v]
        if len(names) != len(set(names)):
            raise ValueError("item type names must be unique")
        return v

    @model_validator(mode="after")
    def _references(self) -> "Template":
        names = {t.name for t in self.item_types}
        for d in self.starter.details:
            if d.type not in names:
                raise ValueError(f"starter detail {d.key}: unknown type {d.type}")
        for t in self.starter.tasks:
            if t.type not in names:
                raise ValueError(f"starter task {t.title}: unknown type {t.type}")
        places = {p.id for p in self.catalog.places}
        for a in self.starter.activities:
            if a.type not in names:
                raise ValueError(f"starter activity {a.key}: unknown type {a.type}")
            if a.place and places and a.place not in places:
                raise ValueError(f"starter activity {a.key}: unknown place {a.place}")
        cond = [c.name for c in self.conditions]
        if len(cond) != len(set(cond)):
            raise ValueError("condition names must be unique")
        return self

    # ------------------------------------------------------------ lookups

    def item_type(self, name: str) -> ItemType | None:
        return next((t for t in self.item_types if t.name == name), None)

    def default_type(self, shape: str) -> ItemType | None:
        return next((t for t in self.item_types if t.shape == shape), None)

    def condition(self, name: str) -> ConditionType | None:
        """By template name (home_by) or by generic check (latest_end)."""
        lower = (name or "").strip().lower()
        return next((c for c in self.conditions if c.name == lower), None) or next((c for c in self.conditions if c.check == lower), None)

    @property
    def has_activities(self) -> bool:
        return any(t.shape == "activity" for t in self.item_types)


@functools.lru_cache(maxsize=None)
def load_templates(directory: str | None = None) -> dict[str, Template]:
    root = Path(directory) if directory else HERE
    out: dict[str, Template] = {}
    for path in sorted(root.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        t = Template.model_validate(data)
        if t.id != path.stem:
            raise ValueError(f"{path.name}: template id {t.id!r} must match the file name")
        out[t.id] = t
    return out


def get_template(template_id: str) -> Template:
    templates = load_templates()
    if template_id in templates:
        return templates[template_id]
    # Legacy definitions are frozen, discoverable by exact id, never in the
    # default directory. Existing plans keep their original semantics.
    if template_id in {"surprise_party", "weekend_trip", "team_meeting", "chores_shopping"}:
        return load_templates(str(HERE / "legacy"))[template_id]
    raise KeyError(template_id)


def apply_preset(template: Template, preset: str | None) -> Template:
    if not preset:
        return template
    if preset not in {"surprise", "weekend_cottage"}:
        raise ValueError("Unknown preset. Use a preset listed in get_template.")
    path = HERE / "presets" / f"{preset}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.pop("template") != template.id:
        raise ValueError("This preset belongs to another template.")
    merged = template.model_dump()
    merged.update(data)
    return Template.model_validate(merged)


def presets_for(template_id: str) -> list[dict[str, str]]:
    return ({"gathering": [{"id": "surprise", "description": "Surprise gathering; requires explicit hidden_from."}],
             "trip": [{"id": "weekend_cottage", "description": "Synthetic three-day cottage demo with a fixture catalog."}]}
            .get(template_id, []))


__all__ = ["Template", "ItemType", "ConditionType", "Place", "Catalog", "Search", "load_templates", "get_template", "CHECK_HINTS"]
