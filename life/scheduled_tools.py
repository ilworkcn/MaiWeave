"""在共同日程的开始时刻执行约好的工具，按宿主回执报信。"""

import json
import re
from datetime import timedelta

from ..core.models import ExperienceInput, tool_plan


def search_term(title):
    """从「用 MCP 查健康并催睡」取出工具搜索词「健康」。"""
    subject = re.split(r"(?:mcp|工具)\s*", title, maxsplit=1, flags=re.I)[-1]
    subject = re.split(r"并|然后|，|、", subject, maxsplit=1)[0]
    return re.sub(r"^(?:查询|查看|读取|查一下|查|调用)\s*", "", subject).strip() or title


class ScheduledTools:
    def __init__(self, plugin):
        self.p = plugin
        self.sending = {}
        self.offered = {}

    async def tick(self, now):
        p = self.p
        cfg = p.config.proactive
        if not cfg.enabled or not cfg.daily_limit:
            return
        sessions = set(cfg.session_ids) | {r["session_id"] for r in p.store.all("profile")}
        plans = [r for r in p.store.all("plan") if tool_plan(r) and r["session_id"] in sessions
                 and r["status"] in {"scheduled", "active", "awaiting_confirmation"}
                 and p.engine.time(r["start"]) <= now]
        if plans and not cfg.group_enabled:
            private = await p.living.private_share_sessions()
            plans = [r for r in plans if r["session_id"] in private]
        for plan in plans:
            session = plan["session_id"]
            task = plan.get("tool_task")
            if not task:
                if now >= p.engine.time(plan["end"]):
                    continue
                intent = f"{plan['start']} 这一场日程已经开始：{plan['title']}。现在需要这一次的新结果。直接调用对应工具一次；未列出时用 tool_search 搜工具名或关键词。麦织会把这次结果发给对方，拿到结果后用 stop 结束。"
                result = await p.ctx.maisaka.proactive.trigger(session, intent, reason="MaiWeave 到点办事",
                                                            metadata={"maiweave_plan_id": plan["id"]})
                if not result["success"]:
                    raise RuntimeError(result.get("error") or "宿主未受理定时工具任务")
                task = result["task_id"]
                with p.store.db:
                    p.store.put("plan", {**p.store.get("plan", plan["id"]), "tool_task": task})
                    p.living.receipts.accept(task, {"session": session, "record_id": "execution:" + plan["id"],
                        "execution_plan": plan["id"], "notice": True, "topic": plan["title"],
                        "mode": cfg.mode, "plan_promise": True, "thread_id": "", "score": "到点办事",
                        "reason": "按日程执行工具"}, now)
                continue
            row = p.store.find("share_receipt", task)
            if not row or row["outcome"] != "MaiBot 已受理":
                continue
            detail = ""
            success = False
            for call_id, name in row["calls"].items():
                if name in {"tool_search", "stop", "wait"}:
                    continue
                record = await p.ctx.db.get("ToolRecord", filters={"session_id": session, "tool_id": call_id}, single_result=True)
                if record and record["tool_name"] == name:
                    data = json.loads(record["tool_data"])
                    success = bool(data["success"])
                    if "mcp" in plan["title"].lower() and data.get("provider_type") != "mcp":
                        success = False
                        detail = "本次未取得 MCP 工具的结果"
                    else:
                        detail = str(data.get("content") if success else data.get("error_message") or "工具调用失败")[:1600]
                    break
                break
            if not detail:
                if not ({"stop", "wait"} & set(row["calls"].values())) and now - p.engine.time(row["created"]) < timedelta(minutes=5):
                    continue
                detail = "没有取得对应工具的执行结果"
            text = f"约好的「{plan['title']}」这次没办成：{detail}。"
            if success:
                reply = await p.ctx.maisaka.reply.generate(session,
                    f"约好的事：{plan['title']}。工具实际返回：{detail}。把结果和约好的提醒直接告诉对方，仅使用返回的数据。",
                    format_instruction="一两句聊天原话。", timeout_ms=95000)
                text = reply.get("text") if reply.get("success") else ""
                text = text or detail
            latest = p.store.get("plan", plan["id"])
            if latest["status"] not in {"scheduled", "active", "awaiting_confirmation"} or not cfg.enabled or not cfg.daily_limit:
                continue
            self.sending[session] = text
            try:
                sent = await p.ctx.send.text(text, session, return_details=True, storage_message=True, sync_to_maisaka_history=True)
            finally:
                self.sending.pop(session, None)
            with p.store.db:
                row.update(outcome="实际已发送" if sent["sent"] else "发送失败", delivery="工具执行成功" if success else detail,
                           finished_at=now.isoformat(), tool_success=success)
                if sent["sent"]:
                    row.update(sent_text=text, sent_at=now.isoformat(), message_id=sent.get("message_id"))
                p.store.put("share_receipt", row)
                if success:
                    p.engine.record_experience(ExperienceInput(summary=plan["title"], occurred_at=now.isoformat(),
                        source="observed", evidence="宿主工具回执 " + call_id, plan_id=plan["id"]), session, now)
                else:
                    p.store.put("plan", {**latest, "status": "blocked", "blocking_reason": detail})
