"""MaiBot 原生工作区页面的数据和操作接口。"""

import json
import math
from datetime import date, timedelta

from maibot_sdk import API

from ..core.models import GoalInput, PlanInput, PlanPatch, RoutineInput
from ..integrations.usage import summary as usage_summary

KIND = {"sleep": "睡眠", "cook": "做饭", "meal": "用餐", "work": "工作", "study": "学习", "leisure": "休闲", "travel": "出行", "rest": "休息", "social": "社交", "diary": "写日记"}
STATUS = {"scheduled": "未开始", "active": "进行中", "completed": "已完成", "cancelled": "已取消", "paused": "已暂停", "awaiting_confirmation": "等待完成确认", "blocked": "需要调整"}
SOURCE = {"reported": "用户陈述", "observed": "交互观测", "simulated": "角色模拟", "dream": "梦境"}
FREQUENCY = {"daily": "按日", "weekly": "按周", "monthly": "按月"}
WEEKDAYS = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6}


def display_time(value: str) -> str:
    return value.replace("T", " ")[:19] if value else ""


def list_row(row):
    """列表只携带当前内容，修订历史与写作素材在详情中读取。"""
    return {key: value for key, value in row.items() if key not in {"history", "changes", "material"}}


def page_tables(data, page, keys):
    """同一查询的记录表按页浏览，保留完整的历史记录。"""
    pages = max(1, *(math.ceil(len(data[key]) / 20) for key in keys))
    page = min(pages, max(1, page))
    total = sum(len(data[key]) for key in keys)
    for key in keys:
        data[key] = [list_row(row) for row in data[key][(page - 1) * 20:page * 20]]
    data["pagination"] = {"page": page, "pages": pages, "total": total}
    return data


def plan_row(plan):
    return {**list_row(plan), "kind_label": KIND[plan["kind"]], "status_label": STATUS[plan["status"]],
            "start_label": display_time(plan["start"]), "end_label": display_time(plan["end"]),
            "scope_label": plan["session_id"] or "公共生活", "participants_label": "、".join(plan["participants"]) or "独自活动",
            "origin_label": "重复日程" if plan.get("routine_id") else ("自主安排" if plan["autonomous"] else "对话或页面安排")}


class WebUI:
    @API("ui_retention", description="预览数据保留与预计清理数量")
    async def ui_retention(self) -> dict:
        return self.retention.preview(self.now())

    @API("ui_cleanup", description="按当前保留设置清理旧记录")
    async def ui_cleanup(self) -> dict:
        return self.retention.clean(self.now())

    @API("ui_keep_experience", description="设置经历是否长期保留")
    async def ui_keep_experience(self, experience_id: str, keep: bool) -> dict:
        row = self.store.get("experience", experience_id)
        with self.store.db:
            self.store.put("experience", {**row, "keep": keep})
        return {"keep": keep}

    @API("ui_record_detail", description="分页读取修订历史与日记素材")
    async def ui_record_detail(self, category: str, record_id: str, field: str, page: int = 1) -> dict:
        fields = {"experience": {"history"}, "relationship": {"history"}, "plan": {"changes"}, "diary": {"material"}, "reading": {"content"}, "research": {"results"}, "research_task": {"sources"}}
        if field not in fields.get(category, set()):
            raise ValueError("请选择记录中的详情字段")
        value = self.store.get(category, record_id)[field]
        if category == "research_task" and field == "sources":
            text = "\n\n".join(f"{i}. {source['title']}\n来源：{source['url']}\n发布时间：{source.get('published_at') or '未提供'}\n"
                                 f"获取时间：{display_time(source['retrieved_at'])} · {source['scope']}\n\n{source['content']}"
                                 for i, source in enumerate(value, 1))
        else:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        pages = max(1, math.ceil(len(text) / 12000))
        page = min(pages, max(1, page))
        return {"text": text[(page - 1) * 12000:page * 12000], "page": page, "pages": pages}

    @API("ui_books", description="书架、阅读进度与笔记")
    async def ui_books(self, book_id: str = "", page: int = 0) -> dict:
        current = self.store.find("runtime", "current_book")
        notes = self.books.notes(book_id, page)
        return {"current_book": current["book_id"] if current else "", "books": self.store.all("book"), "uploads": self.books.uploads(),
                "places": self.world.planning_places(), "notes": notes["items"], "notes_total": notes["total"], "page": notes["page"], "capacity": self.books.capacity(),
                "sessions": [{**n, "status": "日程已取消" if (self.store.find("plan", n["id"]) or {"status": n.get("plan_status", "completed")})["status"] == "cancelled" else n["status"]} for n in self.store.all("book_note")[-10:]], "now": self.now().isoformat()}

    @API("ui_book_chapters", description="分页查看书籍章节与正文预览")
    async def ui_book_chapters(self, book_id: str, page: int = 0) -> dict:
        return self.books.chapters(book_id, page)

    @API("ui_book_upload", description="上传书籍资料")
    async def ui_book_upload(self, action: str, upload_id: str = "", filename: str = "", title: str = "", author: str = "", content: str = "", size: int = 0) -> dict:
        if action == "begin":
            return self.books.begin(filename, title, author, size)
        if action == "abort":
            return self.books.abort_upload(upload_id)
        if action == "append":
            return await self.books.append(upload_id, content)
        if action == "finish":
            return await self.books.finish(upload_id, self.now())
        raise ValueError("请选择开始上传、上传片段或完成导入")

    @API("ui_book_action", description="选书、安排阅读与重试阅读")
    async def ui_book_action(self, action: str, book_id: str = "", start: str = "", minutes: int = 30, plan_id: str = "", title: str = "", author: str = "", location: str = "", chapter: int = 0) -> dict:
        if action in {"edit", "archive", "restore", "delete"}:
            return self.books.manage(action, book_id, title, author)
        self.require_life()
        if action == "select":
            return self.books.select(book_id)
        if action in {"switch", "seek", "skip"}:
            now = self.now()
            current = self.engine.current(now)
            # 管理页面沿用当前日程的归属，聊天工具使用调用者的聊天流。
            session = current["session_id"] if current else ""
            return self.books.navigate(action, book_id, chapter, self.spatial, now, session)
        if action == "schedule":
            return self.books.schedule(book_id, start, minutes, self.spatial, self.now(), location=location)
        if action == "retry":
            note = self.store.get("book_note", plan_id)
            if note["status"] == "阅读失败":
                with self.store.db:
                    self.store.put("book_note", {**note, "retry_at": ""})
            return self.start_job("书籍阅读", self.books.process(self.now()))
        raise ValueError("请选择设为当前读物、安排阅读或重试")

    @API("ui_operation_status", description="查看后台操作与日记写作进度")
    async def ui_operation_status(self, task_id: str = "", plan_id: str = "") -> dict:
        if task_id:
            job = self.store.get("job", task_id)
            status = job["status"]
            message = job["label"] + "：" + status
            if status == "已完成" and job["label"] == "搜索城市地点":
                message = "地点搜索：" + self.world.place_status()["status"]
            return {"done": status != "进行中", "error": job["error"] if status == "失败" else "",
                    "message": message}
        plan = self.store.get("plan", plan_id)
        diary = next((row for row in self.store.all("diary", omit=('material',)) if row.get("plan_id") == plan_id), None)
        if diary and diary["status"] == "已生成":
            return {"done": True, "error": "", "message": "日记已生成，可在日记本中阅读"}
        if diary and diary["status"] == "生成失败":
            return {"done": True, "error": diary["error"], "message": "日记生成失败"}
        if diary and diary["status"] == "生成中":
            return {"done": False, "error": "", "message": "模型正在写日记…"}
        if plan["status"] == "cancelled":
            return {"done": True, "error": "", "message": "写日记的安排已取消"}
        start = self.engine.time(plan["start"])
        if start > self.now():
            return {"done": True, "error": "", "message": "已安排写日记，开始时间：" + display_time(plan["start"])}
        if plan["status"] in {"completed", "awaiting_confirmation"}:
            return {"done": True, "error": "写作窗口已结束，本次尚未生成日记，请重新安排写作", "message": "写作窗口已结束"}
        return {"done": False, "error": "", "message": "日记已安排，等待生活时钟进入写作窗口…"}

    @API("ui_change_begin", description="异步核对资料修改")
    async def ui_change_begin(self, action: str, payload: str) -> dict:
        record = self.reviews.create(action, json.loads(payload))
        self.start_job("资料核对", self.reviews.check(record["id"]))
        return {"review_id": record["id"]}

    @API("ui_change_status", description="查询核对结果")
    async def ui_change_status(self, review_id: str) -> dict:
        record = self.store.get("review", review_id)
        return {key: list_row(value) if key in {"result", "proposed"} and isinstance(value, dict) else value
                for key, value in record.items()}

    @API("ui_change_decide", description="确认或取消有冲突的修改")
    async def ui_change_decide(self, review_id: str, accept: bool) -> dict:
        self.start_job("确认资料修改", self.reviews.decide(review_id, accept))
        return {"review_id": review_id}

    @API("ui_chat_choices", description="读取 MaiBot 聊天流选项")
    async def ui_chat_choices(self) -> dict:
        rows = await self.ctx.chat.get_all_streams(platform="all_platforms")
        return {"items": [{"id": row["stream_id"], "name": row.get("group_name") or row.get("user_cardname") or row.get("user_nickname") or row["stream_id"]} for row in rows]}

    @API("ui_diaries", description="按日期查看生活日记")
    async def ui_diaries(self, day: str = "", page: int = 1) -> dict:
        rows = sorted(self.store.all("diary", omit=('material',)), key=lambda row: row["day"], reverse=True)
        if day:
            rows = [row for row in rows if row["day"] == day]
        return page_tables({"entries": [{**list_row(row), "detail_kind": "diary", "coverage": "全天记录" if row["complete"] else "截至写作时刻",
                              "material_text": "", "publication_status": (self.store.find("diary_publication", row["id"]) or {}).get("status", "尚未生成文案")} for row in rows]}, page, ['entries'])

    @API("ui_diary_publication", description="读取日记空间文案和发布状态")
    async def ui_diary_publication(self, day: str) -> dict:
        return self.diary_publisher.view(day)

    @API("ui_diary_publish_action", description="生成、编辑和发布日记空间文案")
    async def ui_diary_publish_action(self, action: str, day: str = "", content: str = "") -> dict:
        if action in {"confirm_published", "confirm_unpublished"}:
            return self.diary_publisher.reconcile(day, action == "confirm_published", self.now())
        if action == "check":
            return self.start_job("检查空间连接", self.diary_publisher.check_connection())
        if action == "generate":
            return self.start_job("生成空间文案", self.diary_publisher.prepare(day, self.now()))
        if action == "save":
            return self.diary_publisher.save(day, content, self.now())
        if action == "publish":
            return self.start_job("发布日记到空间", self.diary_publisher.publish(day, self.now()))
        raise ValueError("请选择检查连接、生成文案、保存或发布")

    @API("ui_write_diary", description="生成或重新整理指定日期的生活日记")
    async def ui_write_diary(self, day: str = "") -> dict:
        self.require_life()
        now = self.now()
        chosen = date.fromisoformat(day) if day else now.date()
        if chosen > now.date():
            raise ValueError("请选择今天或过去的日期")
        material = self.diary.material(chosen, now)
        if not any(material[key] for key in ("活动", "经历", "状态变化", "梦境")):
            raise ValueError("这一天还没有生活记录，积累活动或经历后即可写日记")
        plan = PlanInput(title="写生活日记", kind="diary", start=now.isoformat(), end=(now + timedelta(minutes=15)).isoformat(),
                         diary_day=chosen.isoformat(), motivation="用户安排写作窗口", flexible=True)
        return self.spatial.schedule(plan, "", now)

    @API("ui_usage", description="模型调用用量与趋势")
    async def ui_usage(self, days: str = "7", feature: str = "") -> dict:
        result = usage_summary(self.store, self.now(), int(days), "" if feature == "all" else feature)
        result["chat_context"] = list(self.chat_context.recent)
        return result

    @API("ui_editor_records", description="按类别查找可编辑记录")
    async def ui_editor_records(self, category: str, query: str = "", page: int = 1, record_id: str = "") -> dict:
        if category not in {"profile", "anniversary", "plan", "routine", "place", "route", "experience", "thread"}:
            raise ValueError("请选择资料、纪念日、日程、重复规则、地点、路线、经历或跟进事项")
        rows = self.anniversaries.upcoming(self.now().date()) if category == "anniversary" else self.store.all(category, omit=("history", "changes", "material"))
        if category == "profile":
            rows = [{**row, "effective_intimacy": self.living.intimacy(row["session_id"], row["user_id"])} for row in rows]
        if category == "plan":
            rows = [row for row in rows if not row.get("activity_id") and row["status"] in {"scheduled", "active", "paused", "blocked", "awaiting_confirmation", "cancelled"}]
        if query.strip():
            rows = [row for row in rows if query.casefold() in json.dumps(row, ensure_ascii=False).casefold()]
        if record_id:
            index = next((i for i, row in enumerate(reversed(rows)) if row["id"] == record_id), None)
            if index is not None:
                page = index // 20 + 1
        return page_tables({"today": self.now().date().isoformat(), "records": rows[::-1], "total": len(rows), "profiles": [{"id": p["id"], "name": p["name"]} for p in self.store.all("profile")],
                "places": [{"id": p["id"], "name": p["name"]} for p in self.store.all("place")], "memory_enabled": self.config.memory.enabled}, page, ['records'])

    @API("ui_anniversaries", description="个人资料和纪念日")
    async def ui_anniversaries(self) -> dict:
        return {"profiles": self.store.all("profile"), "anniversaries": self.anniversaries.upcoming(self.now().date())}

    @API("ui_memory_connection", description="原生记忆实际连接状态")
    async def ui_memory_connection(self) -> dict:
        info = self.memory.connection_info()
        if info["checked_at"]:
            info["checked_at"] = self.engine.time(info["checked_at"]).astimezone(self.now().tzinfo).strftime("%Y-%m-%d %H:%M:%S")
        return info

    @API("ui_check_memory", description="检查原生记忆连接")
    async def ui_check_memory(self) -> dict:
        self.memory.connection["status"] = "检查中"
        return self.start_job("检查原生记忆连接", self.memory.check_connection())

    @API("ui_place_discovery", description="城市地点搜索状态")
    async def ui_place_discovery(self) -> dict:
        status = self.world.place_status()
        search = await self.world.search_status()
        return {**status, "search_status": search["summary"]}

    @API("ui_discover_places", description="按天气城市搜索并收录生活地点")
    async def ui_discover_places(self, activity: str = "", force: bool = False) -> dict:
        if not self.config.world.places_enabled:
            raise ValueError("请在天气与见闻配置中开启按城市自动搜索生活地点")
        return self.start_job("搜索城市地点", self.world.discover_places(self.spatial, self.now(), force=force, activity=activity))

    @API("ui_memory_people", description="搜索原生记忆人物画像")
    async def ui_memory_people(self, query: str = "") -> dict:
        result = await self.memory.people(query)
        return {"items": [{key: row.get(key, "") for key in ("person_id", "person_name", "user_nickname", "profile_text")}
                          for row in result["items"]]}

    @API("ui_memory_person", description="读取选中的原生人物画像")
    async def ui_memory_person(self, person_id: str) -> dict:
        result = await self.memory.people(person_id=person_id)
        row = next((item for item in result["items"] if item["person_id"] == person_id), None)
        if row is None:
            raise ValueError("请在原生人物画像中选择已有的人物")
        override = row.get("manual_override") or ""
        return {"person_id": person_id, "name": row.get("person_name") or row.get("user_nickname") or person_id,
                "profile_text": row.get("profile_text", ""),
                "override_text": (override.get("override_text") or override.get("text", "")) if isinstance(override, dict) else override}

    async def ui_edit_memory_person(self, person_id: str, text: str) -> dict:
        await self.ui_memory_person(person_id)
        return await self.memory.edit_person(person_id, text)

    async def ui_save_profile(self, name: str, session_id: str, user_id: str = "", notes: str = "", profile_id: str = "", person_id: str = "", intimacy_mode: str = "auto", intimacy: int = 0, relationship: str = "普通关系") -> dict:
        if intimacy_mode not in {"auto", "manual"} or intimacy not in {0, 1, 2, 3}:
            raise ValueError("请选择自动或手动关系，以及普通、熟悉、亲近或亲密等级")
        if person_id and not profile_id:
            existing = next((row for row in self.store.all("profile") if row.get("person_id") == person_id and row["session_id"] == session_id), None)
            profile_id = existing["id"] if existing else ""
        if person_id:
            person = await self.ui_memory_person(person_id)
            name = person["name"]
            user_id = str(await self.ctx.person.get_value(person_id=person_id, field_name="user_id") or "")
        record = self.anniversaries.save_profile(name, session_id, user_id, notes, profile_id, person_id)
        record.update(intimacy_mode=intimacy_mode, intimacy=intimacy, relationship=relationship)
        with self.store.db:
            self.store.put("profile", record)
        return record

    async def ui_save_anniversary(self, profile_id: str, title: str, calendar: str, month: int, day: int,
                                  year: int = 0, leap_month: bool = False, enabled: bool = True, anniversary_id: str = "") -> dict:
        return self.anniversaries.save_date(profile_id, title, calendar, month, day, year, leap_month, enabled, anniversary_id)

    async def ui_delete_profile(self, profile_id: str) -> dict:
        return self.anniversaries.delete("profile", profile_id)

    async def ui_delete_anniversary(self, anniversary_id: str) -> dict:
        return self.anniversaries.delete("anniversary", anniversary_id)

    @API("ui_overview", description="生活状态和趋势")
    async def ui_overview(self) -> dict:
        await self.living.receipts.refresh(self.now())
        self.living.settle_shares()
        share_summary = self.living.share_summary(self.now())
        share_decisions = self.living.receipts.history(self.living.share_decisions)
        if share_summary or share_decisions:
            streams = {row["stream_id"]: row for row in await self.ctx.chat.get_all_streams(platform="all_platforms")}
            profiles = {row["session_id"]: row.get("name", "") for row in self.store.all("profile")}
            for row in [*share_summary, *share_decisions]:
                session = row["session"]
                stream = streams.get(session, {})
                group = bool(stream.get("group_id"))
                if session == "全部":
                    row["session_name"] = "全部聊天"
                else:
                    row["session_name"] = (stream.get("group_name") or stream.get("user_cardname") or
                        stream.get("user_nickname") or profiles.get(session) or
                        ("群聊" if group else "私聊" if stream else "未找到聊天资料"))
                platform = stream.get("platform", "")
                platform = {"qq": "QQ", "internal": "WebUI"}.get(platform, platform)
                target = stream.get("group_id") if group else stream.get("user_id")
                row["session_detail"] = " · ".join(part for part in [platform, "群聊" if group else "私聊" if stream else "",
                    ("群号 " if group else "账号 ") + str(target) if target else ""] if part)
        now, state = self.now(), self.engine.state()
        cfg = self.config.proactive
        share_settings = {"mode": cfg.mode, "hours": f"{cfg.start_hour:02}:00—{cfg.end_hour:02}:00" if cfg.start_hour != cfg.end_hour else "全天",
                          "interval": cfg.cooldown_minutes, "retry": cfg.retry_minutes,
                          "next_check": (display_time(self.next_share_check.isoformat()) if self.next_share_check and self.next_share_check > now else "下一轮生活更新时")
                              if cfg.enabled and self.config.life.enabled else "主动交流已关闭" if not cfg.enabled else "生活引擎已关闭"}
        current = self.engine.current(now)
        plans = self.store.all("plan", omit=('changes',))
        choices = sorted((p for p in plans if p.get("autonomous") and not p.get("activity_id")
                          and not p.get("routine_id") and p["kind"] not in {"travel", "diary"}),
                         key=lambda p: p["created_at"], reverse=True)[:10]
        decisions = []
        for p in choices:
            if "decision" in p:
                decisions.append(p["decision"])
            else:
                decisions.append({"at": self.engine.time(p["created_at"]).strftime("%m-%d %H:%M"),
                                  "source": "已有自主日程", "title": p["title"], "place": p["location"],
                                  "minutes": round((self.engine.time(p["end"]) - self.engine.time(p["start"])).total_seconds()/60),
                                  "reason": p["motivation"]})
        latest = decisions[0] if decisions else None
        failure = self.store.find("runtime", "planning_failure")
        journey = state.get("journey")
        travel = f"{journey['location']} → {journey['next']}（{journey['progress']}%）" if journey and journey["next"] else "当前没有移动"
        history = sorted(self.store.all("state_history"), key=lambda r: r["at"])[-144:]
        since = now - timedelta(hours=24)
        trend = [r for r in history if since <= self.engine.time(r["at"]) <= now]
        if len(trend) > 13:
            trend = [trend[round(i * (len(trend) - 1) / 12)] for i in range(13)]
        return {"state": state, "activity": current["title"] if current else "空闲", "travel": travel,
                "planning_latest": (f"{latest['at']} · {latest['title']} · {latest['source']} · "
                                    f"{latest['place']} · {latest['minutes']} 分钟" if latest else "暂无自主安排"),
                "planning_reason": latest["reason"] if latest else "等待首次安排",
                "planning_status": self.planning_status if self.config.life.enabled else "生活引擎已关闭",
                "planning_failure": failure["message"] if failure else "暂无失败记录",
                "planning_history": decisions,
                "share_status": f"已启用 · {self.config.proactive.mode}" if self.config.proactive.enabled else "已关闭",
                "share_decisions": share_decisions,
                "share_summary": share_summary,
                "share_settings": share_settings,
                "location": state["location"] or "未登记", "sleep": "睡眠中" if current and current["kind"] == "sleep" else "清醒",
                "schedule_mode": "按日常作息" if self.config.life.schedule_enabled else "全天候交流",
                "awake_until": display_time(self.living.awake_until(now).isoformat()) if self.living.awake_until(now) and self.config.life.schedule_enabled else "当前没有陪伴时段",
                "mood_label": "心情不错" if state["mood"] > 10 else ("有些低落" if state["mood"] < -10 else "平静"),
                "updated_at": display_time(state["updated_at"]), "enabled": "已启用" if self.config.life.enabled else "已停用",
                "pending_count": sum(p["status"] in {"scheduled", "active"} for p in plans),
                "experience_count": len(self.store.all("experience", omit=('history',))), "dream_count": len(self.store.all("dream")),
                "habit_count": sum(h["active"] for h in self.store.all("habit")), "history": history,
                "energy_trend": [{"time": self.engine.time(r["at"]).strftime("%H:%M"),
                                  "energy": r["energy"], "mood": r["mood"]} for r in trend],
                "next_plans": [plan_row(p) for p in sorted(plans, key=lambda p: p["start"]) if p["status"] in {"scheduled", "active"}][:12],
                "errors": "\n".join(f"{name}：{error}" for name, error in self.last_errors.items()) or "无运行错误",
                "jobs": self.store.all("job")[-10:]}

    @API("ui_dashboard", description="此刻生活总览与每日轨迹")
    async def ui_dashboard(self, day_offset: int = 0) -> dict:
        if day_offset not in {-1, 0, 1}:
            raise ValueError("请选择昨天、今天或明天")
        data = await self.ui_overview()
        now = self.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
        end = start + timedelta(days=1)
        plans = self.store.all("plan", omit=("changes",))
        daily = sorted((p for p in plans if p["status"] != "cancelled" and self.engine.time(p["start"]) < end
                        and self.engine.time(p["end"]) > start), key=lambda p: self.engine.time(p["start"]))
        def brief(plan):
            return {**{key: plan[key] for key in ("id", "title", "kind", "location", "status", "start", "end", "motivation")},
                    "kind_label": KIND[plan["kind"]], "status_label": STATUS[plan["status"]],
                    "source_label": "角色模拟" if plan["simulation"] and not plan["participants"] else ("共同安排" if plan["participants"] else "个人安排")}
        current = self.engine.current(now)
        upcoming = min((p for p in plans if p["status"] == "scheduled" and self.engine.time(p["start"]) > now),
                       key=lambda p: self.engine.time(p["start"]), default=None)
        weather = self.world.context(now)["weather"]
        weather_brief = None if not weather else {"city": weather["city"], "label": weather["label"],
            "temperature": weather["current"]["temperature_2m"], "stale": weather["stale"], "at": display_time(weather["observed_at"])}
        pointer = self.store.find("runtime", "current_book")
        book = self.store.find("book", pointer["book_id"]) if pointer else None
        notes = self.books.notes(book["id"])["items"] if book else []
        shelf = None if not book else {**{key: book[key] for key in ("title", "author", "status", "read_characters", "characters")},
            "progress": round(book["read_characters"] / book["characters"] * 100),
            "thoughts": notes[0]["thoughts"][:300] if notes else "", "question": notes[0]["question"][:200] if notes else ""}
        diaries = sorted(self.store.all("diary", omit=("material",)), key=lambda r: r["day"], reverse=True)
        diary = next((r for r in diaries if r["status"] == "已生成"), None)
        events = sorted(self.store.all("experience", omit=("history",)), key=lambda r: r["occurred_at"], reverse=True)[:4]
        data.pop("history")
        data.update(now=now.isoformat(timespec="seconds"), day=start.date().isoformat(), day_offset=day_offset,
            current=brief(current) if current else None, upcoming=brief(upcoming) if upcoming else None,
            timeline=[{**brief(plan),
                "start_minute": max(0, (self.engine.time(plan["start"]) - start).total_seconds()/60),
                "end_minute": min(1440, (self.engine.time(plan["end"]) - start).total_seconds()/60),
                "time_label": self.engine.time(plan["start"]).strftime("%H:%M") + "–" + self.engine.time(plan["end"]).strftime("%H:%M")}
                for plan in daily[:200]], timeline_total=len(daily),
            weather=weather_brief, book=shelf,
            diary={"day": diary["day"], "content": diary["content"][:450]} if diary else None,
            moments=[{"summary": r["summary"][:400], "source": SOURCE[r["source"]],
                      "time": display_time(r["occurred_at"]), "importance": r["importance"]} for r in events],
            usage=usage_summary(self.store, now, 1)["totals"])
        return data

    @API("ui_life_timeline", description="查看一项活动从安排到完成的来龙去脉")
    async def ui_life_timeline(self, plan_id: str) -> dict:
        plan = self.store.get("plan", plan_id)
        decision = plan.get("decision", {})
        rows = [{"at": plan["created_at"], "title": decision.get("source", "安排活动"),
                 "text": decision.get("reason") or plan["motivation"] or plan["title"]}]
        labels = {"title": "活动", "start": "开始", "end": "结束", "location": "地点", "motivation": "缘由", "kind": "类型"}
        actions = {"cancel": "取消活动", "pause": "暂停活动", "resume": "继续活动", "record_completion": "确认完成"}
        for change in plan["changes"][-30:]:
            after = change.get("after", {})
            text = change.get("reason", "")
            if after:
                before = change.get("before", {})
                text = "；".join(f"{labels.get(k, k)}：{before.get(k, '')} → {v}" for k, v in after.items())
            action = change.get("action", "修改安排")
            rows.append({"at": change["at"], "title": actions.get(action, action), "text": text})
        event = self.store.find("experience", plan.get("experience_id", ""))
        if event:
            rows.append({"at": event["occurred_at"], "title": "活动结果 · " + SOURCE[event["source"]], "text": event["summary"]})
        note = self.store.find("book_note", plan_id)
        if note and note["status"] == "已完成":
            rows.append({"at": note["finished_at"], "title": "读后感想", "text": note["thoughts"] + ("\n还在想：" + note["question"] if note["question"] else "")})
        reflection = self.store.find("reflection", plan_id)
        memory = next((r for r in self.store.all("activity_memory") if event and r["experience_id"] == event["id"]), None)
        if memory and memory["next_action"]:
            rows.append({"at": memory["updated_at"], "title": "留下的念头", "text": memory["next_action"] + (" · " + memory["next_date"] + "再接着做" if memory.get("next_date") else "")})
        balance = decision.get("balance")
        if balance:
            for item in balance["totals"]:
                item["activity"] = KIND.get(item["activity"], item["activity"])
            balance["last_activity"] = KIND.get(balance["last_activity"], balance["last_activity"])
        return {"title": plan["title"], "events": sorted(rows, key=lambda r: r["at"]),
                "balance": balance, "reflection": reflection["status"] if reflection else ""}

    @API("ui_schedule", description="日程与重复规则")
    async def ui_schedule(self, session_id: str = "", start_date: str = "", page: int = 1) -> dict:
        plans = self.store.all("plan", omit=('changes',))
        if session_id:
            plans = [p for p in plans if self.engine.visible(p, session_id)]
        if start_date:
            after = self.engine.time(start_date)
            plans = [p for p in plans if self.engine.time(p["end"]) >= after]
        plans = sorted(plans, key=lambda p: self.engine.time(p["start"]), reverse=True)
        rules = [r for r in self.store.all("routine") if not session_id or self.engine.visible(r, session_id)]
        sleep_rules = []
        cfg = self.config.life
        for rule in rules:
            if not cfg.schedule_enabled or rule["status"] != "active" or rule["kind"] != "sleep":
                continue
            minutes = int(rule["start_time"][:2]) * 60 + int(rule["start_time"][3:5])
            duration = ((cfg.wake_hour - cfg.sleep_hour) % 24 or 24) * 60
            if minutes != cfg.sleep_hour * 60 or rule["duration_minutes"] != duration:
                sleep_rules.append({"title": rule["title"], "reason": f"这条睡眠规则从 {rule['start_time']} 开始，持续 {rule['duration_minutes']} 分钟；日常作息为 {cfg.sleep_hour:02d}:00–{cfg.wake_hour:02d}:00。该规则按单独设置执行，可在重复日程中修改或停止。"})
        return page_tables({"plans": [plan_row(p) for p in plans],
                "rules": [{**r, "frequency_label": FREQUENCY[r["frequency"]], "status_label": STATUS[r["status"]],
                           "weekdays_label": "、".join(list(WEEKDAYS)[d] for d in r["weekdays"]), "exceptions_label": "、".join(r["exceptions"]),
                           "kind_label": KIND[r["kind"]]} for r in rules], "issues": self.store.all("routine_issue")[::-1],
                "schedule_issues": self.store.all("schedule_issue") + sleep_rules, "notices": self.store.all("schedule_notice"), "adjustments": self.store.all("adjustment")[::-1]}, page, ['plans', 'rules', 'issues', 'schedule_issues', 'notices', 'adjustments'])

    @API("ui_create_plan", description="新增生活安排")
    async def ui_create_plan(self, title: str, start: str, end: str, kind: str = "leisure", location: str = "", session_id: str = "", participants: str = "", simulation: bool = True, flexible: bool = False) -> dict:
        self.require_life()
        self.engine.advance(self.now())
        value = PlanInput(title=title, start=start, end=end, kind=kind, location=location,
                          participants=[v.strip() for v in participants.split(",") if v.strip()], simulation=simulation, flexible=flexible)
        return self.spatial.schedule(value, session_id, self.now())

    @API("ui_plan_action", description="取消、暂停、恢复或删除已取消的日程")
    async def ui_plan_action(self, plan_id: str, action: str) -> dict:
        self.require_life()
        self.engine.advance(self.now())
        plan = self.store.get("plan", plan_id)
        if action == "delete":
            if plan["status"] != "cancelled":
                raise ValueError("请先取消日程，再删除记录")
            ids = {plan_id, *(p["id"] for p in self.store.all("plan") if p.get("activity_id") == plan_id)}
            with self.store.db:
                for key in ids:
                    note = self.store.find("book_note", key)
                    if note:
                        note["plan_status"] = "cancelled"
                        self.store.put("book_note", note)
                    for category in ("plan", "schedule_issue", "schedule_notice", "reflection", "research_task"):
                        self.store.delete(category, key)
                for row in self.store.all("adjustment"):
                    if row["plan_id"] in ids:
                        self.store.delete("adjustment", row["id"])
            return {"id": plan_id, "deleted": True}
        return list_row(self.spatial.transition(plan_id, action, plan["session_id"], self.now()))

    @API("ui_update_plan", description="修改所选日程")
    async def ui_update_plan(self, plan_id: str, new_title: str = "", new_start: str = "", new_end: str = "", new_location: str = "") -> dict:
        self.require_life()
        self.engine.advance(self.now())
        plan = self.store.get("plan", plan_id)
        changes = {k: v for k, v in {"title": new_title, "start": new_start, "end": new_end, "location": new_location}.items() if v}
        return list_row(self.spatial.change(plan_id, PlanPatch.model_validate(changes), plan["session_id"], self.now()))

    @API("ui_save_routine", description="创建或更新重复日程")
    async def ui_save_routine(self, title: str, anchor_date: str, start_time: str, duration_minutes: int,
                              frequency: str = "weekly", interval: int = 1, weekdays: str = "周一,周二,周三,周四,周五",
                              month_day: int = 1, until: str = "", kind: str = "leisure", location: str = "",
                              session_id: str = "", participants: str = "", simulation: bool = True, rule_id: str = "") -> dict:
        self.require_life()
        names = [v.strip() for v in weekdays.replace("，", ",").split(",") if v.strip()]
        if any(name not in WEEKDAYS for name in names):
            raise ValueError("星期请填写周一到周日，用逗号分隔")
        if rule_id:
            session_id = self.store.get("routine", rule_id)["session_id"]
        value = RoutineInput(title=title, anchor_date=anchor_date, start_time=start_time, duration_minutes=duration_minutes,
                             frequency=frequency, interval=interval, weekdays=[WEEKDAYS[n] for n in names], month_day=month_day,
                             until=until, kind=kind, location=location, participants=[v.strip() for v in participants.split(",") if v.strip()], simulation=simulation)
        rule = self.rhythm.save(value, session_id, self.now(), rule_id)
        self.rhythm.materialize(self.now())
        return rule

    @API("ui_routine_action", description="停用重复规则或跳过一次")
    async def ui_routine_action(self, rule_id: str, action: str, skip_date: str = "") -> dict:
        self.require_life()
        rule = self.store.get("routine", rule_id)
        if action == "skip":
            return self.rhythm.skip(rule_id, skip_date, rule["session_id"], self.now())
        if action == "cancel":
            return self.rhythm.cancel(rule_id, rule["session_id"], self.now())
        raise ValueError("未知重复规则操作")

    @API("ui_world", description="天气、预报和外部资料")
    async def ui_world(self, page: int = 1) -> dict:
        exploration = await self.world.exploration_status(self.now())
        tables = {key: self.store.page(category, page, omit=omit, time_field=field) for key, category, omit, field in (
            ("news", "news", (), "retrieved_at"), ("research", "research", ("results",), "retrieved_at"),
            ("readings", "reading", ("content",), "retrieved_at"), ("explorations", "research_task", ("sources",), "at"))}
        for key, category in (("research", "research"), ("readings", "reading"), ("explorations", "research_task")):
            for row in tables[key]["items"]:
                row["detail_kind"] = category
        for row in tables["explorations"]["items"]:
            plan = self.store.find("plan", row["id"])
            if plan and plan["status"] == "cancelled":
                row["status"] = "日程已取消"
            reflection = self.store.find("reflection", row["id"])
            if reflection and reflection["status"] == "生成中":
                row["status"] = "模型正在整理感想"
            elif reflection and reflection["status"] == "失败":
                row.update(status="感想整理失败，将自动重试", error=reflection["error"])
        search = await self.world.search_status()
        environment = self.world.context(self.now())
        weather = environment["weather"]
        status = self.store.find("world_status", "weather")
        if weather:
            summary = f"{weather['city']} · {weather['label']} · {weather['current']['temperature_2m']}℃"
            freshness = "数据已过期" if weather["stale"] else "最近观测"
        else:
            summary = "尚无天气数据；请先配置天气城市" if not self.config.world.city else "尚未获取天气"
            freshness = "尚无观测"
        return {"summary": summary, "freshness": freshness,
                "search_enabled": search["ready"],
                "search_status": search["summary"],
                "observed_at": display_time(weather["observed_at"]) if weather else "—",
                "source": "Open-Meteo", "forecast": weather["forecast"] if weather else [],
                "weather_error": status["error"] if status and status["error"] else "无",
                "exploration_status": f"{exploration['reason']} · 今日 {exploration['used']}/{exploration['limit']} 次",
                "statuses": self.store.all("world_status"), **{key: value["items"] for key, value in tables.items()},
                "pagination": {"page": max(t["page"] for t in tables.values()), "pages": max(t["pages"] for t in tables.values()),
                               "total": sum(t["total"] for t in tables.values())}, "jobs": self.store.page("job", size=10, time_field="created_at")["items"]}

    @API("ui_refresh_world", description="后台更新天气与资讯")
    async def ui_refresh_world(self) -> dict:
        self.require_life()
        return self.start_job("更新天气与资讯", self.world.refresh(self.now(), force=True))

    @API("ui_research", description="后台检索或阅读网页")
    async def ui_research(self, query: str = "", url: str = "", freshness: str = "current") -> dict:
        self.require_life()
        if url:
            return self.start_job("阅读网页", self.world.read(url, self.now(), freshness))
        if query:
            return self.start_job("联网检索", self.world.search(query, self.now(), freshness))
        raise ValueError("请填写检索词或网页地址")

    @API("ui_schedule_research", description="在下一个空档安排上网了解")
    async def ui_schedule_research(self, query: str, freshness: str = "current") -> dict:
        self.require_life()
        if not query.strip():
            raise ValueError("请填写想了解的问题")
        status = await self.world.search_status()
        if not status["ready"]:
            raise ValueError(status["summary"])
        now = self.now()
        start, location = now, self.engine.state()["location"]
        for plan in sorted(self.engine.plans(""), key=lambda p: p["start"]):
            if plan["status"] in {"scheduled", "active", "blocked"} and self.engine.time(plan["end"]) > start:
                if self.engine.time(plan["start"]) >= start + timedelta(minutes=20):
                    break
                start, location = self.engine.time(plan["end"]), plan["location"]
        plan = self.spatial.schedule(PlanInput(title="上网了解：" + query.strip()[:120], kind="study", start=start.isoformat(),
                    end=(start + timedelta(minutes=20)).isoformat(), location=location, research_query=query.strip(),
                    research_freshness=freshness, motivation="从天气与见闻页面安排了解这个问题"), "", now)
        return {"message": "已安排上网了解，开始时间：" + display_time(plan["start"]), "id": plan["id"]}

    @API("ui_delete_world", description="删除所选外部资料缓存")
    async def ui_delete_world(self, category: str, record_id: str) -> dict:
        if category not in {"news", "research", "reading"}:
            raise ValueError("请选择资讯、检索或网页资料")
        with self.store.db:
            self.store.delete(category, record_id)
        return {"message": "所选资料已删除"}

    @API("ui_space", description="地点、路线及移动进度")
    async def ui_space(self) -> dict:
        places = self.store.all("place")
        names = {p["id"]: p["name"] for p in places}
        current = self.engine.current(self.now())
        route = current.get("route") if current else None
        return {"location": self.engine.state()["location"], "places": [{**p, "outdoor_label": "室外" if p["outdoor"] else "室内"} for p in places],
                "routes": [{**r, "origin_name": names[r["origin"]], "destination_name": names[r["destination"]]} for r in self.store.all("route")],
                "journey": self.engine.state().get("journey") or {"location": self.engine.state()["location"], "next": "", "progress": 100, "mode": "停留", "destination": ""},
                "legs": route["legs"] if route else []}

    @API("ui_save_place", description="添加或编辑地点")
    async def ui_save_place(self, name: str, outdoor: bool = False, description: str = "", place_id: str = "") -> dict:
        return self.spatial.save_place(name, outdoor, description, place_id)

    @API("ui_save_route", description="添加或更新双向路线")
    async def ui_save_route(self, origin: str, destination: str, minutes: int, mode: str = "步行") -> dict:
        return self.spatial.save_route(origin, destination, minutes, mode)

    @API("ui_move", description="开始移动到指定地点")
    async def ui_move(self, destination: str) -> dict:
        self.require_life()
        return self.spatial.move(destination, "", self.now())

    @API("ui_dreams", description="梦境档案与睡眠记录")
    async def ui_dreams(self, page: int = 1) -> dict:
        dreams = self.store.all("dream")[::-1]
        return page_tables({"dreams": [{"title": "尚未生成", "story": "", "feeling": "", **d, "source_label": "梦境"} for d in dreams],
                "sleeps": [{**plan_row(p), "hours": round(p["elapsed_seconds"] / 3600, 1)} for p in self.store.all("plan", omit=('changes',)) if p["kind"] == "sleep"][::-1],
                "jobs": self.store.all("job")[-10:]}, page, ['dreams', 'sleeps'])

    @API("ui_generate_dream", description="为所选睡眠生成梦境")
    async def ui_generate_dream(self, plan_id: str) -> dict:
        self.require_life()
        return self.start_job("生成梦境", self.dreams.generate(plan_id, self.now()))

    @API("ui_habits", description="已学习习惯与长期目标")
    async def ui_habits(self, page: int = 1) -> dict:
        return page_tables({"habits": [{**h, "kind_label": KIND[h["kind"]], "enabled_label": "参与规划" if h["enabled"] else "已忽略",
                            "active_label": "近期习惯" if h["active"] else "近期未出现", "evidence": "、".join(h["evidence_ids"])} for h in self.store.all("habit")],
                "goals": [{**g, "status_label": STATUS[g["status"]], "progress": round(g["completed_sessions"] / g["target_sessions"] * 100)} for g in self.store.all("goal")],
                "relations": [{**list_row(r), "detail_kind": "relationship", "history_text": ""} for r in self.store.all("relationship", omit=('history',))]}, page, ['habits', 'goals', 'relations'])

    @API("ui_habit_action", description="启用或忽略所选习惯")
    async def ui_habit_action(self, habit_id: str, enabled: bool) -> dict:
        habit = self.store.get("habit", habit_id)
        return self.rhythm.toggle(habit_id, enabled, habit["session_id"])

    @API("ui_learn_habits", description="立即分析已完成活动")
    async def ui_learn_habits(self) -> dict:
        self.require_life()
        self.rhythm.learn(self.now())
        return {"message": "习惯分析完成"}

    @API("ui_records", description="生活经历与修订历史")
    async def ui_records(self, query: str = "", session_id: str = "", page: int = 1) -> dict:
        records = [e for e in self.store.all("experience", omit=('history',)) if (not session_id or self.engine.visible(e, session_id)) and (not query or query in e["summary"])]
        records.sort(key=lambda e: e["occurred_at"], reverse=True)
        pages = max(1, math.ceil(len(records) / 30))
        page = min(pages, max(1, page))
        rows = [{**list_row(e), "detail_kind": "experience", "source_label": SOURCE[e["source"]], "time_label": display_time(e["occurred_at"]),
                 "details_text": "\n".join(f"{k}：{v}" for k, v in e["details"].items()),
                 "history_text": "",
                 "memory_label": "已同步" if e["memory_revision"] == e["revision"] else ("保存在生活档案" if e["source"] == "simulated" else "尚未同步当前修订")}
                for e in records[(page - 1) * 30:page * 30]]
        return {"records": rows, "pagination": {"page": page, "pages": pages, "total": len(records)}, "jobs": self.store.all("job")[-10:]}

    @API("ui_revise_record", description="补充或更正所选经历")
    async def ui_revise_record(self, experience_id: str, evidence: str, detail_key: str = "", detail_value: str = "", summary: str = "") -> dict:
        self.require_life()
        old = self.store.get("experience", experience_id)
        if not detail_key and not summary:
            raise ValueError("请填写需要修改的摘要或细节")
        record = self.engine.revise_experience(experience_id, {detail_key: detail_value} if detail_key else {}, summary, evidence, old["session_id"], self.now())
        return list_row(record)

    @API("ui_sync_record", description="后台同步所选经历到原生记忆")
    async def ui_sync_record(self, experience_id: str) -> dict:
        self.require_life()
        return await self.ui_change_begin("sync", json.dumps({"experience_id": experience_id}))

    @API("ui_create_goal", description="新增长期目标")
    async def ui_create_goal(self, title: str, target_sessions: int = 5, reason: str = "", session_id: str = "") -> dict:
        self.require_life()
        return self.engine.create_goal(GoalInput(title=title, target_sessions=target_sessions, reason=reason), session_id, self.now())

    @API("ui_cancel_goal", description="停止所选长期目标")
    async def ui_cancel_goal(self, goal_id: str) -> dict:
        self.require_life()
        goal = self.store.get("goal", goal_id)
        return self.engine.close_goal(goal_id, goal["session_id"])

    @API("ui_continuity", description="活动接续、阶段目标与待跟进事项")
    async def ui_continuity(self, page: int = 1) -> dict:
        goals = self.store.all("goal")
        active_steps = {p.get("goal_step_id") for p in self.store.all("plan", omit=('changes',)) if p["status"] == "active"}
        return page_tables({
            "reflections": self.store.all("reflection")[::-1],
            "preferences": [{**p, "evidence": "、".join(p["evidence_ids"])} for p in self.store.all("preference")],
            "continuations": [{**p, "next_date": p.get("next_date") or "有兴致时", "progress_status": "已完成" if p.get("completed", False) else "进行中", "details_text": json.dumps(p["details"], ensure_ascii=False)} for p in self.store.all("activity_memory")],
            "effects": [{**e, "status_label": {"active": "持续影响中", "expired": "已消退", "resolved": "已解决"}[e["status"]]} for e in self.store.all("effect")],
            "threads": [{**t, "status_label": {"open": "待跟进", "resolved": "已解决", "expired": "已到期"}[t["status"]],
                         "request_label": "已发起跟进" if t["requested_at"] else "等待合适时机" if t["status"] == "open" else "已结束跟进",
                         "expires_at": t.get("expires_at") or (self.engine.time(t["due_at"]) + timedelta(days=1)).isoformat()} for t in self.store.all("thread")],
            "goals": [{**g, "outline_status": g.get("outline_status", "待分解"), "outline_error": g.get("outline_error", ""), "status_label": STATUS[g["status"]]} for g in goals],
            "steps": [{**s, "goal_title": g["title"], "status_label": "进行中" if s["id"] in active_steps else {"pending": "未开始", "active": "进行中", "completed": "已完成"}[s["status"]]} for g in goals for s in g.get("steps", [])],
            "jobs": self.store.all("job")[-10:],
        }, page, ['reflections', 'preferences', 'continuations', 'effects', 'threads', 'goals', 'steps'])

    @API("ui_reflect", description="后台生成或重试活动具体结果")
    async def ui_reflect(self, plan_id: str) -> dict:
        self.require_life()
        return self.start_job("整理活动结果", self.continuity.reflect(plan_id, self.now()))

    @API("ui_outline", description="后台分解或调整目标阶段")
    async def ui_outline(self, goal_id: str, revise: bool = False) -> dict:
        self.require_life()
        return self.start_job("规划目标阶段", self.continuity.outline(goal_id, self.now(), revise))

    @API("ui_replan", description="根据临时事件调整可变动安排")
    async def ui_replan(self, reason: str, unavailable_until: str) -> dict:
        self.require_life()
        if not reason.strip() or not unavailable_until:
            raise ValueError("请填写临时事件原因和结束时间")
        return {"adjustments": self.living.adjust(self.now(), reason, unavailable_until)}

    @API("ui_save_thread", description="保存需要接续的话题")
    async def ui_save_thread(self, title: str, context: str, due_at: str, session_id: str, thread_id: str = "", expires_at: str = "") -> dict:
        self.require_life()
        if thread_id:
            session_id = self.store.get("thread", thread_id)["session_id"]
        return self.continuity.save_thread(title, context, session_id, due_at, self.now(), thread_id, expires_at=expires_at)

    @API("ui_resolve_thread", description="关闭已经解决的跟进事项")
    async def ui_resolve_thread(self, thread_id: str) -> dict:
        self.require_life()
        thread = self.store.get("thread", thread_id)
        return self.continuity.resolve_thread(thread_id, thread["session_id"], self.now())

    @API("ui_resolve_effect", description="结束所选事件的持续影响")
    async def ui_resolve_effect(self, effect_id: str) -> dict:
        self.require_life()
        effect = self.store.get("effect", effect_id)
        effect["status"] = "resolved"
        with self.store.db:
            self.store.put("effect", effect)
        return effect
