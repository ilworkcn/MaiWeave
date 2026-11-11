"""生活状态推进、日程管理和经历记录。"""

from datetime import datetime, timedelta
import math
from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import ExperienceInput, GoalInput, LifeState, PlanInput, PlanPatch, RelationshipChange, local_time
from .store import Store

ACTIVE = {"scheduled", "active"}


def uid() -> str:
    return uuid4().hex[:12]


def bounded(value: float, low: float = 0, high: float = 100) -> float:
    return round(min(high, max(low, value)), 3)


def travel_position(plan: dict) -> dict:
    route = plan["route"]
    elapsed = plan["elapsed_seconds"] / 60
    for leg in route["legs"]:
        if elapsed < leg["minutes"]:
            return {"location": leg["origin"], "next": leg["destination"], "mode": leg["mode"],
                    "progress": round(elapsed / leg["minutes"] * 100), "destination": route["destination"]}
        elapsed -= leg["minutes"]
    return {"location": route["destination"], "next": "", "mode": "已到达", "progress": 100, "destination": route["destination"]}


def read_life_status(store: Store, now: datetime, *, enabled: bool, schedule_enabled: bool) -> dict:
    """读取生活状态，供插件 API 和首页卡片共用。"""
    state = store.find("state", "life")
    result = {"version": 1, "ready": state is not None, "enabled": enabled,
              "schedule_enabled": schedule_enabled, "updated_at": None, "queried_at": now.isoformat(),
              "sleeping": None, "awake_until": None, "current": None,
              "next_plan": None, "location": None, "energy": None, "mood": None}
    if state is None:
        return result
    plans = store.all("plan", omit=("changes",))
    current = next((p for p in plans if p["status"] in ACTIVE
                    and datetime.fromisoformat(p["start"]) <= now < datetime.fromisoformat(p["end"])), None)
    upcoming = min((p for p in plans if p["status"] in ACTIVE
                    and datetime.fromisoformat(p["start"]) > now),
                   key=lambda p: datetime.fromisoformat(p["start"]), default=None)
    def brief(plan):
        return {key: plan[key] for key in ("id", "title", "kind", "start", "end", "location", "status")} if plan else None
    awakening = store.find("runtime", "awakening")
    until = awakening["until"] if awakening and datetime.fromisoformat(awakening["until"]) > now else None
    result.update(updated_at=state["updated_at"], sleeping=bool(current and current["kind"] == "sleep"),
                  awake_until=until, current=brief(current), next_plan=brief(upcoming),
                  location=state["location"], energy=state["energy"], mood=state["mood"])
    return result


class LifeEngine:
    def __init__(self, store: Store, timezone: str, now: datetime):
        self.store = store
        self.timezone = timezone
        ZoneInfo(timezone)
        if not store.find("runtime", "experience_score_10"):
            with store.db:
                for event in store.all("experience"):
                    old = event["importance"]
                    event.update(importance={1: 2, 2: 6, 3: 9}[old], score_reason=f"旧版重要度 {old}/3 分换算")
                    store.put("experience", event)
                store.put("runtime", {"id": "experience_score_10"})
        if not store.find("runtime", "completion_scope_v1"):
            with store.db:
                for plan in store.all("plan", omit=("changes",)):
                    event = store.find("experience", plan.get("experience_id", ""))
                    if event and event.get("plan_id") == plan["id"] and event["session_id"] != plan["session_id"]:
                        # 完成经历与关联计划共用范围，恢复旧记录的活动回顾。
                        event["session_id"] = plan["session_id"]
                        store.put("experience", event)
                        reflection = store.find("reflection", plan["id"])
                        if reflection and reflection["status"] == "失败" and reflection.get("error") == "该记录不属于当前聊天流":
                            reflection["retry_at"] = now.isoformat()
                            store.put("reflection", reflection)
                store.put("runtime", {"id": "completion_scope_v1"})
        if store.find("state", "life") is None:
            with store.db:
                store.put("state", {"id": "life", **LifeState(updated_at=now.isoformat()).model_dump()})

    def time(self, value: str) -> datetime:
        return local_time(value, self.timezone)

    def state(self) -> dict:
        return self.store.get("state", "life")

    def visible(self, record: dict, session: str) -> bool:
        return record["session_id"] in {"", session}

    def owned(self, category: str, record_id: str, session: str) -> dict:
        record = self.store.get(category, record_id)
        if not self.visible(record, session):
            raise ValueError("该记录不属于当前聊天流")
        return record

    def _check_interval(self, start: datetime, end: datetime, exclude: str = ""):
        if end <= start:
            raise ValueError("结束时间必须晚于开始时间")
        for plan in self.store.all("plan"):
            excluded = {exclude} if isinstance(exclude, str) else exclude
            if plan["id"] not in excluded and plan["status"] in ACTIVE and not (plan.get("activity_id") and plan["status"] == "scheduled"):
                if start < self.time(plan["end"]) and end > self.time(plan["start"]):
                    raise ValueError("这段时间已有安排，请选择其他时间或先调整原安排")

    def create_plan(self, value: PlanInput, session: str, now: datetime, autonomous: bool = False) -> dict:
        start, end = self.time(value.start), self.time(value.end)
        if start < now - timedelta(minutes=1):
            raise ValueError("过去的事情请记录为经历；新计划请选择当前或未来的时间")
        self._check_interval(start, end)
        if value.research_query and (value.book_id or not value.simulation or value.participants or value.kind not in {"study", "leisure"}):
            raise ValueError("联网了解使用角色独自进行的学习或休闲日程")
        if value.book_id:
            book = self.store.get("book", value.book_id)
            if book.get("archived"):
                raise ValueError("请先恢复已归档书籍，再安排阅读")
            if book["status"] == "已读完":
                raise ValueError("这本书已读完，请选择下一本")
            if not value.simulation or value.participants:
                raise ValueError("书架阅读使用角色独自阅读的日程")
        if value.goal_id:
            goal = self.owned("goal", value.goal_id, session)
            if goal["status"] != "active":
                raise ValueError("该目标已结束")
            if goal.get("steps"):
                step = next((s for s in goal["steps"] if s["status"] != "completed"), None)
                if not step or value.goal_step_id != step["id"]:
                    raise ValueError("关联分阶段目标时，请填写当前未完成阶段的 goal_step_id")
        if value.continuation_id:
            self.owned("activity_memory", value.continuation_id, session)
        plan = {**value.model_dump(), "id": uid(), "session_id": session,
                "start": start.isoformat(), "end": end.isoformat(), "status": "scheduled",
                "autonomous": autonomous, "flexible": value.flexible or autonomous,
                "elapsed_seconds": 0, "created_at": now.isoformat(), "changes": []}
        with self.store.db:
            self.store.put("plan", plan)
        return plan

    def change_plan(self, record_id: str, patch: PlanPatch, session: str, now: datetime) -> dict:
        plan = self.owned("plan", record_id, session)
        if plan["status"] not in ACTIVE | {"paused", "blocked"}:
            raise ValueError("这项计划已经结束，请创建新计划或修订对应经历")
        changes = patch.model_dump(exclude_unset=True)
        updated = {**plan, **changes}
        start, end = self.time(updated["start"]), self.time(updated["end"])
        if "start" in changes and start < now:
            raise ValueError("请选择当前或未来的开始时间")
        if end <= now:
            raise ValueError("结束时间必须在当前时间之后")
        self._check_interval(start, end, {record_id, plan.get("travel_id", "")})
        updated.update(start=start.isoformat(), end=end.isoformat())
        updated["changes"].append({"at": now.isoformat(), "before": {k: plan[k] for k in changes}, "after": changes})
        if "start" in changes or updated["status"] == "blocked":
            updated["status"] = "scheduled"
        elif updated["status"] == "paused" and "end" in changes:
            updated["remaining_seconds"] = (end - now).total_seconds()
        with self.store.db:
            self.store.put("plan", updated)
        return updated

    def transition(self, record_id: str, action: str, session: str, now: datetime) -> dict:
        plan = self.owned("plan", record_id, session)
        previous = plan["status"]
        if action == "cancel" and previous in ACTIVE | {"paused", "awaiting_confirmation", "blocked"}:
            plan["status"] = "cancelled"
        elif action == "pause" and previous == "active":
            plan["status"] = "paused"
            plan["remaining_seconds"] = max(0, (self.time(plan["end"]) - now).total_seconds())
        elif action == "resume" and previous == "paused":
            end = now + timedelta(seconds=plan["remaining_seconds"])
            self._check_interval(now, end, record_id)
            plan.update(start=now.isoformat(), end=end.isoformat(), status="active")
        else:
            raise ValueError(f"不能对状态 {previous} 的计划执行 {action}")
        plan["changes"].append({"at": now.isoformat(), "action": action, "before": previous})
        with self.store.db:
            self.store.put("plan", plan)
            if action == "cancel" and plan.get("travel_id"):
                travel = self.store.get("plan", plan["travel_id"])
                if travel["status"] in ACTIVE | {"paused"}:
                    travel["status"] = "cancelled"
                    self.store.put("plan", travel)
        return plan

    def plans(self, session: str, start: str = "", end: str = "") -> list[dict]:
        plans = [p for p in self.store.all("plan") if self.visible(p, session)]
        if start:
            plans = [p for p in plans if self.time(p["end"]) >= self.time(start)]
        if end:
            plans = [p for p in plans if self.time(p["start"]) <= self.time(end)]
        return sorted(plans, key=lambda p: self.time(p["start"]))

    def record_experience(self, value: ExperienceInput, session: str, now: datetime) -> dict:
        occurred = self.time(value.occurred_at)
        if occurred > now:
            raise ValueError("尚未发生的事情请创建计划")
        plan = self.owned("plan", value.plan_id, session) if value.plan_id else None
        if plan and plan["status"] == "completed":
            raise ValueError("该计划已经记录完成，请补充原经历")
        if plan and occurred < self.time(plan["start"]):
            raise ValueError("经历时间早于关联计划，请核对计划 ID 和发生时间")
        if plan and plan["participants"] and value.source == "simulated":
            raise ValueError("请根据用户陈述或已确认的交互记录共同安排的完成情况")
        record = {**value.model_dump(), "id": uid(), "session_id": plan["session_id"] if plan else session,
                  "occurred_at": occurred.isoformat(), "recorded_at": now.isoformat(),
                  "revision": 1, "history": [], "memory_id": "", "memory_revision": 0}
        if not record["score_reason"]:
            record["score_reason"] = ("记录时指定的重要度" if "importance" in value.model_fields_set else "普通生活记录")
        with self.store.db:
            self.store.put("experience", record)
            if plan:
                plan["status"] = "completed"
                plan["experience_id"] = record["id"]
                plan["changes"].append({"at": now.isoformat(), "action": "record_completion", "source": value.source})
                self.store.put("plan", plan)
                self.progress_goal(plan, confirmed=True)
                state = self.state()
                self._completion_effect(state, plan)
                self.store.put("state", state)
        return self.store.get("experience", record["id"])

    def revise_experience(self, record_id: str, details: dict[str, str], summary: str,
                          evidence: str, session: str, now: datetime) -> dict:
        record = self.owned("experience", record_id, session)
        if not evidence.strip():
            raise ValueError("补全或更正经历需要说明来源")
        record["history"].append({"revision": record["revision"], "summary": record["summary"],
                                  "details": dict(record["details"]), "evidence": record["evidence"],
                                  "revised_at": now.isoformat()})
        record["details"].update(details)
        if summary:
            record["summary"] = summary
        record["evidence"] = evidence
        record["revision"] += 1
        with self.store.db:
            self.store.put("experience", record)
            for memory in self.store.all("activity_memory"):
                if memory["experience_id"] == record_id:
                    memory.update(details=dict(record["details"]), updated_at=now.isoformat())
                    if "进度" in details:
                        memory["next_action"] = "从更新后的进度继续：" + details["进度"]
                    self.store.put("activity_memory", memory)
        return record

    def experiences(self, session: str, query: str = "", limit: int = 10) -> list[dict]:
        records = [e for e in self.store.all("experience") if self.visible(e, session)
                   and (not query or query in e["summary"] or any(query in v for v in e["details"].values()))]
        return sorted(records, key=lambda e: self.time(e["occurred_at"]), reverse=True)[:limit]

    def create_goal(self, value: GoalInput, session: str, now: datetime) -> dict:
        record = {"id": uid(), **value.model_dump(), "session_id": session,
                  "completed_sessions": 0, "status": "active", "created_at": now.isoformat()}
        with self.store.db:
            self.store.put("goal", record)
        return record

    def close_goal(self, record_id: str, session: str) -> dict:
        goal = self.owned("goal", record_id, session)
        goal["status"] = "cancelled"
        with self.store.db:
            self.store.put("goal", goal)
            for plan in self.store.all("plan"):
                if plan["goal_id"] == record_id and plan["autonomous"] and plan["status"] == "scheduled":
                    plan["status"] = "cancelled"
                    self.store.put("plan", plan)
                    if plan.get("travel_id"):
                        trip = self.store.get("plan", plan["travel_id"])
                        if trip["status"] == "scheduled":
                            trip["status"] = "cancelled"
                            self.store.put("plan", trip)
        return goal

    def progress_goal(self, plan: dict, confirmed=False):
        if plan.get("goal_step_id"):
            if confirmed:
                self.progress_stage(plan, 1, "已通过经历确认完成")
            return
        if plan["goal_id"]:
            goal = self.store.get("goal", plan["goal_id"])
            if goal["status"] == "active":
                goal["completed_sessions"] += 1
                if goal["completed_sessions"] >= goal["target_sessions"]:
                    goal["status"] = "completed"
                    self.mark_goal_result(plan, goal, 9, "达成目标")
                self.store.put("goal", goal)

    def mark_goal_result(self, plan, goal, score, result):
        milestone = f"{result}：{goal['title']}（累计完成 {goal['completed_sessions']} 次）"
        event = self.store.get("experience", plan["experience_id"])
        event.update(goal_score=score, goal_result=milestone)
        self.score_result(event, event["importance"], event["score_reason"])
        self.store.put("experience", event)

    @staticmethod
    def score_result(event, score, reason):
        event["importance"] = max(score, event.get("goal_score", 0))
        event["score_reason"] = f"{event['goal_result']}。{reason}" if event.get("goal_result") else reason

    def progress_stage(self, plan, units, note):
        if plan.get("goal_progress_applied"):
            return
        goal = self.store.get("goal", plan["goal_id"])
        step = next(s for s in goal["steps"] if s["id"] == plan["goal_step_id"])
        step["note"] = note
        if goal["status"] == "active":
            added = min(units, step["sessions"] - step["completed_sessions"])
            step["completed_sessions"] += added
            step["status"] = "completed" if step["completed_sessions"] >= step["sessions"] else "active"
            goal["completed_sessions"] += added
            stage_name = step.get("title") or plan["title"]
            if all(s["status"] == "completed" for s in goal["steps"]):
                goal["status"] = "completed"
                self.mark_goal_result(plan, goal, 9, f"完成阶段「{stage_name}」并达成目标")
            elif added and step["status"] == "completed":
                self.mark_goal_result(plan, goal, 7, f"完成阶段「{stage_name}」")
        plan["goal_progress_applied"] = True
        with self.store.db:
            self.store.put("goal", goal)
            self.store.put("plan", plan)

    def relationship(self, session: str, person: str) -> dict:
        key = f"{session}:{person}"
        return self.store.find("relationship", key) or {
            "id": key, "session_id": session, "person_id": person, "familiarity": 0,
            "trust": 50, "closeness": 0, "boundary": "", "unresolved": "", "history": []}

    def relate(self, session: str, person: str, change: RelationshipChange, now: datetime) -> dict:
        relation = self.relationship(session, person)
        for key in ("familiarity", "trust", "closeness"):
            relation[key] = bounded(relation[key] + getattr(change, key))
        for key in ("boundary", "unresolved"):
            if key in change.model_fields_set:
                relation[key] = getattr(change, key)
        relation["history"].append({"at": now.isoformat(), **change.model_dump(exclude_unset=True)})
        with self.store.db:
            self.store.put("relationship", relation)
            if "unresolved" in change.model_fields_set:
                key = "relationship:" + relation["id"]
                if relation["unresolved"]:
                    self.add_effect(key, relation["unresolved"], -1, 2, now, 72, session)
                else:
                    effect = self.store.find("effect", key)
                    if effect:
                        effect["status"] = "resolved"
                        self.store.put("effect", effect)
        return relation

    def add_effect(self, key, reason, mood, stress, now, duration_hours, session=""):
        record = {"id": key, "session_id": session, "reason": reason, "mood": mood, "stress": stress,
                  "started_at": now.isoformat(), "expires_at": (now + timedelta(hours=duration_hours)).isoformat(),
                  "half_life_hours": max(1, duration_hours / 2), "status": "active"}
        self.store.put("effect", record)
        return record

    def affect(self, mood: float, stress: float, reason: str, now: datetime, duration_hours: float = 48, session: str = "") -> dict:
        if not -10 <= mood <= 10 or not -10 <= stress <= 10 or not reason.strip():
            raise ValueError("单次情绪变化须在 -10 到 10 之间，并说明具体原因")
        state = self.state()
        state["mood"] = bounded(state["mood"] + mood, -100, 100)
        state["stress"] = bounded(state["stress"] + stress)
        with self.store.db:
            self.store.put("state", state)
            key = uid()
            self.store.put("emotion", {"id": key, "at": now.isoformat(), "mood": mood, "stress": stress, "reason": reason})
            if duration_hours > 0 and (mood or stress):
                self.add_effect(key, reason, mood, stress, now, duration_hours, session)
        return state

    def _evolve(self, state: dict, hours: float, kind: str):
        if kind == "sleep":
            state["energy"] = bounded(state["energy"] + 10 * hours)
            state["sleep_debt"] = bounded(state["sleep_debt"] - hours, 0, 24)
        else:
            drain = 6 if kind in {"work", "study", "travel"} else 3
            state["energy"] = bounded(state["energy"] + (5 if kind == "rest" else -drain) * hours)
            state["sleep_debt"] = bounded(state["sleep_debt"] + hours / 3, 0, 24)
        state["hunger"] = bounded(state["hunger"] + (2 if kind == "sleep" else 5) * hours)
        state["mood"] = round(state["mood"] * (0.94 ** hours), 3)
        stress_rate = 3 if kind in {"work", "study"} else -2
        state["stress"] = bounded(state["stress"] + stress_rate * hours)

    def _completion_effect(self, state: dict, plan: dict):
        if plan["kind"] == "meal":
            state["hunger"] = bounded(state["hunger"] - 55)
        if plan["kind"] in {"leisure", "social"}:
            state["mood"] = bounded(state["mood"] + 3, -100, 100)

    def advance(self, now: datetime) -> dict:
        """按已有日程结算活动，并更新空闲时段的身体状态。"""
        state = self.state()
        cursor = self.time(state["updated_at"])
        if now <= cursor:
            return state
        plans = [p for p in self.store.all("plan") if p["status"] in ACTIVE]
        boundaries = {cursor, now}
        for p in plans:
            for key in ("start", "end"):
                t = self.time(p[key])
                if cursor < t < now:
                    boundaries.add(t)
        times = sorted(boundaries)
        with self.store.db:
            for left, right in zip(times, times[1:]):
                current = next((p for p in plans if self.time(p["start"]) <= left < self.time(p["end"])), None)
                seconds = (right - left).total_seconds()
                self._evolve(state, seconds / 3600, current["kind"] if current else "idle")
                if current:
                    if current["elapsed_seconds"] == 0:
                        current["elapsed_days"] = {}
                    if "elapsed_days" in current:
                        part = left
                        while part < right:
                            stop = min(right, (part + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
                            day = part.date().isoformat()
                            current["elapsed_days"][day] = current["elapsed_days"].get(day, 0) + (stop - part).total_seconds()
                            part = stop
                    current["last_active_at"] = right.isoformat()
                    current["elapsed_seconds"] += seconds
                    if current.get("route"):
                        position = travel_position(current)
                        state["location"] = position["location"]
                        state["journey"] = position
                    elif current["location"]:
                        state["location"] = current["location"]
                        state["journey"] = None
                for plan in plans:
                    if plan["status"] in ACTIVE and self.time(plan["end"]) <= right:
                        if plan["simulation"] and not plan["participants"]:
                            self._complete_simulation(plan, right)
                            self._completion_effect(state, plan)
                        else:
                            plan["status"] = "awaiting_confirmation"
                    elif plan["status"] == "scheduled" and self.time(plan["start"]) <= right:
                        plan["status"] = "active"
            for plan in plans:
                self.store.put("plan", plan)
            for effect in self.store.all("effect"):
                if effect["status"] != "active":
                    continue
                start, end = self.time(effect["started_at"]), self.time(effect["expires_at"])
                left, right = max(cursor, start), min(now, end)
                if right > left:
                    rate = math.log(2) / effect["half_life_hours"]
                    a, b = (left - start).total_seconds() / 3600, (right - start).total_seconds() / 3600
                    weight = (math.exp(-rate * a) - math.exp(-rate * b)) / rate / 24
                    state["mood"] = bounded(state["mood"] + effect["mood"] * weight, -100, 100)
                    state["stress"] = bounded(state["stress"] + effect["stress"] * weight)
                if now >= end:
                    effect["status"] = "expired"
                    self.store.put("effect", effect)
            state["updated_at"] = now.isoformat()
            self.store.put("state", state)
        return state

    def _complete_simulation(self, plan: dict, now: datetime):
        score, reason = {
            "sleep": (1, "按作息睡眠"), "travel": (1, "日常出行"), "rest": (2, "日常休息"),
            "meal": (2, "日常用餐"), "diary": (3, "写完日记"), "leisure": (3, "完成休闲活动"),
            "study": (3, "完成学习安排"), "work": (3, "完成工作安排"), "social": (4, "完成社交活动"),
        }[plan["kind"]]
        if plan["goal_id"]:
            score, reason = 4, "完成与目标关联的活动"
        record = {"id": uid(), "session_id": plan["session_id"], "summary": f"完成了{plan['title']}",
                  "occurred_at": plan["end"], "recorded_at": now.isoformat(), "source": "simulated",
                  "details": {"地点": plan["location"], "动机": plan["motivation"]},
                  "evidence": f"角色生活模拟，依据已保存计划 {plan['id']} 推进",
                  "plan_id": plan["id"], "importance": score, "score_reason": reason, "revision": 1, "history": [],
                  "memory_id": "", "memory_revision": 0}
        if plan.get("book_id"):
            record["summary"] = f"结束{plan['title']}的阅读时段，正文笔记正在整理"
        plan.update(status="completed", experience_id=record["id"])
        self.store.put("experience", record)
        if plan.get("book_id") and plan.get("goal_id") and not plan.get("goal_step_id"):
            plan["reading_goal_pending"] = True
        elif not plan.get("book_id"):
            self.progress_goal(plan)

    def current(self, now: datetime) -> dict | None:
        return next((p for p in self.store.all("plan") if p["status"] in ACTIVE
                     and self.time(p["start"]) <= now < self.time(p["end"])), None)

    def free_minutes(self, now: datetime) -> int:
        starts = [self.time(p["start"]) for p in self.store.all("plan")
                  if p["status"] in ACTIVE and self.time(p["start"]) > now]
        return int((min(starts) - now).total_seconds() / 60) if starts else 720

    def snapshot(self, session: str, now: datetime, person: str = "") -> dict:
        state = self.advance(now)
        current = self.current(now)
        if current and not self.visible(current, session):
            current = {"status": "active", "summary": "正在处理其他安排", "end": current["end"]}
        plans = [p for p in self.plans(session) if p["status"] in ACTIVE | {"paused", "awaiting_confirmation", "blocked"}]
        result = {"now": now.isoformat(), "state": state, "current": current, "plans": plans[:12],
                  "experiences": self.experiences(session, limit=6),
                  "goals": [g for g in self.store.all("goal") if self.visible(g, session) and g["status"] == "active"]}
        if person:
            result["relationship"] = self.relationship(session, person)
        return result
