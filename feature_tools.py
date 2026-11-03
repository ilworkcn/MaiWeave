"""扩展生活能力的自然语言工具。"""

from maibot_sdk import Tool

from .models import RoutineInput


def parameters(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


class FeatureTools:
    @Tool("maiweave_books", description="list按page分页查看书目，notes按book_id与page查询已读笔记；page从0开始。select选择当前读物，schedule安排阅读日程。", parameters=parameters({
        "action": {"type": "string", "enum": ["list", "notes", "select", "schedule"]}, "book_id": {"type": "string"},
        "start": {"type": "string"}, "minutes": {"type": "integer", "enum": [15,30,45,60]}, "page": {"type": "integer"},
    }, ["action"]))
    async def books_tool(self, action: str, book_id: str = "", start: str = "", minutes: int = 30, page: int = 0, **kwargs):
        self.require_life()
        if action == "list":
            books = [b for b in self.store.all("book") if not b.get("archived")]
            return {"total_books": len(books), "page": max(0, page), "books": [{k: (b[k][:100] if isinstance(b[k], str) else b[k]) for k in ("id", "title", "author", "status", "chapter", "chapter_count")} for b in books[max(0,page)*5:(max(0,page)+1)*5]]}
        if action == "notes":
            rows = [n for n in self.store.all("book_note") if n["status"] == "已完成" and (not book_id or n["book_id"] == book_id)]
            rows.sort(key=lambda n: n["finished_at"], reverse=True)
            return {"total": len(rows), "page": max(0, page), "items": [{k: n[k][:500] for k in ("book_id", "book_title", "chapter_title", "summary", "thoughts", "quote", "question")} for n in rows[max(0,page)*2:(max(0,page)+1)*2]]}
        if action == "select":
            return self.books.select(book_id)
        return self.books.schedule(book_id, start, minutes, self.spatial, self.now(), kwargs["stream_id"], kwargs.get("user_id", ""))

    @Tool("maiweave_continuity", description="管理生活接续。status 查看偏好和持续事项；reflect 为已完成活动生成或重试具体结果；outline/revise_goal 分解或调整目标阶段；replan 根据临时事件重排可变动安排；save_thread 保存有明确上下文的后续关心事项，resolve_thread 关闭已解决事项。", parameters=parameters({
        "action": {"type": "string", "enum": ["status", "reflect", "outline", "revise_goal", "replan", "save_thread", "resolve_thread"]},
        "record_id": {"type": "string"}, "title": {"type": "string"}, "context": {"type": "string"},
        "due_at": {"type": "string"}, "reason": {"type": "string"}, "unavailable_until": {"type": "string"},
    }, ["action"]))
    async def continuity_tool(self, action: str, record_id: str = "", title: str = "", context: str = "", due_at: str = "",
                              reason: str = "", unavailable_until: str = "", **kwargs):
        self.require_life()
        session, now = kwargs["stream_id"], self.now()
        if action == "status":
            return self.continuity.context(session, now)
        if action == "reflect":
            self.engine.owned("plan", record_id, session)
            return await self.continuity.reflect(record_id, now)
        if action in {"outline", "revise_goal"}:
            self.engine.owned("goal", record_id, session)
            return await self.continuity.outline(record_id, now, revise=action == "revise_goal")
        if action == "replan":
            if not reason.strip() or not unavailable_until:
                raise ValueError("临时调整需说明具体原因和结束时间")
            until = self.engine.time(unavailable_until)
            affected = [p for p in self.store.all("plan") if p["status"] in {"scheduled", "active"}
                        and self.engine.time(p["start"]) < until and self.engine.time(p["end"]) > now]
            self.living.check_influence(session, kwargs.get("user_id", ""), affected)
            return self.living.adjust(now, reason, unavailable_until)
        if action == "save_thread":
            return self.continuity.save_thread(title, context, session, due_at, now, record_id)
        if action == "resolve_thread":
            return self.continuity.resolve_thread(record_id, session, now)
        raise ValueError("未知生活接续操作")

    @Tool("maiweave_world", description="查询真实天气和外部资料。status 查看已有数据；weather 刷新天气；search 使用 MaiBot 联网搜索；read 读取网页。外部资料带有来源与采集时间，供了解环境和讨论话题使用；工具操作按当前对话的需求执行。", parameters=parameters({
        "action": {"type": "string", "enum": ["status", "weather", "search", "read"]},
        "query": {"type": "string"}, "url": {"type": "string"},
    }, ["action"]))
    async def world_tool(self, action: str, query: str = "", url: str = "", **kwargs):
        self.require_life()
        if action == "status":
            return self.world.context(self.now())
        if action == "weather":
            return await self.world.weather(self.now())
        if action == "search":
            return await self.world.search(query, self.now())
        if action == "read":
            return await self.world.read(url, self.now())
        raise ValueError("未知外部世界操作")

    @Tool("maiweave_routine", description="管理每天、每周或每月的重复日程。save 提供完整规则；rule_id 为空表示创建，否则修改整条规则。skip 跳过指定日期，cancel 停止今后重复。周一为0，周日为6。", parameters=parameters({
        "action": {"type": "string", "enum": ["list", "save", "skip", "cancel"]},
        "routine": RoutineInput.model_json_schema(), "rule_id": {"type": "string"}, "on_date": {"type": "string"},
    }, ["action"]))
    async def routine_tool(self, action: str, routine: dict | None = None, rule_id: str = "", on_date: str = "", **kwargs):
        self.require_life()
        session, now = kwargs["stream_id"], self.now()
        self.engine.advance(now)
        if action == "list":
            return [r for r in self.store.all("routine") if self.engine.visible(r, session)]
        if action == "save":
            result = self.rhythm.save(RoutineInput.model_validate(routine or {}), session, now, rule_id, user_id=kwargs.get("user_id", ""))
            self.rhythm.materialize(now)
            return {"rule": result, "issues": [i for i in self.store.all("routine_issue") if i["routine_id"] == result["id"]]}
        if action == "skip":
            return self.rhythm.skip(rule_id, on_date, session, now)
        if action == "cancel":
            return self.rhythm.cancel(rule_id, session, now)
        raise ValueError("未知重复日程操作")

    @Tool("maiweave_space", description="查询角色虚拟地图和路线，开始出行，添加地点或连接路线。路线为双向、耗时按分钟；移动进度按路线耗时更新。map 查看已登记地点，route 查看耗时，move 出发。", parameters=parameters({
        "action": {"type": "string", "enum": ["map", "route", "move", "save_place", "save_route"]},
        "origin": {"type": "string"}, "destination": {"type": "string"}, "name": {"type": "string"},
        "description": {"type": "string"}, "outdoor": {"type": "boolean"},
        "minutes": {"type": "integer", "minimum": 1}, "mode": {"type": "string"},
    }, ["action"]))
    async def space_tool(self, action: str, origin: str = "", destination: str = "", name: str = "", description: str = "",
                         outdoor: bool = False, minutes: int = 1, mode: str = "步行", **kwargs):
        self.require_life()
        self.engine.advance(self.now())
        if action == "map":
            return await self.ui_space()
        if action == "route":
            return self.spatial.route(origin or self.engine.state()["location"], destination)
        if action == "move":
            return self.spatial.move(destination, kwargs["stream_id"], self.now())
        if action == "save_place":
            return self.spatial.save_place(name, outdoor, description)
        if action == "save_route":
            return self.spatial.save_route(origin, destination, minutes, mode)
        raise ValueError("未知地点操作")

    @Tool("maiweave_dream", description="回忆已经保存的梦境，或为一段已完成睡眠生成梦境。梦境保存在独立档案中，回忆时以梦境视角讲述。", parameters=parameters({
        "action": {"type": "string", "enum": ["list", "generate"]}, "plan_id": {"type": "string"},
    }, ["action"]))
    async def dream_tool(self, action: str, plan_id: str = "", **kwargs):
        self.require_life()
        if action == "list":
            return [d for d in self.store.all("dream") if self.engine.visible(d, kwargs["stream_id"])][-5:]
        if action == "generate":
            self.engine.owned("plan", plan_id, kwargs["stream_id"])
            return await self.dreams.generate(plan_id, self.now())
        raise ValueError("未知梦境操作")

    @Tool("maiweave_habits", description="查询从已完成活动学到的习惯，立即学习，或启用、忽略一条习惯。每条习惯关联已完成活动的记录。", parameters=parameters({
        "action": {"type": "string", "enum": ["list", "learn", "enable", "disable"]}, "habit_id": {"type": "string"},
    }, ["action"]))
    async def habits_tool(self, action: str, habit_id: str = "", **kwargs):
        self.require_life()
        if action == "learn":
            self.rhythm.learn(self.now())
        if action in {"learn", "list"}:
            return [h for h in self.store.all("habit") if self.engine.visible(h, kwargs["stream_id"])]
        if action in {"enable", "disable"}:
            return self.rhythm.toggle(habit_id, action == "enable", kwargs["stream_id"])
        raise ValueError("未知习惯操作")
