"""自主活动与主动交流，复用 MaiBot 模型和发言机制。"""

import json
import re
import hashlib
from collections import deque
from datetime import datetime, timedelta

from ..config import MaiWeaveConfig
from ..core.engine import LifeEngine
from ..core.models import ActivityChoice, PlanInput
from .anniversaries import Anniversaries
from .greetings import Greetings
from .presence import Presence
from ..integrations.usage import generate
from ..integrations.share_receipts import ShareReceipts


def in_hours(hour: int, start: int, end: int) -> bool:
    return start <= hour < end if start < end else hour >= start or hour < end


class Living:
    def __init__(self, engine: LifeEngine, context, config: MaiWeaveConfig, spatial, rhythm, world, continuity):
        self.engine = engine
        self.ctx = context
        self.config = config
        self.spatial, self.rhythm, self.world = spatial, rhythm, world
        self.continuity = continuity
        self.share_decisions = deque(maxlen=10)
        self.receipts = ShareReceipts(engine.store, context)
        self.greetings = Greetings(self)
        self.presence = Presence(self)

    def person_key(self, session, user):
        identity = self.engine.store.find("identity", f"{session}:{user}")
        return identity["person_id"] if identity else ""

    def intimacy(self, session, user):
        identity = self.person_key(session, user)
        profiles = [p for p in self.engine.store.all("profile") if
                    (identity and p.get("person_id") == identity) or
                    ((not identity or not p.get("person_id")) and p["session_id"] == session and p["user_id"] == user)]
        profile = next((p for p in profiles if p.get("intimacy_mode") == "manual"), profiles[0] if profiles else {})
        level = profile.get("intimacy", 0)
        if profile.get("intimacy_mode") != "manual":
            sessions = {session, *(p["session_id"] for p in profiles)}
            sessions.update(i["session_id"] for i in self.engine.store.all("identity") if identity and i["person_id"] == identity)
            score = max((self.engine.relationship(stream, user)["closeness"] for stream in sessions), default=0)
            level = max(level, 3 if score >= 80 else 2 if score >= 50 else 1 if score >= 20 else 0)
        return level

    def sleeping(self, now):
        config = self.config.life
        if not (config.enabled and config.schedule_enabled) or self.awake_until(now):
            return False
        current = self.engine.current(now)
        if current:
            return current["kind"] == "sleep"
        wake = self.engine.store.find("runtime", "awakening")
        return bool(wake and wake.get("sleep_end") and self.engine.time(wake["sleep_end"]) > now)

    def resume_sleep(self, now):
        wake = self.engine.store.find("runtime", "awakening")
        if not wake or not wake.get("sleep_end") or self.awake_until(now) or self.engine.current(now):
            return
        end = min(self.engine.time(wake["sleep_end"]), now + timedelta(minutes=self.engine.free_minutes(now)))
        if end <= now:
            return
        self.spatial.schedule(PlanInput(title="继续睡觉", kind="sleep", start=now.isoformat(), end=end.isoformat(),
                                       location=wake["sleep_location"]), "", now, autonomous=True)

    def check_influence(self, session, user, plans):
        level = self.intimacy(session, user)
        permitted = {0: set(), 1: {"leisure", "rest"}, 2: {"leisure", "rest", "study", "work", "diary"}, 3: {"leisure", "rest", "study", "work", "diary", "sleep"}}[level]
        plans = [self.engine.store.get("plan", p["activity_id"]) if p.get("activity_id") else p for p in plans]
        if any(p["autonomous"] and p["kind"] not in permitted for p in plans):
            raise ValueError("当前关系优先在空闲时间安排陪伴，请选择活动结束后的时间")

    def awake_until(self, now):
        record = self.engine.store.find("runtime", "awakening")
        return self.engine.time(record["until"]) if record and self.engine.time(record["until"]) > now else None

    def apply_schedule(self, now):
        """切换全天候模式时撤销自主睡眠，保留用户明确安排的日程。"""
        if not self.config.life.enabled:
            return
        if self.config.life.schedule_enabled:
            self.resume_sleep(now)
            return
        self.engine.advance(now)
        for plan in self.engine.store.all("plan"):
            if plan["autonomous"] and plan["kind"] == "sleep" and plan["status"] in {"scheduled", "active", "paused", "blocked"}:
                self.spatial.transition(plan["id"], "cancel", plan["session_id"], now)

    def wake(self, session, now, reason, user_id=""):
        config = self.config.life
        if not (config.enabled and config.schedule_enabled and config.wake_enabled):
            return False
        current = self.engine.current(now)
        if not self.sleeping(now) and not self.awake_until(now):
            return False
        if self.intimacy(session, user_id) < 3:
            return False
        previous = self.engine.store.find("runtime", "awakening") or {}
        if current and current["kind"] == "sleep":
            previous = {"sleep_end": current["end"], "sleep_location": current["location"]}
            self.engine.advance(now)
            self.spatial.transition(current["id"], "cancel", current["session_id"], now)
        minutes = config.wake_minutes * (1 + self.intimacy(session, user_id) / 3)
        with self.engine.store.db:
            self.engine.store.put("runtime", {**previous, "id": "awakening", "until": (now + timedelta(minutes=minutes)).isoformat(),
                                             "reason": reason, "session_id": session, "user_id": user_id, "person_id": self.person_key(session, user_id)})
        self.ctx.logger.info("MaiWeave 已醒来，留出 %s 分钟聊天", minutes)
        return True

    def wake_for_message(self, text, session, now, user_id=""):
        config = self.config.life
        if text.lstrip().startswith("/"):
            return False
        if not (config.enabled and config.schedule_enabled and config.wake_enabled):
            return False
        active = self.engine.store.find("runtime", "awakening")
        same_person = active and ((self.person_key(session, user_id) and active.get("person_id") == self.person_key(session, user_id)) or
                                  (not active.get("person_id") and active["session_id"] == session and active.get("user_id", "") == user_id))
        if self.awake_until(now) and same_person:
            if any(word in text for word in ("晚安", "去睡吧", "我去睡了", "先聊到这里")):
                with self.engine.store.db:
                    self.engine.store.put("runtime", {**active, "until": now.isoformat(), "reason": "这段聊天已结束，恢复作息"})
                self.resume_sleep(now)
                return True
            if text.strip():
                return self.wake(session, now, "正在继续聊天", user_id)
        for clause in re.split(r"[。！？；\n]", text.casefold()):
            for word in config.wake_keywords:
                word = word.strip().casefold()
                if not word:
                    continue
                for match in re.finditer(re.escape(word), clause):
                    prefix = clause[:match.start()]
                    if re.search(r"(?:不再|并不|没有|没那么|不那么|不怎么|不太|不)$", prefix):
                        continue
                    if re.search(r"(?:现在|已经).*(?:好了|过去了|缓过来了)", clause):
                        continue
                    return self.wake(session, now, "聊天中表达了需要陪伴的心情", user_id)
        return False

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
                if plan.get("book_id") and bad_weather and outdoor and state["energy"] >= 25 and state["stress"] < 70:
                    plan["location"] = self.spatial.place("home")["name"]
                    note = store.find("book_note", plan["id"])
                    if note and note["status"] == "等待活动结束":
                        with store.db:
                            store.delete("book_note", plan["id"])
                else:
                    plan.update(title="室内休息" if state["energy"] < 25 or state["stress"] >= 70 else "室内休闲",
                                kind="rest" if state["energy"] < 25 or state["stress"] >= 70 else "leisure",
                                location=self.spatial.place("home")["name"], goal_id="", goal_step_id="", book_id="", continuation_id="")
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

    def bedtime(self, now):
        config = self.config.life
        if not config.schedule_enabled or in_hours(now.hour, config.sleep_hour, config.wake_hour):
            return None
        start = now.replace(hour=config.sleep_hour, minute=0, second=0, microsecond=0)
        return start if start > now else start + timedelta(days=1)

    def activity_balance(self, now):
        """汇总今天实际进行的活动，以及最近连续的生活节奏。"""
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        plans = sorted((p for p in self.engine.store.all("plan", omit=("changes",))
                        if p.get("elapsed_seconds", 0) > 0 and self.engine.time(p["start"]) <= now),
                       key=lambda p: self.engine.time(p["end"]))
        totals = {}
        recent = []
        for plan in plans:
            activity = "阅读" if plan.get("book_id") or self.reading_title(plan["title"]) else plan.get("activity_label") or {"sleep":"睡眠", "meal":"用餐", "work":"工作", "study":"学习", "leisure":"休闲", "travel":"出行", "rest":"休息", "social":"社交", "diary":"写日记"}[plan["kind"]]
            seconds = min(plan["elapsed_seconds"], max(0, (min(now, self.engine.time(plan["end"])) - max(start, self.engine.time(plan["start"]))).total_seconds()))
            if seconds:
                item = totals.setdefault(activity, {"activity": activity, "minutes": 0, "sessions": 0})
                item["minutes"] += seconds / 60
                item["sessions"] += 1
            if plan["status"] == "completed" and plan["kind"] != "travel":
                recent.append({"activity": activity, "title": plan["title"], "end": plan["end"],
                               "minutes": round(plan["elapsed_seconds"] / 60)})
        consecutive = 0
        for item in reversed(recent):
            if item["activity"] != recent[-1]["activity"]:
                break
            consecutive += 1
        return {"day": now.date().isoformat(), "totals": [{**r, "minutes": round(r["minutes"])} for r in totals.values()],
                "recent": recent[-8:], "consecutive": consecutive,
                "last_activity": recent[-1]["activity"] if recent else ""}

    async def choose_activity(self, now: datetime):
        engine, config = self.engine, self.config.life
        if not config.autonomous:
            return "自主安排已关闭"
        awake = self.awake_until(now) if config.schedule_enabled else None
        if awake:
            return f"陪伴中，保持清醒至 {awake:%H:%M}"
        current = engine.current(now)
        if current:
            return f"正在{current['title']}，预计 {engine.time(current['end']):%H:%M} 结束"
        busy = engine.store.find("runtime", "busy")
        if busy and engine.time(busy["until"]) > now:
            return f"临时有事，预计 {engine.time(busy['until']):%H:%M} 结束"
        free = engine.free_minutes(now)
        if free < 10:
            return f"距离下一项安排只剩 {free} 分钟"
        state = engine.state()
        signature = hashlib.sha256(json.dumps({
            "day": now.date().isoformat(), "sleep": config.schedule_enabled and in_hours(now.hour, config.sleep_hour, config.wake_hour),
            "body": [state["hunger"] >= 65, state["energy"] <= 25, state["stress"] >= 70],
            "plans": [(p["id"], p["status"], p["start"], p["end"], len(p["changes"])) for p in engine.store.all("plan")],
            "goals": engine.store.all("goal"), "interests": config.interests,
            "continuations": [(r["id"], r["updated_at"]) for r in engine.store.all("activity_memory")],
            "weather": self.world.context(now)["weather"],
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        previous = engine.store.find("runtime", "planning")
        if previous and previous.get("signature") == signature and (now - engine.time(previous["at"])).total_seconds() < config.planning_interval_minutes * 60:
            check_at = engine.time(previous["at"]) + timedelta(minutes=config.planning_interval_minutes)
            return f"生活状态未变，预计 {check_at:%H:%M} 再规划"
        with engine.store.db:
            engine.store.put("runtime", {"id": "planning", "at": now.isoformat(), "signature": signature})
        session = ""
        exploration = await self.world.exploration_status(now) if self.config.world.explore_enabled else {"ready": False}
        research_query, research_freshness = "", "current"
        search_ready = self.config.world.places_enabled and (await self.world.search_status())["ready"]
        goal_id = ""
        goal_step_id = ""
        continuation_id = ""
        place_query = ""
        book_id = ""
        books = [b for b in engine.store.all("book") if b["status"] != "已读完" and not b.get("archived")]
        current_book = engine.store.find("runtime", "current_book")
        books.sort(key=lambda b: b["id"] != (current_book or {}).get("book_id"))
        books = [{k: b[k] for k in ("id", "title", "author", "status", "chapter", "chapter_count")} for b in books[:10]]
        for book in books:
            notes = [n for n in engine.store.all("book_note") if n["book_id"] == book["id"] and n["status"] == "已完成"]
            if notes:
                note = max(notes, key=lambda n: n["finished_at"])
                book.update(thoughts=note["thoughts"][:300], question=note["question"][:200])
        habit = self.rhythm.candidate(now)
        if habit and habit["kind"] == "sleep" and not config.schedule_enabled:
            habit = None
        next_step = self.continuity.next_step()
        continuations = [r for r in engine.store.all("activity_memory") if r["next_action"]
                         and r.get("kind") in {"study", "work", "leisure", "rest", "meal", "social"}
                         and (not r.get("next_date") or r["next_date"] <= now.date().isoformat())]
        if config.schedule_enabled and in_hours(now.hour, config.sleep_hour, config.wake_hour):
            source = "日常作息"
            title, kind, location, motivation = "睡觉", "sleep", self.spatial.place("home")["name"], "按习惯休息，恢复精力"
            minutes = free
        elif self.bedtime(now) and (self.bedtime(now) - now).total_seconds() / 60 <= self.spatial.route(state["location"], self.spatial.place("home")["name"])["minutes"] + 10:
            source = "睡前收尾"
            title, kind, location, motivation, minutes = "回家准备休息", "rest", self.spatial.place("home")["name"], "收好手边的事，准备睡觉", free
        elif state["hunger"] >= 65:
            source = "身体需要 · 用餐"
            title, kind, location, motivation, minutes = "吃饭", "meal", state["location"], "有些饿了", min(free, 30)
            if self.config.world.places_enabled and state["location"] not in {self.spatial.place("home")["name"], self.spatial.place("kitchen")["name"]} and free >= 30:
                place_query = "餐厅"
        elif state["energy"] <= 25:
            source = "身体需要 · 休息"
            title, kind, location, motivation, minutes = "休息一会儿", "rest", state["location"], "精力不足", min(free, 45)
        else:
            source = "模型自主选择"
            balance = self.activity_balance(now)
            goals = [g for g in engine.store.all("goal") if g["status"] == "active"]
            completed = sorted((p for p in engine.store.all("plan") if p["status"] == "completed"),
                               key=lambda p: engine.time(p["end"]), reverse=True)
            window = now - timedelta(hours=2)
            reading_seconds = sum(min(p["elapsed_seconds"], (engine.time(p["end"]) - max(window, engine.time(p["start"]))).total_seconds())
                                  for p in completed if p.get("book_id") and engine.time(p["end"]) > window)
            reading_minutes = round(reading_seconds / 60)
            last_meal = next((engine.time(p["end"]).strftime("%m-%d %H:%M") for p in completed if p["kind"] == "meal"), "暂无记录")
            personality = await self.ctx.config.get("personality")
            recent = [{"summary": e["summary"], "source": e["source"]} for e in engine.store.all("experience")[-8:]]
            environment = self.world.context(now)
            places = self.world.planning_places()
            prompt = (
                "为角色选择接下来独自进行的一件小活动，输出 JSON 对象。"
                "根据人格、兴趣、精力和长期目标，在角色的生活空间中安排活动。"
                "读书、工作、休闲和休息都可以。kind 从 study/work/leisure/rest/meal/social 中选择，出行由地点路线自动安排。"
                "字段 title、kind、minutes（10到120）、location、motivation、goal_id（无关联填空字符串）。"
                f"当前时间：{now.isoformat(timespec='minutes')}；可用分钟：{free}；"
                f"今日实际活动与最近节奏：{json.dumps(balance, ensure_ascii=False)}；"
                "结合累计时长、连续次数、饭点和身体状态决定继续还是换活动；有具体进展或兴致时可以继续。"
                "motivation 简短说明这次继续或切换的具体理由。"
                f"此刻的习惯候选：{json.dumps(habit, ensure_ascii=False)}；目标下一步：{json.dumps(next_step, ensure_ascii=False)}；"
                f"可接续的具体念头：{json.dumps(continuations[-12:], ensure_ascii=False)}。"
                "选择接续时填写 continuation_id，并在活动里回应该念头；选择目标时填写 goal_id 和 goal_step_id。"
                "习惯、目标、接续与新活动一起权衡；next_date 表示适合接续的日期，日期未到的念头留到之后。"
                f"上次用餐结束：{last_meal}。过去两小时已读书 {reading_minutes} 分钟。到饭点可以安排做饭、用餐。"
                f"人格：{json.dumps(personality, ensure_ascii=False)}；补充背景：{config.background}；"
                f"兴趣：{config.interests}；状态：{json.dumps(state, ensure_ascii=False)}；"
                f"目标：{json.dumps(goals, ensure_ascii=False)}；最近经历：{json.dumps(recent, ensure_ascii=False)}"
                f"；持续影响、偏好和接续内容：{json.dumps(self.continuity.context('', now), ensure_ascii=False)}"
                "location 填已有地点名称；想去的新场所用 place_query 填简短稳定的地点搜索词，location 可留空。"
                "同类需求复用相同搜索词，例如咖啡馆、东北菜餐厅、安静的咖啡馆；保留菜系、环境等具体要求。"
                "根据本次活动决定是否需要新地点，例如喝咖啡找咖啡馆、吃饭找餐厅、购物找商场，类别自由选择。"
                f"自主见闻条件：{json.dumps(exploration, ensure_ascii=False)}。"
                "ready为true时，可以安排10至30分钟上网了解，kind选study或leisure，research_query填写一个具体检索问题。"
                "结合兴趣、订阅资料、最近经历或未完念头选择话题，并在motivation说明缘由。"
                "新闻、天气、本地活动的research_freshness填current；菜谱、背景知识填knowledge。"
                "查询近期信息时在问题中写明日期或城市；其他活动research_query留空。书架阅读与联网了解分别安排。"
                f"书架：{json.dumps(books, ensure_ascii=False)}；阅读书架中的书时填写book_id，从已读位置继续。"
                f"城市地点联网可用：{search_ready}。开启时可填写 place_query；关闭时从已有地点选择，place_query 留空。"
                "阅读可以在住处、图书馆、安静的咖啡馆或公园进行。结合有效天气、时段、精力、出行耗时和近期地点选择；"
                "天气舒适时可偶尔在公园阅读，寒冷、降雨、风大时选择室内。想换环境时可搜索适合阅读的场所。"
                "外出时优先复用当前城市已收录的合适地点；居家活动使用住处或厨房。"
                f"；可选地点名称：{json.dumps(places, ensure_ascii=False)}；"
                f"环境资料：{json.dumps(environment, ensure_ascii=False)}。这些资料供选择活动时参考，安排活动按本提示中的规则进行。"
                "根据仍在有效期内的天气选择活动，恶劣天气优先在室内休息或活动。"
            )
            def validate_activity(choice):
                if choice.research_query and (not exploration["ready"] or choice.book_id or choice.kind not in {"study", "leisure"}):
                    raise ValueError("请按自主见闻条件安排独立的学习或休闲活动，其他活动research_query留空")
                if choice.continuation_id and not any(r["id"] == choice.continuation_id for r in continuations):
                    raise ValueError("continuation_id 请选择本次可接续念头的 ID，新活动填空字符串")
            choice = await generate(self.ctx, engine.store, self.config, "自主活动", prompt, ActivityChoice, validate_activity)
            if choice.kind not in {"study", "work", "leisure", "rest", "meal", "social"}:
                raise ValueError("自主规划返回了未允许的活动类型")
            title, kind, location, motivation = choice.title, choice.kind, choice.location, choice.motivation
            minutes = min(choice.minutes, free)
            research_query, research_freshness = choice.research_query, choice.research_freshness
            place_query = choice.place_query
            book_id = choice.book_id
            goal_id = choice.goal_id
            goal_step_id = choice.goal_step_id
            if choice.continuation_id:
                continuation = next(r for r in continuations if r["id"] == choice.continuation_id)
                continuation_id, session = continuation["id"], continuation["session_id"]
                source = "接续生活念头"
            if goal_id:
                goal = engine.store.get("goal", goal_id)
                session = goal["session_id"]
        if place_query and search_ready:
            found = await self.world.discover_places(self.spatial, now, activity=place_query)
            if not found:
                raise ValueError(f"本次活动尚未找到合适地点：{place_query}")
            location = found[0]["name"]
        engine.advance(now)
        if engine.current(now):
            return "规划期间已有活动开始"
        state = engine.state()
        free = engine.free_minutes(now)
        travel_minutes = self.spatial.route(state["location"], location)["minutes"] if location else 0
        minutes = min(minutes, free - travel_minutes)
        if source == "睡前收尾" and self.bedtime(now) and (self.bedtime(now) - now).total_seconds() / 60 <= travel_minutes:
            title, kind, motivation = "睡觉", "sleep", "回到住处后按作息休息"
            minutes = free - travel_minutes
        if kind == "sleep":
            wake = now.replace(hour=config.wake_hour, minute=0, second=0, microsecond=0)
            if wake <= now:
                wake += timedelta(days=1)
            minutes = min(minutes, int((wake - now).total_seconds() / 60) - travel_minutes)
        bedtime = self.bedtime(now)
        if bedtime and kind != "sleep":
            home = self.spatial.place("home")["name"]
            return_minutes = self.spatial.route(location or state["location"], home)["minutes"]
            minutes = min(minutes, int((bedtime - now).total_seconds() / 60) - travel_minutes - return_minutes)
            if minutes < 1 and location != home:
                title, kind, location, motivation = "回家准备休息", "rest", home, "收好手边的事，准备睡觉"
                research_query = ""
                source, book_id, goal_id, goal_step_id, continuation_id = "睡前收尾", "", "", "", ""
                travel_minutes = self.spatial.route(state["location"], home)["minutes"]
                minutes = min(free - travel_minutes, max(1, int((bedtime - now).total_seconds() / 60) - travel_minutes))
        if minutes < 1:
            return "预留出行时间后，剩余空档不足"
        start = now + timedelta(minutes=travel_minutes)
        if not research_query and not book_id and books and self.reading_title(title):
            current_book = engine.store.find("runtime", "current_book")
            book_id = next((b["id"] for b in books if current_book and b["id"] == current_book["book_id"]), books[0]["id"])
        if book_id:
            book = engine.store.get("book", book_id)
            title = f"阅读《{book['title']}》"
        value = PlanInput(title=title, book_id=book_id, start=start.isoformat(), end=(start + timedelta(minutes=minutes)).isoformat(),
                          kind=kind, location=location, motivation=motivation, goal_id=goal_id, goal_step_id=goal_step_id,
                          continuation_id=continuation_id, research_query=research_query, research_freshness=research_freshness)
        plan = self.spatial.schedule(value, session, now, autonomous=True)
        with engine.store.db:
            plan["decision"] = {"at": now.strftime("%m-%d %H:%M"), "source": source, "title": title,
                                "place": plan["location"], "minutes": minutes, "reason": motivation, "balance": self.activity_balance(now)}
            engine.store.put("plan", plan)
        if continuation_id:
            with engine.store.db:
                continuation = engine.store.get("activity_memory", continuation_id)
                engine.store.put("activity_memory", {**continuation, "last_continued_at": now.isoformat()})
        return f"已安排：{title}"

    @staticmethod
    def reading_title(title):
        return any(word in title for word in ("阅读", "读书", "读几页", "翻书", "读一", "看书", "看小说", "读小说", "翻翻《"))

    def pending_share_result(self, event):
        if event["source"] != "simulated" or not event.get("plan_id"):
            return False
        plan = self.engine.store.find("plan", event["plan_id"])
        if plan is None:
            return False
        if plan.get("book_id"):
            return True
        return ((self.config.continuity.outcomes_enabled or bool(plan.get("research_query"))) and event["revision"] == 1
                and not plan["participants"] and plan["kind"] not in {"travel", "sleep", "diary"}
                and plan["status"] == "completed")

    def share_activity(self, event):
        """用书籍关联和已有活动分类归类分享话题。"""
        plan = self.engine.store.find("plan", event.get("plan_id", ""))
        if not plan:
            return "生活经历"
        if plan.get("book_id"):
            return "阅读"
        return plan.get("activity_label") or {
            "sleep": "睡眠", "meal": "用餐", "work": "工作", "study": "学习",
            "leisure": "休闲", "travel": "出行", "rest": "休息", "social": "社交", "diary": "日记",
        }[plan["kind"]]

    def select_share(self, available, session, now):
        """重要经历优先，日常话题参考本聊天流最近一天实际分享的类别。"""
        eligible = [e for e in available if e["importance"] >= self.config.proactive.share_threshold]
        counts = {}
        for row in self.engine.store.all("share_receipt"):
            activity = row.get("share_activity")
            if row["session"] == session and row.get("sent_at") and activity:
                age = now - self.engine.time(row["sent_at"])
                if timedelta() <= age < timedelta(days=1):
                    counts[activity] = counts.get(activity, 0) + (1 if row.get("followup") == "answered" else 2)
        important = [e for e in eligible if e["importance"] >= 7]
        if important:
            event = max(important, key=lambda e: (e["importance"], self.engine.time(e["occurred_at"])))
            return event, "优先分享达到7分的重要经历"
        event = max(eligible, key=lambda e: (-counts.get(self.share_activity(e), 0), e["importance"], self.engine.time(e["occurred_at"])), default=None)
        if event:
            activity = self.share_activity(event)
            return event, f"优先选择近24小时向本聊天流较少分享且有接话的活动：{activity}，重复权重{counts.get(activity, 0)}；尚待回应的话题留出更多间隔"
        return None, ""

    async def share(self, now: datetime):
        config, engine = self.config.proactive, self.engine
        if not config.enabled:
            return

        def record(outcome, reason, session="全部", score="本轮未筛选经历"):
            self.share_decisions.appendleft({"created": now.isoformat(), "at": now.strftime("%m-%d %H:%M:%S"), "session": session,
                                             "outcome": outcome, "reason": reason, "score": score, "mode": config.mode})

        greeting_window = self.greetings.window(now)
        ordinary_hours = in_hours(now.hour, config.start_hour, config.end_hour)
        if not ordinary_hours and not greeting_window:
            record("跳过", f"允许交流的时段为 {config.start_hour:02}:00 至 {config.end_hour:02}:00")
            return
        current = engine.current(now)
        if self.sleeping(now) or (current and current["kind"] in {"sleep", "work", "study"}):
            record("跳过", f"正在{current['title']}" if current else "角色正在睡眠")
            return
        await self.receipts.refresh(now)
        sessions = dict.fromkeys([*config.session_ids, *(p["session_id"] for p in engine.store.all("profile"))])
        if not sessions:
            record("跳过", "请在个人资料或额外接收列表中选择聊天流")
        for session in sessions:
            presence = self.presence.targets(session, now)
            if presence and all(p["status"] in {"准备睡觉", "忙碌", "外出"} and not p["active_here"] for p in presence):
                streams = await self.ctx.chat.get_all_streams(platform="all_platforms")
                stream = next((s for s in streams if s["stream_id"] == session), None)
                if stream and not stream.get("group_id"):
                    record("稍后再聊", "对方近期状态：" + "、".join(p["status"] for p in presence) + "；状态更新或到期后再选择时机", session)
                    continue
            replies = [engine.time(r["responded_at"]) for r in engine.store.all("share_receipt")
                       if r["session"] == session and r.get("responded_at") and r.get("followup") == "answered"]
            if replies and now < max(replies) + timedelta(minutes=20):
                record("接着聊", "对方刚接上上次分享，先延续当前话题；20分钟后再选择新的主动话题", session)
                continue
            sent = engine.store.find("sharing", session) or {"id": session, "experience_ids": []}
            times = self.receipts.sent_times(session, now.tzinfo)
            count = sum(at.date() == now.date() for at in times)
            if count >= config.daily_limit:
                record("跳过", f"今日实际已发送 {count}/{config.daily_limit} 次", session)
                continue
            if times and now < (next_at := max(times) + timedelta(minutes=config.cooldown_minutes)):
                record("跳过", f"上次实际发送在 {max(times):%H:%M}，下次可请求时间 {next_at:%m-%d %H:%M}", session)
                continue
            if any(row["session"] == session and row["outcome"] == "MaiBot 已受理"
                   for row in self.engine.store.all("share_receipt")):
                record("跳过", "上一次主动交流正在等待回执", session)
                continue
            greeting = await self.greetings.candidate(session, now, greeting_window) if greeting_window else None
            if not ordinary_hours and not greeting:
                record("跳过", "当前处于作息问候窗口，本聊天流暂无合适问候", session)
                continue
            in_window = [e for e in engine.store.all("experience") if engine.visible(e, session)
                         and timedelta() <= now - engine.time(e["occurred_at"]) < timedelta(hours=6)]
            unseen = [e for e in in_window if e["id"] not in sent["experience_ids"]]
            available = [e for e in unseen if not self.pending_share_result(e)]
            best = max(available, key=lambda e: (e["importance"], engine.time(e["occurred_at"])), default=None)
            threshold = config.share_threshold
            event, selection_reason = self.select_share(available, session, now)
            best = event or best
            score = (f"《{best['summary'][:48]}》{best['importance']}/10 分，门槛 {threshold} 分。{best['score_reason']}"
                     if best else "本轮没有可评分的经历")
            screening = (f"近六小时内 {len(in_window)} 条经历，{len(in_window) - len(unseen)} 条已向本聊天流请求分享，"
                         f"{len(unseen) - len(available)} 条等待笔记或活动回顾")
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
            if not ordinary_hours:
                notices, event = [], None
            if greeting and not notices and (not event or event["importance"] < 7):
                notices.append(("greeting:" + greeting["key"], greeting["intent"], None))
            else:
                greeting = None
            if not notices and not event:
                reason = f"没有待发提醒。{screening}"
                if best:
                    reason += "，剩余经历的最高分低于门槛"
                record("跳过", reason, session, score)
                continue
            intent = notices[0][1] if notices else (
                "分享一件刚发生的生活经历。"
                "从角色自己的视角分享这次经历，给对方自由接话的空间。"
                f"值得分享的原因={event['score_reason']}；经历来源={event['source']}；内容={event['summary']}；细节={json.dumps(event['details'], ensure_ascii=False)}"
            )
            intent += (f"\n接收人物的有效近况：{json.dumps(presence, ensure_ascii=False)}。"
                       f"最近实际分享与回应：{json.dumps(self.receipts.context(session, now), ensure_ascii=False)}。"
                       "已经接话的主题顺着回应继续；尚待回应的主题留出空间，有新进展时再自然提起。")
            record_id = notices[0][0] if notices else event["id"]
            topic = "经历分享"
            if notices:
                topics = {"anniversary": "纪念日", "reminder": "共同安排提醒", "confirmation": "安排确认",
                          "thread": "话题跟进", "issue": "日程调整", "greeting": greeting["kind"] if greeting else "作息问候"}
                topic = topics[record_id.split(":", 1)[0]]
                score = f"本次处理{topic}"
                reason = f"{len(notices)} 条待发提醒，优先处理{topic}（{record_id}）"
            else:
                reason = f"{topic}：{screening}。{selection_reason}"
            row = {"at": now.strftime("%m-%d %H:%M:%S"), "session": session,
                   "reason": reason, "score": score, "mode": config.mode,
                   "topic": notices[0][1][:400] if notices else event["summary"][:400],
                   "experience_id": event["id"] if not notices else "",
                   "share_activity": self.share_activity(event) if not notices else ""}
            if greeting:
                row.update(mode="自然交流", greeting_key=greeting["key"], greeting_kind=greeting["kind"],
                           greeting_until=greeting["until"], score="作息问候，按近期互动与关系判断")
            if config.mode == "主动表达" and not greeting:
                await self.receipts.express(session, intent, row, now)
            else:
                try:
                    result = await self.ctx.maisaka.proactive.trigger(
                        session, intent + "结合当前对话判断是否适合开口，可以选择沉默。",
                        reason="MaiWeave 生活跟进", metadata={"maiweave_record_id": record_id})
                    if not result["success"]:
                        raise RuntimeError(result.get("error") or "主动交流请求失败")
                except Exception as error:
                    record("请求失败", f"{reason}。MaiBot 返回：{error}", session, score)
                    raise
                self.receipts.accept(result["task_id"], row, now)
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
