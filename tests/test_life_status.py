"""检查只读状态接口和首页卡片的数据一致性。"""

from datetime import timedelta
from unittest.mock import Mock

import pytest

from maiweave.core.engine import read_life_status
from test_plugin import plugin


async def test_status_read_only_and_registration(plugin, monkeypatch):
    now = plugin.now()
    monkeypatch.setattr(plugin, "now", lambda: now)
    before = list(plugin.store.db.iterdump())
    monkeypatch.setattr(plugin.engine, "advance", Mock(side_effect=AssertionError("查询不推进日程")))
    plugin.ctx.llm.generate.reset_mock()
    status = await plugin.get_life_status()
    assert status["ready"] and status["version"] == 1
    assert status["energy"] == plugin.engine.state()["energy"]
    assert list(plugin.store.db.iterdump()) == before
    plugin.ctx.llm.generate.assert_not_called()
    api = next(c for c in plugin.get_components() if c["name"] == "get_life_status")
    assert api["metadata"]["public"] is True
    card = next(c for c in plugin.get_components() if c["name"] == "maiweave_home")
    assert any(c.get("entries", {}).get("精力") == f"{status['energy']:.0f} / 100" for c in card["metadata"]["content"])


@pytest.mark.parametrize("enabled,schedule", [(True, True), (True, False), (False, True)])
async def test_status_sleep_wake_and_next(plugin, monkeypatch, enabled, schedule):
    now = plugin.now()
    monkeypatch.setattr(plugin, "now", lambda: now)
    plugin.config.life.enabled = enabled
    plugin.config.life.schedule_enabled = schedule
    with plugin.store.db:
        for p in plugin.store.all("plan"):
            p["status"] = "cancelled"
            plugin.store.put("plan", p)
        for name, kind, start, end in [("sleep-test", "sleep", now-timedelta(minutes=5), now+timedelta(minutes=5)),
                                        ("next-test", "leisure", now+timedelta(minutes=10), now+timedelta(minutes=20))]:
            plugin.store.put("plan", {"id": name, "title": name, "kind": kind, "start": start.isoformat(),
                                      "end": end.isoformat(), "location": "住处", "status": "scheduled"})
        plugin.store.put("runtime", {"id": "awakening", "until": (now+timedelta(minutes=2)).isoformat()})
    before = list(plugin.store.db.iterdump())
    status = await plugin.get_life_status()
    assert status["enabled"] == enabled and status["schedule_enabled"] == schedule
    assert status["sleeping"] is True and status["awake_until"]
    assert status["current"]["id"] == "sleep-test"
    assert status["next_plan"]["id"] == "next-test"
    assert list(plugin.store.db.iterdump()) == before
    monkeypatch.setattr(plugin, "now", lambda: now+timedelta(minutes=6))
    status = await plugin.get_life_status()
    assert status["current"] is None and status["sleeping"] is False
    assert status["awake_until"] is None


def test_status_without_saved_state(tmp_path):
    from maiweave.core.store import Store
    from datetime import datetime, timezone
    store = Store(tmp_path / "empty.sqlite3")
    before = list(store.db.iterdump())
    result = read_life_status(store, datetime.now(timezone.utc), enabled=True, schedule_enabled=True)
    assert result["ready"] is False and result["sleeping"] is None
    assert list(store.db.iterdump()) == before
    store.close()


@pytest.mark.parametrize("kind,hour,nap,schedule,conversation,proactive", [
    ("sleep", 13, True, True, "defer", "defer"),
    ("sleep", 13, False, True, "allow", "defer"),
    ("sleep", 23, False, True, "defer", "defer"),
    ("sleep", 13, True, False, "allow", "defer"),
    ("work", 13, True, True, "allow", "defer"),
    ("study", 13, True, True, "allow", "defer"),
    ("rest", 13, True, True, "allow", "allow"),
    ("leisure", 13, True, True, "allow", "allow"),
])
async def test_behavior_matches_internal_policy(plugin, monkeypatch, kind, hour, nap, schedule, conversation, proactive):
    from maiweave.core.models import PlanInput
    now = plugin.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(plugin, "now", lambda: now)
    plugin.config.life.nap_quiet = nap
    plugin.config.life.schedule_enabled = schedule
    end = (now + timedelta(minutes=30)).isoformat()
    plugin.engine.create_plan(PlanInput(title="状态查询验证", kind=kind, start=now.isoformat(), end=end), "", now)
    before = list(plugin.store.db.iterdump())
    monkeypatch.setattr(plugin.engine, "advance", Mock(side_effect=AssertionError("查询不推进日程")))
    monkeypatch.setattr(plugin.living, "resume_sleep", Mock(side_effect=AssertionError("查询不恢复睡眠")))
    status = await plugin.get_life_status()
    assert status["effective_sleeping"] == plugin.living.sleeping(now)
    assert status["quiet"] == plugin.living.quiet_sleep(now)
    assert status["queried_at"] == now.isoformat()
    assert status["sleep_until"] == (end if kind == "sleep" and schedule else None)
    for behavior, expected in [("conversation", conversation), ("proactive", proactive)]:
        result = await plugin.evaluate_behavior(behavior)
        assert result["decision"] == expected
        assert result["until"] == (end if expected == "defer" else None)
    assert list(plugin.store.db.iterdump()) == before
    plugin.ctx.llm.generate.assert_not_called()


async def test_wake_expiry_before_resume_is_visible(plugin, monkeypatch):
    from maiweave.core.models import PlanInput
    from test_sleep_identity import linked_person
    await linked_person(plugin)
    now = plugin.now().replace(hour=13, minute=0, second=0, microsecond=0)
    plugin.config.life.wake_minutes = 1
    end = now + timedelta(minutes=30)
    plugin.engine.create_plan(PlanInput(title="午睡", kind="sleep", start=now.isoformat(), end=end.isoformat()), "", now)
    assert plugin.living.wake("private", now, "陪伴请求", "u")
    monkeypatch.setattr(plugin, "now", lambda: now)
    awake = await plugin.get_life_status()
    assert not awake["sleeping"] and not awake["effective_sleeping"] and not awake["quiet"]
    assert awake["awake_until"] and awake["sleep_until"] is None
    expired = plugin.engine.time(awake["awake_until"])
    monkeypatch.setattr(plugin, "now", lambda: expired)
    before = list(plugin.store.db.iterdump())
    status = await plugin.get_life_status()
    assert status["current"] is None and not status["sleeping"] and status["awake_until"] is None
    assert status["effective_sleeping"] and status["quiet"]
    assert status["sleep_until"] == end.isoformat()
    for behavior in ["conversation", "proactive"]:
        result = await plugin.evaluate_behavior(behavior)
        assert result["decision"] == "defer" and result["reason"] == "sleeping"
    assert list(plugin.store.db.iterdump()) == before
    plugin.config.life.schedule_enabled = False
    status = await plugin.get_life_status()
    assert not status["effective_sleeping"] and not status["quiet"]
    assert (await plugin.evaluate_behavior("proactive"))["decision"] == "allow"
    plugin.config.life.schedule_enabled = True
    monkeypatch.setattr(plugin, "now", lambda: end)
    assert not (await plugin.get_life_status())["effective_sleeping"]
    assert (await plugin.evaluate_behavior("conversation"))["decision"] == "allow"


async def test_disabled_unknown_invalid_and_registration(plugin):
    plugin.config.life.enabled = False
    for behavior in ["conversation", "proactive"]:
        assert (await plugin.evaluate_behavior(behavior))["decision"] == "abstain"
    plugin.config.life.enabled = True
    with plugin.store.db:
        plugin.store.delete("state", "life")
    status = await plugin.get_life_status()
    assert not status["ready"]
    assert status["effective_sleeping"] is None and status["quiet"] is None and status["sleep_until"] is None
    for behavior in ["conversation", "proactive"]:
        assert (await plugin.evaluate_behavior(behavior))["decision"] == "unknown"
    with pytest.raises(ValueError, match="请选择"):
        await plugin.evaluate_behavior("cognition")
    api = next(c for c in plugin.get_components() if c["name"] == "evaluate_behavior")
    assert api["metadata"]["public"] is True and api["metadata"]["version"] == "1"
