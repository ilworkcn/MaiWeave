"""生活业务模型；时间统一保存为带时区的 ISO 字符串。"""

from datetime import date, datetime, timedelta
import re
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

Kind = Literal["sleep", "cook", "meal", "work", "study", "leisure", "travel", "rest", "social", "diary"]
Source = Literal["reported", "observed", "simulated"]
SHARE_CONTEXT_WINDOW = timedelta(days=2)
SHARE_REPLY_PAUSE = timedelta(minutes=20)


def local_time(value: str, timezone: str) -> datetime:
    """无偏移量的输入按角色时区解释。"""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=ZoneInfo(timezone)) if parsed.tzinfo is None else parsed


class WeeklyDuty(BaseModel):
    kind: Literal["study", "work"]
    title: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=80)
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    duration_minutes: int = Field(ge=1, le=720)

    @model_validator(mode="after")
    def check_days(self):
        if any(day not in range(7) for day in self.weekdays):
            raise ValueError("星期请填写0至6")
        return self


class Occupation(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    duties: list[WeeklyDuty] = Field(max_length=4)


class ShiftScene(BaseModel):
    task: str = Field(min_length=1, max_length=90)
    situation: Literal["日常", "返工", "赶工", "受委屈"]
    load: Literal["常规", "持续用力", "高度专注", "密集沟通"] = "常规"
    detail: str = Field(max_length=100)
    minutes: int = Field(ge=1)
    deadline_minutes: int | None = Field(default=None, ge=1, le=1440)
    resolution: str = Field(min_length=1, max_length=100)
    payoff: Literal["平常", "有成就感", "有人搭手"] = "平常"
    next_shift: str = Field(default="", max_length=120)


def tool_plan(plan):
    """带参与人和接收聊天的显式工具指令按时间执行。"""
    return bool(plan.get("session_id") and plan.get("participants") and re.match(
        r"^(?:请(?:麦麦|你)?|麦麦)?(?:用|使用|调用|通过)\s*(?:MCP|工具)", plan["title"].strip(), re.I))


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
    cost: int = Field(default=0, ge=0, description="本次实际购买商品或用餐的预计支出，按钱包单位填写")
    groceries: int = Field(default=0, ge=0, description="实际买回的食材够自己吃几餐")
    food: Literal["", "做好留餐", "现做现吃", "吃留餐", "外食", "受招待", "备料"] = ""
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

    @model_validator(mode="before")
    @classmethod
    def skip_unused_freshness(cls, value):
        """普通生活活动沿用默认时效，只有联网检索才区分资料类型。"""
        if isinstance(value, dict) and not value.get("research_query") and "research_freshness" in value:
            return {**value, "research_freshness": "current"}
        return value


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


class ChatPlanUpdate(BaseModel):
    plan_id: str
    patch: PlanPatch


class ChatArrangement(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    start: str
    end: str
    evidence: str = Field(min_length=1, max_length=400)
    source: Literal["reported", "observed"] = "reported"
    speaker_id: str = ""
    blocked_kinds: list[Kind] = Field(default_factory=list)
    record_id: str = ""
    plan_updates: list[ChatPlanUpdate] = Field(default_factory=list)
    cancel_plan_ids: list[str] = Field(default_factory=list)


class PlanPromise(BaseModel):
    plan_id: str
    when: Literal["arrival", "completion", "task_completion"]
    topic: str = Field(default="", max_length=200)
    action: Literal["promise", "answered"]
    evidence: str = Field(min_length=1, max_length=500)


class ChatArrangements(BaseModel, extra="forbid"):
    arrangements: list[ChatArrangement] = Field(default_factory=list, max_length=8)
    promises: list[PlanPromise] = Field(default_factory=list, max_length=8)


class RoutineInput(BaseModel):
    pay: int = Field(default=0, ge=0, description="每班总报酬。新工作省略时按时长和30元时薪估算，明确无薪填0")
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
    meal_ready: bool = Field(default=False, description="这次做出了留给角色自己吃的一餐")
    hourly_pay: int = Field(default=0, ge=0, description="这次经历里遇到的临时工作时薪，按钱包单位填写")
    new_followup: str = Field(default="", max_length=300, description="上一件已收尾的事接着办完后，本次新发现的下一件事")
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
    image: bool = False
    candidate: int = Field(default=1, ge=1, le=3, description="所选候选的序号，单条候选为1")
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="before")
    @classmethod
    def normalize_decision(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            if value.get("send") is False:
                value.pop("candidate", None)
            if isinstance(value.get("reason"), str):
                value["reason"] = value["reason"].strip()[:300]
        return value
