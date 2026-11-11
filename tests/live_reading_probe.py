"""在实机临时数据目录核验阅读节奏，模型与消息接口使用 MaiBot 服务。"""
import asyncio
import base64
import importlib.util
import sys
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from maibot_sdk import API, MaiBotPlugin


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
            if case == "settings":
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
            self.result={"status":"失败","case":case,"error":str(error)}
        finally:
            await p.on_unload()


def create_plugin():
    return Probe()
