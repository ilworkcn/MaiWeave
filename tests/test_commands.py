"""管理指令隔离与自然语言保留。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from maibot_sdk.capabilities.chat import ChatCapability
from maibot_sdk.capabilities.send import SendCapability

from test_plugin import plugin


def incoming(text, group=False):
    return {
        "processed_plain_text": text, "session_id": "chat-a", "platform": "qq",
        "account_id": "bot-a", "scope": "scope-a",
        "message_info": {"user_info": {"user_id": "user-a"},
                         "group_info": {"group_id": "group-a"} if group else None},
    }


@pytest.mark.parametrize("text", ["/maiweave", "/maiweave 状态", "/织生活", "/织生活 状态"])
@pytest.mark.parametrize("group", [False, True])
async def test_management_stops_receive_and_sends_without_history(plugin, text, group):
    transport = SimpleNamespace(call_capability=AsyncMock(return_value=True))
    plugin.ctx.chat = ChatCapability(transport)
    plugin.ctx.send = SendCapability(transport)
    before = list(plugin.store.db.iterdump())
    message = incoming(text, group)
    original = deepcopy(message)

    result = await plugin.management_message(message)

    assert result == {"action": "abort"}
    assert message == original
    assert list(plugin.store.db.iterdump()) == before
    calls = transport.call_capability.await_args_list
    assert len(calls) == 2
    assert calls[0].args == ("chat.open_session",)
    assert calls[0].kwargs == {
        "platform": "qq", "chat_type": "group" if group else "private",
        "user_id": "user-a", "group_id": "group-a" if group else "",
        "account_id": "bot-a", "scope": "scope-a",
    }
    assert calls[1].args == ("send.text",)
    assert calls[1].kwargs["stream_id"] == "chat-a"
    assert calls[1].kwargs["storage_message"] is False
    assert calls[1].kwargs["sync_to_maisaka_history"] is False
    assert "聊天流：chat-a" in calls[1].kwargs["text"]
    plugin.ctx.llm.generate.assert_not_awaited()


@pytest.mark.parametrize("text", [
    "你现在在干什么？", "明天七点一起看电影", "把电影改到八点", "取消那个安排",
    "指令 /maiweave 是什么意思", "/maiweave 帮助", "/maiweavefoo", "/help", None,
])
async def test_normal_messages_are_untouched(plugin, text):
    message = incoming(text)
    original = deepcopy(message)
    assert await plugin.management_message(message) == {"action": "continue"}
    assert message == original
    plugin.ctx.send.text.assert_not_awaited()


@pytest.mark.parametrize("failure", ["send_false", "send_exception", "open_exception"])
async def test_failed_status_still_consumes_command(plugin, failure, caplog):
    plugin.ctx.chat = SimpleNamespace(open_session=AsyncMock())
    if failure == "send_false":
        plugin.ctx.send.text.return_value = False
    elif failure == "send_exception":
        plugin.ctx.send.text.side_effect = RuntimeError("平台不可用")
    else:
        plugin.ctx.chat.open_session.side_effect = RuntimeError("会话不可用")
    assert await plugin.management_message(incoming("/maiweave")) == {"action": "abort"}
    assert "失败" in caplog.text


async def test_disabled_life_status_remains_available(plugin):
    plugin.ctx.chat = SimpleNamespace(open_session=AsyncMock())
    plugin.config.life.enabled = False
    assert await plugin.management_message(incoming("/maiweave")) == {"action": "abort"}
    assert "启用：False" in plugin.ctx.send.text.await_args.args[0]


async def test_status_registers_as_receive_hook(plugin):
    component = next(c for c in plugin.get_components() if c["name"] == "maiweave_status")
    assert component["type"] == "HOOK_HANDLER"
    assert component["metadata"]["hook"] == "chat.receive.after_process"
