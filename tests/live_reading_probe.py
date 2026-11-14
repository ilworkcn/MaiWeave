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

from maibot_sdk import API, HookHandler, MaiBotPlugin


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

    @HookHandler("maisaka.planner.before_request")
    async def planner_context(self, **kwargs):
        if self.active and kwargs.get("session_id") == "3a2048f0aa247f60f912da52c3a8ece6":
            result = await self.active.planner_context(**kwargs)
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
            return await self.active.planner_receipt(**kwargs)
        return {"action": "continue"}

    @HookHandler("maisaka.replyer.before_request")
    async def reply_context(self, **kwargs):
        if self.active and kwargs.get("session_id") == "3a2048f0aa247f60f912da52c3a8ece6":
            return await self.active.reply_context(**kwargs)
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
                assert row["outcome"] in {"实际已发送","Planner 选择沉默"},row
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
                p.engine.advance(now)
                marker="麦织实机验收-"+uuid4().hex[:8]
                stages=[]
                self.result.update(stages=stages, data_dir=str(folder))
                event=p.engine.record_experience(ExperienceInput(summary=marker+"：已在 Edge 完成测试日程的新建和修改。",occurred_at=now.isoformat(),source="observed",evidence="本次浏览器实际操作结果",details={"用途":"插件验收测试"}),stream,now)
                saved=await p.saved_experience(event)
                assert saved["memory"]["status"]=="已保存",saved["memory"]
                event=p.store.get("experience",event["id"])
                fact=await p.memory.fact(event["memory_id"])
                assert marker in str(fact),fact
                stages.append({"step":"记忆创建与原文读取","claim_id":event["memory_id"],"status":"通过"})
                event=p.engine.revise_experience(event["id"],{"日程结果":"已取消"},"","浏览器历史记录已显示测试日程取消",stream,now)
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
            self.result={**self.result,"status":"失败","case":case,"error":str(error)}
        finally:
            self.active = None
            await p.on_unload()


def create_plugin():
    return Probe()
