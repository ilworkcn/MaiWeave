"""宿主联网配置检查、地点缓存和实际调用状态。"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_plugin import plugin

@pytest.mark.parametrize("config", [
    '[web.services.test]\nadapter="tavily"\napi_key="key"',
    '[model_task_config.web_search]\nmodel_list=["missing"]',
    '[model_task_config.web_search]\nmodel_list=["test"]\n[web.services.test]\nadapter="tavily"',
    '[model_task_config.web_search]\nmodel_list=["test"]\n[web.services.test]\nadapter="stepsearch_mcp"',
])
async def test_incomplete_host_config_stops_all_network_operations(plugin, config):
    plugin.config.world.search_enabled = True
    plugin.config.world.places_enabled = True
    plugin.world.host_config_path.write_text(config, encoding="utf-8")
    plugin.ctx.web = SimpleNamespace(search=AsyncMock(), fetch=AsyncMock())
    now = plugin.now()
    for operation in [lambda: plugin.world.search("咖啡", now), lambda: plugin.world.read("https://example.org", now),
                      lambda: plugin.world.discover_places(plugin.spatial, now, force=True, activity="咖啡")]:
        with pytest.raises(ValueError, match="请"):
            await operation()
    plugin.ctx.web.search.assert_not_awaited()
    plugin.ctx.web.fetch.assert_not_awaited()
    assert not (await plugin.ui_world())["search_enabled"]

async def test_master_switch_cache_force_and_connection_results(plugin):
    plugin.config.world.places_enabled = True
    plugin.ctx.web = SimpleNamespace(search=AsyncMock(return_value={"results":[]}), fetch=AsyncMock(return_value={"title":"网页", "content":"正文"}))
    now = plugin.now()
    with pytest.raises(ValueError, match="开启宿主联网搜索"):
        await plugin.world.discover_places(plugin.spatial, now, activity="咖啡")
    plugin.ctx.web.search.assert_not_awaited()
    plugin.config.world.search_enabled = True
    assert "等待首次联网调用" in (await plugin.world.search_status())["summary"]
    plugin.ctx.llm.generate.return_value = {"success":True,"response":json.dumps({"places":[]})}
    await plugin.world.discover_places(plugin.spatial, now, activity="咖啡")
    assert "连接成功" in (await plugin.ui_world())["search_status"]
    plugin.config.world.search_enabled = False
    plugin.world.host_config_path.write_text("",encoding="utf-8")
    assert await plugin.world.discover_places(plugin.spatial, now, activity="咖啡") == []
    assert plugin.ctx.web.search.await_count == 1
    with pytest.raises(ValueError):
        await plugin.world.discover_places(plugin.spatial, now, force=True, activity="咖啡")

async def test_fetch_failure_recovery_and_service_change(plugin):
    plugin.config.world.search_enabled = True
    plugin.ctx.web = SimpleNamespace(fetch=AsyncMock(side_effect=TimeoutError("网页读取超时")))
    with pytest.raises(TimeoutError):
        await plugin.world.read("https://example.org",plugin.now())
    assert "连接失败" in (await plugin.world.search_status())["summary"]
    plugin.ctx.web.fetch.side_effect = None
    plugin.ctx.web.fetch.return_value = {"title":"网页","content":"正文"}
    await plugin.world.read("https://example.org",plugin.now())
    assert "连接成功" in (await plugin.world.search_status())["summary"]
    plugin.world.host_config_path.write_text('[model_task_config.web_search]\nmodel_list=["mcp"]\n[web.services.mcp]\nadapter="stepsearch_mcp"\nserver_ref="step"',encoding="utf-8")
    status=await plugin.world.search_status()
    assert status["ready"] and status["connection"] == "等待首次联网调用"

async def test_missing_config_is_visible(plugin):
    plugin.world.host_config_path.unlink()
    assert "配置读取失败" in (await plugin.ui_world())["search_status"]

async def test_autonomous_planning_uses_existing_places_when_host_unassigned(plugin):
    plugin.config.world.search_enabled = True
    plugin.config.world.places_enabled = True
    plugin.config.life.autonomous = True
    plugin.config.life.schedule_enabled = False
    plugin.world.host_config_path.write_text("", encoding="utf-8")
    plugin.ctx.web = SimpleNamespace(search=AsyncMock())
    plugin.ctx.llm.generate.return_value = {"success":True,"response":json.dumps({
        "title":"在家读书","kind":"leisure","minutes":30,"location":"住处","motivation":"接着读"})}
    await plugin.living.choose_activity(plugin.now())
    assert plugin.store.all("plan")[0]["location"] == "住处"
    plugin.ctx.web.search.assert_not_awaited()
