"""生活业务模型；时间统一保存为带时区的 ISO 字符串。"""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

Kind = Literal["sleep", "meal", "work", "study", "leisure", "travel", "rest", "social"]
Source = Literal["reported", "observed", "simulated"]


def local_time(value: str, timezone: str) -> datetime:
    """无偏移量的输入按角色时区解释。"""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=ZoneInfo(timezone)) if parsed.tzinfo is None else parsed


class PlanInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    start: str
    end: str
    kind: Kind = "leisure"
    location: str = ""
    motivation: str = ""
    participants: list[str] = Field(default_factory=list)
    simulation: bool = True
    goal_id: str = ""


class ExperienceInput(BaseModel):
    summary: str = Field(min_length=1, max_length=1500)
    occurred_at: str
    source: Source = "reported"
    details: dict[str, str] = Field(default_factory=dict)
    evidence: str = Field(min_length=1)
    plan_id: str = ""
    importance: int = Field(default=1, ge=1, le=3)


class ActivityChoice(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    kind: Kind
    minutes: int = Field(ge=10, le=120)
    location: str
    motivation: str
    goal_id: str = ""


class LifeState(BaseModel):
    energy: float = 80
    hunger: float = 20
    mood: float = 0
    stress: float = 10
    sleep_debt: float = 0
    location: str = ""
    updated_at: str


class GoalInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    reason: str = ""
    target_sessions: int = Field(default=5, ge=1)


class RelationshipChange(BaseModel):
    familiarity: float = Field(default=0, ge=-5, le=5)
    trust: float = Field(default=0, ge=-5, le=5)
    closeness: float = Field(default=0, ge=-5, le=5)
    reason: str = Field(min_length=1)
    boundary: str = ""
    unresolved: str = ""


class PlanPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    start: str | None = None
    end: str | None = None
    kind: Kind | None = None
    location: str | None = None
    motivation: str | None = None

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set:
            raise ValueError("请提供需要修改的字段")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("修改字段不能为 null")
        return self


class RoutineInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    frequency: Literal["daily", "weekly", "monthly"] = "weekly"
    interval: int = Field(default=1, ge=1, le=52)
    anchor_date: str
    start_time: str
    duration_minutes: int = Field(ge=1, le=1440)
    weekdays: list[int] = Field(default_factory=list, description="周一为0，周日为6")
    month_day: int = Field(default=1, ge=1, le=31)
    until: str = ""
    kind: Kind = "leisure"
    location: str = ""
    participants: list[str] = Field(default_factory=list)
    simulation: bool = True


class DreamContent(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    story: str = Field(min_length=1, max_length=1600)
    feeling: str = Field(min_length=1, max_length=160)
    mood_delta: float = Field(default=0, ge=-3, le=3)
