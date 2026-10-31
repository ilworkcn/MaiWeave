"""按生活日期整理角色的一天，保存日记与写作依据。"""

import json
from datetime import date, datetime, time, timedelta

from .usage import generate


class Diary:
    def __init__(self, engine, context, config):
        self.engine, self.ctx, self.config = engine, context, config

    def material(self, day, now):
        start = datetime.combine(day, time.min, tzinfo=now.tzinfo)
        end = start + timedelta(days=1)
        store = self.engine.store
        plans = [p for p in store.all("plan") if self.engine.time(p["start"]) < min(end, now) and self.engine.time(p["end"]) > start]
        experiences = [e for e in store.all("experience") if start <= self.engine.time(e["occurred_at"]) < min(end, now)]
        states = [s for s in store.all("state_history") if start <= self.engine.time(s["at"]) < min(end, now)]
        dreams = [d for d in store.all("dream") if d["status"] == "已生成" and start <= self.engine.time(d["sleep_end"]) < min(end, now)]
        return {"日期": day.isoformat(), "记录截止": min(end, now).isoformat(), "活动": plans, "经历": experiences,
                "状态变化": [{k: s[k] for k in ("at", "energy", "hunger", "mood", "stress") if k in s} for s in states], "梦境": dreams}

    async def process(self, now):
        if not self.config.diary.enabled or now.hour < self.config.diary.write_hour:
            return
        day = now.date() - timedelta(days=1)
        old = self.engine.store.find("diary", day.isoformat())
        if old and (old["status"] != "已生成" or old["complete"]):
            return
        material = self.material(day, now)
        if any(material[key] for key in ("活动", "经历", "状态变化", "梦境")):
            await self.write(day.isoformat(), now)

    async def write(self, day_text, now):
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
                  "created_at": now.isoformat(), "complete": day < now.date(), "material": material}
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
        except Exception as error:
            record.update(status="生成失败", error=str(error))
            raise
        finally:
            with store.db:
                store.put("diary", record)
        return record
