"""生活业务模型；时间统一保存为带时区的 ISO 字符串。"""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

Kind = Literal["sleep", "cook", "meal", "work", "study", "leisure", "travel", "rest", "social", "diary"]
Source = Literal["reported", "observed", "simulated"]


def local_time(value: str, timezone: str) -> datetime:
    """无偏移量的输入按角色时区解释。"""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=ZoneInfo(timezone)) if parsed.tzinfo is None else parsed


class PlanInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    start: str
    end: str
    kind: Kind = Field(default="leisure", description="cook 表示备菜烹饪；meal 表示实际用餐，或明确包含用餐的做饭加吃饭。")
    location: str = ""
    motivation: str = ""
    participants: list[str] = Field(default_factory=list)
    simulation: bool = True
    goal_id: str = ""
    goal_step_id: str = ""
    continuation_id: str = ""
    diary_day: str = ""
    book_id: str = ""
    research_query: str = Field(default="", max_length=200)
    research_freshness: Literal["current", "knowledge"] = "current"
    flexible: bool = False


SCORE_GUIDE = (
    "importance 填1至10的整数，score_reason 用一句话说明这次发生了什么、为什么值得分享。"
    "1–2分是普通起居记录，3–4分有一些感受但暂时没有想聊的细节，5–6分有具体趣味、感受或发现，想顺口分享。"
    "例如做饭终于没糊、听到一首喜欢的歌、散步遇到有趣的小景，都可以是5–6分。阅读与其他活动使用同一标准。"
    "7–8分有明显情绪变化、重要进展或与对方有关的特别经历，9–10分达成长期目标或带来持续影响。按本次实际结果评分。"
)


class SharingScore(BaseModel):
    importance: int = Field(ge=1, le=10)
    score_reason: str = Field(min_length=1, max_length=240)


class ExperienceInput(BaseModel):
    summary: str = Field(min_length=1, max_length=1500)
    occurred_at: str
    source: Source = "reported"
    details: dict[str, str] = Field(default_factory=dict)
    evidence: str = Field(min_length=1)
    plan_id: str = ""
    importance: int = Field(default=2, ge=1, le=10, description=SCORE_GUIDE)
    score_reason: str = Field(default="", max_length=240)


class ActivityChoice(BaseModel):
    continuation_id: str = ""
    title: str = Field(min_length=1, max_length=160)
    kind: Kind = Field(description="cook 表示做饭，meal 表示吃饭；合并安排时明确包含吃饭并预留用餐时间。")
    minutes: int = Field(ge=10, le=120)
    location: str
    motivation: str
    goal_id: str = ""
    goal_step_id: str = ""
    place_query: str = ""
    book_id: str = ""
    research_query: str = Field(default="", max_length=200)
    research_freshness: Literal["current", "knowledge"] = "current"


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
    kind: Kind | None = Field(default=None, description="cook 表示备菜烹饪；meal 表示实际用餐，或明确包含用餐的做饭加吃饭。")
    location: str | None = None
    motivation: str | None = None
    flexible: bool | None = None

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set:
            raise ValueError("请提供需要修改的字段")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("请填写修改字段的值，保持原值的字段可省略")
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
    kind: Kind = Field(default="leisure", description="cook 表示备菜烹饪；meal 表示实际用餐，或明确包含用餐的做饭加吃饭。")
    location: str = ""
    participants: list[str] = Field(default_factory=list)
    simulation: bool = True


class DreamContent(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    story: str = Field(min_length=1, max_length=1600)
    feeling: str = Field(min_length=1, max_length=160)
    mood_delta: float = Field(default=0, ge=-3, le=3)


class ActivityMeaning(BaseModel):
    activity: str = Field(min_length=1, max_length=80)
    subject: str = Field(default="", max_length=160)


class ActivityOutcome(ActivityMeaning, SharingScore):
    next_date: date | None = None
    summary: str = Field(min_length=1, max_length=1500)
    details: dict[str, str]
    enjoyment: float = Field(ge=-5, le=5)
    mood: float = Field(default=0, ge=-5, le=5)
    stress: float = Field(default=0, ge=-5, le=5)
    next_action: str = Field(default="", max_length=300)
    goal_units: int = Field(default=1, ge=0, le=1)
    completed: bool = False


class ActivityAssessment(ActivityMeaning):
    goal_units: int = Field(default=0, ge=0, le=1)
    next_action: str = Field(default="", max_length=300)


class GoalStep(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    criterion: str = Field(min_length=1, max_length=300)
    sessions: int = Field(ge=1, le=100)
    minutes: int = Field(default=30, ge=10, le=120)
    kind: Literal["study", "work", "leisure", "rest"] = "study"


class GoalOutline(BaseModel):
    steps: list[GoalStep] = Field(min_length=1, max_length=12)


class ShareDecision(BaseModel):
    """结合已发送内容判断这次经历是否值得主动提起。"""
    send: bool
    reason: str = Field(min_length=1, max_length=300)
