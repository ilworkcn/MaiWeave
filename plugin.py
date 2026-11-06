"""MaiBot SDK 入口，注册对话工具和生活上下文。"""

import asyncio
import json
import re
from contextlib import closing, suppress
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.error import URLError
from uuid import uuid4
from zoneinfo import ZoneInfo

from maibot_sdk import HomeCard, HookHandler, MaiBotPlugin, Tool

from .config import MaiWeaveConfig
from .core.engine import LifeEngine
from .life.living import Living
from .life.presence import Presence
from .integrations.memory import NativeMemory
from .core.models import ExperienceInput, GoalInput, PlanInput, PlanPatch, RelationshipChange
from .core.store import Store
from .integrations.share_receipts import planner_task
from .integrations.world import World
from .life.spatial import Spatial
from .life.rhythm import Rhythm
from .life.dreams import Dreams
from .life.books import Books
from .life.diary import Diary
from .interfaces.feature_tools import FeatureTools
from .interfaces.webui import WebUI
from .life.continuity import Continuity
from .life.anniversaries import Anniversaries
from .interfaces.review import Reviews


def schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def text_field(description: str) -> dict:
    return {"type": "string", "description": description}


def operation(function):
    """把业务输入错误返回给 Planner，运行错误仍由 MaiBot 记录。"""
    @wraps(function)
    async def invoke(self, *args, **kwargs):
        if not self.config.life.enabled:
            return {"success": False, "message": "MaiWeave 已停用"}
        try:
            self.engine.advance(self.now())
            return {"success": True, "content": await function(self, *args, **kwargs)}
        except ValueError as error:
            return {"success": False, "message": str(error)}
    return invoke


class MaiWeavePlugin(WebUI, FeatureTools, MaiBotPlugin):
    config_model = MaiWeaveConfig
    model_tasks = ["utils"]

    @classmethod
    def build_config_schema(cls, **kwargs):
        schema = super().build_config_schema(**kwargs)
        # 展示分组仍指向原配置节，保存时沿用原有配置键。
        groups = [
            ("life", "life", "生活开关", "控制生活推进与自主活动。", ["enabled", "autonomous"]),
            ("profile", "life", "角色设定", "时区已有默认值，背景和兴趣按需补充。", ["timezone", "interests", "background"]),
            ("sleep", "life", "日常作息", "关闭作息可全天候交流；开启后按 24 小时制安排睡眠。", ["schedule_enabled", "sleep_hour", "wake_hour"]),
            ("wake", "life", "睡眠唤醒", "设置能唤醒角色的词语和醒来后留给聊天的时间。", ["wake_enabled", "wake_minutes", "wake_keywords"]),
            ("planning", "life", "模型与更新频率", "已有可用初始值，通常无需调整。", ["model_task", "tick_seconds", "planning_interval_minutes"]),
            ("weather", "world", "实时天气", "填写城市，查看当地天气和预报。", ["weather_enabled", "weather_minutes", "city", "country_code"]),
            ("news", "world", "资讯订阅", "按需开启；订阅地址需要自行提供。", ["news_enabled", "news_minutes", "feed_urls"]),
            ("search", "world", "联网搜索", "复用 MaiBot 已配置的搜索服务。", ["search_enabled", "places_enabled"]),
            ("proactive", "proactive", "主动交流对象", "个人资料的聊天流用于交流和提醒；其他接收对象可在下方补充。", ["enabled", "mode", "natural_greetings", "session_ids", "share_threshold"]),
            ("limits", "proactive", "交流时段与额度", "提醒、完成确认和话题跟进共用这些限制；数值已有默认值。", ["start_hour", "end_hour", "daily_limit", "cooldown_minutes"]),
        ]
        original = schema["sections"]
        from zoneinfo import available_timezones
        original["life"]["fields"]["timezone"].update(choices=sorted(available_timezones()), ui_type="select")
        original["proactive"]["fields"]["mode"].update(choices=["自然交流", "主动表达"], ui_type="select")
        original["proactive"]["fields"]["share_threshold"].update(choices=list(range(1, 11)), ui_type="select")
        for section, keys in (("life", ("sleep_hour", "wake_hour")), ("proactive", ("start_hour", "end_hour"))):
            for key in keys:
                original[section]["fields"][key].update(choices=list(range(24)), ui_type="select")
        schema["sections"] = {key: value for key, value in original.items() if key not in {"life", "world", "proactive"}}
        for key, source, title, description, fields in groups:
            schema["sections"][key] = {
                **original[source], "name": source, "title": title, "description": description,
                "fields": {name: {**original[source]["fields"][name], "order": order} for order, name in enumerate(fields)},
                "collapsed": key == "planning",
            }
        schema["layout"] = {"type": "tabs", "tabs": [
            {"id": "life", "title": "角色与作息", "sections": ["life", "profile", "sleep", "wake", "dreams", "planning"], "order": 0},
            {"id": "growth", "title": "日程与成长", "sections": ["rhythm", "continuity", "diary", "books"], "order": 1},
            {"id": "world", "title": "天气与见闻", "sections": ["weather", "news", "search"], "order": 2},
            {"id": "connections", "title": "交流与记忆", "sections": ["proactive", "limits", "memory", "plugin"], "order": 3},
        ]}
        return schema

    def get_webui_config_schema(self, **kwargs):
        schema = super().get_webui_config_schema(**kwargs)
        field = schema["sections"]["planning"]["fields"]["model_task"]
        field.update(choices=list(dict.fromkeys([*self.model_tasks, self.config.life.model_task])), ui_type="select")
        return schema

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.config.life.timezone))

    async def on_load(self):
        self.model_tasks = await self.ctx.llm.get_available_models()
        self.store = Store(Path(self.ctx.paths.data_dir) / "maiweave.sqlite3")
        self.anniversaries = Anniversaries(self.store)
        self.reviews = Reviews(self)
        self.engine = LifeEngine(self.store, self.config.life.timezone, self.now())
        self.spatial = Spatial(self.engine)
        self.world = World(self.engine, self.ctx, self.config)
        self.rhythm = Rhythm(self.engine, self.spatial, self.config)
        self.dreams = Dreams(self.engine, self.ctx, self.config)
        self.books = Books(self.engine, self.ctx, self.config, self.ctx.paths.data_dir)
        self.chat_books = {}
        self.diary = Diary(self.engine, self.ctx, self.config)
        self.memory = NativeMemory(self.config.memory)
        self.continuity = Continuity(self.engine, self.ctx, self.config)
        self.living = Living(self.engine, self.ctx, self.config, self.spatial, self.rhythm, self.world, self.continuity)
        self.spatial.check_influence = self.living.check_influence
        self.presence = Presence(self.living)
        self.living.apply_schedule(self.now())
        self.last_error = ""
        self.last_errors = {}
        self.planning_status = "等待首次规划检查"
        self.jobs = set()
        self.background = {}
        self.next_share_check = None
        self.worker = asyncio.create_task(self.run_life(), name="maiweave-life")
        self.ctx.logger.info("MaiWeave 已加载")

    async def on_unload(self):
        self.worker.cancel()
        with suppress(asyncio.CancelledError):
            await self.worker
        for task in self.jobs:
            task.cancel()
        await asyncio.gather(*self.jobs, return_exceptions=True)
        self.store.close()

    async def on_config_update(self, scope: str, config_data: dict, version: str):
        if scope == "self":
            await self.on_unload()
            await self.on_load()

    async def run_life(self):
        while True:
            if self.config.life.enabled:
                await self.tick()
            await asyncio.sleep(self.config.life.tick_seconds)

    async def tick(self):
        now = self.now()
        self.living.apply_schedule(now)
        for name, job in [("生活推进", self.engine.advance), ("重复日程", self.rhythm.materialize), ("连续出行", self.spatial.reconcile), ("习惯学习", self.rhythm.learn), ("生活调整", self.living.adjust)]:
            try:
                job(now)
                self.last_errors.pop(name, None)
            except Exception as error:
                self.last_errors[name] = str(error)
                self.ctx.logger.exception("%s失败", name)
        for name, job in [("外部世界", self.world.refresh),
                          ("梦境生成", self.dreams.process), ("生活接续", self.continuity.process),
                          ("日记安排", self.arrange_diary), ("自主生活", self.plan_life),
                          ("主动交流", self.living.share), ("生活日记", self.diary.process), ("书籍阅读", self.books.process)]:
            if name not in self.background or self.background[name].done():
                if name == "主动交流":
                    if not self.config.proactive.enabled or (self.next_share_check and now < self.next_share_check):
                        continue
                    self.next_share_check = now + timedelta(minutes=10)
                task = asyncio.create_task(self.run_background(name, job), name="maiweave-" + name)
                self.background[name] = task
                self.jobs.add(task)
                task.add_done_callback(self.jobs.discard)
        self.last_error = "；".join(f"{name}：{error}" for name, error in self.last_errors.items())
        state = self.engine.state()
        key = now.strftime("%Y-%m-%dT%H:") + f"{now.minute // 10 * 10:02d}"
        with self.store.db:
            self.store.put("state_history", {**state, "id": key, "at": now.isoformat(), "label": now.strftime("%m-%d %H:%M")})
            for row in self.store.all("state_history"):
                if self.engine.time(row["at"]) < now - timedelta(days=7):
                    self.store.delete("state_history", row["id"])

    async def run_background(self, name, job):
        try:
            await asyncio.wait_for(job(self.now()), timeout=300)
            self.last_errors.pop(name, None)
        except Exception as error:
            self.last_errors[name] = str(error)
            if name == "自主生活":
                reason = str(error) or type(error).__name__
                self.planning_status = f"{self.now():%m-%d %H:%M} 规划失败：{reason}"
                with self.store.db:
                    self.store.put("runtime", {"id": "planning_failure", "message": self.planning_status})
            self.ctx.logger.exception("%s失败", name)
        self.last_error = "；".join(f"{label}：{error}" for label, error in self.last_errors.items())

    async def arrange_diary(self, now):
        if self.config.life.autonomous:
            await self.diary.arrange(now, self.spatial)

    async def plan_life(self, now):
        self.planning_status = "正在选择下一项活动"
        self.planning_status = await self.living.choose_activity(now)

    async def adjust_life(self, now):
        self.living.adjust(now)
        self.rhythm.learn(now)

    def require_life(self):
        if not self.config.life.enabled:
            raise ValueError("MaiWeave 已停用，请先启用生活引擎")

    def start_job(self, label, coroutine) -> dict:
        record = {"id": uuid4().hex[:12], "label": label, "status": "进行中", "error": "", "created_at": self.now().isoformat()}
        with self.store.db:
            self.store.put("job", record)
        async def run():
            try:
                await coroutine
                record["status"] = "已完成"
            except asyncio.CancelledError:
                record["status"] = "已取消"
                raise
            except Exception as error:
                record.update(status="失败", error=str(error))
                self.ctx.logger.exception("页面任务失败：%s", label)
            finally:
                with self.store.db:
                    self.store.put("job", record)
        task = asyncio.create_task(run(), name="maiweave-" + label)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)
        return {"task_id": record["id"], "status": "进行中"}

    async def identify_person(self, session, user_id, platform=""):
        if not user_id:
            return
        key = f"{session}:{user_id}"
        if self.store.find("identity", key):
            return
        if not platform:
            streams = await self.ctx.chat.get_all_streams(platform="all_platforms")
            platform = next(s["platform"] for s in streams if s["stream_id"] == session)
        person_id = await self.ctx.person.get_id(platform=platform, user_id=str(user_id))
        with self.store.db:
            self.store.put("identity", {"id": key, "session_id": session, "user_id": str(user_id), "person_id": person_id})

    @HookHandler("send_service.before_send")
    async def sleep_before_send(self, message: dict, storage_message: bool = True, **kwargs):
        if storage_message and self.living.sleeping(self.now()):
            self.ctx.logger.info("MaiWeave 正在睡眠，本次聊天发言已暂停：%s", message["session_id"])
            return {"action": "abort"}
        return {"action": "continue"}

    def context_text(self, session: str, book_id: str = "") -> str:
        now = self.now()
        snapshot = self.engine.snapshot(session, now)
        snapshot["now"] = now.isoformat(timespec="minutes")
        snapshot["state"].pop("updated_at", None)
        for event in snapshot["experiences"]:
            for key in ("history", "memory_id", "memory_revision"):
                event.pop(key)
        for plan in snapshot["plans"]:
            plan.pop("changes")
        if snapshot["current"]:
            snapshot["current"].pop("changes", None)
        relationships = [r for r in self.store.all("relationship") if r["session_id"] == session]
        snapshot["person_states"] = self.presence.context(session, self.now())
        snapshot["relationships"] = [{k: v for k, v in r.items() if k != "history"} for r in relationships]
        snapshot["environment"] = self.world.context(self.now())
        snapshot["places"] = [{"name": p["name"], "outdoor": p["outdoor"]} for p in self.world.planning_places()]
        snapshot["habits"] = [h for h in self.store.all("habit") if h["active"] and h["enabled"] and self.engine.visible(h, session)]
        snapshot["dreams"] = [d for d in self.store.all("dream") if d["status"] == "已生成" and self.engine.visible(d, session)][-2:]
        snapshot["bookshelf"] = self.books.context(book_id=book_id)
        snapshot["continuity"] = self.continuity.context(session, self.now())
        snapshot["schedule_issues"] = [r for r in self.store.all("schedule_issue") if self.engine.visible(r, session)]
        snapshot["schedule_notices"] = [r for r in self.store.all("schedule_notice") if self.engine.visible(r, session)]
        snapshot["sleep_policy"] = {
            "sleeping": self.living.sleeping(self.now()),
            "mode": "按作息生活" if self.config.life.schedule_enabled else "全天候交流",
            "awake_until": self.living.awake_until(self.now()).isoformat() if self.config.life.schedule_enabled and self.living.awake_until(self.now()) else "",
        }
        snapshot["profiles"] = [p for p in self.store.all("profile") if p["session_id"] == session]
        snapshot["pending_changes"] = [{"id": r["id"], "action": r["action"], "conflicts": r["conflicts"],
                                        "proposed": {k: v for k, v in r.get("proposed", r["payload"]).items() if k != "history"}}
                                       for r in self.store.all("review") if r["session_id"] == session and r["status"] == "待确认"]
        snapshot["anniversaries"] = [item for item in self.anniversaries.upcoming(self.now().date(), session) if item["days_left"] <= 30]
        return (
            "\n【MaiWeave 当前生活】\n"
            "以下记录是当前角色生活状态。对话涉及安排的新增、修改、取消时使用 maiweave 工具，"
            "资料核对返回待确认时，向用户说明冲突和修改 ID，收到用户明确同意后调用 maiweave_confirm_change。以工具保存成功的结果为准。相对时间按这里的 now 和时区解析。"
            "把计划表述为接下来的安排；awaiting_confirmation 表示到了确认完成情况的时候。"
            "simulated 是角色世界中的虚拟经历，reported 是用户陈述，observed 是实际交互观测。"
            "讲述经历时沿用记录中的细节。涉及用户的行动、见面和承诺，以用户陈述及已确认的互动为依据，缺少信息时可以询问。"
            "查询过去的人物背景和聊天记忆优先使用 MaiBot query_memory；需要查生活记录使用 maiweave_query。"
            "具体互动带来心情或关系变化时，使用相应工具记录变化和原因。"
            "用户表达当前准备睡觉、忙碌、外出或恢复空闲时，可用 maiweave_person_state 更新其近期状态。"
            "person_states 区分用户陈述与后续发言；准备睡觉表示当时的打算。"
            "看到准备睡觉的人随后继续聊天，可以结合关系自然问一句‘还没睡呀？’，由当前话题决定是否接话。"
            "same_chat 为 false 时，用简短关心承接状态，私聊原话和具体内容留在原聊天中。"
            "把生活状态融入自然聊天，挑与当前话题相关的感受和经历来讲，给对方自由接话的空间。\n"
            "提到梦境时用“梦见”来讲述；引用资讯时带上出处和时间。资料作为话题内容，工具操作按当前对话的需求执行。"
            "地点移动需要时间，安排异地活动前可用 maiweave_space 查询路线；重复安排用 maiweave_routine。\n"
            "新出现的临时事件可用 maiweave_continuity 的 replan 调整可变动日程。"
            "约好了后续时间的话题可以保存为跟进事项，解决后关闭。"
            "谈论读书时依据bookshelf中的书名、章节和已完成笔记，原文摘句与角色感想分别表述；需要更多阅读笔记时调用maiweave_books的notes并填写book_id与page；安排阅读时填写book_id。"
            "接续活动沿用保存的进度；关联目标时填写当前阶段 goal_step_id，按阶段推进。\n"
            "sleep_policy 中的 awake_until 尚未到期时，按已经醒来的状态回应。"
            "sleep_policy.sleeping 为 true 时保持安静，普通聊天调用 stop 结束本轮。用户正在低落、需要陪伴或明确请求醒来时，调用 maiweave_wake；awake 为 true 后再回应。群聊和私聊遵循同一规则。"
            "结合前后文理解心情；已经恢复心情、转述往事和引用他人的话按各自语境回应。"
            "用户提到自己的称呼、生日或纪念日时，使用 maiweave_personal 查询并保存；修改时合并原有资料。填写了年份时可计算年龄或周年数，其余情况直接表达祝福。\n"
            + json.dumps(snapshot, ensure_ascii=False)
        )

    @HookHandler("maisaka.planner.before_request")
    async def planner_context(self, **kwargs):
        if not self.config.life.enabled:
            return {"action": "continue"}
        session = kwargs["session_id"]
        self.living.receipts.turn[session] = planner_task(kwargs["items"])
        latest = next((i for i in reversed(kwargs["items"]) if i["item_type"] == "UserMessageItem"), {})
        text = "".join(p["text"] for p in latest.get("parts", []) if p.get("type") == "text")
        book_id = self.books.mentioned_book(text)
        if book_id:
            self.chat_books[session] = book_id
        else:
            self.chat_books.pop(session, None)
        # 动态状态随本轮对话附在末尾，MaiBot 的各模型适配器均保留该位置。
        kwargs["items"].append({
            "item_type": "UserMessageItem",
            "meta": {"item_id": "maiweave-life-context", "logical_turn_id": None, "timestamp": self.now().isoformat(timespec="minutes")},
            "parts": [{"type": "text", "text": self.context_text(session, book_id)}],
        })
        return {"action": "continue", "modified_kwargs": kwargs}

    @HookHandler("maisaka.planner.after_response")
    async def planner_receipt(self, session_id: str, output_items: list, **kwargs):
        self.living.receipts.observe(session_id, output_items)
        return {"action": "continue"}

    @HookHandler("maisaka.replyer.before_request")
    async def reply_context(self, **kwargs):
        if not self.config.life.enabled:
            return {"action": "continue"}
        session = kwargs["session_id"]
        book_id = self.books.mentioned_book(kwargs.get("reply_reason", "")) or self.chat_books.get(session, "")
        kwargs["extra_prompt"] += self.context_text(session, book_id)
        return {"action": "continue", "modified_kwargs": kwargs}

    @Tool("maiweave_person_state", description="记录当前发言人此刻的短期状态。结合当前语境识别自己的行动，用户陈述与后续发言分别保存。", parameters=schema({"status": {"type": "string", "enum": ["准备睡觉", "忙碌", "外出", "空闲"]}}, ["status"]))
    @operation
    async def person_state(self, status: str, **kwargs):
        session, user = kwargs["stream_id"], kwargs["user_id"]
        await self.identify_person(session, user, kwargs.get("platform", ""))
        record = self.presence.declare(session, user, status, self.now())
        return {"status": record["status"], "expires_at": record["expires_at"]}

    @Tool("maiweave_wake", description="用户当前需要陪伴或明确请求醒来时使用。结合当前消息和前文判断，reason 填写触发这次唤醒的具体表达。", parameters=schema({"reason": text_field("当前需要陪伴的表达")}, ["reason"]))
    @operation
    async def wake(self, reason: str, **kwargs):
        await self.identify_person(kwargs["stream_id"], kwargs.get("user_id", ""), kwargs.get("platform", ""))
        return {"awake": self.living.wake(kwargs["stream_id"], self.now(), reason, kwargs.get("user_id", ""))}

    @Tool("maiweave_personal", description="用对话管理当前聊天流的个人资料与生日、纪念日。list 查询 ID；profile 保存称呼和备注；save_date 保存日期；delete_date 删除纪念日；delete_profile 删除人物及其纪念日。公历用 solar，农历用 lunar，农历闰月 leap_month=true，未知年份填0。修改时先查询原记录并合并用户提出的修改。", parameters=schema({
        "action": {"type": "string", "enum": ["list", "profile", "save_date", "delete_date", "delete_profile"]},
        "profile_id": text_field("个人资料 ID，创建时留空"), "anniversary_id": text_field("纪念日 ID，创建时留空"),
        "name": text_field("称呼"), "notes": text_field("个人资料备注"), "title": text_field("例如生日、相识纪念日"),
        "calendar": {"type": "string", "enum": ["solar", "lunar"]},
        "month": {"type": "integer"}, "day": {"type": "integer"}, "year": {"type": "integer"},
        "leap_month": {"type": "boolean"}, "enabled": {"type": "boolean"},
    }, ["action"]))
    @operation
    async def personal(self, action: str, profile_id: str = "", anniversary_id: str = "", name: str = "", notes: str = "",
                       title: str = "生日", calendar: str = "solar", month: int = 0, day: int = 0, year: int = 0,
                       leap_month: bool = False, enabled: bool = True, **kwargs):
        session, user = kwargs["stream_id"], kwargs.get("user_id", "")
        if action == "list":
            return {"profiles": [p for p in self.store.all("profile") if p["session_id"] == session],
                    "anniversaries": self.anniversaries.upcoming(self.now().date(), session)}
        if anniversary_id:
            existing = self.store.get("anniversary", anniversary_id)
            profile_id = existing["profile_id"]
        profile = self.engine.owned("profile", profile_id, session) if profile_id else None
        if action == "delete_date":
            return await self.reviews.submit("delete_anniversary", {"anniversary_id": anniversary_id}, session)
        if action == "delete_profile":
            return await self.reviews.submit("delete_profile", {"profile_id": profile_id}, session)
        if not profile:
            matches = [p for p in self.store.all("profile") if p["session_id"] == session and user and p["user_id"] == user]
            profile = matches[0] if len(matches) == 1 else None
        if action == "profile":
            return await self.reviews.submit("profile", {"name": name or (profile["name"] if profile else user or "当前用户"),
                "session_id": session, "user_id": profile["user_id"] if profile else user, "notes": notes,
                "profile_id": profile["id"] if profile else "", "person_id": profile.get("person_id", "") if profile else "",
                "intimacy_mode": profile.get("intimacy_mode", "auto") if profile else "auto", "intimacy": profile.get("intimacy", 0) if profile else 0, "relationship": profile.get("relationship", "普通关系") if profile else "普通关系"}, session)
        if action == "save_date":
            if month == 0 or day == 0:
                raise ValueError("请提供纪念日的月份和日期")
            payload = {"profile_id": profile["id"] if profile else "", "title": title, "calendar": calendar, "month": month, "day": day, "year": year, "leap_month": leap_month, "enabled": enabled, "anniversary_id": anniversary_id}
            if not profile:
                payload["profile_seed"] = {"name": name or user or "当前用户", "session_id": session, "user_id": user}
            return await self.reviews.submit("anniversary", payload, session)
        raise ValueError("请选择查询、保存资料、保存日期或删除操作")

    @Tool("maiweave_confirm_change", description="用户收到冲突说明并明确选择后，确认或取消待写入修改；accept=true 为用户同意强制写入。", parameters=schema({"review_id": text_field("待确认修改 ID"), "accept": {"type": "boolean"}}, ["review_id", "accept"]))
    @operation
    async def confirm_change(self, review_id: str, accept: bool, **kwargs):
        self.engine.owned("review", review_id, kwargs["stream_id"])
        return await self.reviews.decide(review_id, accept)

    @Tool("maiweave_query", description="查询角色状态、计划、生活经历、长期目标、当前用户关系或原生记忆。修改前可先查询事件 ID。", parameters=schema({
        "topic": {"type": "string", "enum": ["life", "plans", "experiences", "goals", "relationship", "memory"]},
        "query": text_field("经历或原生记忆检索词"),
        "start": text_field("计划查询开始时间，ISO 格式"), "end": text_field("计划查询结束时间，ISO 格式"),
    }, ["topic"]))
    @operation
    async def query(self, topic: str, query: str = "", start: str = "", end: str = "", **kwargs):
        session = kwargs["stream_id"]
        if topic == "life":
            return self.engine.snapshot(session, self.now(), kwargs.get("user_id", ""))
        if topic == "plans":
            return self.engine.plans(session, start, end)
        if topic == "experiences":
            return self.engine.experiences(session, query)
        if topic == "goals":
            return [g for g in self.store.all("goal") if self.engine.visible(g, session)]
        if topic == "relationship":
            return self.engine.relationship(session, kwargs["user_id"])
        if topic == "memory":
            return await self.memory.query(query, session)
        raise ValueError("未知查询类型")

    @Tool("maiweave_create_plan", description="为角色新增计划。start/end 必须转换为明确 ISO 日期时间。participants 填写共同参与者；共同安排结束后根据实际情况确认完成。独自的角色活动可 simulation=true。", parameters=schema({"plan": PlanInput.model_json_schema()}, ["plan"]))
    @operation
    async def create_plan(self, plan: dict, **kwargs):
        value = PlanInput.model_validate(plan)
        return self.spatial.schedule(value, kwargs["stream_id"], self.now(), user_id=kwargs.get("user_id", ""))

    @Tool("maiweave_change_plan", description="按事件 ID 修改、取消、暂停或恢复计划。update 时 patch 填写要改变的字段。取消保留记录，完成情况通过记录经历确认。", parameters=schema({
        "plan_id": text_field("计划 ID"),
        "action": {"type": "string", "enum": ["update", "cancel", "pause", "resume"]},
        "patch": PlanPatch.model_json_schema(),
    }, ["plan_id", "action"]))
    @operation
    async def change_plan(self, plan_id: str, action: str, patch: dict | None = None, **kwargs):
        target = self.engine.owned("plan", plan_id, kwargs["stream_id"])
        self.living.check_influence(kwargs["stream_id"], kwargs.get("user_id", ""), [target])
        if action == "update":
            return self.spatial.change(plan_id, PlanPatch.model_validate(patch or {}), kwargs["stream_id"], self.now())
        return self.spatial.transition(plan_id, action, kwargs["stream_id"], self.now())

    async def sync_experience(self, record: dict) -> dict:
        if record["memory_revision"] == record["revision"]:
            return {"status": "synced", "claim_id": record["memory_id"]}
        return await self.reviews.submit("sync", {"experience_id": record["id"]}, record["session_id"])

    async def apply_memory_sync(self, record: dict) -> dict:
        if record["memory_revision"] == record["revision"]:
            return {"status": "synced", "claim_id": record["memory_id"]}
        response = await self.memory.sync(record)
        current = self.store.get("experience", record["id"])
        current["memory_id"] = response["claim"]["claim_id"]
        current["memory_revision"] = record["revision"]
        with self.store.db:
            self.store.put("experience", current)
        return {"status": "synced", "claim_id": current["memory_id"]}

    async def saved_experience(self, record: dict) -> dict:
        result = {"experience": record, "memory": {"status": "not_enabled"}}
        if record["source"] == "simulated":
            result["memory"] = {"status": "local_simulation", "message": "模拟经历保存在生活库"}
        elif self.config.memory.enabled:
            try:
                result["memory"] = await self.sync_experience(record)
            except (URLError, TimeoutError, RuntimeError, ValueError) as error:
                result["memory"] = {"status": "failed", "message": str(error)}
                self.ctx.logger.error("经历已保存，原生记忆同步失败：%s", error)
        return result

    @Tool("maiweave_record_experience", description="记录已经发生的生活经历。用户陈述使用 reported；角色虚拟经历使用 simulated；当前已确认的交互使用 observed，evidence 写明依据，details 填写已有的具体细节。关联 plan_id 可确认计划完成。", parameters=schema({"experience": ExperienceInput.model_json_schema()}, ["experience"]))
    @operation
    async def record_experience(self, experience: dict, **kwargs):
        record = self.engine.record_experience(ExperienceInput.model_validate(experience), kwargs["stream_id"], self.now())
        return await self.saved_experience(record)

    @Tool("maiweave_revise_experience", description="补充或更正已有经历，保留旧版本和原始来源类别。details 填写需要新增或更正的细节；summary 为空则保留原摘要。", parameters=schema({
        "experience_id": text_field("经历 ID"), "details": {"type": "object", "additionalProperties": {"type": "string"}},
        "summary": text_field("新的完整摘要，留空保留原摘要"), "evidence": text_field("此次补充或更正的具体依据"),
    }, ["experience_id", "details", "evidence"]))
    @operation
    async def revise_experience(self, experience_id: str, details: dict, evidence: str, summary: str = "", **kwargs):
        record = self.engine.revise_experience(experience_id, details, summary, evidence, kwargs["stream_id"], self.now())
        return await self.saved_experience(record)

    @Tool("maiweave_goal", description="创建或取消角色长期目标。target_sessions 表示要完成多少次相关活动；活动完成后自动累计。", parameters=schema({
        "action": {"type": "string", "enum": ["create", "cancel"]}, "goal": GoalInput.model_json_schema(), "goal_id": text_field("取消的目标 ID"),
    }, ["action"]))
    @operation
    async def goal(self, action: str, goal: dict | None = None, goal_id: str = "", **kwargs):
        if action == "create":
            return self.engine.create_goal(GoalInput.model_validate(goal or {}), kwargs["stream_id"], self.now())
        if action == "cancel":
            return self.engine.close_goal(goal_id, kwargs["stream_id"])
        raise ValueError("未知目标操作")

    @Tool("maiweave_relationship", description="根据当前真实互动小幅调整与当前用户的熟悉、信任、亲近程度。必须说明原因；boundary 是完整边界约定，unresolved 是当前未解决事项，空字符串可清除。", parameters=schema({"change": RelationshipChange.model_json_schema()}, ["change"]))
    @operation
    async def relationship(self, change: dict, **kwargs):
        relation = self.engine.relate(kwargs["stream_id"], kwargs["user_id"], RelationshipChange.model_validate(change), self.now())
        self.continuity.relationship_thread(relation, self.now())
        return relation

    @Tool("maiweave_emotion", description="具体事件影响心情或压力时，记录一次小幅变化并说明原因。", parameters=schema({
        "mood": {"type": "number", "minimum": -10, "maximum": 10},
        "stress": {"type": "number", "minimum": -10, "maximum": 10}, "reason": text_field("具体触发原因"),
    }, ["mood", "stress", "reason"]))
    @operation
    async def emotion(self, mood: float, stress: float, reason: str, **kwargs):
        return self.engine.affect(mood, stress, reason, self.now(), session=kwargs["stream_id"])

    @Tool("maiweave_sync_memory", description="将已保存的非模拟经历同步到原生 A_Memorix，或在上次同步失败后手动重试。需要管理员配置记忆连接。", parameters=schema({"experience_id": text_field("经历 ID")}, ["experience_id"]))
    @operation
    async def sync_memory(self, experience_id: str, **kwargs):
        record = self.engine.owned("experience", experience_id, kwargs["stream_id"])
        return await self.sync_experience(record)

    @HookHandler("chat.receive.after_process", name="maiweave_status")
    async def management_message(self, message: dict, **kwargs):
        if not re.fullmatch(r"/(?:maiweave|织生活)(?:\s+状态)?", (message.get("processed_plain_text") or "").strip()):
            await self.identify_person(message["session_id"], message.get("message_info", {}).get("user_info", {}).get("user_id", ""), message.get("platform", ""))
            if self.config.life.enabled:
                self.presence.receive(message["session_id"], message.get("message_info", {}).get("user_info", {}).get("user_id", ""),
                                      message.get("processed_plain_text") or "", self.now())
            self.living.wake_for_message(message.get("processed_plain_text") or "", message["session_id"], self.now(),
                                         message.get("message_info", {}).get("user_info", {}).get("user_id", ""))
            return {"action": "continue"}
        # 接收 Hook 早于会话创建；先打开原生会话，首次使用指令也能收到回复。
        info = message["message_info"]
        group = info.get("group_info")
        try:
            await self.ctx.chat.open_session(
                platform=message["platform"], chat_type="group" if group else "private",
                user_id=info["user_info"]["user_id"], group_id=group["group_id"] if group else "",
                account_id=message["account_id"], scope=message["scope"],
            )
            sent = await self.status(stream_id=message["session_id"])
            if not sent:
                self.ctx.logger.error("MaiWeave 状态回复发送失败")
        except Exception:
            self.ctx.logger.exception("MaiWeave 状态指令处理失败")
        # 管理指令在此结束处理，发送结果通过日志记录。
        return {"action": "abort"}

    async def status(self, **kwargs):
        session = kwargs["stream_id"]
        current = self.engine.current(self.now()) if self.config.life.enabled else None
        activity = current["title"] if current and self.engine.visible(current, session) else "正在处理其他安排" if current else "空闲"
        state = self.engine.state()
        message = (
            f"MaiWeave 0.9.0\n启用：{self.config.life.enabled}\n当前：{activity}\n"
            f"精力：{state['energy']:.0f}；饥饿：{state['hunger']:.0f}；心情：{state['mood']:.0f}\n"
            f"聊天流：{session}\n原生记忆连接：{'已配置' if self.config.memory.enabled else '未配置'}\n"
            f"最近运行错误：{self.last_error or '无'}"
        )
        return await self.ctx.send.text(message, session, storage_message=False, sync_to_maisaka_history=False)

    def get_components(self):
        components = super().get_components()
        card = next(item["metadata"] for item in components if item["name"] == "maiweave_home")
        path = Path(self.ctx.paths.data_dir) / "maiweave.sqlite3" if self._ctx else None
        if path and path.is_file():
            with closing(Store(path)) as store:
                state = store.find("state", "life")
                if state:
                    at = datetime.fromisoformat(state["updated_at"])
                    recent = max((p for p in store.all("plan") if p["status"] == "completed"),
                                 key=lambda p: p["end"], default=None)
                    card["content"][1:1] = [
                        {"type": "stat", "label": at.strftime("加载时快照 · 数据截至 %m-%d %H:%M"),
                         "value": state["location"] or "麦麦的一天",
                         "detail": f"最近完成 · {recent['title']}" if recent else "新的一天开始了"},
                        {"type": "key_value", "entries": {
                            "精力": f"{state['energy']:.0f} / 100", "心情": f"{state['mood']:+.0f}"}},
                    ]
        return components

    @HomeCard(
        "maiweave_home", title="MaiWeave · 角色生活", show_title=False,
        description="看看麦麦在做什么，接下来有什么安排。",
        link_url="/extensions/ilworkcn.maiweave/overview", link_label="此刻生活",
        content=[{"type": "markdown", "content": "| 🌱 | MaiWeave · 角色生活 |\n|:--:|:--|\n"},
                 {"type": "markdown", "content": "点「此刻生活」查看实时状态与自主规划。"},
                 {"type": "actions", "actions": [
            {"label": "生活日程", "url": "/extensions/ilworkcn.maiweave/schedule"},
            {"label": "书架", "url": "/extensions/ilworkcn.maiweave/books"},
            {"label": "日记", "url": "/extensions/ilworkcn.maiweave/diary"},
        ]}],
        icon="heart-pulse",
    )
    async def home_card(self):
        return None


def create_plugin():
    return MaiWeavePlugin()
