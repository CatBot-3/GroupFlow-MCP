from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Calendar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    start: str = "09:00"
    end: str = "17:00"
    unavailable: list[tuple[datetime, datetime]] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid(self):
        if not self.weekdays or any(d not in range(7) for d in self.weekdays):
            raise ValueError("weekdays must contain numbers 0 (Monday) to 6")
        for t in (self.start, self.end):
            datetime.strptime(t, "%H:%M")
        if self.end <= self.start:
            raise ValueError("calendar end must be after start on the same day")
        for lo, hi in self.unavailable:
            if lo.tzinfo is None or hi.tzinfo is None or hi <= lo:
                raise ValueError("unavailable windows require ordered timestamps with timezone offsets")
        return self


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str = Field(default="Delivery", min_length=1, max_length=100)
    approvers: list[str] = Field(default_factory=list)
    budget_minor: int | None = Field(default=None, ge=0)
    calendar: Calendar = Field(default_factory=Calendar)
    private_calendars: dict[str, Calendar] = Field(default_factory=dict)
    approval_days: int = Field(default=14, ge=1, le=90)


class WorkSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    not_before: date
    due: date
    duration_minutes: int = Field(default=60, ge=0, le=480)
    workstream: str = Field(default="General", min_length=1, max_length=100)
    milestone: str | None = Field(default=None, max_length=100)
    resources: dict[str, int] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    cost_minor: int = Field(default=0, ge=0)
    priority: int = Field(default=1, ge=1, le=10)
    locked_start: datetime | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.due < self.not_before:
            raise ValueError("due must be on or after not_before")
        if self.duration_minutes % 15:
            raise ValueError("duration must be a multiple of 15 minutes; split long work into daily work units")
        if any(isinstance(v, bool) or not 1 <= v <= 1000 for v in self.resources.values()):
            raise ValueError("resource quantities must be positive integers")
        if self.locked_start is not None and self.locked_start.tzinfo is None:
            raise ValueError("locked_start requires an explicit timezone offset")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("dependencies must be unique")
        return self


class RecurrenceSpec(BaseModel):
    """Explicit RFC5545 subset, with original local dates as occurrence identity."""
    model_config = ConfigDict(extra="forbid")
    frequency: Literal["DAILY", "WEEKLY"] = "WEEKLY"
    interval: int = Field(default=1, ge=1, le=52)
    dtstart: date
    byday: list[int] = Field(default_factory=list)
    count: int | None = Field(default=None, ge=1, le=1000)
    until: date | None = None
    exdates: list[date] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid(self):
        if self.count and self.until:
            raise ValueError("use count or until, not both")
        if self.until and self.until < self.dtstart:
            raise ValueError("until precedes dtstart")
        if any(d not in range(7) for d in self.byday):
            raise ValueError("byday contains weekdays 0..6")
        if self.byday and self.dtstart.weekday() not in self.byday:
            raise ValueError("dtstart must match byday")
        return self
