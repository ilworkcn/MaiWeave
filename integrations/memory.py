"""通过 MaiBot 公开的 WebUI 接口访问 A_Memorix。"""

import asyncio
import json
import os
from http.cookies import SimpleCookie
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import Request, urlopen

from ..config import MemoryConfig


class NativeMemory:
    def __init__(self, config: MemoryConfig):
        self.config = config
        self.root = Path.cwd()
        self.runtime = json.loads(os.environ.get("MAIBOT_RUNTIME_CONTEXT", "{}"))
        self.shared_catalog = os.environ.get("MAIBOT_SHARED_MODEL_CATALOG", "")
        self.session = ""
        self.session_lock = Lock()
        self.connection = {"status": "尚未检查", "checked_at": "", "error": ""}

    def credential_path(self):
        paths = [self.root / "host" / "webui.json", self.root / "webui.json", self.root / "data" / "webui.json"]
        if self.shared_catalog:
            paths.insert(0, Path(self.shared_catalog).parent / "webui.json")
        return next((path for path in paths if path.is_file()), None)

    def cookie(self):
        if self.config.session_cookie:
            return self.config.session_cookie
        path = self.credential_path()
        if path is None:
            raise ValueError("请确认 MaiBot 的 host/webui.json 中包含 access_token，或填写网页登录凭据")
        token = json.loads(path.read_text(encoding="utf-8"))["access_token"]
        return token if token.isascii() else quote(token, safe="")

    def uses_host_session(self):
        path = self.credential_path()
        return bool(self.runtime or "/api/host/bots/" in self.config.base_url or (path and path.parent.name == "host") or self.shared_catalog)

    def login(self):
        """用登录密码换取 MaiBot 1.4 的会话 Cookie。"""
        address = urlsplit(self.config.base_url)
        origin = f"{address.scheme}://{address.netloc}"
        path = self.credential_path()
        token = self.config.session_cookie or json.loads(path.read_text(encoding="utf-8"))["access_token"]
        request = Request(origin + "/api/webui/auth/verify",
                          data=json.dumps({"token": token}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
            if not result.get("valid"):
                raise ValueError(result.get("message") or "MaiBot 登录失败")
            cookies = SimpleCookie()
            for value in response.headers.get_all("Set-Cookie", []):
                cookies.load(value)
            self.session = "; ".join(f"{key}={value.value}" for key, value in cookies.items())
            if not self.session:
                raise ValueError("MaiBot 登录响应未返回会话 Cookie")

    def _once(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.config.enabled:
            raise ValueError("请开启原生记忆连接")
        base = self.config.base_url.rstrip("/")
        host_session = self.uses_host_session()
        if host_session:
            with self.session_lock:
                if not self.session:
                    self.login()
            cookie = self.session
            if "/api/host/bots/" not in base:
                bot_id = self.runtime.get("bot_id")
                if not bot_id:
                    raise ValueError("请在 MaiBot 网页地址后填写 /api/host/bots/目标BotID/proxy")
                base += f"/api/host/bots/{quote(bot_id, safe='')}/proxy"
        else:
            cookie = f"maibot_session={self.cookie()}"
        request = Request(base + "/api/webui/memory" + path,
            data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", "Cookie": cookie}, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                result = json.load(response)
        except HTTPError as error:
            if host_session and error.code == 401:
                with self.session_lock:
                    if self.session == cookie:
                        self.login()
                request.add_header("Cookie", self.session)
                with urlopen(request, timeout=20) as response:
                    result = json.load(response)
            else:
                raise
        if result.get("success") is False:
            raise RuntimeError(result.get("error") or result.get("message") or "原生记忆操作失败")
        return result

    def connection_info(self):
        return {**self.connection, "address": self.config.base_url, "status": self.connection["status"] if self.config.enabled else "未启用"}

    async def check_connection(self):
        if self.config.enabled:
            try:
                await self.people()
            except Exception:
                return self.connection_info()
        return self.connection_info()

    def _request(self, method, path, payload=None):
        try:
            result = self._request_retry(method, path, payload)
        except Exception as error:
            self.connection = {"status": "连接失败", "checked_at": datetime.now(timezone.utc).isoformat(), "error": str(error)}
            raise
        self.connection = {"status": "已连接", "checked_at": datetime.now(timezone.utc).isoformat(), "error": ""}
        return result

    def _request_retry(self, method, path, payload=None):
        for attempt in range(3):
            try:
                return self._once(method, path, payload)
            except (URLError, TimeoutError, ConnectionError) as error:
                if method != "GET" or attempt == 2 or (isinstance(error, HTTPError) and error.code not in {408, 429, 500, 502, 503, 504}):
                    raise
                time.sleep(2 ** attempt)

    async def people(self, query: str = "", person_id: str = "") -> dict:
        params = urlencode({"person_keyword": query, "person_id": person_id, "limit": 50})
        return await asyncio.to_thread(self._request, "GET", "/profiles/search?" + params)

    async def edit_person(self, person_id: str, text: str) -> dict:
        return await asyncio.to_thread(self._request, "POST", "/profiles/override", {
            "person_id": person_id, "override_text": text, "updated_by": "ilworkcn.maiweave", "source": "webui"})

    async def fact(self, claim_id: str) -> dict:
        return await asyncio.to_thread(self._request, "GET", "/facts/" + quote(claim_id, safe=""))

    async def query(self, query: str, session: str) -> dict:
        params = urlencode({"query": query, "chat_id": session, "limit": 5})
        return await asyncio.to_thread(self._request, "GET", "/query/aggregate?" + params)

    async def sync(self, experience: dict) -> dict:
        if experience["source"] == "simulated":
            raise ValueError("这条模拟经历已保存在生活档案中；原生记忆同步适用于用户陈述或已确认的交互")
        if not experience["session_id"]:
            raise ValueError("原生记忆写入需要具体聊天流")
        labels = {"reported": "用户陈述", "observed": "交互观测"}
        text = json.dumps({"来源": labels[experience["source"]], "发生时间": experience["occurred_at"],
                           "经历": experience["summary"], "细节": experience["details"],
                           "依据": experience["evidence"]}, ensure_ascii=False)
        fields = {"fact_key": "maiweave.experience." + experience["id"], "value_text": text,
                  "cardinality": "single", "stability": "temporal", "profile_section": "recent_interactions",
                  "authority": "direct_user" if experience["source"] == "reported" else "imported",
                  "reason": "MaiWeave 生活经历同步", "updated_by": "ilworkcn.maiweave"}
        if experience["memory_id"]:
            return await asyncio.to_thread(self._request, "PATCH", "/facts/" + quote(experience["memory_id"], safe=""), fields)
        return await asyncio.to_thread(self._request, "POST", "/facts", {
            "scope_type": "chat", "scope_id": experience["session_id"], **fields})
