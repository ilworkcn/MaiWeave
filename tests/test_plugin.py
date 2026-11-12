"""使用真实 SDK 检查注册和工具链，MaiBot 服务使用测试替身。"""

import json
import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from maiweave.config import MaiWeaveConfig
from maiweave.plugin import create_plugin


@pytest.fixture
async def plugin(tmp_path):
    plugin = create_plugin()
    config = MaiWeaveConfig()
    config.life.autonomous = False
    config.world.weather_enabled = False
    plugin.set_plugin_config(config.model_dump())
    context = SimpleNamespace(person=SimpleNamespace(get_id=AsyncMock(side_effect=lambda platform, user_id: platform + ":" + user_id)),
                              chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=[{"stream_id": name, "platform": "qq"} for name in ["a", "chat-a", "group", "private"]])),
                              paths=SimpleNamespace(data_dir=tmp_path), logger=logging.getLogger("test"),
                              send=SimpleNamespace(text=AsyncMock(return_value=True)),
                              config=SimpleNamespace(get=AsyncMock(return_value={"personality": "喜欢安静"})),
                              llm=SimpleNamespace(get_available_models=AsyncMock(return_value=["utils", "replyer", "planner"]), generate=AsyncMock(return_value={"success": False, "error": "测试尚未设置模型结果"})),
                              db=SimpleNamespace(get=AsyncMock(return_value=None)),
                              maisaka=SimpleNamespace(proactive=SimpleNamespace(trigger=AsyncMock(return_value={"success": True, "task_id": "proactive:ilworkcn.maiweave:1"}))))
    async def model_reply(prompt, **kwargs):
        if prompt.startswith("判断这次生活经历"):
            return {"success": True, "response": json.dumps({"send": True, "reason": "本次有新的具体内容"})}
        return context.llm.generate.return_value
    context.llm.generate.side_effect = model_reply
    plugin._set_context(context)
    await plugin.on_load()
    plugin.world.host_config_path = tmp_path / "model_config.toml"
    plugin.world.host_config_path.write_text('[model_task_config.web_search]\nmodel_list=["test"]\n[web.services.test]\nadapter="tavily"\napi_key="test-key"\n', encoding="utf-8")
    yield plugin
    await plugin.on_unload()


async def test_sdk_components_register(plugin):
    components = plugin.get_components()
    assert {c["name"] for c in components} >= {
        "maiweave_query", "maiweave_create_plan", "maiweave_change_plan", "maiweave_record_experience",
        "maiweave_revise_experience", "maiweave_goal", "maiweave_relationship", "maiweave_emotion",
        "maiweave_sync_memory", "maiweave_status", "planner_context", "reply_context",
        "maiweave_world", "maiweave_routine", "maiweave_space", "maiweave_dream", "maiweave_habits", "maiweave_home",
    }


async def test_tool_round_trip_and_hook(plugin):
    now = plugin.now()
    created = await plugin.create_plan({"title": "一起看电影", "start": (now + timedelta(hours=1)).isoformat(), "end": (now + timedelta(hours=3)).isoformat(), "participants": ["用户"]}, stream_id="chat-a")
    assert created["success"]
    plan_id = created["content"]["id"]
    updated = await plugin.change_plan(plan_id, "update", {"title": "一起看纪录片"}, stream_id="chat-a")
    assert updated["success"]
    result = await plugin.planner_context(items=[], session_id="chat-a")
    text = result["modified_kwargs"]["items"][0]["parts"][0]["text"]
    assert "一起看纪录片" in text
    reply = await plugin.reply_context(session_id="chat-a", extra_prompt="原提示")
    assert reply["modified_kwargs"]["extra_prompt"].startswith("原提示")
    await plugin.change_plan(plan_id, "cancel", stream_id="chat-a")
    assert plugin.store.get("plan", plan_id)["status"] == "cancelled"


async def test_validation_returns_business_error(plugin):
    result = await plugin.create_plan({"title": "缺少时间"}, stream_id="chat-a")
    assert result["success"] is False


async def test_autonomous_activity_uses_host_model(plugin):
    plugin.config.life.autonomous = True
    plugin.living.config = plugin.config
    now = plugin.now().replace(hour=12)
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"title": "读几页书", "kind": "leisure", "minutes": 30, "location": "住处", "motivation": "想继续昨天的阅读"})}
    await plugin.living.choose_activity(now)
    plans = plugin.store.all("plan")
    assert len(plans) == 1
    assert plans[0]["autonomous"]
    assert plans[0]["title"] == "读几页书"


async def test_failed_model_does_not_create_fake_activity(plugin):
    plugin.config.life.autonomous = True
    plugin.ctx.llm.generate.return_value = {"success": False, "error": "模型不可用"}
    with pytest.raises(RuntimeError, match="模型不可用"):
        await plugin.living.choose_activity(plugin.now().replace(hour=12))
    assert plugin.store.all("plan") == []


async def test_sleep_trip_arrives_before_sleep_and_wakes_on_time(plugin):
    plugin.config.life.autonomous = True
    now = plugin.now().replace(hour=23, minute=0, second=0, microsecond=0)
    state = plugin.engine.state()
    state["location"] = "公园"
    with plugin.store.db:
        plugin.store.put("state", state)
    await plugin.living.choose_activity(now)
    sleep = next(p for p in plugin.store.all("plan") if p["kind"] == "sleep")
    assert plugin.engine.time(sleep["start"]) == now + timedelta(minutes=15)
    assert plugin.engine.time(sleep["end"]).hour == 7
    assert plugin.store.get("plan", sleep["travel_id"])["route"]["destination"] == "住处"


@pytest.mark.parametrize("tool, outcome", [("reply", "实际已发送"), ("stop", "Planner 选择沉默")])
async def test_proactive_delivery_controls_budget(plugin, tool, outcome):
    from maiweave.core.models import ExperienceInput
    from maiweave.integrations.share_receipts import planner_task
    now = plugin.now().replace(hour=12)
    cfg = plugin.config.proactive
    cfg.enabled, cfg.session_ids, cfg.daily_limit, cfg.cooldown_minutes = True, ["chat-a"], 1, 60
    for score in (6, 7):
        plugin.engine.record_experience(ExperienceInput(summary="读书有了新发现", importance=score,
            occurred_at=now.isoformat(), source="simulated", evidence="角色模拟"), "chat-a", now)
    await plugin.living.share(now)
    task = plugin.ctx.maisaka.proactive.trigger.return_value["task_id"]
    marker = {"item_type": "UserMessageItem", "parts": [{"type": "text", "text":
        f'<plugin_proactive_task id="{task}" plugin_id="ilworkcn.maiweave">'}]}
    ordinary = {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": "<message msg_id=123>你好"}]}
    assert not planner_task([marker, ordinary])
    await plugin.planner_context(session_id="chat-a", items=[marker])
    await plugin.planner_receipt("chat-a", [{"item_type": "FunctionCallItem", "tool_call": {"func_name": tool, "call_id": "c1"}}])
    at = now + timedelta(minutes=2)
    data = {"success": True, "metadata": {"send_results": [{"success": True}, {"success": True}]} if tool == "reply" else {}}
    plugin.ctx.db.get.side_effect = lambda table, **kwargs: ({"tool_name": tool, "timestamp": at.isoformat(), "tool_data": json.dumps(data)} if table == "ToolRecord" else [])
    await plugin.living.receipts.refresh(at)
    await plugin.living.receipts.refresh(at)
    assert plugin.store.get("share_receipt", task)["outcome"] == outcome
    assert len(plugin.living.receipts.sent_times("chat-a", now.tzinfo)) == int(tool == "reply")
    plugin.ctx.maisaka.proactive.trigger.return_value = {"success": True, "task_id": task + "2"}
    await plugin.living.share(at + timedelta(minutes=10))
    if tool == "stop":
        assert plugin.ctx.maisaka.proactive.trigger.await_count == 2
    else:
        assert "今日实际已发送 1/1" in plugin.living.share_decisions[0]["reason"]
        cfg.daily_limit = 4
        await plugin.living.share(at + timedelta(minutes=59))
        assert "上次实际发送" in plugin.living.share_decisions[0]["reason"]
        await plugin.living.share(at + timedelta(minutes=60))
        assert plugin.ctx.maisaka.proactive.trigger.await_count == 2


async def test_proactive_requests_are_limited(plugin, monkeypatch):
    now = plugin.now().replace(hour=12)
    monkeypatch.setattr(plugin, "now", lambda: now + timedelta(minutes=1))
    plugin.config.proactive.enabled = True
    plugin.config.proactive.session_ids = ["chat-a"]
    from maiweave.core.models import ExperienceInput
    plugin.engine.record_experience(ExperienceInput(summary="完成了一个小作品", occurred_at=now.isoformat(), source="simulated", evidence="角色模拟", importance=6), "chat-a", now)
    await plugin.living.share(now)
    await plugin.living.share(now + timedelta(minutes=1))
    assert plugin.ctx.maisaka.proactive.trigger.await_count == 1
    assert plugin.living.receipts.sent_times("chat-a", now.tzinfo) == []
    decisions = (await plugin.ui_overview())["share_decisions"]
    assert [row["outcome"] for row in decisions[:2]] == ["跳过", "MaiBot 已受理"]
    assert decisions[0]["session"] == "chat-a"
    assert "等待回执" in decisions[0]["reason"]
    assert "经历分享" in decisions[1]["reason"]


async def test_share_schedule_and_recent_decisions(plugin, monkeypatch):
    plugin.worker.cancel()
    await asyncio.gather(plugin.worker, return_exceptions=True)
    await asyncio.gather(*plugin.jobs)
    plugin.config.proactive.enabled = True
    start = plugin.now().replace(hour=12, minute=0, second=0)
    async def run(name, job):
        if name == "主动交流":
            await job(plugin.now())
    monkeypatch.setattr(plugin, "run_background", run)
    for minute in [0, 1, 9, 10, *range(20, 101, 10)]:
        now = start + timedelta(minutes=minute)
        monkeypatch.setattr(plugin, "now", lambda: now)
        await plugin.tick()
        await asyncio.gather(*plugin.jobs)
        if minute < 10:
            assert len(plugin.living.share_decisions) == 1
    rows = (await plugin.ui_overview())["share_decisions"]
    assert [r["at"] for r in rows] == [
        (start + timedelta(minutes=m)).strftime("%m-%d %H:%M:%S") for m in range(100, 0, -10)]


async def test_default_share_threshold(plugin):
    from maiweave.core.models import ExperienceInput

    now = plugin.now().replace(hour=12)
    plugin.config.proactive.enabled = True
    plugin.config.proactive.session_ids = ["chat-a"]
    for score in (4, 5):
        plugin.engine.record_experience(ExperienceInput(
            summary=f"散步时的发现（{score}分）", importance=score, occurred_at=now.isoformat(),
            source="simulated", evidence="角色模拟"), "chat-a", now)
        await plugin.living.share(now)
        assert plugin.ctx.maisaka.proactive.trigger.await_count == score - 4


async def test_proactive_selects_highest_eligible_score(plugin):
    from maiweave.core.models import ExperienceInput
    now = plugin.now().replace(hour=12)
    plugin.config.proactive.enabled = True
    plugin.config.proactive.session_ids = ["chat-a"]

    def add(title, score, minutes):
        return plugin.engine.record_experience(ExperienceInput(
            summary=title, importance=score, occurred_at=(now - timedelta(minutes=minutes)).isoformat(),
            source="simulated", evidence="角色模拟"), "chat-a", now)

    add("散步", 1, 1)
    add("昨天的成果", 10, 360)
    await plugin.living.share(now)
    row = (await plugin.ui_overview())["share_decisions"][0]
    assert row["outcome"] == "跳过" and "1/10 分" in row["score"]
    assert "近六小时内 1 条" in row["reason"]
    add("整理笔记", 6, 2)
    chosen = add("完成作品", 7, 15)
    add("之前的突破", 7, 30)
    for i in range(12):
        add(f"普通记录{i}", 2, 1)
    plugin.config.proactive.share_threshold = 8
    await plugin.living.share(now)
    assert "门槛 8 分" in plugin.living.share_decisions[0]["score"]
    assert plugin.living.share_decisions[0]["outcome"] == "跳过"
    plugin.config.proactive.share_threshold = 7
    await plugin.living.share(now)
    row = (await plugin.ui_overview())["share_decisions"][0]
    assert "完成作品》7/10 分" in row["score"]
    assert chosen["score_reason"] in plugin.ctx.maisaka.proactive.trigger.await_args.args[1]
    assert plugin.store.all("share_receipt")[0]["experience_id"] == chosen["id"]
    assert not plugin.store.all("sharing")


async def test_disabled_plugin_does_not_write(plugin):
    plugin.config.life.enabled = False
    result = await plugin.emotion(2, -2, "开心", stream_id="chat-a")
    assert result["success"] is False
    assert plugin.store.all("emotion") == []


async def test_reload_preserves_data_and_stops_old_worker(plugin):
    created = await plugin.goal("create", {"title": "阅读", "target_sessions": 3}, stream_id="chat-a")
    old_worker = plugin.worker
    await plugin.on_config_update("self", plugin.get_plugin_config_data(), "0.1.0")
    assert old_worker.done()
    result = await plugin.query("goals", stream_id="chat-a")
    assert result["content"][0]["id"] == created["content"]["id"]


async def test_memory_failure_reports_local_save_separately(plugin):
    from urllib.error import URLError
    plugin.config.memory.enabled = True
    plugin.memory.sync = AsyncMock(side_effect=URLError("连接失败"))
    result = await plugin.record_experience({"summary": "昨晚看书", "occurred_at": (plugin.now() - timedelta(days=1)).isoformat(), "evidence": "用户明确说明"}, stream_id="chat-a")
    assert result["success"]
    assert result["content"]["memory"]["status"] == "failed"
    assert len(plugin.engine.experiences("chat-a")) == 1



async def test_model_task_dropdown_uses_host_tasks_and_keeps_selection(plugin):
    plugin.ctx.llm.get_available_models.assert_awaited_once()
    plugin.config.life.model_task = "custom_life"
    field = plugin.get_webui_config_schema()["sections"]["model_default"]["fields"]["model_task"]
    assert field["ui_type"] == "select"
    assert field["choices"] == ["utils", "replyer", "planner", "custom_life"]
    plugin.config.life.model_task = "planner"
    field = plugin.get_webui_config_schema()["sections"]["model_default"]["fields"]["model_task"]
    assert field["choices"] == ["utils", "replyer", "planner"]
    from maiweave.integrations.usage import generate
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "已完成"}
    await generate(plugin.ctx, plugin.store, plugin.config, "测试任务选择", "测试")
    assert plugin.ctx.llm.generate.call_args.kwargs["task_name"] == "planner"
