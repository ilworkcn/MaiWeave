"""日常话题评分、轮换与实际发送回执的耦合。"""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from maiweave.core.models import ExperienceInput, PlanInput
from test_plugin import plugin


def event(plugin, now, activity, score):
    item = plugin.engine.record_experience(ExperienceInput(summary=activity + "中的新感受", importance=score,
        score_reason="有具体细节想分享", source="observed", evidence="测试活动", occurred_at=now.isoformat()), "a", now)
    with plugin.store.db:
        plugin.store.put("plan", {"id": item["id"], "book_id": "book" if activity == "阅读" else "",
            "activity_label": activity, "kind": "leisure"})
        item["plan_id"] = item["id"]
        plugin.store.put("experience", item)
    return item


def receipt(plugin, now, activity, session="a", sent=True):
    with plugin.store.db:
        plugin.store.put("share_receipt", {"id": "receipt-" + session, "session": session,
            "created": now.isoformat(), "outcome": "实际已发送" if sent else "Planner 选择沉默",
            "share_activity": activity, **({"sent_at": now.isoformat()} if sent else {})})


@pytest.mark.parametrize("sent,session,hours,expected", [
    (True,"a",2,"听歌"), (False,"a",2,"阅读"), (True,"other",2,"阅读"), (True,"a",25,"阅读")])
def test_daily_choice_uses_recent_actual_sends(plugin, sent, session, hours, expected):
    now = plugin.now()
    books, music = event(plugin,now,"阅读",6), event(plugin,now,"听歌",5)
    receipt(plugin,now-timedelta(hours=hours),"阅读",session,sent)
    selected, reason = plugin.living.select_share([books,music],"a",now)
    assert plugin.living.share_activity(selected) == expected
    assert "近24小时" in reason
    assert plugin.config.proactive.share_threshold == 5


def test_important_events_and_threshold_keep_priority(plugin):
    now = plugin.now()
    important, small = event(plugin,now,"阅读",9), event(plugin,now,"听歌",5)
    receipt(plugin,now-timedelta(hours=2),"阅读")
    assert plugin.living.select_share([important,small],"a",now)[0] == important
    plugin.config.proactive.share_threshold = 6
    small["importance"] = 4
    important["importance"] = 5
    assert plugin.living.select_share([important,small],"a",now)[0] is None


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
async def test_meal_reflection_reaches_share_and_receipt(plugin, mode):
    now = (plugin.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    plugin.now = lambda: now + timedelta(minutes=16)
    cfg = plugin.config.proactive
    cfg.enabled, cfg.session_ids, cfg.mode = True, ["a"], mode
    cfg.start_hour = cfg.end_hour = 0
    plugin.config.life.schedule_enabled = False
    plan = plugin.engine.create_plan(PlanInput(title="炒蛋炒饭", kind="meal", start=now.isoformat(),
        end=(now+timedelta(minutes=15)).isoformat(), location="住处"),"a",now)
    plugin.engine.advance(plugin.now())
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({
        "activity":"做饭", "subject":"蛋炒饭", "summary":"这次炒饭粒粒分明", "details":{"感受":"火候掌握得恰好"},
        "enjoyment":3, "importance":5, "score_reason":"想聊聊终于炒散米饭的小满足"},ensure_ascii=False)}
    await plugin.living.share(plugin.now())
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    await plugin.continuity.reflect(plan["id"],plugin.now())
    plugin.ctx.db.get.return_value = []
    plugin.ctx.maisaka.reply = SimpleNamespace(generate=AsyncMock(return_value={"success":True,"text":"今天炒饭终于粒粒分明了。"}))
    plugin.ctx.send.text.return_value = {"sent":True,"message_id":"daily-meal"}
    await plugin.living.share(plugin.now())
    row, = plugin.store.all("share_receipt")
    assert row["share_activity"] == "做饭"
    assert row["experience_id"] == plugin.store.get("plan",plan["id"])["experience_id"]
    assert "5/10" in row["score"] and "做饭" in row["reason"]
    assert bool(row.get("sent_at")) == (mode == "主动表达")
