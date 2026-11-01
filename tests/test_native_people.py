"""原生人物关联、编辑和对话资料接续。"""
from unittest.mock import AsyncMock
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from maiweave.memory import NativeMemory
from maiweave.config import MemoryConfig
from test_plugin import plugin
from test_memory import server


async def test_native_person_binding_and_conversation_share_record(plugin):
    plugin.ctx.llm.generate.return_value = {"success": True, "response": '{"conflicts":[],"level":1,"relationship":"朋友","reason":"测试关系"}'}
    plugin.memory.people = AsyncMock(return_value={"items": [{"person_id": "p1", "person_name": "小明", "profile_text": "喜欢读书", "manual_override": {"override_text": "住在上海"}}]})
    plugin.ctx.person = SimpleNamespace(get_value=AsyncMock(return_value="123"))
    plugin.memory.edit_person = AsyncMock(return_value={"success": True})
    person = await plugin.ui_save_profile("", "chat-a", person_id="p1")
    assert person["name"] == "小明" and person["user_id"] == "123"
    assert person["person_id"] == "p1"
    again = await plugin.ui_save_profile("", "chat-a", person_id="p1")
    assert again["id"] == person["id"] and len(plugin.store.all("profile")) == 1
    await plugin.personal("profile", notes="喜欢散步", stream_id="chat-a", user_id="123")
    assert plugin.store.get("profile", person["id"])["person_id"] == "p1"
    birthday = await plugin.personal("save_date", month=8, day=3, stream_id="chat-a", user_id="123")
    assert birthday["content"]["result"]["profile_id"] == person["id"]
    loaded = await plugin.ui_memory_person("p1")
    assert loaded["override_text"] == "住在上海"
    await plugin.ui_edit_memory_person("p1", "喜欢摄影")
    plugin.memory.edit_person.assert_awaited_once_with("p1", "喜欢摄影")


async def test_native_person_http_contract(server):
    url, requests = server
    memory = NativeMemory(MemoryConfig(enabled=True, base_url=url, session_cookie="test-session"))
    await memory.people("小明", "p1")
    request = urlparse(requests[-1][1])
    assert request.path == "/api/webui/memory/profiles/search"
    assert parse_qs(request.query)["person_id"] == ["p1"]
    assert parse_qs(request.query)["person_keyword"] == ["小明"]
    await memory.edit_person("p1", "喜欢摄影")
    assert requests[-1][0:2] == ("POST", "/api/webui/memory/profiles/override")
    assert requests[-1][3]["person_id"] == "p1"
    assert requests[-1][3]["override_text"] == "喜欢摄影"
