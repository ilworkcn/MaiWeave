"""最小预算下保留睡眠决策和生活操作关联。"""

import json
from copy import deepcopy
from datetime import timedelta

import pytest

from maiweave.core.models import PlanInput
from test_plugin import plugin


@pytest.mark.parametrize("session", ["group", "private"])
@pytest.mark.parametrize("budget", [1200, 2400, 4800])
@pytest.mark.parametrize("action", ["anniversary", "delete_anniversary"])
async def test_linked_activity_and_confirmation_fit_budget(plugin, session, budget, action):
    now = plugin.now()
    with plugin.store.db:
        plugin.store.put("state", {**plugin.engine.state(), "energy": 20})
    plugin.chat_context.kinds[session] = session
    setattr(plugin.config.context, session + "_chars", budget)
    plan = plugin.engine.create_plan(PlanInput(title="很长的阅读活动名称" * 12, kind="leisure",
        location="很长的地点名称" * 20, start=now.isoformat(), end=(now + timedelta(minutes=30)).isoformat()), session, now)
    links = {"book_id": "b" * 32, "goal_step_id": "g" * 32, "continuation_id": "c" * 32}
    with plugin.store.db:
        plugin.store.put("plan", {**plan, **links})
        plugin.store.put("runtime", {"id": "awakening", "until": (now + timedelta(minutes=20)).isoformat()})
        plugin.store.put("review", {"id": "r" * 32, "session_id": session, "status": "待确认",
            "action": action, "conflicts": ["生日日期变更" * 100]})
    before = deepcopy(plugin.store.get("plan", plan["id"]))
    text = plugin.context_text(session, text="确认生日修改，继续这本书")
    snapshot = json.loads(text.split("\n")[-1])
    assert len(text) <= budget
    assert snapshot["current"]["id"] == plan["id"]
    assert all(snapshot["current"][key] == value for key, value in links.items())
    assert "pending_changes" in snapshot, (len(text), snapshot)
    assert snapshot["pending_changes"][0]["id"] == "r" * 32
    assert snapshot["sleep_policy"]["awake_until"]
    assert snapshot["sleep_policy"]["quiet"] is False
    assert plugin.store.get("plan", plan["id"])["title"] == before["title"]
    report = plugin.chat_context.recent[0]
    assert report["chars"] == len(text)
    assert report["base_chars"] + sum(row["chars"] for row in report["sections"]) == len(text)
