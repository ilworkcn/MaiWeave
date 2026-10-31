"""通过宿主公开的 WebUI 接口访问 A_Memorix。"""

import asyncio
import json
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from .config import MemoryConfig


class NativeMemory:
    def __init__(self, config: MemoryConfig):
        self.config = config

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.config.enabled or not self.config.session_cookie:
            raise ValueError("原生记忆连接未配置，请启用并填写 WebUI 的 maibot_session Cookie")
        request = Request(
            self.config.base_url.rstrip("/") + "/api/webui/memory" + path,
            data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", "Cookie": f"maibot_session={self.config.session_cookie}"},
            method=method,
        )
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
        if result.get("success") is False:
            raise RuntimeError(result.get("error") or result.get("message") or "原生记忆操作失败")
        return result

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
