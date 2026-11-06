"""主动交流模式的配置、生成、发送与额度结算。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
from pathlib import Path

import pytest
from maibot_sdk.context import PluginContext
from maibot_sdk.capabilities.maisaka import MaisakaReplyCapability
from maibot_sdk.capabilities.send import SendCapability
from maiweave.config import MaiWeaveConfig
from maiweave.core.models import ExperienceInput
from maiweave.plugin import create_plugin
from test_plugin import plugin


def test_mode_config_roundtrip():
    instance = create_plugin()
    instance.set_plugin_config(instance.get_default_config())
    field = instance.get_webui_config_schema()["sections"]["proactive"]["fields"]["mode"]
    assert field["ui_type"] == "select"
    assert field["choices"] == ["自然交流", "主动表达"]
    assert instance.config.proactive.mode == "自然交流"
    data = instance.get_plugin_config_data()
    data["proactive"]["mode"] = "主动表达"
    assert MaiWeaveConfig.model_validate(data).proactive.mode == "主动表达"
    with pytest.raises(ValueError):
        MaiWeaveConfig.model_validate({"proactive": {"mode": "未知模式"}})
    root = Path(__file__).resolve().parents[1]
    assert "maisaka.reply.generate" in json.loads((root / "_manifest.json").read_text(encoding="utf-8"))["capabilities"]
    page = json.loads((root / "webui.json").read_text(encoding="utf-8"))["pages"][0]
    table = next(item for item in page["content"] if item.get("value", {}).get("field") == "share_decisions")
    assert {"field": "mode", "label": "交流模式"} in table["columns"]


def prepare(plugin, monkeypatch):
    now = plugin.now().replace(hour=12, minute=0, second=0, microsecond=0)
    import maiweave.integrations.share_receipts as receipts
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now + timedelta(minutes=2)
    monkeypatch.setattr(receipts, "datetime", Clock)
    cfg = plugin.config.proactive
    cfg.enabled, cfg.session_ids, cfg.mode = True, ["chat-a"], "主动表达"
    plugin.ctx.db.get.return_value = [{"timestamp": now.isoformat(), "user_nickname": "读者", "processed_plain_text": "读到哪里了？"}]
    plugin.ctx.maisaka.reply = SimpleNamespace(generate=AsyncMock(return_value={"success": True, "text": "刚读到一个有意思的地方，想和你聊聊。"}))
    plugin.ctx.send.text.return_value = {"sent": True, "message_id": "sent-1"}
    for score in (6, 7):
        plugin.engine.record_experience(ExperienceInput(summary="阅读的新发现", importance=score,
            occurred_at=now.isoformat(), source="simulated", evidence="角色模拟"), "chat-a", now)
    return now


async def test_expression_sent_once_with_shared_budget(plugin, monkeypatch):
    now = prepare(plugin, monkeypatch)
    await plugin.living.share(now)
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    call = plugin.ctx.maisaka.reply.generate.await_args
    assert "读到哪里了" in call.args[1]
    assert "可以选择沉默" not in call.args[1]
    plugin.ctx.db.get.assert_awaited_with("Messages", filters={"session_id": "chat-a", "is_command": False}, order_by="-timestamp", limit=10)
    plugin.ctx.send.text.assert_awaited_with("刚读到一个有意思的地方，想和你聊聊。", "chat-a", return_details=True,
                                           storage_message=True, sync_to_maisaka_history=True)
    receipt, = plugin.store.all("share_receipt")
    assert receipt["outcome"] == "实际已发送" and receipt["mode"] == "主动表达"
    assert plugin.living.receipts.sent_times("chat-a", now.tzinfo) == [now + timedelta(minutes=2)]
    await plugin.living.receipts.refresh(now)
    await plugin.living.share(now + timedelta(minutes=61))
    assert "上次实际发送" in plugin.living.share_decisions[0]["reason"]
    plugin.config.proactive.mode = "自然交流"
    plugin.config.proactive.daily_limit = 1
    await plugin.living.share(now + timedelta(minutes=62))
    assert "今日实际已发送 1/1" in plugin.living.share_decisions[0]["reason"]
    plugin.config.proactive.daily_limit = 4
    await plugin.living.share(now + timedelta(minutes=62))
    plugin.ctx.maisaka.proactive.trigger.assert_awaited_once()
    assert "可以选择沉默" in plugin.ctx.maisaka.proactive.trigger.await_args.args[1]
    assert {r["mode"] for r in plugin.living.receipts.history([])} == {"自然交流", "主动表达"}
    assert (await plugin.ui_overview())["share_status"] == "已启用 · 自然交流"


@pytest.mark.parametrize("failure", ["生成失败", "空文本", "发送失败", "网络错误"])
async def test_failed_expression_preserves_topic_and_quota(plugin, monkeypatch, failure):
    now = prepare(plugin, monkeypatch)
    if failure == "生成失败":
        plugin.ctx.maisaka.reply.generate.return_value = {"success": False, "error": "模型不可用"}
    elif failure == "空文本":
        plugin.ctx.maisaka.reply.generate.return_value = {"success": True, "text": " "}
    elif failure == "网络错误":
        plugin.ctx.maisaka.reply.generate.side_effect = TimeoutError("生成超时")
    else:
        plugin.ctx.send.text.return_value = {"sent": False, "message_id": None}
    with pytest.raises((RuntimeError, TimeoutError)):
        await plugin.living.share(now)
    assert not plugin.store.all("sharing")
    assert not plugin.living.receipts.sent_times("chat-a", now.tzinfo)
    receipt, = plugin.store.all("share_receipt")
    assert receipt["outcome"] == ("发送失败" if failure == "发送失败" else "生成失败")
    assert receipt["delivery"]
    if failure != "发送失败":
        plugin.ctx.send.text.assert_not_awaited()
    plugin.ctx.maisaka.reply.generate.side_effect = None
    plugin.ctx.maisaka.reply.generate.return_value = {"success": True, "text": "继续分享"}
    plugin.ctx.send.text.return_value = {"sent": True, "message_id": "retry-1"}
    await plugin.living.share(now + timedelta(minutes=10))
    assert len(plugin.living.receipts.sent_times("chat-a", now.tzinfo)) == 1
    assert len(plugin.store.get("sharing", "chat-a")["experience_ids"]) == 1


@pytest.mark.parametrize("gate", ["关闭", "时段", "睡眠", "学习", "额度", "低分"])
async def test_expression_respects_life_and_sharing_settings(plugin, monkeypatch, gate):
    now = prepare(plugin, monkeypatch)
    cfg = plugin.config.proactive
    if gate == "关闭":
        cfg.enabled = False
    elif gate == "时段":
        cfg.start_hour, cfg.end_hour = 14, 22
    elif gate == "睡眠":
        monkeypatch.setattr(plugin.living, "sleeping", lambda at: True)
    elif gate == "学习":
        monkeypatch.setattr(plugin.engine, "current", lambda at: {"kind": "study", "title": "学习"})
    elif gate == "额度":
        cfg.daily_limit = 0
    else:
        cfg.share_threshold = 10
    await plugin.living.share(now)
    plugin.ctx.maisaka.reply.generate.assert_not_awaited()
    plugin.ctx.send.text.assert_not_awaited()


async def test_real_sdk_expression_capabilities(plugin, monkeypatch):
    now = prepare(plugin, monkeypatch)
    calls = []
    async def invoke(capability, **kwargs):
        calls.append((capability, kwargs))
        raw = {"success": True, "text": "原生回复"} if capability == "maisaka.reply.generate" else {
            "success": True, "sent": True, "message_id": "native-1"}
        return PluginContext._normalize_capability_result(capability, raw, return_details=kwargs.get("return_details", False))
    context = SimpleNamespace(call_capability=invoke)
    plugin.ctx.maisaka.reply = MaisakaReplyCapability(context)
    plugin.ctx.send = SendCapability(context)
    await plugin.living.share(now)
    assert [name for name, _ in calls] == ["maisaka.reply.generate", "send.text"]
    assert calls[1][1]["sync_to_maisaka_history"] is True
    assert plugin.store.all("share_receipt")[0]["message_id"] == "native-1"


async def test_expression_sends_reminder_once(plugin, monkeypatch):
    from maiweave.core.models import PlanInput
    now = prepare(plugin, monkeypatch)
    plan = plugin.engine.create_plan(PlanInput(title="一起散步", participants=["读者"],
        start=(now + timedelta(minutes=15)).isoformat(), end=(now + timedelta(minutes=45)).isoformat()), "chat-a", now)
    await plugin.living.share(now)
    assert "共同安排即将开始" in plugin.ctx.maisaka.reply.generate.await_args.args[1]
    sent = plugin.store.get("sharing", "chat-a")
    assert sent["notice_ids"] == [f"reminder:{plan['id']}:{plan['start']}"]
    assert sent["experience_ids"] == []


async def test_mode_switch_waits_for_accepted_natural_request(plugin, monkeypatch):
    now = prepare(plugin, monkeypatch)
    plugin.config.proactive.mode = "自然交流"
    await plugin.living.share(now)
    plugin.config.proactive.mode = "主动表达"
    await plugin.living.share(now + timedelta(minutes=1))
    plugin.ctx.maisaka.reply.generate.assert_not_awaited()
    assert "等待回执" in plugin.living.share_decisions[0]["reason"]


@pytest.mark.parametrize("old_version", ["0.6.0", "0.8.0", "0.8.4"])
def test_mode_config_upgrade_keeps_saved_values(old_version):
    """使用 SDK 升级配置，保留用户设置并补齐交流模式。"""
    from copy import deepcopy
    from maibot_sdk.config import rebuild_plugin_config_data
    instance = create_plugin()
    defaults = instance.get_default_config()
    assert defaults["plugin"]["config_version"] == "0.9.0"
    previous = deepcopy(defaults)
    previous["plugin"]["config_version"] = old_version
    previous["proactive"].pop("mode")
    previous["proactive"].update(enabled=True, daily_limit=7, cooldown_minutes=45, session_ids=["chat-a"], share_threshold=6)
    previous["life"].update(timezone="Asia/Tokyo", interests=["画画"], autonomous=False)
    expected = deepcopy(previous)
    expected["plugin"]["config_version"] = "0.9.0"
    expected["proactive"]["mode"] = "自然交流"
    upgraded = rebuild_plugin_config_data(defaults, previous)
    instance.set_plugin_config(upgraded)
    assert instance.get_plugin_config_data() == expected
    upgraded["proactive"]["mode"] = "主动表达"
    assert rebuild_plugin_config_data(defaults, upgraded)["proactive"]["mode"] == "主动表达"
