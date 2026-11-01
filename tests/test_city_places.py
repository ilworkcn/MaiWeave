"""城市地点收录、日程选择与原生记忆连接状态。"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.error import HTTPError
import pytest
from maiweave.config import WorldConfig, MemoryConfig
from maiweave.memory import NativeMemory
from test_plugin import plugin


def test_city_defaults():
    assert WorldConfig().city == "大庆"
    assert WorldConfig().country_code == "CN"


async def test_city_search_connects_places_to_schedule(plugin):
    plugin.config.world.places_enabled = True
    plugin.ctx.web = SimpleNamespace(search=AsyncMock(return_value={"results": [
        {"title": "大庆黎明湖公园", "url": "https://example.org/park", "snippet": "大庆市黎明湖公园"}]}))
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"places": [
        {"name": "黎明湖公园", "category": "park", "description": "湖畔公园", "source_index": 0}]})}
    await plugin.world.discover_places(plugin.spatial, plugin.now())
    place = plugin.spatial.place("黎明湖公园")
    assert place["source_url"] == "https://example.org/park"
    assert plugin.spatial.route("住处", "黎明湖公园")["minutes"] == plugin.spatial.route("住处", "公园")["minutes"]
    assert "黎明湖公园" in plugin.context_text("a")
    assert place["id"] in {p["id"] for p in plugin.world.planning_places()}
    assert "park" not in {p["id"] for p in plugin.world.planning_places()}
    await plugin.world.discover_places(plugin.spatial, plugin.now())
    assert plugin.ctx.web.search.await_count == 2
    plugin.config.world.places_enabled = False
    await plugin.world.discover_places(plugin.spatial, plugin.now(), force=True)
    assert plugin.ctx.web.search.await_count == 2
    assert "park" in {p["id"] for p in plugin.world.planning_places()}
    assert place["id"] not in {p["id"] for p in plugin.world.planning_places()}
    assert plugin.world.place_status()["status"] == "已关闭"
    assert any(p["name"] == "黎明湖公园" for p in (await plugin.ui_editor_records("plan"))["places"])
    saved = plugin.spatial.save_place("黎明湖公园", True, "更新介绍", place["id"])
    assert saved["source_url"] == place["source_url"]
    plugin.config.world.places_enabled = True
    plugin.config.world.city = "哈尔滨"
    assert plugin.world.place_status()["status"] == "尚未搜索"
    assert place["id"] not in {p["id"] for p in plugin.world.planning_places()}


async def test_memory_status_tracks_failure_and_reconnect(monkeypatch):
    memory = NativeMemory(MemoryConfig(enabled=True))
    def failed(*args):
        raise HTTPError("http://localhost", 401, "登录失效", {}, None)
    monkeypatch.setattr(memory, "_once", failed)
    result = await memory.check_connection()
    assert result["status"] == "连接失败" and "401" in result["error"]
    monkeypatch.setattr(memory, "_once", lambda *args: {"items": []})
    result = await memory.check_connection()
    assert result["status"] == "已连接" and result["checked_at"] and result["error"] == ""
    memory.config.enabled = False
    assert (await memory.check_connection())["status"] == "未启用"


async def test_weather_changes_city_without_waiting_for_interval(plugin):
    now = plugin.now()
    plugin.config.world.weather_enabled = True
    with plugin.store.db:
        plugin.store.put("world", {"id": "weather_place", "query": "上海|CN"})
        plugin.store.put("world_status", {"id": "weather", "attempted_at": now.isoformat()})
    plugin.world.weather = AsyncMock()
    await plugin.world.refresh(now)
    plugin.world.weather.assert_awaited_once_with(now)
