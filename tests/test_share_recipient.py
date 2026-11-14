"""原生人物资料、聊天范围与主动分享预算的联动。"""
import json
from unittest.mock import AsyncMock
from types import SimpleNamespace
import pytest
from test_plugin import plugin
from test_proactive_modes import prepare


async def recipient_setup(p, group=False):
    p.config.memory.enabled = True
    p.ctx.chat.get_all_streams.return_value = [{"stream_id": "chat-a", "platform": "qq", "user_id": "alice", "user_nickname": "小甲", "group_id": "g" if group else ""}]
    p.memory.person_profile = AsyncMock(return_value={"person_id": "qq:alice", "profile_text": "小甲喜欢科幻小说。"})
    p.memory.query = AsyncMock(return_value={"success": True, "hits": [{"content": "小甲问过这本书的结局。"}]})


async def test_native_profile_refresh_and_chat_scope(plugin):
    p = plugin
    await recipient_setup(p)
    with p.store.db:
        p.store.put("profile", {"id": "old", "session_id": "old-chat", "user_id": "alice", "person_id": "qq:alice", "intimacy_mode": "manual", "intimacy": 2, "relationship": "朋友"})
        p.store.put("profile", {"id": "other", "session_id": "chat-b", "user_id": "bob", "person_id": "qq:bob", "relationship": "其他人关系"})
    before = list(p.store.db.iterdump())
    first = await p.living.share_recipient("chat-a", "读完科幻小说")
    assert first["关系设置"]["intimacy"] == 2
    assert first["有效亲密度"] == 2
    assert "其他人" not in str(first)
    p.memory.person_profile.assert_awaited_once_with("qq:alice")
    p.memory.query.assert_awaited_once_with("读完科幻小说", "chat-a")
    p.memory.person_profile.return_value = {"person_id": "qq:alice", "profile_text": "小甲现在更喜欢历史小说。"}
    assert "历史" in (await p.living.share_recipient("chat-a", "读书"))["原生人物画像"]
    assert list(p.store.db.iterdump()) == before


async def test_group_uses_own_chat_memory(plugin):
    await recipient_setup(plugin, group=True)
    data = await plugin.living.share_recipient("chat-a", "读书")
    assert data["场景"] == "群聊"
    assert "原生人物画像" not in data and "关系设置" not in data
    plugin.memory.person_profile.assert_not_awaited()
    plugin.memory.query.assert_awaited_once_with("读书", "chat-a")


async def test_unknown_and_disabled_memory(plugin):
    await recipient_setup(plugin)
    plugin.memory.person_profile.return_value = {"profile_text": ""}
    assert "尚未记录" in (await plugin.living.share_recipient("chat-a", "读书"))["原生人物画像"]
    plugin.config.memory.enabled = False
    plugin.memory.query.reset_mock()
    plugin.memory.person_profile.reset_mock()
    await plugin.living.share_recipient("chat-a", "读书")
    plugin.memory.person_profile.assert_not_awaited()
    plugin.memory.query.assert_not_awaited()


async def test_manual_native_profile_and_lookup_error(plugin):
    await recipient_setup(plugin)
    plugin.memory.person_profile.return_value = {"profile_text": "喜欢看鸟", "has_manual_override": True}
    assert (await plugin.living.share_recipient("chat-a", "散步"))["原生人物画像"] == "喜欢看鸟"
    plugin.memory.query.return_value = {"success": False, "error": "检索异常"}
    with pytest.raises(RuntimeError, match="检索异常"):
        await plugin.living.share_recipient("chat-a", "读书")


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
async def test_both_modes_use_recipient(plugin, monkeypatch, mode):
    now = prepare(plugin, monkeypatch)
    await recipient_setup(plugin)
    plugin.config.proactive.mode = mode
    await plugin.living.share(now)
    prompt = plugin.ctx.llm.generate.await_args.args[0]
    assert "小甲喜欢科幻小说" in prompt and "小甲问过这本书" in prompt
    assert len(prompt) <= plugin.config.context.private_chars
    if mode == "自然交流":
        plugin.ctx.maisaka.proactive.trigger.assert_awaited_once()
    else:
        plugin.ctx.send.text.assert_awaited_once()


@pytest.mark.parametrize("budget", [1200, 2400, 4800])
@pytest.mark.parametrize("kind", ["私聊", "群聊"])
async def test_large_recipient_respects_budget(plugin, budget, kind):
    setattr(plugin.config.context, "group_chars" if kind == "群聊" else "private_chars", budget)
    plugin.ctx.db.get.return_value = [{"timestamp": plugin.now().isoformat(), "user_nickname": "甲", "processed_plain_text": "近期聊天" * 200} for _ in range(10)]
    await plugin.living.receipts.decide("chat-a", "生活经历" * 2000, plugin.now(), plugin.config, {"场景": kind, "原生人物画像": "人物兴趣" * 3000, "当前聊天相关记忆": ["共同话题" * 1000]})
    prompt = plugin.ctx.llm.generate.await_args.args[0]
    assert len(prompt) <= budget
    assert "生活经历" in prompt and "人物兴趣" in prompt and "近期聊天" in prompt and "共同话题" in prompt


async def test_share_uses_effective_cross_chat_intimacy(plugin):
    await recipient_setup(plugin)
    with plugin.store.db:
        plugin.store.put("identity", {"id": "old:alice", "session_id": "old", "user_id": "alice", "person_id": "qq:alice"})
        plugin.store.put("relationship", {"id": "old:alice", "session_id": "old", "user_id": "alice", "familiarity": 90, "trust": 90, "closeness": 90})
    data = await plugin.living.share_recipient("chat-a", "读书")
    assert data["有效亲密度"] == 3


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
@pytest.mark.parametrize("failure", ["画像", "检索"])
async def test_memory_failure_preserves_other_chat_reminder(plugin, monkeypatch, mode, failure):
    from datetime import timedelta
    from maiweave.core.models import PlanInput
    now = prepare(plugin, monkeypatch)
    await recipient_setup(plugin)
    plugin.config.proactive.mode = mode
    plugin.config.proactive.session_ids = ["chat-a", "chat-b"]
    target = plugin.memory.person_profile if failure == "画像" else plugin.memory.query
    target.side_effect = RuntimeError("原生记忆读取失败")
    plugin.engine.create_plan(PlanInput(title="一起散步", participants=["乙"], start=(now+timedelta(minutes=10)).isoformat(), end=(now+timedelta(minutes=40)).isoformat()), "chat-b", now)
    await plugin.living.share(now)
    assert not plugin.living.receipts.sent_times("chat-a", now.tzinfo)
    assert not plugin.store.find("sharing", "chat-a")
    assert any(r["session"] == "chat-a" and "读取失败" in r["reason"] for r in plugin.living.share_decisions)
    receipt, = plugin.store.all("share_receipt")
    assert receipt["session"] == "chat-b" and receipt["notice"]


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
@pytest.mark.parametrize("kind", ["早安", "晚安"])
async def test_greeting_keeps_own_relationship_gate_without_memory(plugin, mode, kind):
    from test_greetings import prepare as greeting_prepare
    now = await greeting_prepare(plugin, kind)
    plugin.config.proactive.mode = mode
    plugin.config.memory.enabled = True
    plugin.living.share_recipient = AsyncMock(side_effect=RuntimeError("原生记忆读取失败"))
    plugin.ctx.maisaka.reply = SimpleNamespace(generate=AsyncMock(return_value={"success": True, "text": kind}))
    plugin.ctx.send.text.return_value = {"sent": True, "message_id": "greeting"}
    await plugin.living.share(now)
    plugin.living.share_recipient.assert_not_awaited()
    receipt, = plugin.store.all("share_receipt")
    assert receipt["greeting_kind"] == kind and receipt["notice"]


async def test_profile_with_different_native_person_is_not_reused(plugin):
    await recipient_setup(plugin)
    with plugin.store.db:
        plugin.store.put("profile", {"id": "stale", "session_id": "chat-a", "user_id": "alice", "person_id": "qq:bob", "intimacy_mode": "manual", "intimacy": 3})
    data = await plugin.living.share_recipient("chat-a", "读书")
    assert data["关系设置"] == {} and data["有效亲密度"] == 0


@pytest.mark.parametrize("error", [TimeoutError("请求超时"), OSError("连接失败")])
async def test_memory_recovery_keeps_unsent_experience(plugin, monkeypatch, error):
    now = prepare(plugin, monkeypatch)
    await recipient_setup(plugin)
    plugin.memory.person_profile.side_effect = error
    await plugin.living.share(now)
    assert not plugin.store.all("share_receipt") and not plugin.store.all("sharing")
    plugin.memory.person_profile.side_effect = None
    await plugin.living.share(now)
    assert plugin.store.all("share_receipt")[0]["outcome"] == "实际已发送"
    assert len(plugin.store.get("sharing", "chat-a")["experience_ids"]) == 1
