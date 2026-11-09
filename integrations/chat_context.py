"""按话题、聊天场景和字符预算组织生活上下文。"""

import json
from collections import deque


GUIDE = (
    "\n【MaiWeave 当前生活】\n"
    "按 now 与 timezone 理解时间。计划是未来安排；simulated 是角色模拟，reported 是用户陈述，observed 是交互观测。"
    "使用 maiweave 工具保存日程及生活变化，以保存结果接续对话；更多生活记录用 maiweave_query，人物历史用 MaiBot query_memory。"
    "资料冲突须向用户说明修改 ID，取得明确同意后调用 maiweave_confirm_change。"
    "sleep_policy.sleeping 为 true 时普通聊天用 stop；需要陪伴或请求醒来时用 maiweave_wake，awake 为 true 后回应。群私聊规则一致。"
    "person_states 中准备睡觉是当时的打算，same_chat=false 时只承接近况。shared_topics 是实际分享，接话用 maiweave_share_followup 关联 ID。"
    "读书按书名与已完成笔记接话，原文与感想分别表述；更多笔记用 maiweave_books 的 notes 和 book_id，安排阅读也填写 book_id。"
    "跨地点安排用 maiweave_space 查路线，接续目标填写 goal_step_id；梦境以梦见表述，资讯带出处。\n"
)

LABELS = {"pending_changes": "资料确认", "person_states": "人物近况", "shared_topics": "分享接话",
          "plans": "日程", "schedule_issues": "日程调整", "schedule_notices": "日程通知",
          "bookshelf": "阅读笔记", "profiles": "人物资料", "relationships": "人物关系",
          "anniversaries": "纪念日", "continuity": "生活接续", "goals": "目标",
          "experiences": "近期经历", "environment": "天气与资讯", "places": "地点",
          "dreams": "梦境", "habits": "习惯"}
OMIT = {"history", "changes", "memory_id", "memory_revision", "payload", "material"}


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def brief(value, chars, count):
    """保留结构化字段，缩短单条文本和列表。"""
    if isinstance(value, str):
        return value if len(value) <= chars else value[:chars] + "…"
    if isinstance(value, list):
        return [brief(row, chars, count) for row in value[:count]]
    if isinstance(value, dict):
        return {key: brief(item, chars, count) for key, item in value.items() if key not in OMIT}
    return value


class ChatContext:
    def __init__(self, plugin):
        self.plugin = plugin
        self.kinds = {}
        self.topics = {}
        self.messages = {}
        self.speakers = {}
        self.recent = deque(maxlen=10)

    async def identify(self, session):
        if session not in self.kinds:
            streams = await self.plugin.ctx.chat.get_all_streams(platform="all_platforms")
            self.kinds.update({s["stream_id"]: "group" if s.get("group_id") else "private" for s in streams})

    def build(self, session, book_id="", text="", stage="Planner"):
        p = self.plugin
        cfg, now = p.config.context, p.now()
        group = self.kinds.get(session) == "group"
        mode = {1: "精简", 2: "均衡", 3: "丰富"}[cfg.group_mode if group else cfg.private_mode]
        budget = cfg.group_chars if group else cfg.private_chars
        count, chars = {"精简": (1, 180), "均衡": (3, 300), "丰富": (6, 600)}[mode]
        engine, store = p.engine, p.store
        state = engine.advance(now)
        current = engine.current(now)
        if current and not engine.visible(current, session):
            current = {"summary": "正在处理其他安排", "end": current["end"]}
        current = {k: v for k, v in (current or {}).items() if k in {"id", "title", "summary", "kind", "status", "end", "location", "book_id"}}
        awake = p.living.awake_until(now) if p.config.life.schedule_enabled else None
        snapshot = {"now": now.isoformat(timespec="minutes"), "timezone": engine.timezone,
                    "state": {k: state[k] for k in ("location", "energy", "mood")}, "current": current,
                    "sleep_policy": {"sleeping": p.living.sleeping(now), "mode": "按作息生活" if p.config.life.schedule_enabled else "全天候交流",
                                     "awake_until": awake.isoformat() if awake else ""}}
        snapshot = brief(snapshot, 100, 3)
        base_chars = len(GUIDE) + len(dump(snapshot))
        candidates = []
        def add(key, value, relevant=False):
            if value:
                candidates.append((0 if key == "pending_changes" else 1 if key == "bookshelf" and book_id else 2 if relevant else 3, key, value))
        def rows(category):
            return [r for r in store.all(category) if engine.visible(r, session)]
        def people(rows):
            return sorted(rows, key=lambda row: row.get("user_id") != self.speakers.get(session))
        schedule_topic = any(w in text for w in ("安排", "日程", "明天", "约", "取消", "计划", "几点", "去哪", "出门"))
        reading_topic = bool(book_id) or any(w in text for w in ("书", "阅读", "章节", "读到"))
        people_topic = any(w in text for w in ("生日", "纪念日", "称呼", "资料", "关系"))
        continuation_topic = any(w in text for w in ("继续", "上次", "昨天", "目标", "进度", "后来"))
        experience_topic = any(w in text for w in ("经历", "做了什么", "干了什么", "最近", "今天", "感想"))
        world_topic = any(w in text for w in ("天气", "下雨", "公园", "图书馆", "地点", "新闻", "出门", "见闻", "上网", "查资料", "网页"))
        discoveries = p.world.discoveries(session, text) if cfg.environment else []
        world_topic = world_topic or any(r["relevant"] for r in discoveries)
        # 确认中的修改始终带上 ID，供当前对话继续确认流程。
        add("pending_changes", [{k: r[k] for k in ("id", "action", "conflicts")} for r in rows("review")
                                if r["session_id"] == session and r["status"] == "待确认"], True)
        if cfg.people:
            add("person_states", people(p.presence.context(session, now)), True)
        if cfg.sharing and p.config.proactive.enabled:
            add("shared_topics", p.living.receipts.context(session, now), True)
        if cfg.reading and (reading_topic or mode != "精简"):
            shelf = p.books.context(book_id=book_id)
            if book_id:
                shelf["books"] = [b for b in shelf["books"] if b["id"] == book_id]
            if shelf["books"] or shelf["notes"]:
                add("bookshelf", shelf, reading_topic)
        if cfg.schedule and (schedule_topic or mode != "精简"):
            add("plans", [{k: r[k] for k in ("id", "title", "kind", "start", "end", "status", "location", "book_id", "goal_step_id")}
                          for r in engine.plans(session) if r["status"] in {"scheduled", "active", "paused", "awaiting_confirmation", "blocked"}], schedule_topic)
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
            add("continuity", {k: v[-count:] for k, v in continuity.items() if v}, continuation_topic)
            if p.config.continuity.goal_planning:
                add("goals", [g for g in rows("goal") if g["status"] == "active"], continuation_topic)
        if cfg.experiences and (experience_topic or mode != "精简"):
            add("experiences", [e for e in engine.experiences(session, limit=12) if "书名" not in e["details"]][:count], experience_topic)
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
            if key == "pending_changes":
                options.append([{k: row[k] for k in ("id", "action")} for row in value[:1]])
            for selected in options:
                candidate = {**snapshot, key: selected}
                if (len(GUIDE) + len(dump(candidate)) <= budget
                        and (priority <= 1 or len(dump(candidate)) - before <= budget // 2)):
                    snapshot = candidate
                    sections.append({"category": LABELS[key], "chars": len(dump(snapshot)) - before})
                    break
            else:
                omitted.append(LABELS[key])
        result = GUIDE + dump(snapshot)
        self.recent.appendleft({"at": now.strftime("%m-%d %H:%M:%S"), "session": session,
            "chat": "群聊" if group else "私聊", "stage": stage, "mode": mode, "chars": len(result), "budget": budget,
            "detail": "；".join([f"规则与当前状态 {base_chars} 字符（{base_chars / len(result):.0%}）", *[f"{s['category']} {s['chars']} 字符（{s['chars'] / len(result):.0%}）" for s in sections]]),
            "omitted": "、".join(omitted) or "无", "sections": sections, "base_chars": base_chars})
        return result
