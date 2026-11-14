"""通过 MaiBot 的主动任务和工具回执核对交流结果。"""

import json
import re
import asyncio
from uuid import uuid4
from datetime import datetime, timedelta

from ..core.models import ShareDecision
from .usage import generate

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


def output_summary(items):
    """按原生响应条目统计正文、推理与实际工具调用。"""
    body = sum(len(part.get("text", "")) for item in items
               if item.get("item_type") == "AssistantMessageItem" for part in item.get("parts", []))
    reasoning = sum(len(part) for item in items if item.get("item_type") == "ReasoningItem"
                    for part in (*item.get("text_parts", []), *item.get("summary_parts", [])))
    calls = [item["tool_call"]["func_name"] for item in items if item.get("item_type") == "FunctionCallItem"]
    return f"正文 {body} 字；可见推理 {reasoning} 字；工具调用：{'、'.join(calls) if calls else '无'}"


class ShareReceipts:
    def __init__(self, store, ctx):
        self.store = store
        self.ctx = ctx
        self.turn = {}
        self.incoming = {}
        with store.db:
            for row in store.all("share_receipt"):
                if row["outcome"] == "生成中":
                    row.update(outcome="已中断", delivery="上次生成随插件停止而中断", finished_at=datetime.now().astimezone().isoformat())
                    store.put("share_receipt", row)

    def accept(self, task, row, now):
        with self.store.db:
            self.store.put("share_receipt", {**row, "id": task, "created": now.isoformat(), "calls": {},
                                            "outcome": "MaiBot 已受理", "delivery": "等待 Planner 处理"})

    async def recent_chat(self, session):
        history = await self.ctx.db.get("Messages", filters={"session_id": session, "is_command": False},
                                        order_by="-timestamp", limit=10)
        return [{"时间": item["timestamp"], "人物": item["user_nickname"],
                 "内容": item["processed_plain_text"][:500]} for item in reversed(history or [])]

    async def decide(self, session, intents, now, config, recipient=None):
        recipient = recipient or {"场景": "私聊"}
        guide = (
            "判断这次生活经历是否值得现在主动告诉对方，从候选里选一件或等待，输出JSON：send（布尔值）、candidate（候选序号，从1开始）、reason（中文原因）。"
            "结合人物画像中的明确兴趣、关系和当前聊天判断话题是否适合这个对象。亲近的人可以分享日常小事；"
            "普通、熟悉关系更看重共同兴趣、关心的进展或新发现；亲近、亲密关系也可以因为日常感受、小进展而想联系。兴趣未知时参考当前对话。"
            "明确期待的后续优先于笼统兴趣；偶尔提起的话题按当次交流理解。画像中的近期互动、推测和稳定兴趣分别理解。"
            "群聊按本群话题判断。有效亲密度沿用插件关系计算，0普通、1熟悉、2亲近、3亲密；本聊天互动描述当前聊天的积累。"
            "关系手动设置作为用户偏好，原生画像提供人物资料。"
            "结合实际分享与回应判断新意，相似的煮面、吃饭过程没有新进展时send=false。"
            "对方尚未回应时留出空间；重要度用于候选排序。reason说明与本次对象有关的发送或等待缘由。"
            f"当前时间={now.isoformat()}。"
        )
        budget = config.context.group_chars if recipient["场景"] == "群聊" else config.context.private_chars
        labels = ("\n本次分享：", "\n接收对象：", "\n相关记忆：", "\n最近聊天：")
        available = budget - len(guide) - sum(map(len, labels))
        # 四段资料共用现有场景预算，最近消息从新到旧排列。
        sizes = (available * 2 // 5, available // 4, available * 3 // 20)
        sizes = (*sizes, available - sum(sizes))
        history = list(reversed(await self.recent_chat(session)))
        person = {key: value for key, value in recipient.items() if key != "当前聊天相关记忆"}
        portions = [f"候选{i}：" for i in range(1, len(intents) + 1)]
        per_item = (sizes[0] - sum(map(len, portions)) - len(intents)) // len(intents)
        choices = "\n".join(label + intent[:per_item] for label, intent in zip(portions, intents))
        sent = [{"内容": row["sent_text"] or row["topic"], "回应": row["response"]}
                for row in self.context(session, now)]
        chat_size = (sizes[3] - len("实际分享：；聊天：")) // 2
        recent = ("实际分享：" + json.dumps(sent, ensure_ascii=False)[:chat_size]
                  + "；聊天：" + json.dumps(history, ensure_ascii=False)[:chat_size])
        values = (choices, json.dumps(person, ensure_ascii=False),
                  json.dumps(recipient.get("当前聊天相关记忆", []), ensure_ascii=False), recent)
        prompt = guide + "".join(label + value[:size] for label, value, size in zip(labels, values, sizes))
        def validate(decision):
            if decision.send and decision.candidate > len(intents):
                raise ValueError(f"请选择1至{len(intents)}之间的候选序号")
        return await generate(self.ctx, self.store, config, "分享判断", prompt, ShareDecision, validate=validate, max_chars=budget)

    async def express(self, session, intent, row, now):
        """通过原生 Replyer 生成分享，发送回执用于结算额度。"""
        row = {**row, "id": f"express:{uuid4().hex}", "created": now.isoformat(),
               "calls": {}, "outcome": "生成中", "delivery": "正在组织这次分享"}
        with self.store.db:
            self.store.put("share_receipt", row)
        stage = "生成失败"
        try:
            context = await self.recent_chat(session)
            result = await self.ctx.maisaka.reply.generate(
                session, intent + "\n最近聊天：" + json.dumps(context, ensure_ascii=False),
                format_instruction="以角色自己的口吻，只围绕本次选定的一件事简短表达，通常一小段。最近聊天和其他生活资料用于承接话题，具体表达沿用角色习惯。区分过去经历与此刻活动，直接输出可发送的正文。",
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
                       sent_at=sent_at.isoformat(), message_id=result["message_id"], sent_text=text[:600])
        except asyncio.CancelledError:
            row.update(outcome="已中断", delivery="生成或发送随插件停止而中断")
            raise
        except Exception as error:
            row.update(outcome=stage, delivery=str(error))
            raise
        finally:
            row["finished_at"] = datetime.now(now.tzinfo).isoformat()
            with self.store.db:
                self.store.put("share_receipt", row)

    def observe(self, session, items):
        task = self.turn.pop(session, "")
        row = self.store.find("share_receipt", task) if task else None
        if not row or row["session"] != session or row["outcome"] != "MaiBot 已受理":
            return
        if not any(item.get("item_type") == "FunctionCallItem" for item in items) and not row["calls"]:
            row.update(outcome="Planner 未调用工具", delivery=output_summary(items) + "。本轮尚无发送回执",
                       finished_at=datetime.now().astimezone().isoformat())
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
                    delivered = []
                    for message_id in ids:
                        message = await self.ctx.db.get("Messages", filters={"session_id": row["session"], "message_id": message_id}, single_result=True)
                        if message:
                            delivered.append(message.get("processed_plain_text", ""))
                            if message.get("timestamp"):
                                sent_at = message["timestamp"]
                    if delivered:
                        row["sent_text"] = "\n".join(delivered)[:600]
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
            if row["outcome"] != "MaiBot 已受理":
                row["finished_at"] = now.isoformat()
            latest = self.store.get("share_receipt", row["id"])
            if latest["outcome"] == "MaiBot 已受理" and latest["calls"] == row["calls"] and latest != row:
                with self.store.db:
                    self.store.put("share_receipt", row)

    @staticmethod
    def time(value, zone):
        """MaiBot 的本地回执时间使用角色时区。"""
        at = datetime.fromisoformat(value)
        return at.replace(tzinfo=zone) if at.tzinfo is None else at.astimezone(zone)

    def context(self, session, now):
        rows = sorted((r for r in self.store.all("share_receipt") if r["session"] == session and r.get("sent_at")
                       and timedelta() <= now - self.time(r["sent_at"], now.tzinfo) < timedelta(days=2)),
                      key=lambda r: r["sent_at"], reverse=True)[:3]
        return [{"id": r["id"], "topic": r.get("topic", r.get("share_activity", "生活分享")), "sent_at": r["sent_at"],
                 "sent_text": r.get("sent_text", ""), "status": r.get("followup", "waiting"), "response": r.get("response", ""),
                 "observation": r.get("observation", ""), "experience_id": r.get("experience_id", "")}
                for r in rows]

    def receive(self, session, user, text, now):
        if not text.strip() or text.lstrip().startswith("/"):
            return
        self.incoming = {key: value for key, value in self.incoming.items()
                         if now - datetime.fromisoformat(value["at"]) < timedelta(hours=2)}
        self.incoming[(session, user)] = {"text": text[:600], "at": now.isoformat()}
        rows = self.context(session, now)
        if rows:
            row = self.store.get("share_receipt", rows[0]["id"])
            row.update(observation="分享后收到用户发言", observed_at=now.isoformat())
            with self.store.db:
                self.store.put("share_receipt", row)

    def respond(self, receipt_id, session, user, summary, status, now):
        row = self.store.get("share_receipt", receipt_id)
        if row["session"] != session or not row.get("sent_at"):
            raise ValueError("请选择本聊天流实际发出的分享")
        message = self.incoming.get((session, user))
        if not message or datetime.fromisoformat(message["at"]) < self.time(row["sent_at"], now.tzinfo):
            raise ValueError("当前用户尚未在这次分享后发言")
        if status not in {"answered", "closed"}:
            raise ValueError("请选择已接话或已聊完")
        row.update(followup=status, response=summary[:400], response_user=user,
                   response_text=message["text"], responded_at=message["at"])
        with self.store.db:
            self.store.put("share_receipt", row)
        return {"topic": row.get("topic", "生活分享"), "status": status, "response": row["response"]}

    def history(self, skips):
        rows = [{"mode": "自然交流", **row} for row in [*skips, *self.store.all("share_receipt")]]
        return sorted(rows, key=lambda row: row["created"], reverse=True)[:10]

    def sent_times(self, session, zone):
        times = []
        for row in self.store.all("share_receipt"):
            if row["session"] == session and row.get("sent_at"):
                times.append(self.time(row["sent_at"], zone))
        return times
