"""生成睡后梦境，保存梦境内容与醒后心情。"""

import json

from .models import DreamContent
from .usage import generate


class Dreams:
    def __init__(self, engine, context, config):
        self.engine, self.ctx, self.config = engine, context, config

    async def process(self, now):
        if not self.config.dreams.enabled:
            return
        for plan in self.engine.store.all("plan"):
            if (plan["status"] == "completed" and plan["kind"] == "sleep"
                    and plan["elapsed_seconds"] >= self.config.dreams.minimum_hours * 3600
                    and 0 <= (now - self.engine.time(plan["end"])).total_seconds() < 86400
                    and self.engine.store.find("dream", plan["id"]) is None):
                await self.generate(plan["id"], now)

    async def generate(self, plan_id: str, now) -> dict:
        plan = self.engine.store.get("plan", plan_id)
        if plan["kind"] != "sleep" or plan["status"] != "completed":
            raise ValueError("只能为已完成的睡眠生成梦境")
        old = self.engine.store.find("dream", plan_id)
        if old and old["status"] == "已生成":
            return old
        recent = [{"summary": e["summary"], "source": e["source"]} for e in self.engine.experiences(plan["session_id"], limit=5)]
        record = {"id": plan_id, "session_id": plan["session_id"], "sleep_end": plan["end"],
                  "created_at": now.isoformat(), "source": "dream", "status": "生成中", "error": ""}
        with self.engine.store.db:
            self.engine.store.put("dream", record)
        prompt = (
            "结合角色最近的经历写一段梦境，用睡梦中的视角讲述。"
            "内容围绕角色的感受和想象展开，涉及用户时沿用已有记录。输出 JSON 对象：title（简短标题）、story（150到400字）、"
            "feeling（醒后感受）、mood_delta（-3到3）。"
            f"当前状态：{json.dumps(self.engine.state(), ensure_ascii=False)}；"
            f"最近经历：{json.dumps(recent, ensure_ascii=False)}"
        )
        try:
            content = await generate(self.ctx, self.engine.store, self.config, "梦境", prompt, DreamContent)
            record.update(content.model_dump(), status="已生成")
            with self.engine.store.db:
                self.engine.store.put("dream", record)
                if self.config.dreams.mood_effect:
                    self.engine.affect(content.mood_delta, 0, "梦醒后的感受：" + content.feeling, now)
            return record
        except Exception as error:
            record.update(status="生成失败", error=str(error))
            with self.engine.store.db:
                self.engine.store.put("dream", record)
            raise
