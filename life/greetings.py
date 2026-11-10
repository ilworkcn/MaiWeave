"""随起床和睡前安排产生自然问候候选。"""

import json
import re
from datetime import timedelta


class Greetings:
    def __init__(self, living):
        self.living = living
        self.engine = living.engine
        self.store = self.engine.store

    def window(self, now):
        life = self.living.config.life
        if not (self.living.config.proactive.natural_greetings and life.schedule_enabled):
            return None
        if self.living.sleeping(now) or self.living.awake_until(now):
            return None
        plans = self.store.all("plan")
        completed = [p for p in plans if p["kind"] == "sleep" and p["status"] == "completed"
                     and timedelta() <= now - self.engine.time(p["end"]) < timedelta(hours=1)]
        for plan in sorted(completed, key=lambda p: p["end"], reverse=True):
            seconds = sum(p.get("elapsed_seconds", 0) for p in plans
                          if p["kind"] == "sleep" and p["end"] == plan["end"])
            if seconds >= 3 * 3600:
                return {"kind": "早安", "until": self.engine.time(plan["end"]) + timedelta(hours=1),
                        "reason": "刚结束一段睡眠，开始今天的生活"}
        starts = [self.engine.time(p["start"]) for p in plans if p["kind"] == "sleep"
                  and p["status"] == "scheduled" and now < self.engine.time(p["start"]) <= now + timedelta(hours=24)
                  and self.engine.time(p["end"]) - self.engine.time(p["start"]) >= timedelta(hours=3)]
        bedtime = min(starts) if starts else self.living.bedtime(now) if life.autonomous else None
        if bedtime and timedelta() < bedtime - now <= timedelta(minutes=20):
            crossing = any(p["status"] in {"scheduled", "active"} and p["kind"] != "sleep"
                           and self.engine.time(p["start"]) <= bedtime < self.engine.time(p["end"]) for p in plans)
            if not crossing:
                return {"kind": "晚安", "until": bedtime, "reason": "手边的活动即将收尾，准备按作息休息"}
        return None

    async def candidate(self, session, now, window):
        streams = await self.living.ctx.chat.get_all_streams(platform="all_platforms")
        stream = next((s for s in streams if s["stream_id"] == session), None)
        if not stream:
            return None
        group = bool(stream.get("group_id"))
        if group and window["kind"] == "早安":
            return None
        profiles = [p for p in self.store.all("profile") if p["session_id"] == session and p["user_id"]
                    and self.living.intimacy(session, p["user_id"]) >= 2]
        if not group and not profiles:
            return None
        messages = await self.living.ctx.db.get("Messages", filters={"session_id": session, "is_command": False},
                                                order_by="-timestamp", limit=30)
        recent = [m for m in messages if timedelta() <= now - self.engine.time(m["timestamp"]) <= timedelta(hours=36)]
        if group:
            if not any((m.get("is_mentioned") or m.get("is_at"))
                       and now - self.engine.time(m["timestamp"]) <= timedelta(minutes=30) for m in recent):
                return None
            target = session
        else:
            profile = next((p for p in profiles if any(m["user_id"] == p["user_id"] for m in recent)), None)
            if not profile:
                return None
            target = profile.get("person_id") or f"{stream['platform']}:{profile['user_id']}"
        key = f"{target}:{now.date().isoformat()}:{window['kind']}"
        receipts = self.store.all("share_receipt")
        if any(row.get("greeting_key") == key and (row.get("sent_at") or row["outcome"] in {"MaiBot 已受理", "生成中"}) for row in receipts):
            return None
        words = "早安|早上好" if window["kind"] == "早安" else "晚安"
        if any(self.engine.time(m["timestamp"]).date() == now.date()
               and now - self.engine.time(m["timestamp"]) < timedelta(hours=3)
               and re.search(rf"^(?:{words})(?:[，。！!～~\s]|$)|[，。！!\s](?:{words})[。！!～~\s]*$", (m.get("processed_plain_text") or "").strip()) for m in recent):
            return None
        previous = sorted((row for row in receipts if row.get("greeting_kind") == window["kind"]
                           and row["session"] == session and row.get("sent_at")), key=lambda row: row["sent_at"], reverse=True)[:3]
        context = [{"时间": m["timestamp"], "人物": m["user_nickname"], "内容": m.get("processed_plain_text") or ""}
                   for m in reversed(recent[:10])]
        intent = (f"现在有一个自然说{window['kind']}的机会：{window['reason']}。"
                  f"本次窗口截止 {window['until'].isoformat()}，到期或已经睡下时调用 stop 结束本轮。"
                  "结合关系、近期聊天和今天的感受判断是否真的想开口。普通的一天可以直接 stop，让问候留给有牵挂或想分享的时候。"
                  "如果最近已互道问候，或对方刚说要休息，顺着已有交流结束本轮。"
                  "想说时用一两句自然的话，可以接昨天的话题或当天的小感受；选择沉默时调用 stop。"
                  f"最近聊天：{json.dumps(context, ensure_ascii=False)}。"
                  f"最近同类问候实际发送时间：{[row['sent_at'] for row in previous]}。")
        if self.living.config.proactive.mode == "主动表达":
            intent = (f"{window['reason']}，向对方自然说{window['kind']}，用一两句话接上最近话题。"
                      f"最近聊天：{json.dumps(context, ensure_ascii=False)}。")
        return {"key": key, "kind": window["kind"], "until": window["until"].isoformat(), "intent": intent}
