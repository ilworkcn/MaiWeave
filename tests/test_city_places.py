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
    plugin.config.world.search_enabled = True
    plugin.config.world.places_enabled = True
    plugin.ctx.web = SimpleNamespace(search=AsyncMock(return_value={"results": [
        {"title": "大庆黎明湖公园", "url": "https://example.org/park", "snippet": "大庆市黎明湖公园"}]}))
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"places": [
        {"name": "黎明湖公园", "category": "park", "outdoor": True, "description": "湖畔公园", "source_index": 0}]})}
    await plugin.world.discover_places(plugin.spatial, plugin.now(), activity="湖边散步")
    place = plugin.spatial.place("黎明湖公园")
    assert place["source_url"] == "https://example.org/park"
    assert plugin.spatial.route("住处", "黎明湖公园")["minutes"] == 18
    assert "黎明湖公园" in plugin.context_text("a")
    assert place["id"] in {p["id"] for p in plugin.world.planning_places()}
    assert "park" not in {p["id"] for p in plugin.world.planning_places()}
    plugin.config.world.search_enabled = False
    await plugin.world.discover_places(plugin.spatial, plugin.now(), activity="湖边散步")
    assert plugin.ctx.web.search.await_count == 1
    plugin.config.world.search_enabled = True
    plugin.config.world.places_enabled = False
    await plugin.world.discover_places(plugin.spatial, plugin.now(), force=True)
    assert plugin.ctx.web.search.await_count == 1
    assert "park" in {p["id"] for p in plugin.world.planning_places()}
    assert place["id"] not in {p["id"] for p in plugin.world.planning_places()}
    assert plugin.world.place_status()["status"] == "已关闭"
    assert any(p["name"] == "黎明湖公园" for p in (await plugin.ui_editor_records("plan"))["places"])
    saved = plugin.spatial.save_place("黎明湖公园", True, "更新介绍", place["id"])
    assert saved["source_url"] == place["source_url"]
    plugin.config.world.search_enabled = True
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


@pytest.mark.parametrize("activity,kind,category,name", [
    ("找一家安静的咖啡馆读书", "leisure", "咖啡馆", "湖畔咖啡"),
    ("吃一顿东北菜", "meal", "餐厅", "家常菜馆"),
    ("去商场买生活用品", "leisure", "商场", "城市商场"),
])
async def test_autonomous_activity_searches_and_schedules_its_venue(plugin, activity, kind, category, name):
    plugin.config.world.search_enabled = True
    plugin.config.world.places_enabled = True
    plugin.config.life.autonomous = True
    plugin.config.life.schedule_enabled = False
    plugin.ctx.web = SimpleNamespace(search=AsyncMock(return_value={"results": [
        {"title": name, "url": "https://example.org/venue", "snippet": "大庆的" + category}]}))
    plugin.ctx.llm.generate.side_effect = [
        {"success": True, "response": json.dumps({"title": activity, "kind": kind, "minutes": 40,
            "location": "", "motivation": "想换个地方活动", "place_query": activity})},
        {"success": True, "response": json.dumps({"places": [{"name": name, "category": category,
            "outdoor": False, "description": "适合本次活动", "source_index": 0}]})},
    ]
    await plugin.living.choose_activity(plugin.now())
    plan = next(p for p in plugin.store.all("plan") if p["kind"] != "travel")
    assert plan["location"] == name and plan["autonomous"] and plan["kind"] == kind
    trip = plugin.store.get("plan", plan["travel_id"])
    assert trip["route"]["destination"] == name
    assert plugin.engine.time(trip["end"]) == plugin.engine.time(plan["start"])
    assert activity in plugin.ctx.web.search.call_args.kwargs["query"]
    assert plugin.spatial.place(name)["search_request"] == activity


async def test_existing_place_is_reused_by_autonomous_planning(plugin):
    plugin.config.life.autonomous = True
    plugin.config.life.schedule_enabled = False
    plugin.config.world.search_enabled = True
    plugin.config.world.places_enabled = True
    plugin.ctx.web = SimpleNamespace(search=AsyncMock())
    place = plugin.spatial.save_place("常去咖啡馆", False, "已收录咖啡馆")
    plugin.spatial.save_route(place["id"], "street", 10, "步行")
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({
        "title": "喝咖啡", "kind": "leisure", "minutes": 30, "location": "常去咖啡馆", "motivation": "熟悉的环境"})}
    await plugin.living.choose_activity(plugin.now())
    plugin.ctx.web.search.assert_not_awaited()
    assert any(p["title"] == "喝咖啡" and p["location"] == "常去咖啡馆" for p in plugin.store.all("plan"))


async def test_place_cache_survives_restart_and_query_updates(plugin):
    from maiweave.store import Store
    from datetime import timedelta
    plugin.config.world.search_enabled = True
    plugin.config.world.places_enabled = True
    plugin.ctx.web = SimpleNamespace(search=AsyncMock(return_value={"results": [
        {"title": "湖畔咖啡", "url": "https://example.org/cafe"}]}))
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"places": [
        {"name": "湖畔咖啡", "category": "咖啡馆", "description": "湖边喝咖啡", "source_index": 0}]})}
    now = plugin.now()
    first = await plugin.world.discover_places(plugin.spatial, now, activity="咖啡馆")
    await plugin.world.discover_places(plugin.spatial, now, activity="湖边喝咖啡")
    db_path = plugin.store.db.execute("PRAGMA database_list").fetchone()[2]
    reopened = Store(__import__('pathlib').Path(db_path))
    original = plugin.engine.store
    plugin.engine.store = reopened
    try:
        result = await plugin.world.discover_places(plugin.spatial, now, activity=" 咖啡馆！ ")
        assert [p['id'] for p in result] == [p['id'] for p in first]
        assert plugin.world.place_status()['status'] == '已使用缓存'
        assert plugin.ctx.web.search.await_count == 2
        assert plugin.ctx.llm.generate.await_count == 2
    finally:
        plugin.engine.store = original
        reopened.close()
    await plugin.world.discover_places(plugin.spatial, now, force=True, activity="咖啡馆")
    assert plugin.ctx.web.search.await_count == 3
    await plugin.world.discover_places(plugin.spatial, now + timedelta(days=31), activity="咖啡馆")
    assert plugin.ctx.web.search.await_count == 4
    plugin.config.world.city = "哈尔滨"
    await plugin.world.discover_places(plugin.spatial, now, activity="咖啡馆")
    assert plugin.ctx.web.search.await_count == 5


async def test_empty_place_cache_expires_and_errors_can_retry(plugin):
    from datetime import timedelta
    plugin.config.world.search_enabled = True
    plugin.config.world.places_enabled = True
    plugin.ctx.web = SimpleNamespace(search=AsyncMock(return_value={"results": []}))
    plugin.ctx.llm.generate.return_value = {"success": True, "response": '{"places": []}'}
    now = plugin.now()
    assert await plugin.world.discover_places(plugin.spatial, now, activity="展览馆") == []
    assert await plugin.world.discover_places(plugin.spatial, now + timedelta(hours=5), activity="展览馆") == []
    assert plugin.ctx.web.search.await_count == 1
    assert plugin.ctx.llm.generate.await_count == 1
    await plugin.world.discover_places(plugin.spatial, now + timedelta(hours=6), activity="展览馆")
    assert plugin.ctx.web.search.await_count == 2
    plugin.ctx.web.search.side_effect = TimeoutError('搜索超时')
    with pytest.raises(TimeoutError):
        await plugin.world.discover_places(plugin.spatial, now, activity="书店")
    plugin.ctx.web.search.side_effect = None
    await plugin.world.discover_places(plugin.spatial, now, activity="书店")
    assert plugin.ctx.web.search.await_count == 4



async def test_webui_place_search_uses_cache_and_explicit_refresh(plugin):
    import asyncio
    plugin.config.world.search_enabled = True
    plugin.config.world.places_enabled = True
    plugin.world.discover_places = AsyncMock(return_value=[])
    await plugin.ui_discover_places("咖啡馆")
    await asyncio.gather(*plugin.jobs)
    assert plugin.world.discover_places.call_args.kwargs == {"force": False, "activity": "咖啡馆"}
    await plugin.ui_discover_places("咖啡馆", force=True)
    await asyncio.gather(*plugin.jobs)
    assert plugin.world.discover_places.call_args.kwargs == {"force": True, "activity": "咖啡馆"}
