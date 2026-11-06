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
