"""在实机临时数据目录核验阅读节奏，模型与消息接口使用 MaiBot 服务。"""
import asyncio
import base64
import importlib.util
import re
import sys
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from maibot_sdk import API, HookHandler, MaiBotPlugin, Tool


class Probe(MaiBotPlugin):
    async def on_load(self):
        root = Path(__file__).resolve().parent.parent / "ilworkcn_maiweave"
        spec = importlib.util.spec_from_file_location("maiweave_probe_target", root / "__init__.py", submodule_search_locations=[str(root)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        from maiweave_probe_target.plugin import create_plugin
        self.create = create_plugin
        self.result = {}
        self.task = None
        self.active = None
        self.deliveries = []
        self.planner_outputs = []

    @Tool("maiweave_probe_share_followup", description="关联本次实机核验分享后的用户回应；调用原插件的接话逻辑。", parameters={"type": "object", "properties": {"receipt_id": {"type": "string"}, "summary": {"type": "string"}, "status": {"type": "string", "enum": ["answered", "closed"]}}, "required": ["receipt_id", "summary", "status"]})
    async def probe_share_followup(self, receipt_id: str, summary: str, status: str, **kwargs):
        return self.active.living.receipts.respond(receipt_id, kwargs["stream_id"], kwargs["user_id"], summary, status, self.active.now())

    @HookHandler("maisaka.planner.before_request")
    async def planner_context(self, **kwargs):
        if self.active and kwargs.get("session_id") == "3a2048f0aa247f60f912da52c3a8ece6":
            result = await self.active.planner_context(**kwargs)
            # 隔离数据的接话工具沿用生产实现，使用独立工具名定位测试实例。
            if self.result.get("case") in {"natural_delivery", "recipient_natural", "recipient_express"}:
                for item in result.get("modified_kwargs", {}).get("items", []):
                    if item.get("meta", {}).get("item_id") == "maiweave-life-context":
                        for part in item.get("parts", []):
                            if "text" in part:
                                part["text"] = part["text"].replace("maiweave_share_followup", "maiweave_probe_share_followup")
            # 临时插件的主动任务使用临时插件 ID，回执仍取宿主返回的任务编号。
            for item in reversed(kwargs["items"]):
                text = "".join(part.get("text", "") for part in item.get("parts", []))
                match = re.match(r'<plugin_proactive_task id="([^"]+)" plugin_id="ilworkcn.maiweave-reading-probe">', text)
                if match:
                    self.active.living.receipts.turn[kwargs["session_id"]] = match[1]
                    break
            return result
        return {"action": "continue"}

    @HookHandler("maisaka.planner.after_response")
    async def planner_receipt(self, **kwargs):
        if self.active and kwargs.get("session_id") == "3a2048f0aa247f60f912da52c3a8ece6":
            self.planner_outputs.append(kwargs["output_items"])
            return await self.active.planner_receipt(**kwargs)
        return {"action": "continue"}

    @HookHandler("maisaka.replyer.before_request")
    async def reply_context(self, **kwargs):
        if self.active and kwargs.get("session_id") == "3a2048f0aa247f60f912da52c3a8ece6":
            return await self.active.reply_context(**kwargs)
        return {"action": "continue"}

    @HookHandler("chat.receive.after_process")
    async def farewell_receive(self, message: dict, **kwargs):
        if self.active and message.get("session_id") == "3a2048f0aa247f60f912da52c3a8ece6":
            return await self.active.management_message(message, **kwargs)
        return {"action": "continue"}

    @HookHandler("send_service.before_send")
    async def farewell_before_send(self, message: dict, storage_message: bool = True, **kwargs):
        if self.active and message.get("session_id") == "3a2048f0aa247f60f912da52c3a8ece6":
            result = await self.active.sleep_before_send(message, storage_message=storage_message)
            self.deliveries.append({"stage": "before", "action": result["action"]})
            return result
        return {"action": "continue"}

    @HookHandler("send_service.after_send")
    async def farewell_after_send(self, message: dict, sent: bool, **kwargs):
        if self.active and message.get("session_id") == "3a2048f0aa247f60f912da52c3a8ece6":
            self.deliveries.append({"stage": "after", "sent": sent})
        return {"action": "continue"}

    async def on_unload(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def on_config_update(self, scope, config_data, version):
        pass

    @API("start", version="1")
    async def start(self, case: str):
        self.result = {"status": "运行中", "case": case}
        self.task = asyncio.create_task(self.run(case))
        return self.result

    @API("status", version="1")
    async def status(self):
        return self.result

    async def run(self, case):
        from maiweave_probe_target.core.models import PlanInput, ExperienceInput
        folder = Path(self.ctx.paths.data_dir) / uuid4().hex
        folder.mkdir()
        native = self.ctx
        class Context:
            paths = SimpleNamespace(data_dir=str(folder))
            def __getattr__(self, name):
                return getattr(native, name)
        p = self.create()
        cfg = p.get_default_config()
        cfg["life"].update(enabled=False, autonomous=False, schedule_enabled=False)
        cfg["world"].update(weather_enabled=False)
        cfg["diary"]["enabled"] = False
        p.set_plugin_config(cfg)
        p._set_context(Context())
        try:
            await p.on_load()
            p.worker.cancel()
            with suppress(asyncio.CancelledError):
                await p.worker
            p.config.life.enabled = True
            now = p.now().replace(hour=16, minute=0, second=0, microsecond=0)
            p.now = lambda: now
            with p.store.db:
                p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "hunger": 20, "energy": 80})
            if case in {"natural_delivery", "recipient_natural", "recipient_express"}:
                import json
                stream = "3a2048f0aa247f60f912da52c3a8ece6"
                p.now = lambda: datetime.now(now.tzinfo)
                now = p.now()
                self.active = p
                self.deliveries = []
                self.planner_outputs = []
                p.config.proactive.enabled = True
                p.config.proactive.mode = "主动表达" if case == "recipient_express" else "自然交流"
                p.config.memory.enabled = case.startswith("recipient_")
                p.config.proactive.session_ids = [stream]
                p.config.proactive.start_hour = p.config.proactive.end_hour = 0
                p.config.proactive.daily_limit = 4
                with p.store.db:
                    p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "energy": 80, "hunger": 20})
                async def wait_reply(start):
                    for _ in range(120):
                        await asyncio.sleep(2)
                        calls = [item["tool_call"] for output in self.planner_outputs[start:] for item in output
                                 if item.get("item_type") == "FunctionCallItem" and item["tool_call"]["func_name"] == "reply"]
                        for call in calls:
                            receipt = await p.ctx.db.get("ToolRecord", filters={"session_id": stream, "tool_id": call["call_id"]}, single_result=True)
                            if receipt and json.loads(receipt["tool_data"])["success"]:
                                return receipt
                    raise AssertionError({"阶段": "等待真实回复", "模型输出": self.planner_outputs[start:]})
                seed = "我已经醒啦，现在也空下来了。你刚才拓印的梧桐叶帆布袋做好了吗？我特别想知道叶脉印得清不清楚，等晾好了一定主动告诉我呀，我在这儿等你的成品消息。"
                if case.startswith("recipient_"):
                    seed = ("我喜欢观鸟，下午想去河边看看白鹭。你觉得应该留意它们什么动作？你今天遇到新的白鹭见闻就告诉我，我很想听。" if case == "recipient_natural" else "我喜欢观鸟，最近对翠鸟突然扎进水里的动作很好奇。你观察到过什么细节？碰到新的翠鸟见闻记得告诉我。")
                initial = await self.ctx.call_capability("internal_chat.send", text=seed)
                assert initial["message_id"]
                await wait_reply(0)
                await asyncio.sleep(3)
                native_interest = {}
                topic = "刚才拓印的梧桐叶帆布袋已经晾干了，细叶脉清清楚楚，原本糊成一团的叶柄补成了小鸟，成品比试印时好看，终于可以告诉一直等着看成果的朋友了"
                if case.startswith("recipient_"):
                    from urllib.parse import urlencode
                    person = await p.ctx.person.get_id(platform="internal", user_id="owner")
                    self.result.update(stage="等待原生兴趣写回")
                    for _ in range(60):
                        facts = await asyncio.to_thread(p.memory._request, "GET", "/facts?" + urlencode({"scope_type": "person", "scope_id": person, "statuses": "active", "limit": 100}))
                        native_interest = next((row for row in facts["items"] if "观鸟" in row["value_text"] and row.get("profile_section") == "stable_facts"), {})
                        if native_interest:
                            break
                        await asyncio.sleep(2)
                    assert native_interest, {"阶段": "原生兴趣写回", "事实": facts}
                    topic = ("刚在河边看到一只白鹭先用脚轻轻搅动浅水，再歪头盯着水面，等了一会儿突然啄起一条小鱼，第一次注意到它捕鱼前会先搅水" if case == "recipient_natural" else "刚在河边看见翠鸟从低枝上扎进水里，回来时嘴里叼着小鱼，它先把鱼在树枝上拍了两下，再调转鱼头吞下去，第一次看到它捕到鱼之后的细节")
                event = p.engine.record_experience(ExperienceInput(summary=topic, source="simulated", evidence="实机测试生活场景", importance=9, occurred_at=p.now().isoformat()), stream, p.now())
                recipient = await p.living.share_recipient(stream, topic)
                if case.startswith("recipient_"):
                    assert "观鸟" in recipient.get("原生人物画像", ""), recipient
                assert p.living.share_summary(p.now())[0]["sent"] == 0
                await p.living.share(p.now())
                row, = p.store.all("share_receipt")
                accepted_count = p.living.share_summary(p.now())[0]["sent"]
                assert accepted_count == (1 if case == "recipient_express" else 0), row
                self.result.update(stage="等待主动发送", receipt=row)
                for _ in range(120):
                    await asyncio.sleep(2)
                    await p.living.receipts.refresh(p.now())
                    row = p.store.get("share_receipt", row["id"])
                    if row["outcome"] != "MaiBot 已受理":
                        break
                assert row["outcome"] == "实际已发送", {"receipt": row, "outputs": self.planner_outputs}
                assert row.get("sent_text") and row.get("sent_at"), row
                p.living.settle_shares()
                for _ in range(3):
                    await p.living.receipts.refresh(p.now())
                assert p.living.share_summary(p.now())[0]["sent"] == 1
                assert event["id"] in p.store.get("sharing", stream)["experience_ids"]
                await p.living.share(p.now())
                cooldown = dict(p.living.share_decisions[0])
                assert "下次可请求时间" in cooldown["reason"], cooldown
                context = p.context_text(stream, text="帆布袋上的叶脉")
                assert row["id"] in context and "shared_topics" in context
                assert len(context) <= p.config.context.private_chars
                start = len(self.planner_outputs)
                self.result.update(stage="接续真实聊天", receipt=row)
                answer = "哇，叶柄还能改成小鸟！这个补救太可爱了。你说叶脉现在清清楚楚，是不是晾干以后颜色比湿的时候淡一点？我下次也想试试这种帆布袋。"
                if case.startswith("recipient_"):
                    answer = "这就是我喜欢听的观鸟小事！你刚说的这个动作也太有意思了，它们后来又做什么了？我也想下次散步时留意看看。"
                incoming = await self.ctx.call_capability("internal_chat.send", text=answer)
                assert incoming["message_id"]
                reply = await wait_reply(start)
                for _ in range(30):
                    row = p.store.get("share_receipt", row["id"])
                    if row.get("followup") == "answered":
                        break
                    await asyncio.sleep(2)
                assert row.get("followup") == "answered", {"receipt": row, "outputs": self.planner_outputs[start:]}
                assert row["response_text"] == answer and row["response_user"] == "owner"
                assert row["observation"] == "分享后收到用户发言"
                await p.living.share(p.now())
                continuation = dict(p.living.share_decisions[0])
                assert continuation["outcome"] == "接着聊", continuation
                assert p.living.share_summary(p.now())[0]["sent"] == 1
                data = json.loads(reply["tool_data"])
                ids = data.get("metadata", {}).get("sent_message_ids", [])
                texts = []
                for message_id in ids:
                    message = await p.ctx.db.get("Messages", filters={"session_id": stream, "message_id": message_id}, single_result=True)
                    texts.append(message["processed_plain_text"])
                self.result = {"status": "通过", "case": case, "result": {"receipt": row, "accepted_count": accepted_count,
                    "sent_count": p.living.share_summary(p.now())[0]["sent"], "cooldown": cooldown, "continuation": continuation,
                    "context_chars": len(context), "context_budget": p.config.context.private_chars,
                    "reply_texts": texts, "native_interest": native_interest, "recipient": recipient, "mode": p.config.proactive.mode, "reply_record": data, "deliveries": self.deliveries}}
                return
            if case == "business":
                from datetime import date
                stream = "3a2048f0aa247f60f912da52c3a8ece6"
                profile = await p.ui_save_profile("实机日历核验", stream)
                solar = await p.ui_save_anniversary(profile["id"], "生日", "solar", 2, 29, 2000)
                lunar = await p.ui_save_anniversary(profile["id"], "纪念日", "lunar", 1, 1)
                assert p.anniversaries.occurrence(solar, 2025) == date(2025, 2, 28)
                assert p.anniversaries.occurrence(lunar, 2024) == date(2024, 2, 10)
                assert not p.anniversaries.upcoming(now.date(), "other-chat")
                for ago in (1, 2, 3):
                    at = now - timedelta(days=ago)
                    plan = p.engine.create_plan(PlanInput(title="饭后散步", kind="leisure", location="公园", start=at.isoformat(), end=(at+timedelta(minutes=20)).isoformat()), stream, at)
                    with p.store.db: p.store.put("plan", {**plan, "status": "completed", "elapsed_seconds": 1200})
                p.rhythm.learn(now)
                assert p.store.all("habit")[0]["observed_days"] == 3
                before = list(p.store.db.iterdump())
                for behavior in ("conversation", "proactive"):
                    assert (await p.evaluate_behavior(behavior))["decision"] in {"allow", "defer"}
                assert before == list(p.store.db.iterdump())
                native_status = await self.ctx.api.call("ilworkcn.maiweave.get_life_status", version="1")
                assert native_status["version"] == 1
                queued = await p.ui_change_begin("profile", '{"name":"实机资料核验","session_id":"isolated-test"}')
                await asyncio.gather(*list(p.jobs))
                checked = await p.ui_change_status(queued["review_id"])
                assert checked["status"] in {"已保存", "待确认"}, checked
                async def operation():
                    return None
                task = p.start_job("实机任务状态核验", operation())
                await asyncio.gather(*list(p.jobs))
                assert (await p.ui_operation_status(task_id=task["task_id"]))["done"]
                await p.ui_retention()
                await p.ui_cleanup()
                await p.ui_books()
                await p.ui_anniversaries()
                self.result = {"status": "通过", "case": case, "result": {"calendar": "公历与农历通过", "habits": "多日期活动学习通过",
                    "readonly_api": "跨插件调用与只读检查通过", "review": checked["status"], "operation": "完成", "retention": "预览清理及页面读回通过"}}
                return
            if case == "schedule_edges":
                from maiweave_probe_target.core.models import RoutineInput, ChatArrangement
                now = now.replace(hour=9, minute=40)
                p.now = lambda: now
                p.config.rhythm.horizon_days = 1
                checks = []
                for action in ("cancel", "skip", "edit", "rename", "leave"):
                    with p.store.db:
                        for category in ("plan", "routine", "occurrence", "schedule_issue", "routine_issue", "chat_arrangement"):
                            for record in p.store.all(category):
                                p.store.delete(category, record["id"])
                        p.store.put("state", {**p.engine.state(), "location":"住处", "updated_at":now.isoformat()})
                    p.spatial.save_route("street", "park", 12, "步行")
                    value = RoutineInput(title="公园活动", frequency="daily", anchor_date=now.date().isoformat(),
                        start_time="10:00", duration_minutes=30, location="公园", kind="work" if action == "leave" else "leisure")
                    rule = p.rhythm.save(value, "internal-audit", now)
                    p.rhythm.materialize(now)
                    plan = next(x for x in p.store.all("plan") if x.get("routine_id") == rule["id"])
                    p.spatial.save_route("street", "park", 40, "步行")
                    p.spatial.reconcile(now)
                    assert p.store.get("plan", plan["id"])["status"] == "blocked"
                    if action == "cancel":
                        await p.ui_routine_action(rule["id"], "cancel")
                    elif action == "skip":
                        await p.ui_routine_action(rule["id"], "skip", now.date().isoformat())
                    elif action == "edit":
                        await p.ui_save_routine(title=value.title, anchor_date=value.anchor_date, start_time="11:00",
                            duration_minutes=30, frequency="daily", location="公园", rule_id=rule["id"])
                    elif action == "rename":
                        await p.ui_save_place("湖畔公园", True, "核验地点改名", "park")
                    else:
                        p.chat_schedule.save(ChatArrangement(title="上午调休", start=now.isoformat(),
                            end=now.replace(hour=12,minute=0).isoformat(), evidence="今天上午调休", source="observed",
                            speaker_id="bot", blocked_kinds=["work"]), "internal-audit", "owner", now)
                    p.spatial.save_route("street", "park", 12, "步行")
                    p.spatial.reconcile(now)
                    result = p.store.get("plan", plan["id"])
                    assert result["status"] == ("scheduled" if action == "rename" else "cancelled"), result
                    if action == "rename":
                        assert result["location"] == "湖畔公园"
                        p.spatial.save_place("公园", True, "核验地点", "park")
                    checks.append(action)
                with p.store.db:
                    for row in p.store.all("plan"):
                        p.store.delete("plan", row["id"])
                    p.store.put("book", {"id":"archived", "archived":True})
                old = p.spatial.schedule(PlanInput(title="整理书桌", start=now.isoformat(),
                    end=(now+timedelta(minutes=30)).isoformat()), "", now, autonomous=True)
                try:
                    p.spatial.schedule(PlanInput(title="读书", book_id="archived", start=now.isoformat(),
                        end=(now+timedelta(minutes=15)).isoformat()), "", now)
                except ValueError as error:
                    assert "归档" in str(error)
                else:
                    raise AssertionError("已归档书籍仍可创建阅读")
                assert p.store.get("plan", old["id"]) == old
                checks.append("failed_creation_keeps_schedule")
                p.spatial.transition(old["id"], "cancel", "", now)
                trip = p.spatial.move("公园", "", now)
                at = now + timedelta(minutes=5)
                p.engine.advance(at)
                assert p.engine.state()["journey"]["next"]
                p.now = lambda: at
                await p.ui_plan_action(trip["id"], "cancel")
                assert p.engine.state()["journey"] is None
                checks.append("cancel_trip_stops_movement")
                self.result = {"status":"通过", "case":case, "result":{"checks":checks}}
                return
            if case == "diary_catchup":
                plan = p.engine.create_plan(PlanInput(title="写日记", kind="diary", diary_day=now.date().isoformat(),
                    start=(now+timedelta(minutes=5)).isoformat(), end=(now+timedelta(minutes=20)).isoformat()), "", now)
                later = now + timedelta(minutes=30)
                p.engine.advance(later)
                assert p.store.get("plan", plan["id"])["status"] == "completed"
                assert p.store.find("diary", now.date().isoformat()) is None
                await p.diary.process(later)
                diary = p.store.get("diary", now.date().isoformat())
                assert diary["status"] == "已生成" and diary["plan_id"] == plan["id"] and diary["content"]
                await p.diary.process(later+timedelta(minutes=1))
                assert p.store.get("diary", diary["id"]) == diary
                self.result = {"status":"通过", "case":case, "result":{"day":diary["day"],
                    "status":diary["status"], "chars":len(diary["content"]), "repeat":"保留已生成日记"}}
                return
            if case in {"world", "world_weather", "world_search"}:
                now = datetime.now(now.tzinfo)
                p.config.world.weather_enabled = True
                p.config.world.city = "Shanghai"
                p.config.world.country_code = "CN"
                weather = {}
                if case != "world_search":
                    weather = await p.world.weather(now)
                    assert weather["forecast"] and weather["source"] == "Open-Meteo"
                if case == "world_weather":
                    self.result = {"status": "通过", "case": case, "result": {"city": weather["city"], "weather": weather["label"]}}
                    return
                p.config.world.search_enabled = True
                search = await p.world.search("上海 自然博物馆 开放时间 官方", now)
                assert search["results"], search
                page = await p.world.read(search["results"][0]["url"], now)
                assert page["content"], page
                self.result = {"status": "通过", "case": case, "result": {"city": weather.get("city"), "weather": weather.get("label"),
                    "search_results": len(search["results"]), "page_title": page["title"], "page_chars": len(page["content"])}}
                return
            if case == "proactive_gates":
                stream = "3a2048f0aa247f60f912da52c3a8ece6"
                now = datetime.now(now.tzinfo).replace(hour=14)
                await p.identify_person(stream, "owner", "internal")
                person = p.living.person_key(stream, "owner")
                p.anniversaries.save_profile("测试接收人", stream, "owner", person_id=person)
                p.engine.record_experience(ExperienceInput(summary="实机交流判定核验", importance=6, source="simulated", evidence="测试数据", occurred_at=now.isoformat()), stream, now)
                checks = []
                for mode in ("自然交流", "主动表达"):
                    for gate in ("关闭", "时段", "额度", "冷却", "睡眠", "学习", "忙碌人物", "低精力", "告别"):
                        with p.store.db:
                            for category in ("plan", "share_receipt", "person_state"):
                                for row in p.store.all(category):
                                    p.store.delete(category, row["id"])
                            p.store.delete("runtime", "awakening")
                            p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "energy": 80})
                        cfg = p.config.proactive
                        cfg.enabled, cfg.mode, cfg.session_ids = True, mode, [stream]
                        cfg.start_hour = cfg.end_hour = 0
                        cfg.daily_limit = 4
                        p.config.life.schedule_enabled = True
                        if gate == "关闭": cfg.enabled = False
                        if gate == "时段": cfg.start_hour, cfg.end_hour = 16, 18
                        if gate == "额度": cfg.daily_limit = 0
                        if gate == "冷却":
                            with p.store.db:
                                p.store.put("share_receipt", {"id": "sent", "session": stream, "outcome": "实际已发送", "created": now.isoformat(), "sent_at": now.isoformat()})
                        if gate in {"睡眠", "学习", "告别"}:
                            p.engine.create_plan(PlanInput(title=gate, kind="study" if gate == "学习" else "sleep", location="住处",
                                start=now.isoformat(), end=(now+timedelta(hours=2)).isoformat()), "", now)
                        if gate == "告别":
                            assert p.living.wake(stream, now, "测试", "owner", require_intimacy=False)
                            assert p.living.wake_for_message("晚安", stream, now, "owner")
                        if gate == "忙碌人物": p.presence.declare(stream, "owner", "忙碌", now)
                        if gate == "低精力":
                            with p.store.db: p.store.put("state", {**p.engine.state(), "energy": 20})
                        before = len(p.store.all("share_receipt"))
                        await p.living.share(now)
                        assert len(p.store.all("share_receipt")) == before, (mode, gate)
                        checks.append({"mode": mode, "gate": gate, "result": "未发送"})
                self.result = {"status": "通过", "case": case, "result": {"checks": checks}}
                return
            if case.startswith("greeting_"):
                stream = "3a2048f0aa247f60f912da52c3a8ece6"
                now = datetime.now(now.tzinfo) + timedelta(hours=4)
                p.config.life.schedule_enabled = True
                p.config.proactive.enabled = True
                p.config.proactive.natural_greetings = True
                p.config.proactive.session_ids = [stream]
                p.config.proactive.start_hour = p.config.proactive.end_hour = 0
                p.config.proactive.mode = "自然交流" if case.endswith("natural") else "主动表达"
                profile = p.anniversaries.save_profile("实机测试人物", stream, "owner")
                with p.store.db:
                    p.store.put("profile", {**profile, "intimacy": 2, "intimacy_mode": "manual"})
                    p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "energy": 80})
                if "morning" in case:
                    end = now - timedelta(minutes=5)
                    p.engine.create_plan(PlanInput(title="测试夜间睡眠", kind="sleep", start=(end-timedelta(hours=8)).isoformat(), end=end.isoformat()), "", end-timedelta(hours=8))
                    with p.store.db:
                        p.store.put("state", {**p.engine.state(), "updated_at": (end-timedelta(hours=8)).isoformat()})
                    p.engine.advance(now)
                else:
                    start = now + timedelta(minutes=10)
                    p.engine.create_plan(PlanInput(title="测试夜间睡眠", kind="sleep", start=start.isoformat(), end=(start+timedelta(hours=8)).isoformat()), "", now)
                self.active = p
                self.deliveries = []
                window = p.living.greetings.window(now)
                assert window, case
                candidate = await p.living.greetings.candidate(stream, now, window)
                assert candidate, window
                await p.living.share(now)
                row, = p.store.all("share_receipt")
                for _ in range(90):
                    if row["outcome"] != "MaiBot 已受理":
                        break
                    await asyncio.sleep(2)
                    await p.living.receipts.refresh(now)
                    row = p.store.get("share_receipt", row["id"])
                assert row["outcome"] in {"实际已发送", "Planner 选择沉默", "Planner 未调用工具", "稍后再聊"}, row
                if row["outcome"] == "实际已发送":
                    assert await p.living.greetings.candidate(stream, now, window) is None
                    p.config.proactive.daily_limit = 1
                    await p.living.share(now)
                    assert "今日实际已发送" in p.living.share_decisions[0]["reason"]
                p.config.life.schedule_enabled = False
                assert p.living.greetings.window(now) is None
                self.result = {"status": "通过", "case": case, "result": {"kind": window["kind"], "mode": p.config.proactive.mode,
                    "outcome": row["outcome"], "text": row.get("sent_text"), "deliveries": self.deliveries, "daily_quota": "通过", "all_day": "通过"}}
                return
            if case in {"farewell_nap", "farewell_night"}:
                import json
                stream = "3a2048f0aa247f60f912da52c3a8ece6"
                p.config.life.schedule_enabled = True
                now = now.replace(hour=13 if case == "farewell_nap" else 23)
                with p.store.db:
                    p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "location": "住处"})
                p.engine.create_plan(PlanInput(title="实机告别测试睡眠", kind="sleep", location="住处",
                    start=now.isoformat(), end=(now + timedelta(hours=2)).isoformat()), "", now)
                p.engine.advance(now)
                assert p.living.wake(stream, now, "实机告别测试", "owner", require_intimacy=False)
                self.planner_outputs = []
                self.active = p
                self.deliveries = []
                result = await self.ctx.call_capability("internal_chat.send", text="晚安，去睡吧。跟我说声晚安，再单独一句叮嘱我早点休息就好。")
                assert result["message_id"], result
                assert p.living.proactive_pause(now)["reason"] == "farewell"
                observed = {}
                for _ in range(120):
                    await asyncio.sleep(2)
                    row = p.living.farewell(now)
                    if row and row.get("farewell_calls"):
                        observed = row["farewell_calls"]
                    await p.living.refresh_farewell(now)
                    if p.living.sleeping(now):
                        break
                assert observed, self.deliveries
                records = []
                for call_id, name in observed.items():
                    record = await p.ctx.db.get("ToolRecord", filters={"session_id": stream, "tool_id": call_id}, single_result=True)
                    assert record, {"tool": name, "call_id": call_id, "outputs": self.planner_outputs, "deliveries": self.deliveries, "awakening": p.store.find("runtime", "awakening")}
                    data = json.loads(record["tool_data"])
                    records.append({"name": name, "success": data["success"], "error": data.get("error_message"),
                                    "send_results": data.get("metadata", {}).get("send_results", [])})
                assert any(x.get("sent") for x in self.deliveries), records
                assert all(x["action"] == "continue" for x in self.deliveries if x["stage"] == "before"), self.deliveries
                assert p.living.quiet_sleep(now)
                assert (await p.sleep_before_send({"session_id": stream}))["action"] == "abort"
                self.result = {"status": "通过", "case": case, "result": {"deliveries": self.deliveries,
                    "records": records, "sleep": p.engine.current(now)["title"], "status": await p.get_life_status()}}
                return
            if case == "tool_schema":
                import json
                # 安装临时核验插件时附上实际模型返回的工具参数。
                payloads = json.loads(Path(__file__).with_name("tool_calls.json").read_text("utf-8"))
                now = datetime(2026, 11, 14, 9, tzinfo=now.tzinfo)

                p.spatial.save_route("住处", "公园", 20, "步行")
                output = []
                for model, arguments in payloads.items():
                    with p.store.db:
                        for category in ("plan", "chat_arrangement"):
                            for row in p.store.all(category):
                                p.store.delete(category, row["id"])
                        p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "location": "住处"})
                    row = p.spatial.schedule(PlanInput(title="去公园散步", kind="leisure", location="公园",
                        start="2026-11-15T14:00:00+08:00", end="2026-11-15T15:00:00+08:00"), "probe-chat", now, user_id="reader")
                    # 使用模型请求中提供的日程编号创建对应的测试记录。
                    original = row["id"]
                    with p.store.db:
                        p.store.delete("plan", original)
                        p.store.put("plan", {**row, "id": "issue14-plan"})
                        for trip in p.store.all("plan"):
                            if trip.get("activity_id") == original:
                                p.store.put("plan", {**trip, "activity_id": "issue14-plan"})
                    try:
                        await p.chat_arrangement(**arguments, stream_id="other-chat", user_id="reader")
                    except ValueError as error:
                        assert "当前聊天流" in str(error), str(error)
                    else:
                        raise AssertionError("跨聊天修改应被归属校验拒绝")
                    await p.chat_arrangement(**arguments, stream_id="probe-chat", user_id="reader")
                    changed = p.store.get("plan", "issue14-plan")
                    assert p.engine.time(changed["start"]).hour == 16, changed
                    trip = p.store.get("plan", changed["travel_id"])
                    assert p.engine.time(trip["end"]) == p.engine.time(changed["start"]), trip
                    result = await p.change_plan("issue14-plan", "cancel", stream_id="probe-chat", user_id="reader")
                    assert p.store.get("plan", "issue14-plan")["status"] == "cancelled", result
                    assert p.store.get("plan", trip["id"])["status"] == "cancelled"
                    output.append({"model": model, "start": changed["start"], "trip_end": trip["end"], "ownership": "通过", "cancel": "通过"})
                self.result = {"status": "通过", "case": case, "result": output}
                return
            if case == "chat_statement":
                stream = "3a2048f0aa247f60f912da52c3a8ece6"
                result = await p.ctx.maisaka.reply.generate(stream,
                    "这是本地聊天室的生活日程接续验证。用角色第一人称简短告诉对方：我11月16日12点到18点调休，下午不上班。日期和时段保持完整。",
                    format_instruction="只输出一句角色实际说出的确定安排。", timeout_ms=95000)
                assert result["success"], result
                sent = await p.ctx.send.text(result["text"], stream, storage_message=True, sync_to_maisaka_history=True, return_details=True)
                assert sent["sent"], sent
                self.result = {"status": "通过", "case": case, "result": {"text": result["text"], "delivery": sent}}
                return
            if case == "chat_schedule":
                from maiweave_probe_target.core.models import ChatArrangement, RoutineInput
                from maiweave_probe_target.integrations.usage import generate
                from maiweave_probe_target.core.models import ChatArrangements
                now = now.replace(hour=9)
                with p.store.db:
                    p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "location": "住处"})
                p.spatial.save_place("办公室", False, "实机核验地点")
                p.spatial.save_route("住处", "办公室", 20, "步行")
                rule = p.rhythm.save(RoutineInput(title="下午上班", frequency="daily", anchor_date=now.date().isoformat(),
                    start_time="14:00", duration_minutes=120, kind="work", location="办公室"), "probe-chat", now)
                p.rhythm.materialize(now)
                today = next(x for x in p.store.all("plan") if x.get("routine_date") == now.date().isoformat())
                prompt = ("提取角色已确定的临时安排，输出JSON对象arrangements数组，每项title、start、end（带时区ISO）、"
                          "evidence、source、blocked_kinds、record_id、cancel_plan_ids。用户自己的请假填空数组。"
                          f"现在{now.isoformat()}。用户：你下午上班吗？角色实际回复：今天下午调休，12点到18点休息。"
                          "source为observed，blocked_kinds为work，record_id空字符串，cancel_plan_ids空数组。")
                result = await generate(p.ctx, p.store, p.config, "聊天安排", prompt, ChatArrangements)
                assert result.arrangements
                for value in result.arrangements:
                    p.chat_schedule.save(value, "probe-chat", "reader", now)
                assert p.store.get("plan", today["id"])["status"] == "cancelled"
                assert p.store.get("plan", today["travel_id"])["status"] == "cancelled"
                p.rhythm.materialize(now)
                p.chat_schedule.reconcile(now)
                assert any(x["status"] == "scheduled" and x.get("routine_date") == (now + timedelta(days=1)).date().isoformat() for x in p.store.all("plan"))
                p.config.life.autonomous = True
                now = now.replace(hour=13)
                await p.living.choose_activity(now)
                autonomous = [x for x in p.store.all("plan") if x["autonomous"]]
                assert autonomous and all(x["kind"] != "work" for x in autonomous)
                assert p.chat_schedule.active(now, "other-chat") == []
                assert not p.chat_schedule.active(now.replace(hour=18))
                output = {"arrangements": p.chat_schedule.context(now), "activity": autonomous,
                          "cancelled_trip": p.store.get("plan", today["travel_id"])["status"],
                          "routine_status": p.store.get("routine", rule["id"])["status"],
                          "model_usage": p.store.all("model_usage")}
                self.result = {"status": "通过", "case": case, "result": output}
                return
            elif case == "sleep_schedule":
                now = now.replace(hour=1, minute=30)
                p.config.life.autonomous = True
                p.config.life.schedule_enabled = True
                p.config.life.sleep_hour, p.config.life.wake_hour = 1, 8
                with p.store.db:
                    p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "location": "卧室"})
                fixed = p.spatial.schedule(PlanInput(title="实机核验固定睡眠", kind="sleep", location="卧室",
                    start=now.replace(hour=2, minute=0).isoformat(), end=now.replace(hour=7, minute=0).isoformat()), "", now)
                p.living.apply_schedule(now)
                await p.living.choose_activity(now)
                night = next(x for x in p.store.all("plan") if x.get("sleep_deadline"))
                assert p.engine.time(night["start"]).strftime("%H:%M") == "01:31"
                assert p.engine.time(night["end"]).strftime("%H:%M") == "01:59"
                for minute in range(1, 6):
                    at = now + timedelta(minutes=minute)
                    p.living.apply_schedule(at)
                    await p.living.choose_activity(at)
                assert len([x for x in p.store.all("plan") if x["kind"] == "sleep"]) == 2
                now += timedelta(minutes=6)
                p.engine.advance(now)
                assert p.living.quiet_sleep(now)
                assert p.living.wake("private", now, "实机唤醒核验", require_intimacy=False)
                p.config.life.wake_hour = 9
                p.living.apply_schedule(now)
                assert p.engine.time(p.store.get("runtime", "awakening")["sleep_end"]).hour == 9
                p.config.life.schedule_enabled = False
                p.living.apply_schedule(now)
                assert not p.living.quiet_sleep(now)
                assert p.store.get("plan", fixed["id"])["status"] == "scheduled"
                p.config.life.schedule_enabled = True
                p.living.apply_schedule(now)
                assert p.store.find("runtime", "awakening") is None
                nap_at = now.replace(hour=13, minute=0)
                nap = p.engine.create_plan(PlanInput(title="实机午睡", kind="sleep", start=nap_at.isoformat(),
                    end=(nap_at + timedelta(minutes=30)).isoformat()), "", now)
                now = nap_at + timedelta(minutes=1)
                p.engine.advance(now)
                assert p.living.quiet_sleep(now)
                p.config.life.nap_quiet = False
                assert not p.living.quiet_sleep(now)
                await p.ui_plan_action(nap["id"], "cancel")
                assert (await p.ui_plan_action(nap["id"], "delete"))["deleted"]
                self.result = {"status": "通过", "case": case, "result": {
                    "重复生成": "连续五轮仍为两条睡眠", "自主睡眠": "01:31–01:59，保留往返路程",
                    "修改起床": "临时唤醒记录同步到09:00", "全天候": "保留手动固定安排，清除临时唤醒",
                    "午睡": "静默开关生效", "取消删除": "通过"}}
                return
            book = await p.ui_book_upload("begin", filename="实机验证.md", title="实机验证书")
            raw = "# 午后\n阳光照在书桌上，窗外传来鸟鸣。".encode()
            await p.ui_book_upload("append", upload_id=book["id"], content=base64.b64encode(raw).decode())
            book = await p.ui_book_upload("finish", upload_id=book["id"])
            def reading(minutes, ago):
                end = now - timedelta(minutes=ago)
                start = end - timedelta(minutes=minutes)
                plan = p.engine.create_plan(PlanInput(title="阅读实机验证书", kind="leisure", book_id=book["id"], start=start.isoformat(), end=end.isoformat()), "", start)
                with p.store.db:
                    plan.update(status="completed", elapsed_seconds=minutes*60)
                    p.store.put("plan", plan)
                return plan
            output = {}
            if case in {"share_express", "share_natural"}:
                self.active = p
                now = datetime.now(now.tzinfo)
                stream="3a2048f0aa247f60f912da52c3a8ece6"
                p.config.proactive.enabled=True
                p.config.proactive.mode="主动表达" if case=="share_express" else "自然交流"
                p.config.proactive.session_ids=[stream]
                p.config.proactive.start_hour=p.config.proactive.end_hour=0
                p.config.proactive.daily_limit=20
                with p.store.db:
                    p.store.put("state",{**p.engine.state(),"updated_at":now.isoformat(),"energy":80,"hunger":20})
                    p.store.put("share_receipt",{"id":"earlier-noodles","session":stream,"created":(now-timedelta(hours=3)).isoformat(),"sent_at":(now-timedelta(hours=3)).isoformat(),"outcome":"实际已发送","topic":"煮面","sent_text":"傍晚煮了一碗面，已经吃完了。","followup":"waiting"})
                event=p.engine.record_experience(ExperienceInput(summary="煮了一碗普通面条，吃完了，没有新的变化",source="simulated",evidence="实机测试场景",importance=8,occurred_at=(now-timedelta(hours=1)).isoformat()),stream,now)
                await p.living.share(now)
                deferred=p.store.all("share_receipt")[-1]
                assert deferred["outcome"]=="稍后再聊",deferred
                with p.store.db:
                    p.store.delete("experience",event["id"])
                    p.store.delete("share_receipt","earlier-noodles")
                p.engine.record_experience(ExperienceInput(summary=("刚完成第一只软陶小鸟，原本捏歪的翅膀改成了展开的样子，出炉后竟然能稳稳站在窗台上，特别想分享这次意外的成功" if case=="share_express" else "今天第一次试着把楼下梧桐叶拓印到帆布袋上，叶脉比想象中清楚，刚洗掉多余颜料，想和对方聊聊亲手做成小东西的快乐"),source="simulated",evidence="MaiWeave 实机专用测试生活场景",importance=9,occurred_at=(now-timedelta(minutes=30)).isoformat()),stream,now)
                await p.living.share(now)
                row=p.store.all("share_receipt")[-1]
                for _ in range(90):
                    if row["outcome"] != "MaiBot 已受理":
                        break
                    await asyncio.sleep(2)
                    await p.living.receipts.refresh(datetime.now(now.tzinfo))
                    row=p.store.get("share_receipt",row["id"])
                assert row["outcome"] in {"实际已发送","Planner 选择沉默","Planner 未调用工具","稍后再聊"},row
                if row["outcome"]=="实际已发送":
                    assert row.get("sent_text"),row
                output={"mode":p.config.proactive.mode,"repeat":deferred["delivery"],"outcome":row["outcome"],"sent_text":row.get("sent_text",""),"models":[{"feature":x["feature"],"status":x["status"]} for x in p.store.all("model_usage")]}
            elif case == "qzone":
                output=await p.ctx.call_capability("api.call",api_name="adapter.snowluma.system.get_version_info",version="1",timeout_ms=15000,args={})
            elif case == "services":
                from maiweave_probe_target.core.models import GoalInput
                stream="3a2048f0aa247f60f912da52c3a8ece6"
                p.config.memory.enabled=True
                now=datetime.now(now.tzinfo)
                with p.store.db:
                    p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat()})
                p.engine.advance(now)
                marker="麦织实机验收-"+uuid4().hex[:8]
                stages=[]
                self.result.update(stages=stages, data_dir=str(folder))
                event=p.engine.record_experience(ExperienceInput(summary=marker+"：已通过插件接口完成本次测试记录的创建。",occurred_at=now.isoformat(),source="observed",evidence="本次实机接口核验结果",details={"用途":"插件验收测试"}),stream,now)
                saved=await p.saved_experience(event)
                assert saved["memory"]["status"]=="已保存",saved["memory"]
                event=p.store.get("experience",event["id"])
                fact=await p.memory.fact(event["memory_id"])
                assert marker in str(fact),fact
                stages.append({"step":"记忆创建与原文读取","claim_id":event["memory_id"],"status":"通过"})
                event=p.engine.revise_experience(event["id"],{"日程结果":"已取消"},"","本次实机核验已确认测试事项取消",stream,now)
                revised=await p.saved_experience(event)
                if revised["memory"]["status"]=="待确认":
                    await p.reviews.decide(revised["memory"]["id"],True)
                else:
                    assert revised["memory"]["status"]=="已保存",revised["memory"]
                event=p.store.get("experience",event["id"])
                fact=await p.memory.fact(event["memory_id"])
                assert "已取消" in str(fact),fact
                stages.append({"step":"记忆更新与读回","status":"通过"})
                recalled=await p.memory.query(marker,stream)
                assert marker in str(recalled), recalled
                stages.append({"step":"原生记忆综合检索","status":"命中" if marker in str(recalled) else "未命中", "response_keys":list(recalled), "error":recalled.get("error"), "hits":len(recalled.get("hits", []))})
                plan=p.books.schedule(book["id"],now.isoformat(),15,p.spatial,now)
                now+=timedelta(seconds=1)
                p.engine.advance(now)
                await p.books.process(now)
                note=p.store.get("book_note",plan["id"])
                assert note["status"]=="等待活动结束",note
                now+=timedelta(minutes=15)
                p.engine.advance(now)
                await p.books.process(now)
                note=p.store.get("book_note",plan["id"])
                assert note["status"]=="已完成",note
                stages.append({"step":"实际模型阅读与进度保存","status":"通过","thoughts":note["thoughts"]})
                goal=p.engine.create_goal(GoalInput(title="学会折两种简单纸书签",target_sessions=2),"",now)
                outline=await p.continuity.outline(goal["id"],now)
                assert sum(x["sessions"] for x in outline["steps"])==2
                stages.append({"step":"实际模型目标分解","status":"通过"})
                sleep=p.engine.create_plan(PlanInput(title="实机测试午睡",kind="sleep",start=now.isoformat(),end=(now+timedelta(hours=2)).isoformat()),"",now)
                now+=timedelta(hours=2)
                p.engine.advance(now)
                dream=await p.dreams.generate(sleep["id"],now)
                assert dream["status"]=="已生成",dream
                stages.append({"step":"实际模型梦境生成","status":"通过","title":dream["title"]})
                diary=await p.diary.write(now.date().isoformat(),now)
                assert diary["status"]=="已生成",diary
                stages.append({"step":"实际模型日记生成","status":"通过","characters":len(diary["content"])})
                usage=p.store.all("model_usage")
                assert usage and all(x["status"]=="成功" for x in usage),usage
                output={"stages":stages,"data_dir":str(folder),"marker":marker,"model_calls":[{"feature":x["feature"],"model":x["model"],"tokens":x.get("total_tokens")} for x in usage]}
            elif case == "private_wake":
                checks = []
                for hour, mode in [(14,"zero"),(14,"ordinary"),(23,"ordinary"),(14,"group"),(14,"nap_open"),(23,"all_day"),(23,"wake_off"),(23,"keyword")]:
                    with p.store.db:
                        for plan in p.store.all("plan"):
                            p.store.delete("plan",plan["id"])
                        p.store.delete("runtime","awakening")
                    now = now.replace(hour=hour,minute=0)
                    p.config.life.schedule_enabled = mode != "all_day"
                    p.config.life.wake_enabled = mode != "wake_off"
                    p.config.life.nap_quiet = mode != "nap_open"
                    p.config.life.private_wake_probability = 0 if mode in {"zero","keyword"} else 100
                    p.config.life.wake_minutes = 1
                    p.engine.create_plan(PlanInput(title="实机睡眠核验",kind="sleep",start=now.isoformat(),end=(now+timedelta(hours=2)).isoformat()),"",now)
                    p.engine.advance(now)
                    if mode == "keyword":
                        profile = p.anniversaries.save_profile("实机测试人物","private","")
                        with p.store.db:
                            p.store.put("profile",{**profile,"intimacy":3,"intimacy_mode":"manual"})
                    info = {"group_info":{"group_id":"probe"}} if mode == "group" else {}
                    message = {"session_id":"private","message_info":info,"processed_plain_text":"陪陪我" if mode == "keyword" else "今天看到一只小猫"}
                    await p.management_message(message)
                    await p.management_message({**message,"processed_plain_text":message["processed_plain_text"]+"，想和你聊聊"})
                    await p.planner_context(session_id="private",items=[])
                    awake = p.living.awake_until(now)
                    expected = mode in {"ordinary","keyword"}
                    assert bool(awake) == expected, (hour,mode,awake)
                    send = await p.sleep_before_send({"session_id":"private"})
                    assert (send["action"]=="continue") == (expected or mode in {"all_day","nap_open"}), (mode,send)
                    if mode == "ordinary":
                        now += timedelta(seconds=30)
                        await p.management_message(message)
                        assert p.living.awake_until(now) > awake
                        now = p.living.awake_until(now)
                        assert p.living.quiet_sleep(now)
                        p.living.apply_schedule(now)
                        assert p.engine.current(now)["kind"] == "sleep"
                    checks.append({"hour":hour,"mode":mode,"awake":bool(awake),"send":send["action"]})
                output = {"checks":checks,"default":p.get_default_config()["life"]["private_wake_probability"]}
            elif case == "cooking":
                now = now.replace(hour=18)
                with p.store.db:
                    p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "hunger": 50, "energy": 80})
                cook = p.engine.create_plan(PlanInput(title="为自己做今晚的晚饭：煮番茄鸡蛋面", kind="cook", start=now.isoformat(), end=(now+timedelta(minutes=30)).isoformat()), "", now)
                now += timedelta(minutes=30)
                p.engine.advance(now)
                before = p.engine.state()["hunger"]
                assert before == 52.5, before
                await p.continuity.reflect(cook["id"],now)
                memory = p.store.all("activity_memory")[-1]
                p.config.life.autonomous = True
                await p.living.choose_activity(now)
                meal = p.engine.current(now)
                assert meal["kind"] == "meal", meal
                assert meal["continuation_id"] == memory["id"], (meal,memory)
                context = p.context_text("private",text="晚饭做好了吗，现在在干什么")
                assert meal["title"] in context, context
                minutes = (p.engine.time(meal["end"])-now).total_seconds()/60
                assert 10 <= minutes <= 120, minutes
                now = p.engine.time(meal["end"])
                p.engine.advance(now)
                assert p.engine.state()["hunger"] < before, p.engine.state()
                await p.continuity.reflect(meal["id"],now)
                output = {"cooking":memory,"meal":meal,"hunger_after_cooking":before,"hunger_after_meal":p.engine.state()["hunger"],"meal_minutes":minutes,"chat_context":context,"meal_memory":p.store.all("activity_memory")[-1]}
            elif case == "settings":
                reading(120,30)
                p.config.books.autonomous_minutes=-1
                p.config.books.session_minutes=25
                assert p.living.reading_allowance(now)["available_minutes"]==0
                p.config.books.break_minutes=30
                assert p.living.reading_allowance(now)["available_minutes"]==25
                p.config.books.break_minutes=0
                assert p.living.reading_allowance(now)["available_minutes"]==25
                p.config.books.autonomous_minutes=0
                assert p.living.reading_allowance(now)["available_minutes"]==0
                output={"daily_unlimited":True,"session_minutes":25,"break_minutes":30,"zero_break":True,"disabled":True}
            elif case == "coupling":
                checks=[]
                for hour in (12,23):
                    now=now.replace(hour=hour,minute=30)
                    with p.store.db:
                        p.store.put("state",{**p.engine.state(),"updated_at":now.isoformat()})
                    plan=p.engine.create_plan(PlanInput(title="看书",start=now.isoformat(),end=(now+timedelta(minutes=60)).isoformat()),"",now)
                    p.engine.advance(now+timedelta(minutes=20))
                    p.engine.transition(plan["id"],"pause","",now+timedelta(minutes=20))
                    now+=timedelta(hours=2)
                    p.engine.advance(now)
                    p.engine.transition(plan["id"],"resume","",now)
                    now+=timedelta(minutes=40)
                    p.engine.advance(now)
                    result=p.living.reading_allowance(now)
                    assert result["read_minutes"]==(60 if hour==12 else 40),result
                    assert result["next_at"]==(now+timedelta(minutes=60)).isoformat(),result
                    checks.append(result)
                with p.store.db:
                    p.store.put("activity_memory",{"id":"recipe","session_id":"test-a","activity":"阅读"})
                assert not p.living.is_reading({"title":"去买菜","continuation_id":"recipe"})
                value=PlanInput(title="买菜",continuation_id="recipe",start=now.isoformat(),end=(now+timedelta(minutes=15)).isoformat())
                try:
                    p.engine.create_plan(value,"test-b",now)
                except ValueError as error:
                    assert "不属于当前聊天流" in str(error)
                else:
                    raise AssertionError("跨聊天的接续记录被关联")
                assert p.engine.create_plan(value,"test-a",now)["session_id"]=="test-a"
                output={"reading":checks,"shopping_is_reading":False,"ownership":"通过"}
            elif case == "connections":
                p.config.life.background = "今天在家做饭，刚发现盐快用完了，想在下午空闲时去买一袋盐，家里其他东西都齐全。"
                plan = p.engine.create_plan(PlanInput(title="做一顿家常饭", kind="meal", start=now.isoformat(), end=(now+timedelta(minutes=30)).isoformat()), "", now)
                now += timedelta(minutes=30)
                p.engine.advance(now)
                event = p.store.get("experience", p.store.get("plan", plan["id"])["experience_id"])
                with p.store.db:
                    event["details"].update({"做饭发现": "盐快用完了", "之后的打算": "下午空闲时去买一袋盐"})
                    p.store.put("experience", event)
                await p.continuity.reflect(plan["id"], now)
                memory, = p.store.all("activity_memory")
                assert memory["next_action"], memory
                first = p.store.get("experience", memory["experience_id"])
                if memory["next_date"]:
                    date = p.engine.time(memory["next_date"] + "T16:30:00")
                    now = max(now, date)
                with p.store.db:
                    p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat(), "hunger": 20, "energy": 80})
                p.config.life.autonomous = True
                await p.living.choose_activity(now)
                followup = next(x for x in p.store.all("plan") if x.get("continuation_id") == memory["id"])
                now = p.engine.time(followup["end"])
                p.engine.advance(now)
                await p.continuity.reflect(followup["id"], now)
                saved = p.store.get("activity_memory", memory["id"])
                event = p.store.get("experience", saved["experience_id"])
                assert event["details"]["接续缘由"] == memory["next_action"], event
                assert event["details"]["上次经历"] == first["summary"], event
                assert event["source"] == "simulated"
                assert saved["completed"], saved
                output = {"first": first["summary"], "intention": memory["next_action"], "followup": followup["title"], "reason": followup["motivation"],
                          "minutes": (p.engine.time(followup["end"])-p.engine.time(followup["start"])).total_seconds()/60,
                          "result": event["summary"], "next_action": saved["next_action"], "activity": saved["activity"], "subject": saved["subject"],
                          "models": [{"feature":x["feature"],"task":x["task"],"status":x["status"]} for x in p.store.all("model_usage")]}
            elif case == "rest":
                plan = p.engine.create_plan(PlanInput(title="坐着听两首歌，放松一会儿",kind="rest",start=now.isoformat(),end=(now+timedelta(minutes=10)).isoformat()),"",now)
                now += timedelta(minutes=10)
                p.engine.advance(now)
                await p.continuity.reflect(plan["id"],now)
                memory, = p.store.all("activity_memory")
                assert memory["next_action"] == "", memory
                event = p.store.get("experience",memory["experience_id"])
                output = {"result":event["summary"],"next_action":memory["next_action"],"completed":memory["completed"]}
            elif case == "reading":
                reading(90, 70)
                assert p.living.reading_allowance(now)["available_minutes"] == 0
                p.config.life.autonomous = True
                p.config.life.background = "今天在家休息，喜欢读书、做饭、整理房间和听音乐。"
                await p.living.choose_activity(now)
                plan = next(x for x in p.store.all("plan") if x["status"] in {"scheduled", "active"} and x["kind"] != "travel")
                assert not plan.get("book_id") and not p.living.reading_title(plan["title"])
                manual = p.books.schedule(book["id"], (now+timedelta(hours=4)).isoformat(), 60, p.spatial, now, session="private-test")
                assert manual["session_id"] == "private-test"
                assert p.engine.time(manual["end"]) - p.engine.time(manual["start"]) == timedelta(minutes=60)
                output = {"autonomous": plan["title"], "reason": plan["motivation"], "allowance": p.living.reading_allowance(now), "manual_minutes":60,
                          "models":[{"feature":x["feature"],"task":x["task"],"status":x["status"]} for x in p.store.all("model_usage")]}
            elif case == "interval":
                plan=reading(30, 30)
                assert p.living.reading_allowance(now)["available_minutes"] == 0
                assert p.living.reading_allowance(now+timedelta(minutes=30))["available_minutes"] == 40
                with p.store.db:
                    p.store.delete("plan",plan["id"])
                reading(75,60)
                assert p.living.reading_allowance(now)["available_minutes"] == 15
                output={"waiting":0,"after_break":40,"remaining":15}
            elif case in {"fatigue", "reminder"}:
                stream="3a2048f0aa247f60f912da52c3a8ece6"
                # 发送发生在 WebUI 专用测试聊天，日期使用实际时间。
                now = datetime.now(now.tzinfo)
                p.config.proactive.enabled=True
                p.config.proactive.mode="主动表达"
                p.config.proactive.session_ids=[stream]
                p.config.proactive.start_hour=p.config.proactive.end_hour=0
                with p.store.db:
                    p.store.put("state",{**p.engine.state(),"updated_at":now.isoformat(),"energy":20})
                p.engine.record_experience(ExperienceInput(summary="MaiWeave 实机测试：整理了一小块书桌，测试普通分享恢复发送",source="simulated",evidence="临时测试数据",importance=6,occurred_at=now.isoformat()),stream,now)
                if case == "reminder":
                    p.engine.create_plan(PlanInput(title="MaiWeave 实机测试：约定检查",kind="social",participants=["测试用户"],start=(now+timedelta(minutes=20)).isoformat(),end=(now+timedelta(minutes=40)).isoformat()),stream,now)
                await p.living.share(now)
                if case == "fatigue":
                    assert not p.store.all("share_receipt")
                    output["waiting_reason"]=p.living.share_decisions[0]["reason"]
                    assert "精力较低" in output["waiting_reason"]
                    with p.store.db:
                        p.store.put("state",{**p.engine.state(),"energy":26})
                    await p.living.share(now)
                rows=p.store.all("share_receipt")
                assert rows and rows[0]["outcome"] == "实际已发送", rows
                assert rows[0]["notice"] == (case == "reminder")
                output.update(delivery=rows[0]["outcome"],notice=rows[0]["notice"],quiet=(await p.get_life_status())["quiet"])
                assert output["quiet"] is False
            else:
                raise ValueError("未知测试场景")
            self.result={"status":"通过","case":case,"result":output}
        except Exception as error:
            self.ctx.logger.exception("阅读与精力实机验证失败")
            self.result={**self.result,"status":"失败","case":case,"error":str(error),"trace":__import__("traceback").format_exc()}
        finally:
            self.active = None
            await p.on_unload()


def create_plugin():
    return Probe()
