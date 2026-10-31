"""扩展生活能力的业务闭环测试。"""

import json
import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from maiweave.config import MaiWeaveConfig
from maiweave.dreams import Dreams
from maiweave.engine import LifeEngine
from maiweave.models import PlanInput, PlanPatch, RoutineInput
from maiweave.rhythm import Rhythm, matches
from maiweave.spatial import Spatial
from maiweave.store import Store
from maiweave.world import World, parse_feed


@pytest.fixture
def life(tmp_path):
    now = datetime.fromisoformat("2026-10-31T12:00:00+08:00")
    store = Store(tmp_path / "life.sqlite3")
    engine = LifeEngine(store, "Asia/Shanghai", now)
    spatial = Spatial(engine)
    cfg = MaiWeaveConfig()
    ctx = SimpleNamespace(logger=logging.getLogger("test"), llm=SimpleNamespace(generate=AsyncMock()),
                          web=SimpleNamespace(search=AsyncMock(), fetch=AsyncMock()))
    yield SimpleNamespace(engine=engine, store=store, spatial=spatial, cfg=cfg, ctx=ctx, now=now,
                          rhythm=Rhythm(engine, spatial, cfg), world=World(engine, ctx, cfg), dreams=Dreams(engine, ctx, cfg))
    store.close()


def test_route_progress_pause_resume_and_arrival(life):
    x = life
    route = x.spatial.route("住处", "图书馆")
    assert route["minutes"] == 21
    assert len(route["legs"]) == 2
    plan = x.spatial.move("图书馆", "a", x.now)
    x.engine.advance(x.now + timedelta(minutes=9))
    state = x.engine.state()
    assert state["location"] == "楼下街道"
    assert state["journey"]["progress"] == 33
    x.engine.transition(plan["id"], "pause", "a", x.now + timedelta(minutes=9))
    x.engine.advance(x.now + timedelta(minutes=30))
    x.engine.transition(plan["id"], "resume", "a", x.now + timedelta(minutes=30))
    x.engine.advance(x.now + timedelta(minutes=36))
    assert x.engine.state()["journey"]["progress"] == 67
    x.engine.advance(x.now + timedelta(minutes=42))
    assert x.engine.state()["location"] == "图书馆"
    assert x.store.get("plan", plan["id"])["status"] == "completed"


def test_schedule_travel_change_cancel(life):
    x = life
    plan = x.spatial.schedule(PlanInput(title="阅读", start=(x.now + timedelta(hours=1)).isoformat(),
                            end=(x.now + timedelta(hours=2)).isoformat(), location="图书馆"), "a", x.now)
    first_trip = plan["travel_id"]
    assert x.engine.time(x.store.get("plan", first_trip)["start"]) == x.now + timedelta(minutes=39)
    changed = x.spatial.change(plan["id"], PlanPatch(location="公园"), "a", x.now)
    assert x.store.get("plan", first_trip)["status"] == "cancelled"
    assert x.store.get("plan", changed["travel_id"])["route"]["minutes"] == 15
    x.engine.transition(plan["id"], "cancel", "a", x.now)
    assert x.store.get("plan", changed["travel_id"])["status"] == "cancelled"


def test_travel_requires_enough_time_and_connectivity(life):
    x = life
    value = PlanInput(title="阅读", start=x.now.isoformat(), end=(x.now + timedelta(hours=1)).isoformat(), location="图书馆")
    with pytest.raises(ValueError, match="最早"):
        x.spatial.schedule(value, "", x.now)
    assert not x.store.all("plan")
    x.spatial.save_place("海边", True, "虚拟海岸")
    with pytest.raises(ValueError, match="连通"):
        x.spatial.route("住处", "海边")


def test_routine_materialization_skip_revision_and_restart(life, tmp_path):
    x = life
    value = RoutineInput(title="阅读", frequency="daily", anchor_date="2026-10-31", start_time="20:00", duration_minutes=30)
    rule = x.rhythm.save(value, "a", x.now)
    x.rhythm.materialize(x.now)
    assert len(x.store.all("plan")) == 7
    x.rhythm.materialize(x.now)
    assert len(x.store.all("plan")) == 7
    x.rhythm.skip(rule["id"], "2026-11-01", "a", x.now)
    x.rhythm.materialize(x.now)
    assert sum(p["status"] == "cancelled" for p in x.store.all("plan")) == 1
    x.rhythm.save(value.model_copy(update={"start_time": "21:00"}), "a", x.now, rule["id"])
    x.rhythm.materialize(x.now)
    active = [p for p in x.store.all("plan") if p["status"] == "scheduled"]
    assert len(active) == 6
    assert all(x.engine.time(p["start"]).hour == 21 for p in active)
    reopened = Store(tmp_path / "life.sqlite3")
    try:
        engine = LifeEngine(reopened, "Asia/Shanghai", x.now)
        rhythm = Rhythm(engine, Spatial(engine), x.cfg)
        rhythm.materialize(x.now)
        assert len(reopened.all("plan")) == 13
        rhythm.cancel(rule["id"], "a", x.now)
        rhythm.materialize(x.now)
        assert all(p["status"] == "cancelled" for p in reopened.all("plan"))
    finally:
        reopened.close()


def test_recurrence_calendar_rules():
    rule = {**RoutineInput(title="每月", frequency="monthly", anchor_date="2026-01-31", start_time="23:30", duration_minutes=60, month_day=31).model_dump(), "exceptions": []}
    assert matches(rule, date(2026, 3, 31))
    assert not matches(rule, date(2026, 2, 28))
    rule.update(frequency="weekly", anchor_date="2026-10-26", weekdays=[0, 4], interval=2, until="2026-11-10")
    assert matches(rule, date(2026, 10, 30))
    assert not matches(rule, date(2026, 11, 2))
    assert matches(rule, date(2026, 11, 9))
    assert not matches(rule, date(2026, 11, 13))


def test_routine_conflict_is_visible_then_resolved(life):
    x = life
    value = RoutineInput(title="阅读", frequency="daily", anchor_date="2026-10-31", start_time="23:30", duration_minutes=60)
    x.engine.create_plan(PlanInput(title="已有安排", start="2026-10-31T23:00", end="2026-11-01T00:00"), "a", x.now)
    x.rhythm.save(value, "a", x.now)
    x.rhythm.materialize(x.now)
    assert len(x.store.all("routine_issue")) == 1
    x.engine.transition(x.store.all("plan")[0]["id"], "cancel", "a", x.now)
    x.rhythm.materialize(x.now)
    assert x.store.all("routine_issue") == []
    overnight = next(p for p in x.store.all("plan") if p.get("routine_date") == "2026-10-31")
    assert x.engine.time(overnight["end"]).day == 1


def test_habits_require_distinct_days_and_keep_ignore(life):
    x = life
    for index in range(3):
        start = x.now + timedelta(days=index)
        x.engine.advance(start)
        x.engine.create_plan(PlanInput(title="读书", start=start.isoformat(), end=(start + timedelta(minutes=30)).isoformat(), location="住处"), "a", start)
        x.engine.advance(start + timedelta(minutes=30))
        x.rhythm.learn(start + timedelta(minutes=30))
        assert len(x.store.all("habit")) == (1 if index == 2 else 0)
    habit = x.store.all("habit")[0]
    assert habit["time"] == "12:00" and habit["observed_days"] == 3
    assert x.rhythm.candidate(x.now + timedelta(days=7))["id"] == habit["id"]
    x.rhythm.toggle(habit["id"], False, "a")
    x.rhythm.learn(x.now + timedelta(days=3))
    assert x.rhythm.candidate(x.now + timedelta(days=7)) is None
    x.rhythm.learn(x.now + timedelta(days=30))
    assert not x.store.get("habit", habit["id"])["active"]


async def test_dream_once_separate_from_facts(life):
    x = life
    plan = x.engine.create_plan(PlanInput(title="睡觉", kind="sleep", start=x.now.isoformat(), end=(x.now + timedelta(hours=4)).isoformat()), "a", x.now)
    end = x.now + timedelta(hours=4)
    x.engine.advance(end)
    count = len(x.store.all("experience"))
    x.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"title": "海上的书店", "story": "书页变成了小船。", "feeling": "放松", "mood_delta": 2})}
    await x.dreams.process(end)
    await x.dreams.process(end)
    await x.dreams.generate(plan["id"], end)
    assert x.ctx.llm.generate.await_count == 1
    assert x.store.get("dream", plan["id"])["source"] == "dream"
    assert len(x.store.all("experience")) == count
    assert len(x.store.all("emotion")) == 1


async def test_dream_failure_can_be_explicitly_retried(life):
    x = life
    plan = x.engine.create_plan(PlanInput(title="睡觉", kind="sleep", start=x.now.isoformat(), end=(x.now + timedelta(hours=4)).isoformat()), "a", x.now)
    end = x.now + timedelta(hours=4)
    x.engine.advance(end)
    x.ctx.llm.generate.return_value = {"success": False, "error": "模型不可用"}
    with pytest.raises(RuntimeError, match="模型不可用"):
        await x.dreams.process(end)
    await x.dreams.process(end)
    assert x.ctx.llm.generate.await_count == 1
    assert x.store.get("dream", plan["id"])["status"] == "生成失败"


def test_rss_and_atom_preserve_sources():
    rss = '<rss><channel><item><title>消息</title><link>https://example.com/a</link><description>&lt;b&gt;内容&lt;/b&gt;</description><pubDate>今天</pubDate></item></channel></rss>'
    atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>消息二</title><link href="https://example.com/b"/><summary>摘要</summary></entry></feed>'
    a, b = [parse_feed(xml.encode(), "https://example.com/feed", "2026-10-31")[0] for xml in (rss, atom)]
    assert a["summary"] == "内容" and a["published_at"] == "今天"
    assert b["url"] == "https://example.com/b" and b["source"] == "https://example.com/feed"


async def test_weather_real_response_shape_and_failure_visible(life, monkeypatch):
    x = life
    x.cfg.world.city = "上海"
    geo = {"results": [{"name": "上海", "latitude": 31.2, "longitude": 121.4}]}
    data = {"current": {"time": "2026-10-31T12:00", "weather_code": 61, "precipitation": 1, "wind_speed_10m": 10, "apparent_temperature": 18, "temperature_2m": 19},
            "daily": {"time": ["2026-10-31"], "weather_code": [61], "temperature_2m_max": [20], "temperature_2m_min": [15], "precipitation_probability_max": [80], "sunrise": ["06:00"], "sunset": ["18:00"]}}
    monkeypatch.setattr("maiweave.world.download", lambda url: json.dumps(geo if "geocoding" in url else data).encode())
    await x.world.refresh(x.now)
    weather = x.world.context(x.now)["weather"]
    assert weather["label"] == "小雨" and not weather["outdoor_suitable"]
    assert weather["source"] == "Open-Meteo"
    assert x.world.context(x.now + timedelta(hours=3))["weather"]["stale"]
    def failed(url):
        raise OSError("服务不可用")
    monkeypatch.setattr("maiweave.world.download", failed)
    with pytest.raises(RuntimeError, match="服务不可用"):
        await x.world.refresh(x.now + timedelta(hours=3))
    assert x.store.get("world_status", "weather")["status"] == "更新失败"
    assert x.world.context(x.now + timedelta(hours=3))["weather"]["stale"]


async def test_native_web_results_are_persisted_with_sources(life):
    x = life
    x.cfg.world.search_enabled = True
    x.ctx.web.search.return_value = {"results": [{"title": "资料", "url": "https://example.com/a", "snippet": "正文摘要"}]}
    x.ctx.web.fetch.return_value = {"title": "资料", "content": "正文", "url": "https://example.com/a"}
    result = await x.world.search("资料", x.now)
    await x.world.read(result["results"][0]["url"], x.now)
    assert x.store.all("reading")[0]["content"] == "正文"
    assert x.store.all("experience") == []
