"""通过本地 HTTP 服务验证记忆接口的请求与响应。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError

import pytest

from maiweave.config import MemoryConfig
from maiweave.integrations.memory import NativeMemory


@pytest.fixture
def server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def handle_request(self):
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size)) if size else None
            requests.append((self.command, self.path, self.headers.get("Cookie"), payload))
            if self.headers.get("Cookie") != "maibot_session=test-session":
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({"success": True, "claim": {"claim_id": "claim-123"}, "items": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = handle_request
        do_PATCH = handle_request
        do_GET = handle_request

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{http.server_port}", requests
    http.shutdown()
    http.server_close()
    thread.join()


def event():
    return {"id": "e-123", "session_id": "chat-a", "source": "reported", "summary": "昨晚吃面", "occurred_at": "2026-10-30T19:00:00+08:00", "details": {"食物": "面"}, "evidence": "用户明确说明", "memory_id": ""}


async def test_create_update_and_query_use_native_contract(server):
    url, requests = server
    memory = NativeMemory(MemoryConfig(enabled=True, base_url=url, session_cookie="test-session"))
    experience = event()
    result = await memory.sync(experience)
    assert result["claim"]["claim_id"] == "claim-123"
    method, path, cookie, payload = requests[-1]
    assert (method, path, cookie) == ("POST", "/api/webui/memory/facts", "maibot_session=test-session")
    assert payload["scope_type"] == "chat" and payload["scope_id"] == "chat-a"
    assert payload["authority"] == "direct_user"
    assert "用户陈述" in payload["value_text"]
    experience["memory_id"] = result["claim"]["claim_id"]
    experience["details"]["同行者"] = "小林"
    await memory.sync(experience)
    assert requests[-1][0:2] == ("PATCH", "/api/webui/memory/facts/claim-123")
    assert "scope_id" not in requests[-1][3]
    await memory.query("昨天", "chat-a")
    assert "chat_id=chat-a" in requests[-2][1]
    assert "scope_id=chat-a" in requests[-1][1]


async def test_simulation_is_not_written_as_native_fact(server):
    url, requests = server
    memory = NativeMemory(MemoryConfig(enabled=True, base_url=url, session_cookie="test-session"))
    experience = event()
    experience["source"] = "simulated"
    with pytest.raises(ValueError, match="模拟经历"):
        await memory.sync(experience)
    assert requests == []


async def test_expired_session_is_reported(server):
    url, _ = server
    memory = NativeMemory(MemoryConfig(enabled=True, base_url=url, session_cookie="expired"))
    with pytest.raises(HTTPError) as error:
        await memory.query("昨天", "chat-a")
    assert error.value.code == 401

async def test_query_includes_matching_current_chat_facts_with_bounded_results(monkeypatch):
    memory = NativeMemory(MemoryConfig(enabled=True))
    calls = []

    def request(method, path, payload=None):
        calls.append(path)
        if '/query/aggregate?' in path:
            return {"success": True, "summary": "旧摘要", "hits": [{"content": "旧摘要", "type": "paragraph"}]}
        assert 'scope_type=chat' in path and 'scope_id=chat-a' in path
        assert 'statuses=active' in path and 'limit=200' in path
        return {"items": [{"claim_id": str(i), "value_text": "Edge 验收 已取消" + "正文" * 1000} for i in range(8)]
                + [{"claim_id": "unrelated", "value_text": "只包含验收"}]}

    monkeypatch.setattr(memory, '_request', request)
    result = await memory.query('edge 已取消', 'chat-a')
    assert len(result['hits']) == 5
    assert all(hit['type'] == 'fact' and len(hit['content']) == 1600 for hit in result['hits'])
    assert all(hit['metadata']['chat_id'] == 'chat-a' for hit in result['hits'])
    assert '已取消' in result['summary']
    assert len(calls) == 2


@pytest.mark.parametrize('query,session', [('关键词', ''), (' ', 'chat-a'), ('没有匹配的关键词', 'chat-a')])
async def test_query_preserves_aggregate_when_no_matching_fact(monkeypatch, query, session):
    memory = NativeMemory(MemoryConfig(enabled=True))
    aggregate = {"success": True, "summary": "原生摘要", "hits": [{"content": "原生摘要"}]}
    calls = []

    def request(method, path, payload=None):
        calls.append(path)
        return aggregate if '/query/aggregate?' in path else {'items': [{'claim_id': 'a', 'value_text': '其他事实'}]}

    monkeypatch.setattr(memory, '_request', request)
    assert await memory.query(query, session) == aggregate
    assert len(calls) == (2 if session and query.strip() else 1)


async def test_person_profile_uses_native_detail_endpoint(server):
    url, requests = server
    memory = NativeMemory(MemoryConfig(enabled=True, base_url=url, session_cookie="test-session"))
    result = await memory.person_profile("qq:alice")
    assert result["success"]
    method, path, cookie, payload = requests[-1]
    assert method == "GET" and path.endswith("/profiles/qq%3Aalice/evidence?limit=1")
    assert cookie == "maibot_session=test-session" and payload is None
