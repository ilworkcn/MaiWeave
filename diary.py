"""按生活日期整理角色的一天，保存日记与写作依据。"""

import asyncio
import json
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel
from .models import PlanInput
from .usage import generate


class DiarySlot(BaseModel):
    start: str
    end: str
    reason: str


class Diary:
    def __init__(self, engine, context, config):
        self.engine, self.ctx, self.config = engine, context, config
        with engine.store.db:
            for record in engine.store.all("diary"):
                if record["status"] == "生成中":
                    record.update(status="生成失败", error="上次写作随插件停止而中断")
                    engine.store.put("diary", record)

    def material(self, day, now):
        start = datetime.combine(day, time.min, tzinfo=now.tzinfo)
        end = start + timedelta(days=1)
        store = self.engine.store
        plans = [p for p in store.all("plan") if self.engine.time(p["start"]) < min(end, now) and self.engine.time(p["end"]) > start]
        experiences = [e for e in store.all("experience") if start <= self.engine.time(e["occurred_at"]) < min(end, now)]
        states = [s for s in store.all("state_history") if start <= self.engine.time(s["at"]) < min(end, now)]
        dreams = [d for d in store.all("dream") if d["status"] == "已生成" and start <= self.engine.time(d["sleep_end"]) < min(end, now)]
        return {"阅读笔记": [n for n in store.all("book_note") if n["status"] == "已完成" and start <= self.engine.time(n["finished_at"]) < min(end, now)], "日期": day.isoformat(), "记录截止": min(end, now).isoformat(), "活动": plans, "经历": experiences,
                "状态变化": [{k: s[k] for k in ("at", "energy", "hunger", "mood", "stress") if k in s} for s in states], "梦境": dreams}

    async def arrange(self, now, spatial):
        if not self.config.diary.enabled:
            return
        store = self.engine.store
        day = now.date().isoformat()
        if any(p["kind"] == "diary" and (p.get("diary_day") or p["start"][:10]) == day
               and p["status"] != "cancelled" for p in store.all("plan")):
            return
        prompt = (
            "为角色安排今天写生活日记的时间窗口，结合当前时间、作息和已有日程，选择未来空闲的10到20分钟。"
            "临近午夜时可安排到次日。输出 JSON：start、end（带时区的ISO时间）、reason（选择这个时间的理由）。"
            f"现在：{now.isoformat()}；睡眠时间：{self.config.life.sleep_hour}至{self.config.life.wake_hour}；"
            f"遵循作息：{self.config.life.schedule_enabled}；已有日程：{json.dumps([p for p in store.all('plan') if p['status'] in {'scheduled', 'active'}], ensure_ascii=False)}"
        )
        def validate(slot):
            start, end = self.engine.time(slot.start), self.engine.time(slot.end)
            if start < now or not 10 <= (end - start).total_seconds() / 60 <= 20:
                raise ValueError("日记时间应为未来的10到20分钟")
            if start.date() > now.date() + timedelta(days=1):
                raise ValueError("请安排在今天或次日")
            conflicts = [p for p in store.all("plan") if p["status"] in {"scheduled", "active"}
                         and self.engine.time(p["start"]) < end and self.engine.time(p["end"]) > start]
            if conflicts:
                raise ValueError("请避开这些已有安排：" + json.dumps(
                    [{k: p[k] for k in ("title", "start", "end")} for p in conflicts], ensure_ascii=False))

        slot = await generate(self.ctx, store, self.config, "日记安排", prompt, DiarySlot, validate=validate)
        value = PlanInput(title="写生活日记", kind="diary", start=slot.start, end=slot.end,
                          diary_day=now.date().isoformat(), motivation=slot.reason, flexible=True)
        return spatial.schedule(value, "", now, autonomous=True)

    async def process(self, now):
        for plan in self.engine.store.all("plan"):
            if plan["kind"] != "diary" or plan["status"] != "active":
                continue
            day = plan.get("diary_day") or self.engine.time(plan["start"]).date().isoformat()
            old = self.engine.store.find("diary", day)
            if old and old.get("plan_id") == plan["id"]:
                if old["status"] in {"已生成", "生成中"}:
                    continue
                if old.get("retry_at") and self.engine.time(old["retry_at"]) > now:
                    continue
            await self.write(day, now, plan["id"])

    async def write(self, day_text, now, plan_id=""):
        day = date.fromisoformat(day_text) if day_text else now.date()
        if day > now.date():
            raise ValueError("请选择今天或过去的日期")
        store = self.engine.store
        old = store.find("diary", day.isoformat())
        if old and old["status"] == "生成中":
            return old
        material = self.material(day, now)
        if not any(material[key] for key in ("活动", "经历", "状态变化", "梦境")):
            raise ValueError("这一天还没有生活记录，积累活动或经历后即可写日记")
        record = {"id": day.isoformat(), "day": day.isoformat(), "title": day.isoformat() + " 的一天",
                  "content": old.get("content", "") if old else "", "status": "生成中", "error": "",
                  "plan_id": plan_id, "created_at": now.isoformat(), "complete": day < now.date(), "material": material}
        with store.db:
            store.put("diary", record)
        try:
            personality = await self.ctx.config.get("personality")
            prompt = (
                "请以角色自己的第一人称写今天的生活日记，直接返回中文正文，约300到600字。"
                "按一天的节奏讲讲做了什么、感受如何、作品做到哪里，以及还惦记的事。平淡的一天可以简短些。"
                "叙述以提供的活动和经历为依据：completed 是完成的活动，active 是进行中的活动，"
                "scheduled 是计划，cancelled 是取消，awaiting_confirmation 是等待确认完成情况。"
                "simulated 用角色世界的视角讲述，reported 是用户讲述，observed 是实际互动；梦境以梦见来表达。"
                "涉及对方的行动和约定，沿用记录里的信息。感受可以自然表达，具体过程和进度沿用已有细节。"
                "日记写到记录截止时间为止，尚在进行的事情按当前进展叙述。"
                f"角色人格：{json.dumps(personality, ensure_ascii=False)}；生活背景：{self.config.life.background}；"
                f"生活记录：{json.dumps(material, ensure_ascii=False)}"
            )
            result = await generate(self.ctx, store, self.config, "生活日记", prompt)
            if not result["success"]:
                raise RuntimeError(result.get("error") or "日记模型调用失败")
            content = result["response"].strip()
            if not content:
                raise ValueError("模型返回了空日记，请重新生成")
            record.update(content=content, status="已生成")
        except asyncio.CancelledError:
            record.update(status="生成失败", error="写作任务已中断", retry_at=(now + timedelta(minutes=5)).isoformat())
            raise
        except Exception as error:
            record.update(status="生成失败", error=str(error), retry_at=(now + timedelta(minutes=5)).isoformat())
            raise
        finally:
            with store.db:
                store.put("diary", record)
        return record
