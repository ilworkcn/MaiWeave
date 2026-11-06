"""通过 MaiBot 公开的 WebUI 接口访问 A_Memorix。"""

import asyncio
import json
import time
from urllib.error import HTTPError, URLError
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from ..config import MemoryConfig


class NativeMemory:
    def __init__(self, config: MemoryConfig):
        self.config = config
        self.root = Path.cwd()
        self.connection = {"status": "尚未检查", "checked_at": "", "error": ""}

    def cookie(self):
        for path in (self.root / "webui.json", self.root / "data" / "webui.json"):
            if path.is_file():
                token = json.loads(path.read_text(encoding="utf-8"))["access_token"]
                return token if token.isascii() else quote(token, safe="")
        if self.config.session_cookie:
            return self.config.session_cookie
        raise ValueError("请确认 MaiBot 运行目录中的 webui.json 或 data/webui.json 包含 access_token")

    def _once(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.config.enabled:
            raise ValueError("请开启原生记忆连接")
        request = Request(
            self.config.base_url.rstrip("/") + "/api/webui/memory" + path,
            data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", "Cookie": f"maibot_session={self.cookie()}"},
            method=method,
        )
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
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
