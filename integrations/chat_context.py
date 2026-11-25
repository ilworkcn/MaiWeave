"""按话题、聊天场景和字符预算组织生活上下文。"""

import json
import re
from collections import deque
from copy import deepcopy
from ..core.engine import work_now


def without_sleep_tail(text):
    """普通话题答完后，识别另起一句的催睡或追问睡意。"""
    boundary = r"(?:^|(?<=[。，！？、,.!?；;|\s]))"
    signoff = (r"(?:你(?:也)?|这回|看完|说完|好了|真的|行了|现在|就)*"
               r"(?:赶紧睡|赶快睡|快点睡|早点(?:去)?睡|快(?:去)?睡|乖乖睡|去睡|闭眼睡|睡觉吧|别熬夜|晚安)"
               r"|你[^，,。！？!?\n「」“”]{0,12}(?:不想睡|不打算睡|还不睡|不肯睡|睡不睡|还没睡)")
    match = re.search(boundary + "(?:" + signoff + r")(?!莲|衣|袋|帽|着)"
                      + r"[^「」“”\n。！？!?]*(?:[。！？!?…\s]*)$", text)
    return text[:match.start()].rstrip("，,、；;| \n") if match else text


GUIDE = (
    "\n【MaiWeave 当前生活】\n"
    "时间参照 now、timezone；计划是安排，simulated 为模拟，reported 为陈述，observed 为观测。"
    "用 maiweave 工具保存变化；记录查 maiweave_query，人物历史查 query_memory。"
    "资料冲突说明修改 ID，明确同意后用 maiweave_confirm_change。"
    "sleep_policy.quiet=true时静默。睡眠中需要陪伴用maiweave_wake，awake=true后回应，false结束本轮。提供reply时用它发送，after_reply=stop为发送后结束。"
    "energy<=25简短接话，普通休息照常聊。作息和疲倦描述的是你自己。对方换了话题就顺着新话题聊，口气随你的状态变化。"
    "person_states 是当时近况，same_chat=false 时承接近况。"
    "读书沿用书名和笔记，原文与感想分别表述；查笔记用 maiweave_books 的 notes、book_id，安排阅读填 book_id。"
    "跨地点用 maiweave_space 查路线，目标填 goal_step_id；梦境说梦见，资讯带出处。\n"
)

LABELS = {"work": "工作与语气", "encounters": "群聊接话","life_images": "已展示图片","chat_arrangements": "聊天约定", "pending_changes": "资料确认", "person_states": "人物近况", "shared_topics": "分享接话",
          "plans": "日程", "schedule_issues": "日程调整", "schedule_notices": "日程通知",
          "bookshelf": "阅读笔记", "profiles": "人物资料", "relationships": "人物关系",
          "anniversaries": "纪念日", "continuity": "生活接续", "goals": "目标",
          "experiences": "近期经历", "environment": "天气与资讯", "places": "地点",
          "dreams": "梦境", "habits": "习惯", "已入账收支": "已入账收支", "虚拟钱包": "虚拟钱包", "通勤车费": "通勤车费"}
OMIT = {"history", "changes", "memory_id", "memory_revision", "payload", "material"}


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def discovered_tools(items, definitions, names):
    """把宿主本轮搜索命中的麦织工具带进下一次模型请求。"""
    result = list(definitions)
    present = {tool.get("function", {}).get("name") for tool in definitions}
    for item in reversed(items):
        if item.get("item_type") == "UserMessageItem":
            if any(p.get("text", "").startswith(("<message ", "<plugin_proactive_task", "<internal_", "<subagent_result"))
                   for p in item.get("parts", [])):
                break
        if item.get("item_type") != "FunctionCallOutputItem" or item.get("tool_name") != "tool_search":
            continue
        output = item.get("output")
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except ValueError:
                continue
        if not isinstance(output, dict) or output.get("status") != "loaded":
            continue
        query = output.get("query")
        for function in output.get("tools", []):
            name = function.get("name")
            if name in names and name not in present and (query not in names or name == query):
                result.append({"type": "function", "function": function})
                present.add(name)
    return result


def brief(value, chars, count):
    """保留结构化字段，缩短单条文本和列表。"""
    if isinstance(value, str):
        return value if len(value) <= chars else value[:chars] + "…"
    if isinstance(value, list):
        return [brief(row, chars, count) for row in value[:count]]
    if isinstance(value, dict):
        return {key: brief(item, chars, count) for key, item in value.items() if key not in OMIT}
    return value


def select_fields(row, names):
    return {key: row[key] for key in names if key in row and row[key] not in ("", None, [], {})}


def deduplicate(snapshot):
    """只对已选入上下文的经历去重，独立接续保留背景。"""
    result = deepcopy(snapshot)
    experiences = {row["id"]: row for row in result.get("experiences", [])}
    continuity = result.get("continuity", {})
    for row in continuity.get("continuations", []):
        experience = experiences.get(row.get("experience_id"))
        if experience:
            details = experience.get("details", {})
            if details.get("结果") == row.get("background") or experience.get("summary") == row.get("background"):
                row.pop("background", None)
            if details.get("进度") == row.get("progress"):
                row.pop("progress", None)
    for effect in continuity.get("effects", []):
        for experience in experiences.values():
            if effect.get("reason") and effect["reason"] in (experience.get("summary"), experience.get("details", {}).get("结果")):
                effect.pop("reason", None)
                effect["experience_id"] = experience["id"]
                break
    return result


class ChatContext:
    def __init__(self, plugin):
        self.plugin = plugin
        self.kinds = {}
        self.topics = {}
        self.messages = {}
        self.proactive_turns = set()
        self.speakers = {}
        self.wake_candidates = {}
        self.recent = deque(maxlen=10)

    async def identify(self, session):
        if session not in self.kinds:
            streams = await self.plugin.ctx.chat.get_all_streams(platform="all_platforms")
            self.kinds.update({s["stream_id"]: "group" if s.get("group_id") or s.get("is_group_session") else "private" for s in streams})

    def build(self, session, book_id="", text="", stage="Planner", *, followup_ids=None, closing=""):
        p = self.plugin
        cfg, now = p.config.context, p.now()
        group = self.kinds.get(session) == "group"
        mode = {1: "精简", 2: "均衡", 3: "丰富"}[cfg.group_mode if group else cfg.private_mode]
        budget = (cfg.group_chars if group else cfg.private_chars) - len(closing)
        count, chars = {"精简": (1, 180), "均衡": (3, 300), "丰富": (6, 600)}[mode]
        engine, store = p.engine, p.store
        state = engine.advance(now)
        current = engine.current(now)
        if current and not engine.visible(current, session):
            current = {"summary": "正在处理其他安排", "end": current["end"]}
        work = work_now(current or {}, now)
        current = select_fields(current or {}, ("id", "title", "summary", "kind", "status", "end", "location", "book_id", "goal_step_id", "continuation_id", "simulation"))
        awake = p.living.awake_until(now) if p.config.life.schedule_enabled else None
        snapshot = {"now": now.isoformat(timespec="minutes"), "timezone": engine.timezone,
                    "state": {k: state[k] for k in ("location", "energy", "mood")}, "current": current,
                    "sleep_policy": {"sleeping": p.living.sleeping(now), "quiet": p.living.quiet_sleep(now, session), "mode": "按作息生活" if p.config.life.schedule_enabled else "全天候交流",
                                     "awake_until": awake.isoformat() if awake else ""}}
        from .life_images import asks_progress
        incoming = self.messages.get(session, text)
        note = p.life_images.turn_notes.get(session)
        if note and note[1] == incoming:
            snapshot["本次配图"] = note[2]
        elif asks_progress(incoming):
            if any(stream == session for stream, _ in p.life_images.pending_requests):
                snapshot["本聊天的图片"] = "尚未送达，完成后自动发送。回答催图可说「好了发你」。"
            else:
                receipts = [r for r in store.all("image_delivery") if r["session_id"] == session]
                if receipts:
                    latest = max(receipts, key=lambda r: r["at"])
                    snapshot["本聊天的图片"] = latest["status"] + "，时间 " + latest["at"]
        elif stage == "回复" and any(stream == session for stream, _ in p.life_images.pending_requests):
            snapshot["接话"] = "之前的配图由后台继续处理。这条回复只聊对方刚说的内容。"
        bedtime = p.living.bedtime(now)
        if bedtime:
            snapshot["sleep_policy"].update(bedtime=bedtime.isoformat(timespec="minutes"),
                                           minutes_until_bed=int((bedtime-now).total_seconds()/60))
        farewell = p.living.farewell(now)
        if farewell and farewell["farewell_session"] == session:
            snapshot["sleep_policy"]["farewell"] = "对方已道别，简短自然地回应晚安，reply 使用 after_reply=stop；本轮发送完毕后继续睡觉。"
        snapshot = brief(snapshot, 80, 3)
        pending = [{k: r[k] for k in ("id", "action", "conflicts")} for r in store.all("review")
                   if r["session_id"] == session and r["status"] == "待确认"]
        confirmation = {"pending_changes": [{k: r[k] for k in ("id", "action")} for r in pending[:1]]} if pending else {}
        if len(GUIDE) + len(dump({**snapshot, **confirmation})) > budget:
            # 小预算优先保留操作关联，地点共用状态中的名称，展示文字取短摘要。
            snapshot["sleep_policy"].pop("minutes_until_bed", None)
            current = snapshot["current"]
            if current.get("location") == snapshot["state"]["location"]:
                current.pop("location", None)
            current.pop("status", None)
            if current.get("end"):
                current["end"] = engine.time(current["end"]).isoformat(timespec="minutes")
            if awake:
                snapshot["sleep_policy"]["awake_until"] = awake.isoformat(timespec="minutes")
            for fields, names in ((current, ("title", "summary", "location")), (snapshot["state"], ("location",))):
                for name in names:
                    excess = len(GUIDE) + len(dump({**snapshot, **confirmation})) - budget
                    if name in fields and excess > 0:
                        fields[name] = brief(fields[name], max(8, len(fields[name]) - excess - 1), 1)
        base_chars = len(GUIDE) + len(dump(snapshot))
        candidates = []
        def add(key, value, relevant=False):
            if value:
                candidates.append((0 if key == "pending_changes" else 1 if key == "bookshelf" and book_id else 2 if relevant else 3, key, value))
        private = self.kinds.get(session) == "private"
        identity = p.config.life.occupation.strip() or (store.find("runtime", "occupation") or {}).get("name")
        if identity and identity != "未设定":
            add("日常身份", identity, current.get("kind") in {"study", "work"})
        if not work and current.get("kind") != "work":
            recent_work = next((e for e in engine.experiences(session, limit=8)
                                if "手头工作" in e["details"] and
                                0 <= (now - engine.time(e["occurred_at"])).total_seconds() < 3 * 3600), None)
            if recent_work:
                work = "刚才的进展：" + (recent_work["details"].get("事情的收尾") or recent_work["summary"])
                if recent_work["details"].get("下次接着做"):
                    work += "；下次接着做：" + recent_work["details"]["下次接着做"]
        if any(word in text for word in ("后来", "上次", "昨天", "完了", "做完", "进展", "进度")):
            finished = [r for r in reversed(engine.plans(session)) if r["status"] == "completed"
                        and (r.get("shift_scene") or {}).get("done")
                        and ("昨天" not in text or (now.date() - engine.time(r["end"]).date()).days == 1)]
            related = next((r for r in finished if r["title"] in text), None)
            if not related:
                terms = re.findall(r"[\w]{2,}", re.sub(r"怎么样|做到哪里|后来|上次|昨天|做完|完了|进展|进度", " ", text))
                related = next((r for r in finished if any(term in r["title"] or term in r["shift_scene"]["task"]
                                                           for term in terms)), None)
            if not related and finished and any(word in text for word in ("那件事", "那个后来", "上次课", "上个班")):
                related = finished[0]
            if related:
                scene = related["shift_scene"]
                add("上次进展", {"日期": related["end"][:10], "日程": related["title"],
                               "做到哪里": scene["resolution"], "下次接着做": scene.get("next_shift", "")}, True)
        tone = []
        if state["energy"] <= 30:
            tone.append("累了，说话省力些")
        if state["stress"] >= 60:
            tone.append("有些紧绷，先接眼前的话")
        if state["mood"] < -15:
            tone.append("心里低落，语气收一点")
        if work:
            close = private and p.living.intimacy(session, self.speakers.get(session, "")) >= 2
            if stage == "回复":
                tone.insert(0, "这是你自己的近况。对方问候时，用第一人称挑一件事随口聊一两句")
            tone.append("对方关心时可以说说心里话" if close else "对方问起再简短说说手头的事")
        if work or tone:
            add("work", "；".join(filter(None, [*tone, work])), True)
        if any(w in text for w in ("多少钱", "花了多少", "赚了多少", "挣了多少", "收支", "流水", "对账", "余额", "领了多少", "借款", "还款")):
            ledger = {"单位": engine.wallet.currency_name, "工具": "maiweave_wallet",
                      "最近流水": engine.wallet.recent_payments(session if private else "", self.speakers.get(session) if private else "", 4)}
            if private:
                ledger["余额"] = engine.wallet.account()["balance"]
            add("已入账收支", ledger, True)
        if private:
            requests = [r for r in store.all("money_request") if r["session"] == session
                        and r["user"] == self.speakers.get(session)]
            if any(w in text for w in ("车费", "路费", "坐车的钱")):
                offers = engine.wallet.commute_requests(session, self.speakers.get(session), now)
                if offers:
                    add("通勤车费", {**select_fields(offers[0], ("id", "title", "amount", "departure")),
                                    "工具": "maiweave_wallet", "说明": "出发前明确借出用 lend，超过出发时间先改班。"}, True)
            if requests and any(w in text for w in ("借", "钱", "还款", "给你", "送你", "工资", "元", engine.wallet.currency_name)):
                add("虚拟钱包", {"货币名称": engine.wallet.currency_name, "余额": engine.wallet.account()["balance"], "最近借款":
                    select_fields(requests[-1], ("id", "title", "amount", "status", "owed")), "工具": "maiweave_wallet"}, True)
        def rows(category):
            return [r for r in store.all(category) if engine.visible(r, session)]
        def people(rows):
            return sorted(rows, key=lambda row: row.get("user_id") != self.speakers.get(session))
        schedule_topic = any(w in text for w in ("安排", "日程", "明天", "约", "取消", "计划", "几点", "去哪", "出门", "上班", "下班", "排班", "工作"))
        reading_topic = bool(book_id) or any(w in text for w in ("书", "阅读", "章节", "读到"))
        people_topic = any(w in text for w in ("生日", "纪念日", "称呼", "资料", "关系"))
        continuation_topic = any(w in text for w in ("继续", "上次", "昨天", "目标", "进度", "后来"))
        experience_topic = any(w in text for w in ("经历", "做了什么", "干了什么", "最近", "今天", "感想"))
        world_topic = any(w in text for w in ("天气", "下雨", "公园", "图书馆", "地点", "新闻", "出门", "见闻", "上网", "查资料", "网页"))
        discoveries = p.world.discoveries(session, text) if cfg.environment else []
        world_topic = world_topic or any(r["relevant"] for r in discoveries)
        # 确认中的修改始终带上 ID，供当前对话继续确认流程。
        add("pending_changes", pending, True)
        if cfg.people:
            add("person_states", people(p.presence.context(session, now)), True)
        add("encounters", p.presence.encounter_context(session, self.speakers.get(session, ""), now, group, offer=stage == "Planner"), True)
        if cfg.sharing and p.config.proactive.enabled:
            acknowledgement = re.fullmatch(r"(嗯+|哦+|噢+|啊+|这样|原来如此|好[的吧]?|知道了|明白了|收到)[呀啊哦呢啦哇哈]*[\s。！!，,.~～]*", text.strip())
            if not acknowledgement:
                add("shared_topics", p.living.receipts.context(session, now), True)
        if p.config.diary.chat_images_enabled:
            add("life_images", p.life_images.context(session), True)
        if cfg.reading and (reading_topic or mode != "精简"):
            shelf = p.books.context(book_id=book_id)
            if book_id:
                shelf["books"] = [b for b in shelf["books"] if b["id"] == book_id]
            if shelf["books"] or shelf["notes"]:
                add("bookshelf", shelf, reading_topic)
        if cfg.schedule:
            add("chat_arrangements", p.chat_schedule.context(now, session), True)
        if cfg.schedule and (schedule_topic or mode != "精简"):
            add("plans", [{k: r[k] for k in ("id", "title", "kind", "start", "end", "status", "location", "book_id", "goal_step_id")}
                          for r in engine.plans(session) if r["id"] != current.get("id") and r["status"] in {"scheduled", "active", "paused", "awaiting_confirmation", "blocked"}], schedule_topic)
            add("schedule_issues", rows("schedule_issue")[-count:], schedule_topic)
            add("schedule_notices", rows("schedule_notice")[-count:], schedule_topic)
        if cfg.people and (people_topic or mode != "精简"):
            add("profiles", people([r for r in rows("profile") if r["session_id"] == session]), people_topic)
            add("relationships", people([r for r in rows("relationship") if r["session_id"] == session]), people_topic)
            add("anniversaries", [r for r in p.anniversaries.upcoming(now.date(), session) if r["days_left"] <= 30], people_topic)
        if cfg.continuity and (continuation_topic or mode != "精简"):
            continuity = p.continuity.context(session, now)
            if not p.config.continuity.semantic_learning:
                continuity.pop("preferences", None)
            if not p.config.continuity.outcomes_enabled:
                continuity.pop("continuations", None)
            memories = []
            for row in continuity.get("continuations", []):
                named = any(row.get(key) and row[key] in text for key in ("subject", "activity"))
                if row.get("completed") and not (continuation_topic or named):
                    continue
                item = select_fields(row, ("id", "activity", "subject", "next_action", "next_date", "experience_id", "completed", "location"))
                item["progress"] = row.get("details", {}).get("进度", "")
                item["background"] = row.get("details", {}).get("结果") or row.get("details", {}).get("过程", "")
                event = store.find("experience", row.get("experience_id", ""))
                if event and engine.visible(event, session):
                    item["source"] = event["source"]
                memories.append(item)
            continuity["continuations"] = memories
            continuity["effects"] = [select_fields(row, ("id", "reason", "mood", "stress", "started_at", "expires_at"))
                                     for row in continuity.get("effects", [])]
            add("continuity", {k: v[-count:] for k, v in continuity.items() if v}, continuation_topic)
            if p.config.continuity.goal_planning:
                add("goals", [g for g in rows("goal") if g["status"] == "active"], continuation_topic)
        if cfg.experiences and (experience_topic or mode != "精简"):
            add("experiences", [select_fields(e, ("id", "summary", "occurred_at", "source", "evidence", "details"))
                                for e in engine.experiences(session, limit=12) if "书名" not in e["details"]][:count], experience_topic)
        if cfg.environment and (world_topic or mode != "精简"):
            environment = p.world.context(now)
            environment["discoveries"] = discoveries
            add("environment", {k: v for k, v in environment.items() if v and
                ((k == "weather" and p.config.world.weather_enabled) or (k == "news" and p.config.world.news_enabled) or (k in {"readings", "discoveries"} and p.config.world.search_enabled))}, world_topic)
            if p.config.world.places_enabled and p.config.world.search_enabled:
                places = sorted(p.world.planning_places(), key=lambda row: (row["name"] not in text, not bool(row.get("source_url"))))
                add("places", [{"name": r["name"], "outdoor": r["outdoor"]} for r in places], world_topic)
        if cfg.dreams:
            if p.config.dreams.enabled and ("梦" in text or mode == "丰富"):
                add("dreams", [r for r in rows("dream") if r["status"] == "已生成"][-count:], "梦" in text)
            if p.config.rhythm.habits_enabled and ("习惯" in text or mode == "丰富"):
                add("habits", [r for r in rows("habit") if r["active"] and r["enabled"]], "习惯" in text)
        sections, omitted = [], []
        for priority, key, value in sorted(candidates, key=lambda item: item[0]):
            before = len(dump(snapshot))
            # 缩短文本、减少条目时保持完整 JSON，详情仍由查询工具提供。
            options = [brief(value, chars, count), brief(value, min(chars, 120), 1)]
            if key == "已入账收支":
                options.insert(0, brief(value, 80, 4))
            if key == "pending_changes":
                options.append([{k: row[k] for k in ("id", "action")} for row in value[:1]])
            if key == "shared_topics":
                options.append([{"id": value[0]["id"], "topic": value[0]["topic"][:80]}])
            for selected in options:
                candidate = deduplicate({**snapshot, key: selected})
                if (len(GUIDE) + len(dump(candidate)) <= budget
                        and (priority <= 1 or len(dump(candidate)) - before <= budget // 2)):
                    snapshot = candidate
                    break
            else:
                omitted.append(LABELS.get(key, key))
        sections = [{"category": LABELS.get(key, key), "chars": len(dump({key: snapshot[key]})) - 1}
                    for _, key, _ in candidates if key in snapshot]
        if stage == "Planner":
            if followup_ids is not None:
                followup_ids.extend(row["id"] for row in snapshot.get("shared_topics", []))
            p.presence.offered_context(session, self.speakers.get(session, ""), now, bool(snapshot.get("encounters") and snapshot["encounters"][0].get("stage") == "群里遇见"))
        result = GUIDE + dump(snapshot) + closing
        self.recent.appendleft({"at": now.strftime("%m-%d %H:%M:%S"), "session": session,
            "chat": "群聊" if group else "私聊", "stage": stage, "mode": mode, "chars": len(result), "budget": budget + len(closing),
            "detail": "；".join([f"规则与当前状态 {base_chars} 字符（{base_chars / len(result):.0%}）", *[f"{s['category']} {s['chars']} 字符（{s['chars'] / len(result):.0%}）" for s in sections]]),
            "omitted": "、".join(omitted) or "无", "sections": sections, "base_chars": base_chars})
        return result
