"""通过 MaiBot 的主动任务和工具回执核对交流结果。"""

import json
import re
from uuid import uuid4
from datetime import datetime, timedelta

TASK = re.compile(r'^<plugin_proactive_task id="(proactive:ilworkcn\.maiweave:\d+)" plugin_id="ilworkcn\.maiweave">')


def planner_task(items):
    """识别最近触发本轮的任务，普通聊天和其他插件任务各自开始新一轮。"""
    for item in reversed(items):
        if item.get("item_type") == "FunctionCallOutputItem" and item.get("tool_name") == "stop":
            break
        if item.get("item_type") != "UserMessageItem":
            continue
        text = "".join(p.get("text", "") for p in item.get("parts", []) if p.get("type") == "text")
        if text.startswith("<plugin_proactive_task"):
            match = TASK.match(text)
            return match[1] if match else ""
        if text.startswith(("<message ", "<subagent_result", "<internal_")):
            break
    return ""


class ShareReceipts:
    def __init__(self, store, ctx):
        self.store = store
        self.ctx = ctx
        self.turn = {}

    def accept(self, task, row, now):
        with self.store.db:
            self.store.put("share_receipt", {**row, "id": task, "created": now.isoformat(), "calls": {},
                                            "outcome": "MaiBot 已受理", "delivery": "等待 Planner 处理"})

    async def express(self, session, intent, row, now):
        """通过原生 Replyer 生成分享，发送回执用于结算额度。"""
        row = {**row, "id": f"express:{uuid4().hex}", "created": now.isoformat(),
               "calls": {}, "outcome": "生成中", "delivery": "正在组织这次分享"}
        with self.store.db:
            self.store.put("share_receipt", row)
        stage = "生成失败"
        try:
            history = await self.ctx.db.get("Messages", filters={"session_id": session, "is_command": False},
                                            order_by="-timestamp", limit=10)
            context = [{"时间": item["timestamp"], "人物": item["user_nickname"],
                        "内容": item["processed_plain_text"]} for item in reversed(history)]
            result = await self.ctx.maisaka.reply.generate(
                session, intent + "\n最近聊天：" + json.dumps(context, ensure_ascii=False),
                format_instruction="请以角色自己的口吻向当前聊天中的对方主动说几句话。结合最近聊天承接话题，直接输出可发送的聊天正文。",
                timeout_ms=95000)
            if not result["success"]:
                raise RuntimeError(result.get("error") or "回复生成失败")
            text = result["text"].strip()
            if not text:
                raise RuntimeError("回复生成结果为空")
            stage = "发送失败"
            result = await self.ctx.send.text(text, session, return_details=True,
                                              storage_message=True, sync_to_maisaka_history=True)
            if not result["sent"]:
                raise RuntimeError(result.get("error") or "MaiBot 返回发送失败")
            sent_at = datetime.now(now.tzinfo)
            row.update(outcome="实际已发送", delivery=f"{sent_at:%H:%M:%S} 已确认投递",
                       sent_at=sent_at.isoformat(), message_id=result["message_id"])
        except Exception as error:
            row.update(outcome=stage, delivery=str(error))
            raise
        finally:
            with self.store.db:
                self.store.put("share_receipt", row)

    def observe(self, session, items):
        task = self.turn.pop(session, "")
        row = self.store.find("share_receipt", task) if task else None
        if not row or row["session"] != session or row["outcome"] != "MaiBot 已受理":
            return
        if not any(item.get("item_type") == "FunctionCallItem" for item in items) and not row["calls"]:
            row.update(outcome="Planner 未调用工具", delivery="本轮没有工具调用")
        for item in items:
            call = item.get("tool_call", {})
            if item.get("item_type") == "FunctionCallItem" and call.get("func_name") in {"stop", "reply", "send_emoji"}:
                row["calls"][call["call_id"]] = call["func_name"]
        with self.store.db:
            self.store.put("share_receipt", row)

    async def refresh(self, now):
        for row in self.store.all("share_receipt"):
            if row["outcome"] != "MaiBot 已受理":
                continue
            stops = False
            failure = ""
            complete = True
            for call_id, name in row["calls"].items():
                result = await self.ctx.db.get("ToolRecord", filters={"session_id": row["session"], "tool_id": call_id},
                                               single_result=True)
                if not result or result.get("tool_name") != name:
                    complete = False
                    continue
                data = json.loads(result["tool_data"])
                if name == "stop":
                    stops = data["success"]
                    continue
                meta = data.get("metadata") or {}
                segments = meta.get("send_results") or (data.get("structured_content") or {}).get("send_results", [])
                if (any(part.get("success") for part in segments) or meta.get("sent_message_ids")
                        or (name == "send_emoji" and data["success"])):
                    ids = meta.get("sent_message_ids") or [part["message_id"] for part in segments if part.get("success") and part.get("message_id")]
                    sent_at = result["timestamp"]
                    if ids:
                        message = await self.ctx.db.get("Messages", filters={"session_id": row["session"], "message_id": ids[0]}, single_result=True)
                        if message and message.get("timestamp"):
                            sent_at = message["timestamp"]
                    row.update(outcome="实际已发送", delivery=f"{datetime.fromisoformat(sent_at).astimezone(now.tzinfo):%H:%M:%S} 已确认投递", sent_at=sent_at)
                    break
                if not data["success"]:
                    failure = data.get("error_message") or "MaiBot 返回发送失败"
            if row["outcome"] == "MaiBot 已受理":
                if complete and stops:
                    row.update(outcome="Planner 选择沉默", delivery="Planner 调用了 stop")
                elif complete and failure:
                    row.update(outcome="发送失败", delivery=failure)
                elif now - datetime.fromisoformat(row["created"]) > timedelta(hours=1):
                    row.update(outcome="未确认结果", delivery="尚无已发送或 stop 的回执")
            latest = self.store.get("share_receipt", row["id"])
            if latest["outcome"] == "MaiBot 已受理" and latest["calls"] == row["calls"] and latest != row:
                with self.store.db:
                    self.store.put("share_receipt", row)

    def history(self, skips):
        rows = [{"mode": "自然交流", **row} for row in [*skips, *self.store.all("share_receipt")]]
        return sorted(rows, key=lambda row: row["created"], reverse=True)[:10]

    def sent_times(self, session, zone):
        times = []
        for row in self.store.all("share_receipt"):
            if row["session"] == session and row.get("sent_at"):
                at = datetime.fromisoformat(row["sent_at"])
                times.append(at.astimezone(zone))
        return times
