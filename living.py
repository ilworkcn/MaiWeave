"""自主活动与主动交流，复用宿主模型和发言机制。"""

import json
from datetime import datetime, timedelta

from .config import MaiWeaveConfig
from .engine import LifeEngine
from .models import ActivityChoice, PlanInput


def in_hours(hour: int, start: int, end: int) -> bool:
    return start <= hour < end if start < end else hour >= start or hour < end


class Living:
    def __init__(self, engine: LifeEngine, context, config: MaiWeaveConfig):
        self.engine = engine
        self.ctx = context
        self.config = config

    async def choose_activity(self, now: datetime):
        engine, config = self.engine, self.config.life
        if not config.autonomous or engine.current(now):
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
        if in_hours(now.hour, config.sleep_hour, config.wake_hour):
            wake = now.replace(hour=config.wake_hour, minute=0, second=0, microsecond=0)
            if wake <= now:
                wake += timedelta(days=1)
            title, kind, location, motivation = "睡觉", "sleep", "住处", "按习惯休息，恢复精力"
            minutes = min(free, int((wake - now).total_seconds() / 60))
        elif state["hunger"] >= 65:
            title, kind, location, motivation, minutes = "吃饭", "meal", state["location"], "有些饿了", min(free, 30)
        elif state["energy"] <= 25:
            title, kind, location, motivation, minutes = "休息一会儿", "rest", state["location"], "精力不足", min(free, 45)
        else:
            goals = [g for g in engine.store.all("goal") if g["status"] == "active"]
            personality = await self.ctx.config.get("personality")
            recent = [{"summary": e["summary"], "source": e["source"]} for e in engine.store.all("experience")[-8:]]
            prompt = (
                "为虚拟角色选择接下来一件独自进行的小活动，只返回 JSON 对象。"
                "遵循人格、兴趣、精力和长期目标，不安排与真实用户见面，不创造现实经历。"
                "可选择休息，不必每天追求新鲜。kind 只能是 study/work/leisure/travel/rest。"
                "字段 title、kind、minutes（10到120）、location、motivation、goal_id（无关联填空字符串）。"
                f"当前时间：{now.isoformat()}；可用分钟：{free}；"
                f"人格：{json.dumps(personality, ensure_ascii=False)}；补充背景：{config.background}；"
                f"兴趣：{config.interests}；状态：{json.dumps(state, ensure_ascii=False)}；"
                f"目标：{json.dumps(goals, ensure_ascii=False)}；最近经历：{json.dumps(recent, ensure_ascii=False)}"
            )
            result = await self.ctx.llm.generate(prompt, task_name=config.model_task)
            if not result["success"]:
                raise RuntimeError(result.get("error") or "自主规划模型调用失败")
            text = result["response"].strip()
            if text.startswith("```"):
                text = "\n".join(text.splitlines()[1:-1])
            choice = ActivityChoice.model_validate_json(text)
            if choice.kind not in {"study", "work", "leisure", "travel", "rest"}:
                raise ValueError("自主规划返回了未允许的活动类型")
            title, kind, location, motivation = choice.title, choice.kind, choice.location, choice.motivation
            minutes = min(choice.minutes, free)
            goal_id = choice.goal_id
            if goal_id:
                goal = engine.store.get("goal", goal_id)
                session = goal["session_id"]
        engine.advance(now)
        if engine.current(now):
            return
        engine.create_plan(PlanInput(title=title, start=now.isoformat(), end=(now + timedelta(minutes=minutes)).isoformat(),
                                     kind=kind, location=location, motivation=motivation, goal_id=goal_id), session, now, autonomous=True)

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
            if not candidates:
                continue
            event = candidates[0]
            intent = (
                "有一件刚发生的生活经历可能值得分享。结合当前对话判断是否适合开口，可以选择沉默。"
                "不要索取回复，不说用户也参与过独自发生的事。"
                f"经历来源={event['source']}；内容={event['summary']}；细节={json.dumps(event['details'], ensure_ascii=False)}"
            )
            result = await self.ctx.maisaka.proactive.trigger(session, intent, reason="MaiWeave 生活经历", metadata={"maiweave_experience_id": event["id"]})
            if not result["success"]:
                raise RuntimeError(result.get("error") or "主动交流请求失败")
            sent.update(count=sent["count"] + 1, last_at=now.isoformat())
            sent["experience_ids"].append(event["id"])
            with engine.store.db:
                engine.store.put("sharing", sent)
