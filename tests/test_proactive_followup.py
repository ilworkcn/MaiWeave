"""关心事项与近况、回执、清理的完整流程。"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_plugin import plugin


def setup(p):
    now = p.now().replace(hour=12, minute=0, second=0, microsecond=0)
    p.config.proactive.enabled = True
    p.config.proactive.session_ids = ["private"]
    p.config.proactive.start_hour = p.config.proactive.end_hour = 0
    p.ctx.maisaka.reply = SimpleNamespace(generate=AsyncMock(return_value={"success": True, "text": "睡醒了吗？"}))
    p.ctx.send.text.return_value = {"sent": True, "message_id": "followup"}
    p.ctx.db.get.return_value = []
    return now


async def test_sleep_until_due_then_send_once_and_clean(plugin):
    p = plugin
    now = setup(p)
    p.config.proactive.mode = "主动表达"
    await p.identify_person("private", "u", "qq")
    with p.store.db:
        p.store.put("profile", {"id": "p", "session_id": "private", "person_id": "qq:u"})
    p.presence.declare("private", "u", "准备睡觉", now, (now + timedelta(hours=1)).isoformat())
    item = p.continuity.save_thread("起床关心", "用户说下午一点起床", "private", (now + timedelta(hours=1)).isoformat(), now)
    await p.living.share(now)
    p.ctx.send.text.assert_not_awaited()
    await p.living.share(now + timedelta(hours=1))
    p.ctx.send.text.assert_awaited_once()
    assert p.store.get("thread", item["id"])["requested_at"]
    await p.living.share(now + timedelta(hours=3))
    p.ctx.send.text.assert_awaited_once()
    p.continuity.expire_threads(now + timedelta(days=2))
    assert p.store.get("thread", item["id"])["status"] == "expired"
    p.retention.clean(now + timedelta(days=20))
    assert not p.store.all("thread")
    assert not p.store.all("person_state")
    assert not p.store.all("sharing")


async def test_silence_retry_and_revision_ignores_old_receipt(plugin):
    p = plugin
    now = setup(p)
    item = p.continuity.save_thread("问候", "起床后聊聊", "private", now.isoformat(), now)
    await p.living.share(now)
    first = p.store.all("share_receipt")[0]
    with p.store.db:
        p.store.put("share_receipt", {**first, "outcome": "Planner 选择沉默"})
    await p.living.share(now + timedelta(minutes=59))
    assert p.ctx.maisaka.proactive.trigger.await_count == 1
    p.ctx.maisaka.proactive.trigger.return_value = {"success": True, "task_id": "second"}
    await p.living.share(now + timedelta(minutes=60))
    assert p.ctx.maisaka.proactive.trigger.await_count == 2
    updated = p.continuity.save_thread("改到明天", "用户改时间", "private", (now + timedelta(days=1)).isoformat(), now + timedelta(hours=2), item["id"])
    with p.store.db:
        row = p.store.get("share_receipt", "second")
        p.store.put("share_receipt", {**row, "sent_at": now.isoformat(), "outcome": "实际已发送"})
    p.living.settle_shares()
    assert not p.store.get("thread", updated["id"])["requested_at"]
    p.continuity.resolve_thread(item["id"], "private", now)
    await p.living.share(now + timedelta(days=1))
    assert p.ctx.maisaka.proactive.trigger.await_count == 2


async def test_failure_waits_and_expiry_stops_retry(plugin, monkeypatch):
    p = plugin
    now = setup(p)

    class TestClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr("maiweave.integrations.share_receipts.datetime", TestClock)
    p.config.proactive.mode = "主动表达"
    item = p.continuity.save_thread("问候", "今天聊聊", "private", now.isoformat(), now, expires_at=(now + timedelta(minutes=45)).isoformat())
    p.ctx.maisaka.reply.generate.return_value = {"success": False, "error": "模型请求失败"}
    import pytest
    with pytest.raises(RuntimeError):
        await p.living.share(now)
    await p.living.share(now + timedelta(minutes=10))
    await p.living.share(now + timedelta(hours=1))
    assert p.ctx.maisaka.reply.generate.await_count == 1
    assert p.store.get("thread", item["id"])["status"] == "expired"
    assert not p.living.receipts.sent_times("private", now.tzinfo)


async def test_expression_greeting_uses_native_reply(plugin):
    from test_greetings import prepare
    now = await prepare(plugin)
    plugin.config.proactive.mode = "主动表达"
    plugin.ctx.maisaka.reply = SimpleNamespace(generate=AsyncMock(return_value={"success": True, "text": "晚安，明天聊。"}))
    plugin.ctx.send.text.return_value = {"sent": True, "message_id": "goodnight"}
    await plugin.living.share(now)
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    plugin.ctx.send.text.assert_awaited_once()
    assert plugin.store.all("share_receipt")[0]["mode"] == "主动表达"


async def test_retry_interval_starts_after_result_and_survives_cleanup(plugin):
    p = plugin
    now = setup(p)
    p.config.proactive.retry_minutes = 60
    with p.store.db:
        p.store.put("share_receipt", {"id": "slow", "session": "private", "record_id": "topic",
            "created": (now - timedelta(minutes=30)).isoformat(), "finished_at": now.isoformat(),
            "outcome": "发送失败", "calls": {}})
    p.config.retention.log_count = 1
    p.retention.clean(now + timedelta(minutes=40))
    assert not p.living.share_ready("topic", "private", now + timedelta(minutes=40))
    assert p.living.share_ready("topic", "private", now + timedelta(minutes=60))


async def test_reload_interrupts_generation_and_recovers_old_running_receipt(plugin):
    import asyncio
    import pytest
    from maiweave.integrations.share_receipts import ShareReceipts
    p = plugin
    now = setup(p)
    started = asyncio.Event()
    async def generating(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    p.ctx.maisaka.reply.generate.side_effect = generating
    task = asyncio.create_task(p.living.receipts.express("private", "问候", {"session": "private", "record_id": "topic"}, now))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = p.store.all("share_receipt")[0]
    assert row["outcome"] == "已中断" and row["finished_at"]
    with p.store.db:
        p.store.put("share_receipt", {**row, "outcome": "生成中"})
    ShareReceipts(p.store, p.ctx)
    assert p.store.get("share_receipt", row["id"])["outcome"] == "已中断"
    p.retention.clean(p.now() + timedelta(days=20))
    assert not p.store.all("share_receipt")
