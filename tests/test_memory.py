"""通过本地 HTTP 服务验证记忆接口的请求与响应。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError

import pytest

from maiweave.config import MemoryConfig
from maiweave.memory import NativeMemory


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
    assert "chat_id=chat-a" in requests[-1][1]


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
