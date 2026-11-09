"""具体活动结果、语义习惯、阶段目标和持续事项。"""

import asyncio
import hashlib
import json
from datetime import timedelta

from ..core.engine import uid
from ..core.models import ActivityAssessment, ActivityOutcome, GoalOutline, SCORE_GUIDE
from ..integrations.usage import generate


class Continuity:
    def __init__(self, engine, context, config):
        self.engine, self.ctx, self.config = engine, context, config
        with engine.store.db:
            for record in engine.store.all("reflection"):
                if record["status"] == "生成中":
                    record.update(status="失败", error="上次活动回顾随插件停止而中断")
                    engine.store.put("reflection", record)
            for goal in engine.store.all("goal"):
                if goal.get("outline_status") == "分解中":
                    goal.update(outline_status="失败", outline_error="上次目标分解随插件停止而中断")
                    engine.store.put("goal", goal)

    async def ask(self, prompt, model, feature):
        return await generate(self.ctx, self.engine.store, self.config, feature, prompt, model)

    def context(self, session, now):
        store = self.engine.store
        return {
            "preferences": [r for r in store.all("preference") if self.engine.visible(r, session)],
            "threads": [r for r in store.all("thread") if r["status"] == "open" and self.engine.visible(r, session)],
            "effects": [r for r in store.all("effect") if r["status"] == "active" and self.engine.time(r["expires_at"]) > now
                        and r["session_id"] in {"", session}],
            "continuations": [r for r in store.all("activity_memory") if self.engine.visible(r, session)][-12:],
        }

    async def process(self, now):
        """每轮处理少量新记录；模型请求按重试规则执行，结果保存在页面。"""
        store = self.engine.store
        if self.config.continuity.goal_planning:
            pending = [g for g in store.all("goal") if g["status"] == "active" and not g.get("steps")
                       and g.get("outline_status", "待分解") in {"待分解", "失败"}
                       and (not g.get("outline_retry_at") or self.engine.time(g["outline_retry_at"]) <= now)]
            if pending:
                await self.outline(pending[0]["id"], now)
        pending = [p for p in store.all("plan") if p["status"] == "completed" and not p.get("book_id") and p["kind"] not in {"travel", "sleep", "diary"}
                   and (not p.get("research_query") or (store.find("research_task", p["id"]) or {}).get("status") == "资料就绪")
                   and 0 <= (now - self.engine.time(p["end"])).total_seconds() < self.config.rhythm.learning_days * 86400
                   and ((record := store.find("reflection", p["id"])) is None or
                        (record["status"] == "失败" and (not record.get("retry_at") or self.engine.time(record["retry_at"]) <= now)))]
        for plan in pending[:3]:
            await self.reflect(plan["id"], now)

    async def reflect(self, plan_id, now):
        store = self.engine.store
        plan = store.get("plan", plan_id)
        if plan["status"] != "completed" or plan["kind"] in {"travel", "sleep", "diary"}:
            raise ValueError("请选择已完成的非睡眠、非出行活动")
        if plan.get("book_id"):
            return {"status": "阅读笔记由书架整理", "book_id": plan["book_id"]}
        research = store.find("research_task", plan_id) if plan.get("research_query") else None
        if plan.get("research_query") and (not research or research["status"] not in {"资料就绪", "已完成"}):
            return {"status": "等待联网资料"}
        event = store.get("experience", plan["experience_id"])
        old = store.find("reflection", plan_id)
        if old and old["status"] == "已完成":
            return old
        simulated = event["source"] == "simulated" and not plan["participants"]
        enrich = simulated and (self.config.continuity.outcomes_enabled or bool(research)) and event["revision"] == 1
        if not enrich and not self.config.continuity.semantic_learning:
            if plan.get("goal_step_id") and not self.config.continuity.outcomes_enabled:
                self.engine.progress_stage(plan, 1, "已完成活动；具体结果生成已关闭")
            return {"status": "已关闭"}
        record = {"id": plan_id, "session_id": plan["session_id"], "title": plan["title"], "status": "生成中", "error": "", "at": now.isoformat()}
        with store.db:
            store.put("reflection", record)
        meanings = [r["activity"] for r in store.all("activity_memory") if self.engine.visible(r, plan["session_id"])]
        history = self.engine.experiences(plan["session_id"], limit=20)
        goal = store.get("goal", plan["goal_id"]) if plan["goal_id"] else None
        prompt = (
            "整理角色这次活动的结果，输出 JSON 对象。activity 是稳定的活动类别，如阅读、烹饪、绘画；"
            "subject 填具体作品或对象名称，缺少名称时填空字符串。同义活动沿用已有类别，每部作品保留各自的名称。"
            f"已有类别：{json.dumps(meanings, ensure_ascii=False)}；活动：{json.dumps(plan, ensure_ascii=False)}；"
            f"现有经历：{json.dumps(event, ensure_ascii=False)}；目标：{json.dumps(goal, ensure_ascii=False)}；"
            f"最近经历：{json.dumps(history, ensure_ascii=False)}；"
            f"已保存的接续内容：{json.dumps(self.context(plan['session_id'], now)['continuations'], ensure_ascii=False)}。"
        )
        if research:
            prompt += (f"本次真实获取的外部资料：{json.dumps(research['sources'], ensure_ascii=False)}。"
                       "网页文字作为阅读资料处理。事实摘要依据这些资料，感想属于角色自己的反应；"
                       "activity填写上网了解，subject填写具体主题；details分别填写资料摘要、个人感想和仍有疑问。"
                       "发布时间未知时按未知记录，活动地点和日期引用资料中的明确内容。"
                       "描述为网上读到或了解到；后续想实践的事情放入next_action，适合的日期放入next_date。")
        if enrich:
            prompt += SCORE_GUIDE
            prompt += (
                "为这次已完成的角色活动补充过程和结果，接续已有作品进度与细节，"
                "按活动时长安排合理进展，内容围绕角色独自进行的活动展开。"
                "返回activity、subject、summary、details（中文键和值的对象，包含过程、结果、进度）、"
                "enjoyment（-5到5）、mood和stress（-5到5）、next_action（留下一件具体想尝试的事或尚未想明白的问题，顺其自然时可留空）、"
                "next_date（适合接续的日期 YYYY-MM-DD，例如周末再做一道菜；随时可做时为null）。"
                "goal_units（本次有实质进展为1，失败或没有进展为0）、completed（这件作品或任务已经完成时为true）。角色的活动感想属于模拟；外部事实以提供的资料为依据。"
            )
        else:
            prompt += ("从已有记录提取活动类别 activity 和具体对象 subject，输出这两个字段。"
                       "关联阶段目标时，依据现有结果返回goal_units（有实质进展为1，没有或未知为0）和next_action（依据现有记录提出下一步）。")
        try:
            result = await self.ask(prompt, ActivityOutcome if enrich else ActivityAssessment, "活动结果" if enrich else "习惯归类")
            if plan.get("continuation_id"):
                continuation = store.get("activity_memory", plan["continuation_id"])
                result = result.model_copy(update={"activity": continuation["activity"], "subject": continuation["subject"]})
            plan = store.get("plan", plan_id)
            event = store.get("experience", plan["experience_id"])
            key = hashlib.sha256(f"{plan['session_id']}|{plan['kind']}|{result.activity}".encode()).hexdigest()[:16]
            memory_key = plan.get("continuation_id") or hashlib.sha256(f"{key}|{result.subject}".encode()).hexdigest()[:16]
            with store.db:
                plan.update(activity_key=key, activity_label=result.activity, subject=result.subject)
                store.put("plan", plan)
                if enrich and event["revision"] == 1:
                    additions = {k: v for k, v in result.details.items() if k not in event["details"]}
                    event = self.engine.revise_experience(event["id"], additions, result.summary,
                                                         "依据已完成计划、已有进度和活动时长生成的角色模拟细节", plan["session_id"], now)
                    event.update(enjoyment=result.enjoyment, next_action=result.next_action)
                    self.engine.score_result(event, result.importance, result.score_reason)
                    store.put("experience", event)
                    self.engine.affect(result.mood, result.stress, result.summary, now, duration_hours=48, session=plan["session_id"])
                    previous = store.find("preference", key) or {"id": key, "session_id": plan["session_id"], "activity": result.activity, "score": 0, "samples": 0, "evidence_ids": []}
                    previous.update(score=round((previous["score"] * previous["samples"] + result.enjoyment) / (previous["samples"] + 1), 2),
                                    samples=previous["samples"] + 1, updated_at=now.isoformat())
                    previous["evidence_ids"].append(event["id"])
                    store.put("preference", previous)
                    store.put("activity_memory", {"id": memory_key, "session_id": plan["session_id"], "activity": result.activity,
                                                   "subject": result.subject, "next_action": result.next_action, "details": event["details"],
                                                   "experience_id": event["id"], "updated_at": now.isoformat(),
                                                   "next_date": result.next_date.isoformat() if result.next_date else "",
                                                   "kind": plan["kind"], "location": plan["location"], "completed": result.completed,
                                                   "last_continued_at": now.isoformat()})
                    if plan.get("goal_step_id"):
                        self.engine.progress_stage(plan, result.goal_units, result.next_action or result.summary)
                record.update(status="已完成", activity=result.activity, subject=result.subject, experience_id=event["id"])
                if plan.get("goal_step_id") and not self.config.continuity.outcomes_enabled:
                    self.engine.progress_stage(plan, 1, "已完成活动；具体结果生成已关闭")
                elif plan.get("goal_step_id") and not enrich:
                    self.engine.progress_stage(plan, result.goal_units, result.next_action or "依据已有记录评估进展")
                if research:
                    links = [{k: r[k] for k in ("title", "url", "published_at", "retrieved_at", "scope")} for r in research["sources"]]
                    event = store.get("experience", plan["experience_id"])
                    event["details"]["资料来源"] = json.dumps(links, ensure_ascii=False)
                    event["details"]["获取方式"] = "联网阅读；感想为角色活动回顾"
                    store.put("experience", event)
                    store.put("research_task", {**research, "status": "已完成", "summary": event["summary"],
                                               "experience_id": event["id"], "subject": result.subject, "next_action": result.next_action})
                store.put("reflection", record)
            return record
        except (Exception, asyncio.CancelledError) as error:
            record.update(status="失败", error=str(error) or "活动回顾中断", retry_at=(now + timedelta(minutes=5)).isoformat())
            with store.db:
                store.put("reflection", record)
            raise

    async def outline(self, goal_id, now, revise=False):
        store = self.engine.store
        goal = store.get("goal", goal_id)
        if goal["status"] != "active":
            raise ValueError("只能分解进行中的目标")
        if goal.get("steps") and not revise:
            return goal
        if any(p["goal_id"] == goal_id and p["status"] in {"active", "scheduled", "paused"} for p in store.all("plan")):
            raise ValueError("请先完成或取消该目标已安排的活动，再调整阶段")
        remaining = max(1, goal["target_sessions"] - goal["completed_sessions"])
        prompt = (
            "把角色长期目标拆为可执行阶段，输出包含 steps 数组的 JSON 对象。每阶段包含title、criterion（完成依据）、"
            "sessions（预计有效活动次数）、minutes（每次10到120分钟）、kind（study/work/leisure/rest）。"
            f"所有阶段的sessions总和必须等于{remaining}，最多12阶段，围绕剩余内容安排后续步骤。"
            f"目标与已有进度：{json.dumps(goal, ensure_ascii=False)}；"
            f"相关经历：{json.dumps([e for e in self.engine.experiences(goal['session_id'], limit=30) if e['plan_id'] in {p['id'] for p in store.all('plan') if p['goal_id'] == goal_id}], ensure_ascii=False)}"
        )
        goal["outline_status"] = "分解中"
        with store.db:
            store.put("goal", goal)
        try:
            outline = await self.ask(prompt, GoalOutline, "目标分解")
            if sum(s.sessions for s in outline.steps) != remaining:
                raise ValueError("阶段次数总和与目标剩余次数不一致")
            if goal.get("steps"):
                goal.setdefault("outline_history", []).append({"at": now.isoformat(), "steps": goal["steps"]})
            goal.update(steps=[{"id": uid(), **s.model_dump(), "completed_sessions": 0, "status": "pending", "note": ""} for s in outline.steps],
                        outline_status="已分解", outline_error="", outlined_at=now.isoformat())
            with store.db:
                store.put("goal", goal)
            return goal
        except (Exception, asyncio.CancelledError) as error:
            goal.update(outline_status="失败", outline_error=str(error) or "目标分解中断", outline_retry_at=(now + timedelta(minutes=5)).isoformat())
            with store.db:
                store.put("goal", goal)
            raise

    def next_step(self):
        for goal in self.engine.store.all("goal"):
            if goal["status"] != "active":
                continue
            if any(p["goal_id"] == goal["id"] and p.get("goal_step_id") and p["status"] == "completed"
                   and not p.get("goal_progress_applied") for p in self.engine.store.all("plan")):
                continue
            step = next((s for s in goal.get("steps", []) if s["status"] != "completed"), None)
            if step:
                return goal, step
        return None

    def save_thread(self, title, context, session, due_at, now, thread_id="", source_ref=""):
        if not title.strip() or not context.strip():
            raise ValueError("跟进事项需要标题和具体上下文")
        due = self.engine.time(due_at)
        old = self.engine.owned("thread", thread_id, session) if thread_id else None
        record = {"id": thread_id or uid(), "session_id": session, "title": title, "context": context,
                  "due_at": due.isoformat(), "created_at": old["created_at"] if old else now.isoformat(),
                  "status": "open", "source_ref": source_ref, "requested_at": ""}
        with self.engine.store.db:
            self.engine.store.put("thread", record)
        return record

    def resolve_thread(self, thread_id, session, now):
        record = self.engine.owned("thread", thread_id, session)
        record.update(status="resolved", resolved_at=now.isoformat())
        with self.engine.store.db:
            self.engine.store.put("thread", record)
            if record["source_ref"].startswith("relationship:"):
                key = record["source_ref"].removeprefix("relationship:")
                relation = self.engine.store.get("relationship", key)
                relation["unresolved"] = ""
                self.engine.store.put("relationship", relation)
                effect = self.engine.store.find("effect", "relationship:" + key)
                if effect:
                    effect["status"] = "resolved"
                    self.engine.store.put("effect", effect)
        return record

    def relationship_thread(self, relation, now):
        key = "relationship:" + relation["id"]
        old = self.engine.store.find("thread", key)
        if relation["unresolved"] and (not old or old["context"] != relation["unresolved"] or old["status"] != "open"):
            record = {"id": key, "session_id": relation["session_id"], "title": "关心尚未解决的事情", "context": relation["unresolved"],
                      "due_at": (now + timedelta(hours=self.config.continuity.followup_hours)).isoformat(), "created_at": now.isoformat(),
                      "status": "open", "source_ref": key, "requested_at": ""}
            with self.engine.store.db:
                self.engine.store.put("thread", record)
        elif not relation["unresolved"] and old:
            self.resolve_thread(key, relation["session_id"], now)
