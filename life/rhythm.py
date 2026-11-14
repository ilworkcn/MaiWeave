"""重复日程和基于已完成活动的习惯学习。"""

import hashlib
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..core.engine import ACTIVE, uid
from ..core.models import PlanInput, RoutineInput


def matches(rule: dict, day: date) -> bool:
    anchor = date.fromisoformat(rule["anchor_date"])
    if day < anchor or (rule["until"] and day > date.fromisoformat(rule["until"])) or day.isoformat() in rule["exceptions"]:
        return False
    interval = rule["interval"]
    if rule["frequency"] == "daily":
        return (day - anchor).days % interval == 0
    if rule["frequency"] == "weekly":
        week_start = anchor - timedelta(days=anchor.weekday())
        return ((day - week_start).days // 7) % interval == 0 and day.weekday() in rule["weekdays"]
    months = (day.year - anchor.year) * 12 + day.month - anchor.month
    return months % interval == 0 and day.day == rule["month_day"]


class Rhythm:
    def __init__(self, engine, spatial, config):
        self.engine, self.spatial, self.config = engine, spatial, config

    def save(self, value: RoutineInput, session: str, now, rule_id: str = "", user_id=None) -> dict:
        anchor = date.fromisoformat(value.anchor_date)
        time.fromisoformat(value.start_time)
        if value.until and date.fromisoformat(value.until) < anchor:
            raise ValueError("结束日期请设为开始日期当天或之后")
        if value.frequency == "weekly" and (not value.weekdays or any(d not in range(7) for d in value.weekdays)):
            raise ValueError("每周规则需填写星期，周一为0，周日为6")
        old = self.engine.owned("routine", rule_id, session) if rule_id else None
        record = {"id": rule_id or uid(), **value.model_dump(), "session_id": session, "status": "active",
                  "request_user_id": user_id, "revision": old["revision"] + 1 if old else 1, "exceptions": old["exceptions"] if old else [], "updated_at": now.isoformat()}
        with self.engine.store.db:
            if old:
                self._cancel_future(old["id"], now)
            self.engine.store.put("routine", record)
        return record

    def _cancel_future(self, rule_id: str, now):
        for issue in self.engine.store.all("routine_issue"):
            if issue["routine_id"] == rule_id:
                self.engine.store.delete("routine_issue", issue["id"])
        for plan in self.engine.store.all("plan"):
            if (plan.get("routine_id") == rule_id and plan["status"] in {"scheduled", "blocked"}
                    and (plan["status"] == "blocked" or self.engine.time(plan["start"]) >= now)):
                self.engine.transition(plan["id"], "cancel", plan["session_id"], now)

    def cancel(self, rule_id: str, session: str, now) -> dict:
        rule = self.engine.owned("routine", rule_id, session)
        rule["status"] = "cancelled"
        with self.engine.store.db:
            self._cancel_future(rule_id, now)
            self.engine.store.put("routine", rule)
        self.spatial.reconcile(now)
        return rule

    def skip(self, rule_id: str, on_date: str, session: str, now) -> dict:
        date.fromisoformat(on_date)
        rule = self.engine.owned("routine", rule_id, session)
        if on_date not in rule["exceptions"]:
            rule["exceptions"].append(on_date)
        with self.engine.store.db:
            self.engine.store.put("routine", rule)
            for plan in self.engine.store.all("plan"):
                if plan.get("routine_id") == rule_id and plan.get("routine_date") == on_date and plan["status"] in ACTIVE | {"paused", "blocked"}:
                    self.engine.transition(plan["id"], "cancel", session, now)
        self.spatial.reconcile(now)
        return rule

    def materialize(self, now):
        zone = ZoneInfo(self.engine.timezone)
        for rule in self.engine.store.all("routine"):
            if rule["status"] != "active":
                continue
            for offset in range(self.config.rhythm.horizon_days):
                day = now.date() + timedelta(days=offset)
                if not matches(rule, day):
                    continue
                key = f"{rule['id']}:{rule['revision']}:{day.isoformat()}"
                if self.engine.store.find("occurrence", key):
                    continue
                start = datetime.combine(day, time.fromisoformat(rule["start_time"]), tzinfo=zone)
                if start < now:
                    continue
                value = PlanInput(title=rule["title"], start=start.isoformat(), end=(start + timedelta(minutes=rule["duration_minutes"])).isoformat(),
                                  kind=rule["kind"], location=rule["location"], participants=rule["participants"], simulation=rule["simulation"], motivation="重复日程")
                try:
                    plan = self.spatial.schedule(value, rule["session_id"], now, user_id=rule.get("request_user_id"))
                except ValueError as error:
                    with self.engine.store.db:
                        self.engine.store.put("routine_issue", {"id": key, "routine_id": rule["id"], "day": day.isoformat(), "title": rule["title"], "message": str(error)})
                    continue
                plan.update(routine_id=rule["id"], routine_date=day.isoformat())
                with self.engine.store.db:
                    self.engine.store.put("plan", plan)
                    self.engine.store.put("occurrence", {"id": key, "plan_id": plan["id"]})
                    self.engine.store.delete("routine_issue", key)

    def learn(self, now):
        if not self.config.rhythm.habits_enabled:
            return
        groups = defaultdict(list)
        for plan in self.engine.store.all("plan"):
            if plan["status"] != "completed" or plan["kind"] == "travel":
                continue
            when = self.engine.time(plan["start"]).astimezone(ZoneInfo(self.engine.timezone))
            if 0 <= (now.date() - when.date()).days < self.config.rhythm.learning_days:
                key = (plan["session_id"], plan["kind"], plan.get("activity_label", plan["title"]), plan["location"])
                groups[key].append(plan)
        live_ids = set()
        with self.engine.store.db:
            for key, plans in groups.items():
                times = [self.engine.time(p["start"]).astimezone(ZoneInfo(self.engine.timezone)) for p in plans]
                days = {t.date() for t in times}
                if len(days) < self.config.rhythm.habit_min_days:
                    continue
                record_id = hashlib.sha256("|".join(key).encode()).hexdigest()[:16]
                live_ids.add(record_id)
                old = self.engine.store.find("habit", record_id)
                angles = [(t.hour * 60 + t.minute) / 1440 * math.tau for t in times]
                angle = math.atan2(sum(math.sin(a) for a in angles), sum(math.cos(a) for a in angles)) % math.tau
                minute = round(angle / math.tau * 1440) % 1440
                concentration = math.hypot(sum(math.sin(a) for a in angles), sum(math.cos(a) for a in angles)) / len(angles)
                record = {"id": record_id, "session_id": key[0], "kind": key[1], "title": key[2], "location": key[3],
                          "minute": minute, "time": f"{minute // 60:02d}:{minute % 60:02d}",
                          "weekdays": sorted({t.weekday() for t in times}), "observed_days": len(days),
                          "strength": round(min(100, len(days) / self.config.rhythm.habit_min_days * 60) * concentration),
                          "duration_minutes": max(10, round(sum(p["elapsed_seconds"] for p in plans) / len(plans) / 60)),
                          "last_seen": max(times).isoformat(), "evidence_ids": [p["id"] for p in plans],
                          "enabled": old["enabled"] if old else True, "active": True}
                self.engine.store.put("habit", record)
            for old in self.engine.store.all("habit"):
                if old["id"] not in live_ids:
                    old["active"] = False
                    self.engine.store.put("habit", old)

    def candidate(self, now) -> dict | None:
        if not self.config.rhythm.habits_enabled or not self.config.rhythm.follow_habits:
            return None
        minute = now.hour * 60 + now.minute
        completed_today = {(p.get("activity_label", p["title"]), p["location"], p["session_id"]) for p in self.engine.store.all("plan")
                           if p["status"] in ACTIVE | {"completed"} and self.engine.time(p["start"]).date() == now.date()}
        for habit in sorted(self.engine.store.all("habit"), key=lambda h: h["strength"], reverse=True):
            preference = next((p for p in self.engine.store.all("preference") if p["activity"] == habit["title"] and p["session_id"] == habit["session_id"]), None)
            if preference and preference["samples"] >= 3 and preference["score"] <= -2:
                continue
            distance = abs(minute - habit["minute"])
            if (habit["active"] and habit["enabled"] and habit["strength"] >= 50 and min(distance, 1440 - distance) <= 45
                    and now.weekday() in habit["weekdays"] and (habit["title"], habit["location"], habit["session_id"]) not in completed_today):
                return habit
        return None

    def toggle(self, habit_id: str, enabled: bool, session: str) -> dict:
        habit = self.engine.owned("habit", habit_id, session)
        habit["enabled"] = enabled
        with self.engine.store.db:
            self.engine.store.put("habit", habit)
        return habit
