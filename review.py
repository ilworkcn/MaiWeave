"""资料修改先核对已有记录，冲突由用户决定。"""

import json
from uuid import uuid4
from typing import Literal
from pydantic import BaseModel
from .usage import generate


class Assessment(BaseModel):
    conflicts: list[str]
    relationship: str = "普通关系"
    level: Literal[0, 1, 2, 3] = 0
    reason: str = ""


class Reviews:
    def __init__(self, plugin):
        self.plugin = plugin

    def create(self, action, payload, session=""):
        if action not in {"profile", "anniversary", "delete_profile", "delete_anniversary", "edit_person", "sync"}:
            raise ValueError("请选择资料、纪念日、人物画像或经历同步操作")
        record = {"id": uuid4().hex, "action": action, "payload": payload, "session_id": session,
                  "status": "检查中", "phase": "正在读取经历与相关原生记忆…" if action == "sync" else "正在读取已有资料与原生画像…", "conflicts": [], "error": "", "created_at": self.plugin.now().isoformat()}
        with self.plugin.store.db:
            self.plugin.store.put("review", record)
        return record

    async def submit(self, action, payload, session=""):
        record = self.create(action, payload, session)
        await self.check(record["id"])
        return self.plugin.store.get("review", record["id"])

    async def check(self, record_id):
        p = self.plugin
        record = p.store.get("review", record_id)
        payload = record["payload"]
        try:
            profile_id = payload.get("profile_id", "")
            if payload.get("anniversary_id"):
                profile_id = p.store.get("anniversary", payload["anniversary_id"])["profile_id"]
            profile = p.store.get("profile", profile_id) if profile_id else {}
            person_id = payload.get("person_id") or profile.get("person_id", "")
            if not profile and person_id:
                profile = next((row for row in p.store.all("profile") if row.get("person_id") == person_id
                                and row["session_id"] == payload.get("session_id", record["session_id"])), {})
            identity = payload.get("profile_seed") or profile or payload
            if not person_id and identity.get("user_id") and p.config.memory.enabled:
                streams = await p.ctx.chat.get_all_streams(platform="all_platforms")
                stream = next(row for row in streams if row["stream_id"] == identity.get("session_id", record["session_id"]))
                candidate = await p.ctx.person.get_id(platform=stream["platform"], user_id=identity["user_id"])
                people = await p.memory.people(person_id=candidate)
                if people["items"]:
                    person_id = candidate
                    identity["person_id"] = candidate
                    if record["action"] == "profile":
                        payload["person_id"] = candidate
            native = await p.ui_memory_person(person_id) if person_id else {}
            if record["action"] == "profile":
                payload["name"] = native.get("name") or payload.get("name") or profile.get("name", "")
                if payload.get("intimacy_mode", "auto") == "auto":
                    payload.pop("intimacy", None)
                    payload.pop("relationship", None)
            related_ids = {row["id"] for row in p.store.all("profile") if row["id"] == profile_id or (person_id and row.get("person_id") == person_id)}
            dates = [d for d in p.store.all("anniversary") if d["profile_id"] in related_ids]
            if record["action"] == "sync":
                event = p.store.get("experience", payload["experience_id"])
                native = await p.memory.query(event["summary"], event["session_id"])
                if event["memory_id"]:
                    native = {"相关记忆": native, "当前事实": await p.memory.fact(event["memory_id"])}
                record["proposed"] = event
                payload = event
            record["person_id"] = person_id
            record["existing"] = {"人物": profile, "纪念日": dates, "原生记忆": native}
            prompt = (
                "核对拟写入的个人资料、日期或原生记忆与已有记录是否矛盾。输出 JSON：conflicts（具体冲突说明数组，兼容的补充填空数组）、"
                "relationship（当前用户与角色的关系称呼）、level（0普通、1熟悉、2亲近、3亲密）、reason（判断依据）。"
                "intimacy_mode 为 auto 时，根据已有画像与互动推断关系和等级；新增档案的姓名沿用所选画像。"
                "比较同一人物、同一事件的日期、身份和事实。明确改正已有值也列出差异，交由用户确认。"
                "区分公历农历、不同纪念日、转述和用户自己的信息。关系结合主体、时间和语境理解，"
                "现任恋人可为亲密关系；前任、别人的恋人和引用的话按实际主体判断。删除有内容的记录时列出将删除的内容。"
                f"操作：{record['action']}；已有：{json.dumps(record['existing'], ensure_ascii=False)}；新内容：{json.dumps(payload, ensure_ascii=False)}"
            )
            record["phase"] = "模型正在核对经历与原生记忆是否冲突…" if record["action"] == "sync" else "模型正在核对资料冲突与亲密关系…"
            with p.store.db:
                p.store.put("review", record)
            result = await generate(p.ctx, p.store, p.config, "资料核对", prompt, Assessment)
            record.update(conflicts=result.conflicts, relationship=result.relationship, level=result.level, reason=result.reason,
                          status="待确认" if result.conflicts else "待写入")
            with p.store.db:
                p.store.put("review", record)
            if not result.conflicts:
                await self.apply(record)
        except Exception as error:
            record.update(status="失败", error=str(error))
            with p.store.db:
                p.store.put("review", record)
            raise

    async def decide(self, record_id, accept):
        record = self.plugin.store.get("review", record_id)
        if record["status"] != "待确认":
            raise ValueError("请选择等待确认的修改")
        if accept:
            record["forced"] = True
            await self.apply(record)
        else:
            record["status"] = "已取消"
            with self.plugin.store.db:
                self.plugin.store.put("review", record)
        return record

    async def apply(self, record):
        p, args = self.plugin, dict(record["payload"])
        action = record["action"]
        record["status"] = "写入中"
        with p.store.db:
            p.store.put("review", record)
        try:
            methods = {"profile": p.ui_save_profile, "anniversary": p.ui_save_anniversary,
                       "delete_profile": p.ui_delete_profile, "delete_anniversary": p.ui_delete_anniversary,
                       "edit_person": p.ui_edit_memory_person}
            if action == "sync":
                current = p.store.get("experience", args["experience_id"])
                if current["revision"] != record["proposed"]["revision"]:
                    record.pop("forced", None)
                    with p.store.db:
                        p.store.put("review", record)
                    await self.check(record["id"])
                    record.update(p.store.get("review", record["id"]))
                    return
                result = await p.apply_memory_sync(current)
            else:
                seed = args.pop("profile_seed", None)
                if seed:
                    profile = await p.ui_save_profile(**seed, intimacy=record["level"], relationship=record["relationship"])
                    args["profile_id"] = profile["id"]
                result = await methods[action](**args)
            if action == "profile":
                if args.get("intimacy_mode", "auto") == "auto":
                    result.update(intimacy=record["level"], relationship=record["relationship"], relationship_reason=record["reason"])
                with p.store.db:
                    p.store.put("profile", result)
            if action in {"edit_person", "anniversary"}:
                with p.store.db:
                    for profile in p.store.all("profile"):
                        linked = (record["person_id"] and profile.get("person_id") == record["person_id"]) or profile["id"] == args.get("profile_id")
                        if linked and profile.get("intimacy_mode", "auto") == "auto":
                            if record["person_id"]:
                                profile["person_id"] = record["person_id"]
                            profile.update(intimacy=record["level"], relationship=record["relationship"], relationship_reason=record["reason"])
                            p.store.put("profile", profile)
            record.update(status="已保存", result=result)
        except Exception as error:
            record.update(status="失败", error=str(error))
            raise
        finally:
            with p.store.db:
                p.store.put("review", record)
