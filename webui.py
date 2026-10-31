"""MaiBot 原生工作区页面的数据和操作接口。"""

import json
import math
from datetime import timedelta

from maibot_sdk import API

from .models import GoalInput, PlanInput, PlanPatch, RoutineInput

KIND = {"sleep": "睡眠", "meal": "用餐", "work": "工作", "study": "学习", "leisure": "休闲", "travel": "出行", "rest": "休息", "social": "社交"}
STATUS = {"scheduled": "未开始", "active": "进行中", "completed": "已完成", "cancelled": "已取消", "paused": "已暂停", "awaiting_confirmation": "等待完成确认", "blocked": "需要调整"}
SOURCE = {"reported": "用户陈述", "observed": "交互观测", "simulated": "角色模拟", "dream": "梦境"}
FREQUENCY = {"daily": "按日", "weekly": "按周", "monthly": "按月"}
WEEKDAYS = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6}


def display_time(value: str) -> str:
    return value.replace("T", " ")[:19] if value else ""


def plan_row(plan):
    return {**plan, "kind_label": KIND[plan["kind"]], "status_label": STATUS[plan["status"]],
            "start_label": display_time(plan["start"]), "end_label": display_time(plan["end"]),
            "scope_label": plan["session_id"] or "公共生活", "participants_label": "、".join(plan["participants"]) or "独自活动",
            "origin_label": "重复日程" if plan.get("routine_id") else ("自主安排" if plan["autonomous"] else "对话或页面安排")}


class WebUI:
    @API("ui_anniversaries", description="个人资料和纪念日")
    async def ui_anniversaries(self) -> dict:
        return {"profiles": self.store.all("profile"), "anniversaries": self.anniversaries.upcoming(self.now().date())}

    @API("ui_save_profile", description="保存个人资料")
    async def ui_save_profile(self, name: str, session_id: str, user_id: str = "", notes: str = "", profile_id: str = "") -> dict:
        return self.anniversaries.save_profile(name, session_id, user_id, notes, profile_id)

    @API("ui_save_anniversary", description="保存生日或纪念日")
    async def ui_save_anniversary(self, profile_id: str, title: str, calendar: str, month: int, day: int,
                                  year: int = 0, leap_month: bool = False, enabled: bool = True, anniversary_id: str = "") -> dict:
        return self.anniversaries.save_date(profile_id, title, calendar, month, day, year, leap_month, enabled, anniversary_id)

    @API("ui_delete_profile", description="删除个人资料及关联纪念日")
    async def ui_delete_profile(self, profile_id: str) -> dict:
        return self.anniversaries.delete("profile", profile_id)

    @API("ui_delete_anniversary", description="删除纪念日")
    async def ui_delete_anniversary(self, anniversary_id: str) -> dict:
        return self.anniversaries.delete("anniversary", anniversary_id)

    @API("ui_overview", description="生活状态和趋势")
    async def ui_overview(self) -> dict:
        now, state = self.now(), self.engine.state()
        current = self.engine.current(now)
        plans = self.store.all("plan")
        journey = state.get("journey")
        travel = f"{journey['location']} → {journey['next']}（{journey['progress']}%）" if journey and journey["next"] else "当前没有移动"
        history = sorted(self.store.all("state_history"), key=lambda r: r["at"])[-144:]
        return {"state": state, "activity": current["title"] if current else "空闲", "travel": travel,
                "location": state["location"] or "未登记", "sleep": "睡眠中" if current and current["kind"] == "sleep" else "清醒",
                "schedule_mode": "按日常作息" if self.config.life.schedule_enabled else "全天候交流",
                "awake_until": display_time(self.living.awake_until(now).isoformat()) if self.living.awake_until(now) and self.config.life.schedule_enabled else "未处于关键词唤醒时段",
                "mood_label": "心情不错" if state["mood"] > 10 else ("有些低落" if state["mood"] < -10 else "平静"),
                "updated_at": display_time(state["updated_at"]), "enabled": "已启用" if self.config.life.enabled else "已停用",
                "pending_count": sum(p["status"] in {"scheduled", "active"} for p in plans),
                "experience_count": len(self.store.all("experience")), "dream_count": len(self.store.all("dream")),
                "habit_count": sum(h["active"] for h in self.store.all("habit")), "history": history,
                "next_plans": [plan_row(p) for p in sorted(plans, key=lambda p: p["start"]) if p["status"] in {"scheduled", "active"}][:12],
                "errors": "\n".join(f"{name}：{error}" for name, error in self.last_errors.items()) or "无运行错误",
                "jobs": self.store.all("job")[-10:]}

    @API("ui_schedule", description="日程与重复规则")
    async def ui_schedule(self, session_id: str = "", start_date: str = "") -> dict:
        plans = self.store.all("plan")
        if session_id:
            plans = [p for p in plans if self.engine.visible(p, session_id)]
        if start_date:
            after = self.engine.time(start_date)
            plans = [p for p in plans if self.engine.time(p["end"]) >= after]
        plans = sorted(plans, key=lambda p: self.engine.time(p["start"]), reverse=True)[:200]
        rules = [r for r in self.store.all("routine") if not session_id or self.engine.visible(r, session_id)]
        return {"plans": [plan_row(p) for p in plans],
                "rules": [{**r, "frequency_label": FREQUENCY[r["frequency"]], "status_label": STATUS[r["status"]],
                           "weekdays_label": "、".join(list(WEEKDAYS)[d] for d in r["weekdays"]), "exceptions_label": "、".join(r["exceptions"]),
                           "kind_label": KIND[r["kind"]]} for r in rules], "issues": self.store.all("routine_issue")[-100:],
                "schedule_issues": self.store.all("schedule_issue"), "notices": self.store.all("schedule_notice"), "adjustments": self.store.all("adjustment")[-50:]}

    @API("ui_create_plan", description="新增生活安排")
    async def ui_create_plan(self, title: str, start: str, end: str, kind: str = "leisure", location: str = "", session_id: str = "", participants: str = "", simulation: bool = True, flexible: bool = False) -> dict:
        self.require_life()
        self.engine.advance(self.now())
        value = PlanInput(title=title, start=start, end=end, kind=kind, location=location,
                          participants=[v.strip() for v in participants.split(",") if v.strip()], simulation=simulation, flexible=flexible)
        return self.spatial.schedule(value, session_id, self.now())

    @API("ui_plan_action", description="取消、暂停或恢复所选日程")
    async def ui_plan_action(self, plan_id: str, action: str) -> dict:
        self.require_life()
        self.engine.advance(self.now())
        plan = self.store.get("plan", plan_id)
        return self.spatial.transition(plan_id, action, plan["session_id"], self.now())

    @API("ui_update_plan", description="修改所选日程")
    async def ui_update_plan(self, plan_id: str, new_title: str = "", new_start: str = "", new_end: str = "", new_location: str = "") -> dict:
        self.require_life()
        self.engine.advance(self.now())
        plan = self.store.get("plan", plan_id)
        changes = {k: v for k, v in {"title": new_title, "start": new_start, "end": new_end, "location": new_location}.items() if v}
        return self.spatial.change(plan_id, PlanPatch.model_validate(changes), plan["session_id"], self.now())

    @API("ui_save_routine", description="创建或更新重复日程")
    async def ui_save_routine(self, title: str, anchor_date: str, start_time: str, duration_minutes: int,
                              frequency: str = "weekly", interval: int = 1, weekdays: str = "周一,周二,周三,周四,周五",
                              month_day: int = 1, until: str = "", kind: str = "leisure", location: str = "",
                              session_id: str = "", participants: str = "", simulation: bool = True, rule_id: str = "") -> dict:
        self.require_life()
        names = [v.strip() for v in weekdays.replace("，", ",").split(",") if v.strip()]
        if any(name not in WEEKDAYS for name in names):
            raise ValueError("星期请填写周一到周日，用逗号分隔")
        if rule_id:
            session_id = self.store.get("routine", rule_id)["session_id"]
        value = RoutineInput(title=title, anchor_date=anchor_date, start_time=start_time, duration_minutes=duration_minutes,
                             frequency=frequency, interval=interval, weekdays=[WEEKDAYS[n] for n in names], month_day=month_day,
                             until=until, kind=kind, location=location, participants=[v.strip() for v in participants.split(",") if v.strip()], simulation=simulation)
        rule = self.rhythm.save(value, session_id, self.now(), rule_id)
        self.rhythm.materialize(self.now())
        return rule

    @API("ui_routine_action", description="停用重复规则或跳过一次")
    async def ui_routine_action(self, rule_id: str, action: str, skip_date: str = "") -> dict:
        self.require_life()
        rule = self.store.get("routine", rule_id)
        if action == "skip":
            return self.rhythm.skip(rule_id, skip_date, rule["session_id"], self.now())
        if action == "cancel":
            return self.rhythm.cancel(rule_id, rule["session_id"], self.now())
        raise ValueError("未知重复规则操作")

    @API("ui_world", description="天气、预报和外部资料")
    async def ui_world(self) -> dict:
        environment = self.world.context(self.now())
        weather = environment["weather"]
        status = self.store.find("world_status", "weather")
        if weather:
            summary = f"{weather['city']} · {weather['label']} · {weather['current']['temperature_2m']}℃"
            freshness = "数据已过期" if weather["stale"] else "最近观测"
        else:
            summary = "尚无天气数据；请先配置天气城市" if not self.config.world.city else "尚未获取天气"
            freshness = "尚无观测"
        return {"summary": summary, "freshness": freshness,
                "observed_at": display_time(weather["observed_at"]) if weather else "—",
                "source": "Open-Meteo", "forecast": weather["forecast"] if weather else [],
                "weather_error": status["error"] if status and status["error"] else "无",
                "statuses": self.store.all("world_status"), "news": self.store.all("news")[-20:][::-1],
                "research": [{"query": r["query"], "retrieved_at": display_time(r["retrieved_at"]),
                              "results_text": json.dumps(r["results"], ensure_ascii=False)} for r in self.store.all("research")[-5:][::-1]],
                "readings": self.store.all("reading")[-5:][::-1], "jobs": self.store.all("job")[-10:]}

    @API("ui_refresh_world", description="后台更新天气与资讯")
    async def ui_refresh_world(self) -> dict:
        self.require_life()
        return self.start_job("更新天气与资讯", self.world.refresh(self.now(), force=True))

    @API("ui_research", description="后台检索或阅读网页")
    async def ui_research(self, query: str = "", url: str = "") -> dict:
        self.require_life()
        if url:
            return self.start_job("阅读网页", self.world.read(url, self.now()))
        if query:
            return self.start_job("联网检索", self.world.search(query, self.now()))
        raise ValueError("请填写检索词或网页地址")

    @API("ui_space", description="地点、路线及移动进度")
    async def ui_space(self) -> dict:
        places = self.store.all("place")
        names = {p["id"]: p["name"] for p in places}
        current = self.engine.current(self.now())
        route = current.get("route") if current else None
        return {"location": self.engine.state()["location"], "places": [{**p, "outdoor_label": "室外" if p["outdoor"] else "室内"} for p in places],
                "routes": [{**r, "origin_name": names[r["origin"]], "destination_name": names[r["destination"]]} for r in self.store.all("route")],
                "journey": self.engine.state().get("journey") or {"location": self.engine.state()["location"], "next": "", "progress": 100, "mode": "停留", "destination": ""},
                "legs": route["legs"] if route else []}

    @API("ui_save_place", description="添加或编辑地点")
    async def ui_save_place(self, name: str, outdoor: bool = False, description: str = "", place_id: str = "") -> dict:
        return self.spatial.save_place(name, outdoor, description, place_id)

    @API("ui_save_route", description="添加或更新双向路线")
    async def ui_save_route(self, origin: str, destination: str, minutes: int, mode: str = "步行") -> dict:
        return self.spatial.save_route(origin, destination, minutes, mode)

    @API("ui_move", description="开始移动到指定地点")
    async def ui_move(self, destination: str) -> dict:
        self.require_life()
        return self.spatial.move(destination, "", self.now())

    @API("ui_dreams", description="梦境档案与睡眠记录")
    async def ui_dreams(self) -> dict:
        dreams = self.store.all("dream")[-100:][::-1]
        return {"dreams": [{"title": "尚未生成", "story": "", "feeling": "", **d, "source_label": "梦境"} for d in dreams],
                "sleeps": [{**plan_row(p), "hours": round(p["elapsed_seconds"] / 3600, 1)} for p in self.store.all("plan") if p["kind"] == "sleep"][-100:][::-1],
                "jobs": self.store.all("job")[-10:]}

    @API("ui_generate_dream", description="为所选睡眠生成梦境")
    async def ui_generate_dream(self, plan_id: str) -> dict:
        self.require_life()
        return self.start_job("生成梦境", self.dreams.generate(plan_id, self.now()))

    @API("ui_habits", description="已学习习惯与长期目标")
    async def ui_habits(self) -> dict:
        return {"habits": [{**h, "kind_label": KIND[h["kind"]], "enabled_label": "参与规划" if h["enabled"] else "已忽略",
                            "active_label": "近期习惯" if h["active"] else "近期未出现", "evidence": "、".join(h["evidence_ids"])} for h in self.store.all("habit")],
                "goals": [{**g, "status_label": STATUS[g["status"]], "progress": round(g["completed_sessions"] / g["target_sessions"] * 100)} for g in self.store.all("goal")],
                "relations": [{**r, "history_text": json.dumps(r["history"], ensure_ascii=False)} for r in self.store.all("relationship")][-100:]}

    @API("ui_habit_action", description="启用或忽略所选习惯")
    async def ui_habit_action(self, habit_id: str, enabled: bool) -> dict:
        habit = self.store.get("habit", habit_id)
        return self.rhythm.toggle(habit_id, enabled, habit["session_id"])

    @API("ui_learn_habits", description="立即分析已完成活动")
    async def ui_learn_habits(self) -> dict:
        self.require_life()
        self.rhythm.learn(self.now())
        return {"message": "习惯分析完成"}

    @API("ui_records", description="生活经历与修订历史")
    async def ui_records(self, query: str = "", session_id: str = "", page: int = 1) -> dict:
        records = [e for e in self.store.all("experience") if (not session_id or self.engine.visible(e, session_id)) and (not query or query in e["summary"])]
        records.sort(key=lambda e: e["occurred_at"], reverse=True)
        pages = max(1, math.ceil(len(records) / 30))
        page = min(pages, max(1, page))
        rows = [{**e, "source_label": SOURCE[e["source"]], "time_label": display_time(e["occurred_at"]),
                 "details_text": "\n".join(f"{k}：{v}" for k, v in e["details"].items()),
                 "history_text": json.dumps(e["history"], ensure_ascii=False, indent=2),
                 "memory_label": "已同步" if e["memory_revision"] == e["revision"] else ("保存在生活档案" if e["source"] == "simulated" else "尚未同步当前修订")}
                for e in records[(page - 1) * 30:page * 30]]
        return {"records": rows, "pagination": {"page": page, "pages": pages, "total": len(records)}, "jobs": self.store.all("job")[-10:]}

    @API("ui_revise_record", description="补充或更正所选经历")
    async def ui_revise_record(self, experience_id: str, evidence: str, detail_key: str = "", detail_value: str = "", summary: str = "") -> dict:
        self.require_life()
        old = self.store.get("experience", experience_id)
        if not detail_key and not summary:
            raise ValueError("请填写需要修改的摘要或细节")
        record = self.engine.revise_experience(experience_id, {detail_key: detail_value} if detail_key else {}, summary, evidence, old["session_id"], self.now())
        return record

    @API("ui_sync_record", description="后台同步所选经历到原生记忆")
    async def ui_sync_record(self, experience_id: str) -> dict:
        self.require_life()
        record = self.store.get("experience", experience_id)
        return self.start_job("同步原生记忆", self.sync_experience(record))

    @API("ui_create_goal", description="新增长期目标")
    async def ui_create_goal(self, title: str, target_sessions: int = 5, reason: str = "", session_id: str = "") -> dict:
        self.require_life()
        return self.engine.create_goal(GoalInput(title=title, target_sessions=target_sessions, reason=reason), session_id, self.now())

    @API("ui_cancel_goal", description="停止所选长期目标")
    async def ui_cancel_goal(self, goal_id: str) -> dict:
        self.require_life()
        goal = self.store.get("goal", goal_id)
        return self.engine.close_goal(goal_id, goal["session_id"])

    @API("ui_continuity", description="活动接续、阶段目标与待跟进事项")
    async def ui_continuity(self) -> dict:
        goals = self.store.all("goal")
        return {
            "reflections": self.store.all("reflection")[-50:][::-1],
            "preferences": [{**p, "evidence": "、".join(p["evidence_ids"])} for p in self.store.all("preference")],
            "continuations": [{**p, "details_text": json.dumps(p["details"], ensure_ascii=False)} for p in self.store.all("activity_memory")][-30:],
            "effects": [{**e, "status_label": {"active": "持续影响中", "expired": "已消退", "resolved": "已解决"}[e["status"]]} for e in self.store.all("effect")][-50:],
            "threads": [{**t, "status_label": "待跟进" if t["status"] == "open" else "已解决",
                         "request_label": "已向宿主请求跟进" if t["requested_at"] else "尚未请求"} for t in self.store.all("thread")][-100:],
            "goals": [{**g, "outline_status": g.get("outline_status", "待分解"), "outline_error": g.get("outline_error", ""), "status_label": STATUS[g["status"]]} for g in goals],
            "steps": [{**s, "goal_title": g["title"], "status_label": {"pending": "未开始", "active": "进行中", "completed": "已完成"}[s["status"]]} for g in goals for s in g.get("steps", [])],
            "jobs": self.store.all("job")[-10:],
        }

    @API("ui_reflect", description="后台生成或重试活动具体结果")
    async def ui_reflect(self, plan_id: str) -> dict:
        self.require_life()
        return self.start_job("整理活动结果", self.continuity.reflect(plan_id, self.now()))

    @API("ui_outline", description="后台分解或调整目标阶段")
    async def ui_outline(self, goal_id: str, revise: bool = False) -> dict:
        self.require_life()
        return self.start_job("规划目标阶段", self.continuity.outline(goal_id, self.now(), revise))

    @API("ui_replan", description="根据临时事件调整可变动安排")
    async def ui_replan(self, reason: str, unavailable_until: str) -> dict:
        self.require_life()
        if not reason.strip() or not unavailable_until:
            raise ValueError("请填写临时事件原因和结束时间")
        return {"adjustments": self.living.adjust(self.now(), reason, unavailable_until)}

    @API("ui_save_thread", description="保存需要接续的话题")
    async def ui_save_thread(self, title: str, context: str, due_at: str, session_id: str, thread_id: str = "") -> dict:
        self.require_life()
        if thread_id:
            session_id = self.store.get("thread", thread_id)["session_id"]
        return self.continuity.save_thread(title, context, session_id, due_at, self.now(), thread_id)

    @API("ui_resolve_thread", description="关闭已经解决的跟进事项")
    async def ui_resolve_thread(self, thread_id: str) -> dict:
        self.require_life()
        thread = self.store.get("thread", thread_id)
        return self.continuity.resolve_thread(thread_id, thread["session_id"], self.now())

    @API("ui_resolve_effect", description="结束所选事件的持续影响")
    async def ui_resolve_effect(self, effect_id: str) -> dict:
        self.require_life()
        effect = self.store.get("effect", effect_id)
        effect["status"] = "resolved"
        with self.store.db:
            self.store.put("effect", effect)
        return effect
