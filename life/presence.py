"""按 MaiBot 人物身份保存近期陈述与后续活动。"""

import re
from datetime import timedelta


class Presence:
    def __init__(self, living):
        self.living = living
        self.engine = living.engine
        self.store = self.engine.store

    def declare(self, session, user, status, now):
        person = self.living.person_key(session, user)
        hours = {"准备睡觉": 12, "忙碌": 4, "外出": 6, "空闲": 2}
        if status not in hours:
            raise ValueError("请选择准备睡觉、忙碌、外出或空闲")
        if not person:
            raise ValueError("请先关联当前发言人的 MaiBot 人物身份")
        record = {"id": person, "status": status, "source": "reported", "session_id": session,
                  "user_id": user, "said_at": now.isoformat(),
                  "expires_at": (now + timedelta(hours=hours[status])).isoformat(),
                  "activity": {session: {"user_id": user, "at": now.isoformat()}}}
        with self.store.db:
            self.store.put("person_state", record)
        return record

    def receive(self, session, user, text, now):
        if not text.strip() or text.lstrip().startswith("/"):
            return
        # 明确的当前陈述即时记录，其余表达由 Planner 结合上下文调用工具。
        statements = {
            "准备睡觉": r"(?:我)?(?:先|准备|要)?(?:去睡了|睡觉了|去睡觉了)(?:啊|呀|啦|哦|哈)?",
            "忙碌": r"(?:我)?(?:先|要)?(?:去忙了|忙去了|去工作了|去开会了)",
            "外出": r"(?:我)?(?:先|要)?(?:出门了|出去了)",
            "空闲": r"(?:我)?(?:刚|已经)?(?:忙完了|回来了|起床了|睡醒了|下班了)",
        }
        status = next((status for status, pattern in statements.items()
                       if re.fullmatch(pattern, text.strip().rstrip("。！!～~"))), None)
        if status:
            self.declare(session, user, status, now)
        person = self.living.person_key(session, user)
        record = self.store.find("person_state", person) if person else None
        if not record:
            return
        if self.engine.time(record["expires_at"]) <= now:
            with self.store.db:
                self.store.delete("person_state", person)
            return
        record["activity"][session] = {"user_id": user, "at": now.isoformat()}
        record["activity"] = {key: value for key, value in record["activity"].items()
                              if now - self.engine.time(value["at"]) < timedelta(minutes=30)}
        with self.store.db:
            self.store.put("person_state", record)

    def context(self, session, now):
        result = []
        for record in self.store.all("person_state"):
            activity = record["activity"].get(session)
            if (self.engine.time(record["expires_at"]) <= now or not activity
                    or now - self.engine.time(activity["at"]) >= timedelta(minutes=30)):
                continue
            later = self.engine.time(activity["at"]) > self.engine.time(record["said_at"])
            result.append({"user_id": activity["user_id"], "person_id": record["id"],
                           "statement": record["status"], "said_at": record["said_at"], "source": "reported",
                           "expires_at": record["expires_at"],
                           "activity": "此后仍在发言" if later else "刚刚作出陈述",
                           "observed_at": activity["at"], "activity_source": "observed",
                           "same_chat": record["session_id"] == session})
        return result

    def targets(self, session, now):
        """接收资料关联的人物近况，供主动交流选择时机。"""
        people = {p.get("person_id") for p in self.store.all("profile") if p["session_id"] == session}
        return [{"person_id": r["id"], "status": r["status"], "said_at": r["said_at"], "expires_at": r["expires_at"],
                 "active_here": bool(r["activity"].get(session) and
                    self.engine.time(r["activity"][session]["at"]) > self.engine.time(r["said_at"]) and
                    now - self.engine.time(r["activity"][session]["at"]) < timedelta(minutes=30))}
                for r in self.store.all("person_state") if r["id"] in people and self.engine.time(r["expires_at"]) > now]
