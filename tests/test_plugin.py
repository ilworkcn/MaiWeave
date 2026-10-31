"""使用真实 SDK 检查注册和工具链，宿主服务使用测试替身。"""

import json
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
    plugin.set_plugin_config(config.model_dump())
    context = SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path), logger=logging.getLogger("test"),
                              send=SimpleNamespace(text=AsyncMock(return_value=True)),
                              config=SimpleNamespace(get=AsyncMock(return_value={"personality": "喜欢安静"})),
                              llm=SimpleNamespace(generate=AsyncMock()),
                              maisaka=SimpleNamespace(proactive=SimpleNamespace(trigger=AsyncMock(return_value={"success": True}))))
    plugin._set_context(context)
    await plugin.on_load()
    yield plugin
    await plugin.on_unload()


async def test_sdk_components_register(plugin):
    components = plugin.get_components()
    assert {c["name"] for c in components} == {
        "maiweave_query", "maiweave_create_plan", "maiweave_change_plan", "maiweave_record_experience",
        "maiweave_revise_experience", "maiweave_goal", "maiweave_relationship", "maiweave_emotion",
        "maiweave_sync_memory", "maiweave_status", "planner_context", "reply_context",
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


async def test_proactive_requests_are_limited(plugin):
    now = plugin.now().replace(hour=12)
    plugin.config.proactive.enabled = True
    plugin.config.proactive.session_ids = ["chat-a"]
    from maiweave.models import ExperienceInput
    plugin.engine.record_experience(ExperienceInput(summary="完成了一个小作品", occurred_at=now.isoformat(), source="simulated", evidence="角色模拟", importance=2), "chat-a", now)
    await plugin.living.share(now)
    await plugin.living.share(now + timedelta(minutes=1))
    assert plugin.ctx.maisaka.proactive.trigger.await_count == 1
    assert plugin.store.get("sharing", "chat-a")["count"] == 1


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
