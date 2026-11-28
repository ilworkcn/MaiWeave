from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest

from maiweave.core.models import PlanInput
from test_plugin import plugin


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
async def test_scheduled_tool_during_sleep(plugin, mode):
    p = plugin
    now = p.now().replace(hour=23, minute=15, second=0, microsecond=0)
    p.now = lambda: now
    earlier = now - timedelta(minutes=30)
    session = "测试私聊"
    cfg = p.config.proactive
    cfg.session_ids, cfg.enabled, cfg.mode = [session], True, mode
    p.engine.create_plan(PlanInput(title="睡觉", kind="sleep", start=earlier.isoformat(),
        end=(now + timedelta(hours=7)).isoformat()), "", earlier)
    plan = p.spatial.schedule(PlanInput(title="用MCP查健康并催睡", participants=["听众"],
        start=now.isoformat(), end=(now + timedelta(minutes=15)).isoformat()), session, earlier)
    service = p.scheduled_tools
    cfg.daily_limit = 0
    await service.tick(now)
    cfg.daily_limit = 1
    await service.tick(now - timedelta(seconds=1))
    p.ctx.maisaka.proactive.trigger.assert_not_awaited()
    await service.tick(now)
    row, = p.store.all("share_receipt")
    p.living.receipts.turn[session] = row["id"]
    p.living.receipts.observe(session, [{"item_type": "FunctionCallItem",
        "tool_call": {"call_id": "健康查询", "func_name": "read_health_status"}}])
    p.ctx.db.get.return_value = {"tool_name": "read_health_status",
        "tool_data": json.dumps({"provider_type": "mcp", "success": True, "content": "今天步行 3210 步"})}
    p.ctx.maisaka.reply = SimpleNamespace(generate=AsyncMock(return_value={"success": True, "text": "今天走了3210步，去睡吧。"}))
    async def send(text, target, **kwargs):
        assert p.living.quiet_sleep(now, session)
        assert (await p.sleep_before_send({"session_id": target, "processed_plain_text": text}))["action"] == "continue"
        assert (await p.sleep_before_send({"session_id": target, "processed_plain_text": "另一条聊天"}))["action"] == "abort"
        return {"sent": True, "message_id": "测试回执"}
    p.ctx.send.text.side_effect = send
    await service.tick(now)
    await service.tick(now + timedelta(minutes=16))
    assert p.store.get("plan", plan["id"])["status"] == "completed"
    assert p.store.get("share_receipt", row["id"])["tool_success"]
    p.ctx.maisaka.proactive.trigger.assert_awaited_once()
    p.ctx.send.text.assert_awaited_once()


async def test_paused_tool_occurrence_expires_without_confirmation(plugin):
    now = plugin.now()
    plan = plugin.engine.create_plan(PlanInput(title="用 MCP 查健康", participants=["听众"],
        start=now.isoformat(), end=(now + timedelta(minutes=15)).isoformat()), "测试私聊", now)
    plugin.config.proactive.daily_limit = 0
    plugin.engine.advance(now + timedelta(minutes=16))
    row = plugin.store.get("plan", plan["id"])
    assert row["status"] == "blocked"
    assert "未执行" in row["blocking_reason"]
