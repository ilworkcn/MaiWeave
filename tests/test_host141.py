"""验证 MaiBot 1.4 的登录会话、实例路由与共享联网服务。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from maiweave.config import MemoryConfig
from maiweave.integrations.memory import NativeMemory
from test_plugin import plugin


@pytest.fixture
def host_server():
    calls = []
    state = {"session": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.headers.get('Content-Length') else None
            calls.append((self.command, self.path, self.headers.get('Cookie'), payload))
            login = self.path == '/api/webui/auth/verify'
            if login:
                assert payload == {'token': '测试登录密码'}
                state['session'] += 1
            elif self.headers.get('Cookie') != f"maibot_host_session_test=session-{state['session']}":
                self.send_response(401)
                self.end_headers()
                return
            self.send_response(200)
            if login:
                self.send_header('Set-Cookie', f"maibot_host_session_test=session-{state['session']}; HttpOnly; Path=/; SameSite=Lax")
            self.end_headers()
            self.wfile.write(json.dumps({'valid': True} if login else {'success': True, 'items': [], 'claim': {'claim_id': 'test-claim'}}).encode())

        do_GET = do_POST = do_PATCH = handle_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', calls, state
    server.shutdown()
    server.server_close()
    thread.join()


async def test_host_memory_login_route_reuse_and_expiry(tmp_path, monkeypatch, host_server):
    url, calls, state = host_server
    host = tmp_path / 'host'
    host.mkdir()
    (host / 'webui.json').write_text(json.dumps({'access_token': '测试登录密码'}), encoding='utf-8')
    monkeypatch.setenv('MAIBOT_SHARED_MODEL_CATALOG', str(host / 'model_catalog.toml'))
    monkeypatch.setenv('MAIBOT_RUNTIME_CONTEXT', json.dumps({'bot_id': 'bot-a'}))
    memory = NativeMemory(MemoryConfig(enabled=True, base_url=url))
    await memory.people()
    await memory.query('生日', 'chat-a')
    assert sum(c[1] == '/api/webui/auth/verify' for c in calls) == 1
    assert calls[-1][1].startswith('/api/host/bots/bot-a/proxy/api/webui/memory/query/aggregate?')
    state['session'] = 2
    await memory.edit_person('person-a', '测试备注')
    assert state['session'] == 3
    assert calls[-1][0:3] == ('POST', '/api/host/bots/bot-a/proxy/api/webui/memory/profiles/override', 'maibot_host_session_test=session-3')
    assert memory.connection_info()['status'] == '已连接'


async def test_remote_memory_uses_selected_bot(host_server):
    url, calls, _ = host_server
    memory = NativeMemory(MemoryConfig(enabled=True, base_url=url + '/api/host/bots/remote-bot/proxy', session_cookie='测试登录密码'))
    await memory.people()
    assert calls[-1][1].startswith('/api/host/bots/remote-bot/proxy/api/webui/memory/profiles/search?')


async def test_world_reads_shared_services_and_local_assignment(plugin, tmp_path, monkeypatch):
    plugin.world.host_config_path.write_text('[model_task_config.web_search]\nmodel_list=["shared-search"]\n', encoding='utf-8')
    catalog = tmp_path / 'model_catalog.toml'
    catalog.write_text('[web.services.shared-search]\nadapter="tavily"\napi_key="test"\n', encoding='utf-8')
    monkeypatch.setenv('MAIBOT_SHARED_MODEL_CATALOG', str(catalog))
    result = await plugin.world.search_status()
    assert result['configured'] and result['service'] == 'shared-search'
    catalog.write_text('[web.services.other]\nadapter="tavily"\napi_key="test"\n', encoding='utf-8')
    assert not (await plugin.world.search_status())['configured']


async def test_memory_close_releases_only_its_session(host_server):
    url, calls, state = host_server
    memory = NativeMemory(MemoryConfig(enabled=True, base_url=url + '/api/host/bots/test/proxy', session_cookie='测试登录密码'))
    await memory.people()
    cookie = memory.session
    memory.close()
    assert calls[-1][:3] == ('POST', '/api/webui/auth/logout', cookie)
    assert memory.session == ''
    total = len(calls)
    memory.close()
    assert len(calls) == total
    await memory.people()
    assert state['session'] == 2
    memory.close()


async def test_plugin_unload_releases_memory_session(plugin, monkeypatch):
    from unittest.mock import Mock
    close = Mock()
    monkeypatch.setattr(plugin.memory, 'close', close)
    await plugin.on_unload()
    close.assert_called_once()
    await plugin.on_load()
