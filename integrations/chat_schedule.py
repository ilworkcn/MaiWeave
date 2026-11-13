"""从原生聊天记录接续角色已确定的临时安排。"""

import hashlib
import json
from datetime import timedelta

from ..core.engine import ACTIVE
from ..core.models import ChatArrangement, ChatArrangements
from .usage import generate


class ChatSchedule:
    def __init__(self, plugin):
        self.p = plugin

    def mark(self, session, now, user_id="", bot_id=""):
        store = self.p.store
        record = store.find("runtime", "chat_schedule:" + session) or {
            "id": "chat_schedule:" + session, "session_id": session, "checked": "", "bot_id": "", "user_id": ""}
        record.update(pending=True, updated_at=now.isoformat())
        if user_id:
            record["user_id"] = str(user_id)
        if bot_id:
            record["bot_id"] = str(bot_id)
        with store.db:
            store.put("runtime", record)

    def active(self, now, session=None):
        engine = self.p.engine
        return [r for r in self.p.store.all("chat_arrangement") if r.get("type") == "chat_schedule"
                and r["status"] == "active" and engine.time(r["expires_at"]) > now
                and (session is None or engine.visible(r, session))]

    def context(self, now, session=None):
        return [{k: r[k] for k in ("id", "title", "starts_at", "expires_at", "blocked_kinds")}
                for r in sorted(self.active(now, session), key=lambda r: r["starts_at"])[:12]]

    def save(self, value, session, user, now):
        p = self.p
        start, end = p.engine.time(value.start), p.engine.time(value.end)
        if end <= start or end <= now:
            raise ValueError("请填写尚未结束的有效时间段")
        old = p.engine.owned("chat_arrangement", value.record_id, session) if value.record_id else None
        if old and old.get("type") != "chat_schedule":
            raise ValueError("请选择聊天中确定的安排")
        updates = {change.plan_id: change.patch for change in value.plan_updates}
        updating = [p.engine.owned("plan", key, session) for key in updates]
        targets = [p.engine.owned("plan", key, session) for key in value.cancel_plan_ids]
        projected = [{**r, **(updates[r["id"]].model_dump(exclude_unset=True) if r["id"] in updates else {})}
                     for r in p.store.all("plan")]
        targets += [r for r in projected if p.engine.visible(r, session)
                    and r["kind"] in value.blocked_kinds and r["status"] in ACTIVE
                    and p.engine.time(r["start"]) < end and p.engine.time(r["end"]) > start]
        if value.source == "reported":
            p.living.check_influence(session, user, targets + updating)
        key = old["id"] if old else "chat:" + hashlib.sha256(
            f"{session}|{value.title}|{start.isoformat()}|{end.isoformat()}".encode()).hexdigest()[:20]
        record = {"id": key, "type": "chat_schedule", "session_id": session, "request_user_id": user,
                  "title": value.title, "reason": value.evidence, "source": value.source,
                  "starts_at": start.isoformat(), "expires_at": end.isoformat(), "blocked_kinds": value.blocked_kinds,
                  "status": "active", "updated_at": now.isoformat()}
        with p.store.db:
            for change in value.plan_updates:
                p.spatial.change(change.plan_id, change.patch, session, now)
            for target in {r["id"]: r for r in targets}.values():
                if target["status"] in ACTIVE:
                    p.spatial.transition(target["id"], "cancel", session, now)
            p.store.put("chat_arrangement", record)
        return record

    def reconcile(self, now):
        """重复日程生成后，取消临时约定时间内的对应活动，保留后续规则。"""
        p = self.p
        for row in self.active(now):
            start, end = p.engine.time(row["starts_at"]), p.engine.time(row["expires_at"])
            for plan in p.store.all("plan"):
                if (plan["status"] in ACTIVE and plan["kind"] in row["blocked_kinds"]
                        and p.engine.visible(plan, row["session_id"])
                        and p.engine.time(plan["start"]) < end and p.engine.time(plan["end"]) > start):
                    if row["source"] == "reported":
                        p.living.check_influence(row["session_id"], row["request_user_id"], [plan])
                    p.spatial.transition(plan["id"], "cancel", row["session_id"], now)

    async def process(self, now):
        p = self.p
        pending = [r for r in p.store.all("runtime") if r["id"].startswith("chat_schedule:") and r.get("pending")]
        for row in sorted(pending, key=lambda r: r["updated_at"])[:3]:
            if (now - p.engine.time(row["updated_at"])).total_seconds() < 5:
                continue
            session = row["session_id"]
            await p.chat_context.identify(session)
            history = await p.ctx.db.get("Messages", filters={"session_id": session, "is_command": False},
                                         order_by="-timestamp", limit=10)
            messages = []
            used = 0
            budget = min(4000, p.config.context.group_chars if p.chat_context.kinds.get(session) == "group" else p.config.context.private_chars)
            for item in history or []:
                text = (item.get("processed_plain_text") or "").strip()
                if not text or text.startswith("/"):
                    continue
                at = p.engine.time(str(item["timestamp"]))
                if at < now - timedelta(days=1):
                    continue
                entry = {"发言人ID": str(item.get("user_id", "")), "时间": at.isoformat(), "发言人": item.get("user_nickname", ""),
                         "角色本人": bool(row["bot_id"] and str(item.get("user_id")) == row["bot_id"]),
                         "内容": text[:500]}
                size = len(json.dumps(entry, ensure_ascii=False))
                if used + size > budget:
                    break
                messages.append(entry)
                used += size
            digest = hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()
            if messages and digest != row["checked"]:
                plans = [{k: r[k] for k in ("id", "title", "kind", "start", "end")}
                         for r in p.engine.plans(session) if r["status"] in ACTIVE and not r.get("activity_id")][:20]
                prompt = (
                    "提取聊天中已经确定、会影响角色本人日程的临时安排，输出JSON对象arrangements数组。"
                    "结合双方发言判断事情属于谁；用户自己的调休、请假记录在用户近况，arrangements填空数组。"
                    "只提取明确确定的安排；疑问、假设、愿望、引用和玩笑填空数组。角色本人实际说定的安排也提取。"
                    "字段title、start、end（带时区ISO时间）、speaker_id（依据那条发言的发言人ID）、evidence（原话依据）、source（用户陈述reported，角色实际发言observed）、"
                    "blocked_kinds（此时段明确取消的活动类别，如调休为work；仅有约定内容时填空数组）、"
                    "record_id（修改同一已有事项时填其ID，否则空字符串）、cancel_plan_ids（明确取消的已有日程ID数组）、"
                    "plan_updates（明确改期的已有日程，数组元素为plan_id和patch，patch仅填写更改的start/end/location/title等字段）。"
                    "明确将已有约定改到新时间时，用plan_updates同步日程；填写带时区的完整起止时间，出行由日程模块调整。"
                    "只处理本次聊天确定的变化，已有且一致的事项省略。取消当天工作保留其他日期的重复规则。"
                    "时间根据发言日期解释；下午可按12至18点，全天按当天0点至次日0点，明确时间优先。"
                    "约定变更用原record_id更新内容及时间，取消限制时blocked_kinds填空数组。"
                    f"现在：{now.isoformat()}；已有事项：{json.dumps(self.context(now, session), ensure_ascii=False)}；"
                    f"可调整日程：{json.dumps(plans, ensure_ascii=False)}；最近聊天（按先后）：{json.dumps(list(reversed(messages)), ensure_ascii=False)}"
                )
                def validate(result):
                    for value in result.arrangements:
                        speakers = [m for m in messages if m["发言人ID"] == value.speaker_id]
                        if not speakers:
                            raise ValueError("speaker_id请填写提供的聊天记录中的发言人ID")
                        expected = "observed" if speakers[0]["角色本人"] else "reported"
                        if value.source != expected:
                            raise ValueError(f"该发言人的source应为{expected}")
                result = await generate(p.ctx, p.store, p.config, "聊天安排", prompt, ChatArrangements, validate)
                for value in result.arrangements:
                    self.save(value, session, value.speaker_id, now)
            current = p.store.get("runtime", row["id"])
            current.update(checked=digest, pending=current["updated_at"] != row["updated_at"])
            with p.store.db:
                p.store.put("runtime", current)
        self.reconcile(now)
