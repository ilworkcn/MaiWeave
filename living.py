"""自主活动与主动交流，复用宿主模型和发言机制。"""

import json
from datetime import datetime, timedelta

from .config import MaiWeaveConfig
from .engine import LifeEngine
from .models import ActivityChoice, PlanInput
from .anniversaries import Anniversaries


def in_hours(hour: int, start: int, end: int) -> bool:
    return start <= hour < end if start < end else hour >= start or hour < end


class Living:
    def __init__(self, engine: LifeEngine, context, config: MaiWeaveConfig, spatial, rhythm, world, continuity):
        self.engine = engine
        self.ctx = context
        self.config = config
        self.spatial, self.rhythm, self.world = spatial, rhythm, world
        self.continuity = continuity

    def awake_until(self, now):
        record = self.engine.store.find("runtime", "awakening")
        return self.engine.time(record["until"]) if record and self.engine.time(record["until"]) > now else None

    def apply_schedule(self, now):
        """切换全天候模式时撤销自主睡眠，保留用户明确安排的日程。"""
        if not self.config.life.enabled or self.config.life.schedule_enabled:
            return
        self.engine.advance(now)
        for plan in self.engine.store.all("plan"):
            if plan["autonomous"] and plan["kind"] == "sleep" and plan["status"] in {"scheduled", "active", "paused", "blocked"}:
                self.spatial.transition(plan["id"], "cancel", plan["session_id"], now)

    def wake_for_message(self, text, session, now):
        config = self.config.life
        if text.lstrip().startswith("/"):
            return False
        if not (config.enabled and config.schedule_enabled and config.wake_enabled):
            return False
        if not any(word.strip() and word.strip().casefold() in text.casefold() for word in config.wake_keywords):
            return False
        current = self.engine.current(now)
        if not (current and current["kind"] == "sleep") and not self.awake_until(now):
            return False
        if current and current["kind"] == "sleep":
            self.engine.advance(now)
            self.spatial.transition(current["id"], "cancel", current["session_id"], now)
        with self.engine.store.db:
            self.engine.store.put("runtime", {"id": "awakening", "until": (now + timedelta(minutes=config.wake_minutes)).isoformat(),
                                             "reason": "聊天命中陪伴唤醒关键词，已醒来，可以自然回应", "session_id": session})
        self.ctx.logger.info("MaiWeave 已由关键词唤醒，保持清醒 %s 分钟", config.wake_minutes)
        return True

    def adjust(self, now, reason="", unavailable_until=""):
        """环境和临时事件改变时，重排可变动安排并记录原因。"""
        if not self.config.continuity.adaptive_schedule:
            return []
        engine, store = self.engine, self.engine.store
        engine.advance(now)
        state = engine.state()
        if unavailable_until:
            if engine.time(unavailable_until) < now:
                raise ValueError("临时事件的结束时间请设为当前或未来的时间")
            with store.db:
                store.put("runtime", {"id": "busy", "until": engine.time(unavailable_until).isoformat(), "reason": reason})
        busy = store.find("runtime", "busy")
        if busy and engine.time(busy["until"]) > now:
            unavailable_until, reason = busy["until"], busy["reason"]
        weather = self.world.context(now)["weather"]
        bad_weather = weather and not weather["stale"] and not weather["outdoor_suitable"]
        until = engine.time(unavailable_until) if unavailable_until else now
        if until < now:
            raise ValueError("临时事件的结束时间请设为当前或未来的时间")
        changes = []
        for plan in store.all("plan"):
            if plan["status"] not in {"scheduled", "active"} or plan["kind"] == "travel":
                continue
            if engine.time(plan["start"]) > max(now + timedelta(hours=6), until):
                continue
            outdoor = bool(plan["location"] and self.spatial.place(plan["location"])["outdoor"])
            temporary = bool(unavailable_until and engine.time(plan["start"]) < until and engine.time(plan["end"]) > now)
            why = reason if temporary else (f"{weather['city']}当前{weather['label']}，调整室外活动" if bad_weather and outdoor else
                  ("精力不足，先恢复体力" if state["energy"] < 25 and plan["kind"] in {"work", "study", "leisure"} else
                   ("压力较高，暂缓高负荷活动" if state["stress"] >= 70 and plan["kind"] in {"work", "study"} else "")))
            if not why:
                with store.db:
                    store.delete("schedule_notice", plan["id"])
                continue
            if plan["participants"] or not plan.get("flexible", plan["autonomous"]):
                with store.db:
                    store.put("schedule_notice", {"id": plan["id"], "session_id": plan["session_id"], "title": plan["title"], "reason": why, "at": now.isoformat()})
                continue
            before = {k: plan[k] for k in ("title", "start", "end", "kind", "location")}
            duration = engine.time(plan["end"]) - max(now, engine.time(plan["start"]))
            start = max(now, engine.time(plan["start"]), until)
            if not temporary:
                plan.update(title="室内休息" if state["energy"] < 25 or state["stress"] >= 70 else "室内休闲",
                            kind="rest" if state["energy"] < 25 or state["stress"] >= 70 else "leisure",
                            location=self.spatial.place("home")["name"], goal_id="", goal_step_id="")
                start += timedelta(minutes=self.spatial.route(state["location"], plan["location"])["minutes"])
            plan.update(start=start.isoformat(), end=(start + duration).isoformat(), status="scheduled", motivation=why)
            plan["changes"].append({"at": now.isoformat(), "action": "动态调整", "before": before, "reason": why})
            change = {"id": f"{plan['id']}:{len(plan['changes'])}", "session_id": plan["session_id"], "plan_id": plan["id"],
                      "title": plan["title"], "reason": why, "before": before, "at": now.isoformat()}
            with store.db:
                store.put("plan", plan)
                store.put("adjustment", change)
                store.delete("schedule_notice", plan["id"])
            changes.append(change)
        self.spatial.reconcile(now)
        with store.db:
            for notice in store.all("schedule_notice"):
                plan = store.get("plan", notice["id"])
                if plan["status"] not in {"active", "scheduled", "blocked"}:
                    store.delete("schedule_notice", notice["id"])
        return changes

    async def choose_activity(self, now: datetime):
        engine, config = self.engine, self.config.life
        if config.schedule_enabled and self.awake_until(now):
            return
        if not config.autonomous or engine.current(now):
            return
        busy = engine.store.find("runtime", "busy")
        if busy and engine.time(busy["until"]) > now:
            return
        free = engine.free_minutes(now)
        if free < 10:
            return
        previous = engine.store.find("runtime", "planning")
        if previous and (now - engine.time(previous["at"])).total_seconds() < config.planning_interval_minutes * 60:
            return
        with engine.store.db:
            engine.store.put("runtime", {"id": "planning", "at": now.isoformat()})
        state = engine.state()
        session = ""
        goal_id = ""
        goal_step_id = ""
        habit = self.rhythm.candidate(now)
        if habit and habit["kind"] == "sleep" and not config.schedule_enabled:
            habit = None
        next_step = self.continuity.next_step()
        if config.schedule_enabled and in_hours(now.hour, config.sleep_hour, config.wake_hour):
            title, kind, location, motivation = "睡觉", "sleep", self.spatial.place("home")["name"], "按习惯休息，恢复精力"
            minutes = free
        elif state["hunger"] >= 65:
            title, kind, location, motivation, minutes = "吃饭", "meal", state["location"], "有些饿了", min(free, 30)
        elif state["energy"] <= 25:
            title, kind, location, motivation, minutes = "休息一会儿", "rest", state["location"], "精力不足", min(free, 45)
        elif habit:
            title, kind, location = habit["title"], habit["kind"], habit["location"]
            motivation, minutes, session = "在熟悉的时间延续已有习惯", min(free, habit["duration_minutes"]), habit["session_id"]
            continuation = next((r for r in reversed(engine.store.all("activity_memory"))
                                 if r["activity"] == title and r["session_id"] == session), None)
            if continuation:
                title = f"{title}：{continuation['subject']}" if continuation["subject"] else title
                motivation += "；" + continuation["next_action"]
        elif next_step:
            goal, step = next_step
            title, kind, location = step["title"], step["kind"], state["location"]
            minutes, session = min(free, step["minutes"]), goal["session_id"]
            goal_id, goal_step_id = goal["id"], step["id"]
            motivation = f"推进目标：{goal['title']}；阶段依据：{step['criterion']}；上次进展：{step['note']}"
        else:
            goals = [g for g in engine.store.all("goal") if g["status"] == "active"]
            personality = await self.ctx.config.get("personality")
            recent = [{"summary": e["summary"], "source": e["source"]} for e in engine.store.all("experience")[-8:]]
            environment = self.world.context(now)
            places = engine.store.all("place")
            prompt = (
                "为角色选择接下来独自进行的一件小活动，输出 JSON 对象。"
                "根据人格、兴趣、精力和长期目标，在角色的生活空间中安排活动。"
                "读书、工作、休闲和休息都可以。kind 从 study/work/leisure/rest 中选择，出行由地点路线自动安排。"
                "字段 title、kind、minutes（10到120）、location、motivation、goal_id（无关联填空字符串）。"
                f"当前时间：{now.isoformat()}；可用分钟：{free}；"
                f"人格：{json.dumps(personality, ensure_ascii=False)}；补充背景：{config.background}；"
                f"兴趣：{config.interests}；状态：{json.dumps(state, ensure_ascii=False)}；"
                f"目标：{json.dumps(goals, ensure_ascii=False)}；最近经历：{json.dumps(recent, ensure_ascii=False)}"
                f"；持续影响、偏好和接续内容：{json.dumps(self.continuity.context('', now), ensure_ascii=False)}"
                f"；可选地点名称：{json.dumps(places, ensure_ascii=False)}；"
                f"环境资料：{json.dumps(environment, ensure_ascii=False)}。这些资料供选择活动时参考，安排活动按本提示中的规则进行。"
                "根据仍在有效期内的天气选择活动，恶劣天气优先在室内休息或活动。"
            )
            result = await self.ctx.llm.generate(prompt, task_name=config.model_task)
            if not result["success"]:
                raise RuntimeError(result.get("error") or "自主规划模型调用失败")
            text = result["response"].strip()
            if text.startswith("```"):
                text = "\n".join(text.splitlines()[1:-1])
            choice = ActivityChoice.model_validate_json(text)
            if choice.kind not in {"study", "work", "leisure", "rest"}:
                raise ValueError("自主规划返回了未允许的活动类型")
            title, kind, location, motivation = choice.title, choice.kind, choice.location, choice.motivation
            minutes = min(choice.minutes, free)
            goal_id = choice.goal_id
            goal_step_id = choice.goal_step_id
            if goal_id:
                goal = engine.store.get("goal", goal_id)
                session = goal["session_id"]
        engine.advance(now)
        if engine.current(now):
            return
        travel_minutes = self.spatial.route(state["location"], location)["minutes"] if location else 0
        minutes = min(minutes, free - travel_minutes)
        if kind == "sleep":
            wake = now.replace(hour=config.wake_hour, minute=0, second=0, microsecond=0)
            if wake <= now:
                wake += timedelta(days=1)
            minutes = min(minutes, int((wake - now).total_seconds() / 60) - travel_minutes)
        if minutes < 1:
            return
        start = now + timedelta(minutes=travel_minutes)
        value = PlanInput(title=title, start=start.isoformat(), end=(start + timedelta(minutes=minutes)).isoformat(),
                          kind=kind, location=location, motivation=motivation, goal_id=goal_id, goal_step_id=goal_step_id)
        self.spatial.schedule(value, session, now, autonomous=True)

    async def share(self, now: datetime):
        config, engine = self.config.proactive, self.engine
        current = engine.current(now)
        if not config.enabled or not in_hours(now.hour, config.start_hour, config.end_hour):
            return
        if current and current["kind"] in {"sleep", "work", "study"}:
            return
        day = now.date().isoformat()
        for session in config.session_ids:
            sent = engine.store.find("sharing", session) or {"id": session, "day": day, "count": 0, "last_at": "", "experience_ids": []}
            if sent["day"] != day:
                sent.update(day=day, count=0)
            if sent["count"] >= config.daily_limit:
                continue
            if sent["last_at"] and (now - engine.time(sent["last_at"])).total_seconds() < config.cooldown_minutes * 60:
                continue
            candidates = [e for e in engine.experiences(session, limit=10)
                          if e["importance"] >= 2 and e["id"] not in sent["experience_ids"]
                          and 0 <= (now - engine.time(e["occurred_at"])).total_seconds() < 6 * 3600]
            notices = []
            for item in Anniversaries(engine.store).upcoming(now.date(), session):
                if item["enabled"] and item["days_left"] == 0:
                    notices.append((f"anniversary:{item['id']}:{item['next_date']}",
                                    f"今天是{item['person']}的{item['title']}，按用户配置的{item['calendar_label']}{item['date_label']}计算。"
                                    "结合已记录的资料自然表达祝福；资料中填写了出生年份时，可以提及年龄。", None))
            for plan in engine.plans(session):
                if plan["session_id"] != session or not plan["participants"]:
                    continue
                remaining = (engine.time(plan["start"]) - now).total_seconds() / 60
                if plan["status"] in {"scheduled", "active"} and 0 <= remaining <= self.config.continuity.reminder_minutes:
                    notices.append((f"reminder:{plan['id']}:{plan['start']}", f"共同安排即将开始：{plan['title']}，开始时间{plan['start']}。可以自然提醒对方即将开始的安排。", None))
                if plan["status"] == "awaiting_confirmation" and now - engine.time(plan["end"]) <= timedelta(hours=24):
                    notices.append((f"confirmation:{plan['id']}", f"之前约好的{plan['title']}已经过了计划时间，完成情况尚未确认。可以问问对方实际进展，再根据回复更新记录。", None))
            for item in engine.store.all("thread"):
                if item["session_id"] == session and item["status"] == "open" and not item["requested_at"] and engine.time(item["due_at"]) <= now:
                    notices.append((f"thread:{item['id']}", f"接续此前的话题：{item['title']}。已有上下文：{item['context']}。结合最近聊天跟进仍待解决的部分；已经解决时结束该话题。", item))
            for item in engine.store.all("schedule_issue"):
                if item["session_id"] == session:
                    notices.append((f"issue:{item['id']}:{item['reason']}", f"安排需要调整：{item['title']}。原因：{item['reason']}。说明当前待调整的状态。", None))
            notices = [n for n in notices if n[0] not in sent.get("notice_ids", [])]
            if not notices and not candidates:
                continue
            event = candidates[0] if candidates and not notices else None
            intent = notices[0][1] if notices else (
                "有一件刚发生的生活经历可能值得分享。结合当前对话判断是否适合开口，可以选择沉默。"
                "从角色自己的视角分享这次经历，给对方自由接话的空间。"
                f"经历来源={event['source']}；内容={event['summary']}；细节={json.dumps(event['details'], ensure_ascii=False)}"
            )
            result = await self.ctx.maisaka.proactive.trigger(session, intent, reason="MaiWeave 生活跟进", metadata={"maiweave_record_id": notices[0][0] if notices else event["id"]})
            if not result["success"]:
                raise RuntimeError(result.get("error") or "主动交流请求失败")
            sent.update(count=sent["count"] + 1, last_at=now.isoformat())
            if notices:
                sent.setdefault("notice_ids", []).append(notices[0][0])
                if notices[0][2]:
                    item = notices[0][2]
                    item["requested_at"] = now.isoformat()
                    with engine.store.db:
                        engine.store.put("thread", item)
            else:
                sent["experience_ids"].append(event["id"])
            with engine.store.db:
                engine.store.put("sharing", sent)
