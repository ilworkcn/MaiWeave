"""从聊天入口、真实 HTTP 请求和生活时钟验证功能连接。"""
import asyncio
import json
from contextlib import suppress
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from maiweave.core.models import PlanInput, ExperienceInput, RoutineInput
from maiweave.integrations.memory import NativeMemory
from maiweave.config import MemoryConfig
from test_plugin import plugin
from test_memory import server
from test_review_workflow import decision


async def stop_clock(plugin):
    plugin.worker.cancel()
    with suppress(asyncio.CancelledError):
        await plugin.worker


async def test_native_sync_confirmation_over_http(plugin, server, tmp_path):
    await stop_clock(plugin)
    url, requests = server
    (tmp_path / "webui.json").write_text(json.dumps({"access_token":"test-session"}), encoding="utf-8")
    plugin.memory = NativeMemory(MemoryConfig(enabled=True, base_url=url))
    plugin.memory.root = tmp_path
    event = plugin.engine.record_experience(ExperienceInput(summary="生日是八月三日", source="reported",
        occurred_at=plugin.now().isoformat(), evidence="本人说明"), "a", plugin.now())
    decision(plugin, ["生日与原有记录不同"])
    started = await plugin.ui_sync_record(event["id"])
    await asyncio.gather(*plugin.jobs)
    pending = await plugin.ui_change_status(started["review_id"])
    assert pending["status"] == "待确认"
    assert pending["proposed"]["summary"] == event["summary"]
    assert all(row[0] == "GET" for row in requests)
    await plugin.ui_change_decide(pending["id"], True)
    await asyncio.gather(*plugin.jobs)
    assert requests[-1][0] == "POST"
    assert requests[-1][2] == "maibot_session=test-session"
    assert plugin.store.get("experience", event["id"])["memory_revision"] == 1
    changed = plugin.engine.revise_experience(event["id"], {}, "生日是八月四日", "本人改正", "a", plugin.now())
    record = await plugin.sync_experience(changed)
    assert any(row[1].endswith("/facts/claim-123") and row[0] == "GET" for row in requests)
    writes = sum(row[0] != "GET" for row in requests)
    plugin.engine.revise_experience(event["id"], {}, "生日是八月五日", "再次改正", "a", plugin.now())
    await plugin.reviews.decide(record["id"], True)
    updated = plugin.store.get("review", record["id"])
    assert updated["status"] == "待确认" and updated["proposed"]["revision"] == 3
    assert sum(row[0] != "GET" for row in requests) == writes
    await plugin.reviews.decide(record["id"], True)
    assert requests[-1][0] == "PATCH" and "八月五日" in requests[-1][3]["value_text"]
    assert plugin.store.get("experience", event["id"])["memory_revision"] == 3


async def test_first_spoken_birthday_reads_native_person_before_save(plugin):
    plugin.config.memory.enabled = True
    plugin.ctx.chat = SimpleNamespace(get_all_streams=AsyncMock(return_value=[{"stream_id":"a", "platform":"qq"}]))
    plugin.ctx.person = SimpleNamespace(get_id=AsyncMock(return_value="person-a"), get_value=AsyncMock(return_value="u"))
    plugin.memory.people = AsyncMock(return_value={"items":[{"person_id":"person-a", "person_name":"小明", "profile_text":"生日为八月三日，角色的恋人"}]})
    decision(plugin, ["原生画像生日为八月三日"], 3)
    response = await plugin.personal("save_date", month=8, day=5, stream_id="a", user_id="u")
    pending = response["content"]
    assert pending["status"] == "待确认" and not plugin.store.all("profile")
    assert "八月三日" in pending["existing"]["原生记忆"]["profile_text"]
    assert pending["id"] in plugin.context_text("a")
    await plugin.confirm_change(pending["id"], True, stream_id="a")
    profile = plugin.store.all("profile")[0]
    assert profile["person_id"] == "person-a" and profile["intimacy"] == 3
    assert plugin.living.intimacy("a", "u") == 3


async def test_replan_respects_relationship_and_keeps_original_on_rejection(plugin):
    now = plugin.now()
    plan = plugin.engine.create_plan(PlanInput(title="学习", kind="study", start=(now+timedelta(minutes=5)).isoformat(),
        end=(now+timedelta(minutes=35)).isoformat(), flexible=True), "", now, autonomous=True)
    until=(now+timedelta(minutes=45)).isoformat()
    with pytest.raises(ValueError, match="当前关系"):
        await plugin.continuity_tool("replan", reason="陪我聊天", unavailable_until=until, stream_id="a", user_id="u")
    assert plugin.store.find("runtime", "busy") is None
    assert plugin.store.get("plan", plan["id"])["start"] == plan["start"]
    await plugin.ui_save_profile("小明", "a", "u", intimacy_mode="manual", intimacy=2)
    await plugin.continuity_tool("replan", reason="陪我聊天", unavailable_until=until, stream_id="a", user_id="u")
    assert plugin.store.get("plan", plan["id"])["start"] == until


async def test_travel_time_and_recurring_plans_use_relationship(plugin, monkeypatch):
    # 固定在次日中午，重复规则和活动使用同一天的时间。
    now=(plugin.now()+timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(plugin, "now", lambda: now)
    plan=plugin.engine.create_plan(PlanInput(title="学习",kind="study",start=(now+timedelta(minutes=5)).isoformat(),
        end=(now+timedelta(minutes=20)).isoformat()),"",now,autonomous=True)
    # 活动本身在学习之后，前往公园的路程会占用学习时间。
    request={"title":"逛公园","start":(now+timedelta(minutes=21)).isoformat(),
             "end":(now+timedelta(minutes=40)).isoformat(),"location":"公园"}
    rejected=await plugin.create_plan(request,stream_id="a",user_id="u")
    assert not rejected["success"] and len(plugin.store.all("plan")) == 1
    routine=RoutineInput(title="聊天",kind="social",frequency="daily",anchor_date=now.date().isoformat(),
                         start_time=(now+timedelta(minutes=6)).strftime("%H:%M"),duration_minutes=10)
    plugin.config.rhythm.horizon_days=1
    response=await plugin.routine_tool("save",routine=routine.model_dump(),stream_id="a",user_id="u")
    assert response["issues"] and plugin.store.get("plan",plan["id"])["start"]==plan["start"]
    await plugin.ui_save_profile("小明","a","u",intimacy_mode="manual",intimacy=2)
    plugin.rhythm.materialize(now)
    assert any(p.get("routine_id")==response["rule"]["id"] for p in plugin.store.all("plan"))


async def test_diary_corrects_conflicting_slot_and_rearranges_cancelled_window(plugin):
    now=plugin.now()
    fixed=plugin.engine.create_plan(PlanInput(title="聊天约定",start=(now+timedelta(minutes=10)).isoformat(),
        end=(now+timedelta(minutes=30)).isoformat(),participants=["小明"]),"a",now)
    def slot(minutes):
        return {"success":True,"response":json.dumps({"start":(now+timedelta(minutes=minutes)).isoformat(),
            "end":(now+timedelta(minutes=minutes+15)).isoformat(),"reason":"回顾一天"})}
    plugin.ctx.llm.generate.side_effect=[slot(10),slot(35),slot(55)]
    await plugin.diary.arrange(now,plugin.spatial)
    first=next(p for p in plugin.store.all("plan") if p["kind"]=="diary")
    assert "聊天约定" in plugin.ctx.llm.generate.call_args_list[0].args[0]
    assert first["start"]==(now+timedelta(minutes=35)).isoformat()
    plugin.spatial.transition(first["id"],"cancel","",now)
    await plugin.diary.arrange(now,plugin.spatial)
    replacement=next(p for p in plugin.store.all("plan") if p["kind"]=="diary" and p["status"]=="scheduled")
    assert replacement["id"]!=first["id"]
    assert plugin.store.get("plan",fixed["id"])["start"]==fixed["start"]


async def test_diary_planning_wait_leaves_life_clock_running(plugin, monkeypatch):
    await stop_clock(plugin)
    plugin.config.life.autonomous=True
    entered=asyncio.Event(); release=asyncio.Event()
    async def waiting(now, spatial):
        entered.set(); await release.wait()
    monkeypatch.setattr(plugin.diary,"arrange",waiting)
    plugin.living.choose_activity=AsyncMock()
    await plugin.tick()
    await entered.wait()
    await plugin.background["自主生活"]
    plugin.living.choose_activity.assert_awaited_once()
    assert not plugin.background["日记安排"].done()
    release.set()
    await asyncio.gather(*plugin.background.values())


async def test_diary_writes_only_during_active_window(plugin):
    await stop_clock(plugin)
    now=plugin.now()
    start=now+timedelta(minutes=1)
    plan=plugin.engine.create_plan(PlanInput(title="写日记",kind="diary",start=start.isoformat(),
        end=(start+timedelta(minutes=15)).isoformat(),diary_day=now.date().isoformat()),"",now,autonomous=True)
    plugin.engine.advance(start+timedelta(minutes=16))
    await plugin.diary.process(start+timedelta(minutes=16))
    assert not plugin.store.all("diary")
    plugin.ctx.llm.generate.assert_not_awaited()


async def test_missing_conflict_assessment_retries_instead_of_writing(plugin):
    plugin.ctx.llm.generate.side_effect=[{"success":True,"response":"{}"},
        {"success":True,"response":'{"conflicts":["生日已有记录"]}'}]
    result=await plugin.reviews.submit("profile",{"name":"小明","session_id":"a","user_id":"u"},"a")
    assert result["status"]=="待确认" and not plugin.store.all("profile")
    assert plugin.ctx.llm.generate.await_count==2



async def test_autonomous_planning_uses_diary_slot_added_during_model_call(plugin):
    await stop_clock(plugin)
    plugin.config.life.autonomous = True
    plugin.config.life.schedule_enabled = False
    now = plugin.now()
    async def choose(*args, **kwargs):
        plugin.engine.create_plan(PlanInput(title="写生活日记", kind="diary",
            start=(now + timedelta(minutes=20)).isoformat(),
            end=(now + timedelta(minutes=35)).isoformat()), "", now, autonomous=True)
        return {"success": True, "response": json.dumps({"title": "阅读", "kind": "leisure",
            "minutes": 60, "location": "住处", "motivation": "读一会儿书"})}
    plugin.ctx.llm.generate.side_effect = choose
    await plugin.living.choose_activity(now)
    plans = plugin.store.all("plan")
    reading = next(p for p in plans if p["title"] == "阅读")
    diary = next(p for p in plans if p["kind"] == "diary")
    assert plugin.engine.time(reading["end"]) <= plugin.engine.time(diary["start"])
