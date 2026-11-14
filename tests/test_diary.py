"""日记日程窗口、写作和失败重试。"""
import json
from datetime import timedelta
import pytest
from maiweave.core.models import PlanInput
from test_plugin import plugin


def history(plugin, at):
    with plugin.store.db:
        plugin.store.put("state_history", {"id": at.isoformat(), "at": at.isoformat(), "energy": 60, "mood": 10})


async def test_model_arranges_diary_and_only_writes_in_window(plugin):
    now = plugin.now()
    history(plugin, now - timedelta(minutes=1))
    start = now + timedelta(hours=1)
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"start": start.isoformat(), "end": (start+timedelta(minutes=15)).isoformat(), "reason": "空闲时回顾"})}
    await plugin.diary.arrange(now, plugin.spatial)
    await plugin.diary.arrange(now, plugin.spatial)
    plan = plugin.store.all("plan")[0]
    assert plan["kind"] == "diary"
    await plugin.diary.process(now)
    assert plugin.store.all("diary") == []
    plugin.engine.advance(start)
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "今天过得很平静。"}
    await plugin.diary.process(start)
    await plugin.diary.process(start + timedelta(minutes=1))
    assert plugin.store.all("diary")[0]["plan_id"] == plan["id"]
    assert plugin.ctx.llm.generate.await_count == 2
    assert plugin.engine.current(start)["kind"] == "diary"


async def test_manual_diary_occupies_real_schedule(plugin):
    now=plugin.now();history(plugin,now-timedelta(minutes=1))
    plan=await plugin.ui_write_diary()
    assert plan["kind"]=="diary"
    assert plugin.engine.time(plan["end"])-plugin.engine.time(plan["start"])==timedelta(minutes=15)


async def test_diary_model_retries_empty_output(plugin):
    now=plugin.now();history(plugin,now-timedelta(minutes=1))
    plugin.ctx.llm.generate.side_effect=[{"success":True,"response":""},{"success":True,"response":"写下今天的心情。"}]
    result=await plugin.diary.write("",now)
    assert result["status"]=="已生成"
    assert len(plugin.store.all("model_usage"))==2


async def test_diary_empty_day(plugin):
    with pytest.raises(ValueError,match="生活记录"):
        await plugin.diary.write((plugin.now().date()-timedelta(days=3)).isoformat(),plugin.now())


async def test_missed_diary_window_is_written_after_restart(plugin):
    now = plugin.now()
    history(plugin, now - timedelta(minutes=1))
    plan = plugin.engine.create_plan(PlanInput(title="写日记", kind="diary",
        start=(now + timedelta(minutes=5)).isoformat(), end=(now + timedelta(minutes=20)).isoformat(),
        diary_day=now.date().isoformat()), "", now)
    await plugin.on_unload()
    await plugin.on_load()
    later = now + timedelta(minutes=30)
    plugin.engine.advance(later)
    assert plugin.store.get("plan", plan["id"])["status"] == "completed"
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "今天整理了书桌，留下一点安静的时间。"}
    await plugin.diary.process(later)
    diary = plugin.store.find("diary", now.date().isoformat())
    assert diary and diary["status"] == "已生成"
    assert diary["plan_id"] == plan["id"]
    await plugin.diary.process(later + timedelta(minutes=1))
    assert plugin.ctx.llm.generate.await_count == 1
