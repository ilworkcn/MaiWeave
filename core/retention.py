"""按用途清理旧记录，延续当前生活所需的关联。"""

from datetime import timedelta

from .models import local_time


class Retention:
    def __init__(self, store, config):
        self.store, self.config = store, config

    def time(self, value):
        return local_time(value, self.config.life.timezone)

    def collect(self, now):
        store, cfg = self.store, self.config.retention
        plans = store.all("plan")
        events = store.all("experience")
        protected_plans, protected_events = set(), set()
        goals = {g["id"] for g in store.all("goal") if g["status"] == "active"}
        recent = now - timedelta(days=max(1, self.config.rhythm.learning_days))
        for plan in plans:
            if plan["status"] not in {"completed", "cancelled"} or self.time(plan["end"]) >= recent or plan.get("goal_id") in goals:
                protected_plans.add(plan["id"])
            if plan.get("book_id") and plan["status"] == "completed":
                note = store.find("book_note", plan["id"])
                if not note or note["status"] not in {"已完成", "本书已读完"}:
                    protected_plans.add(plan["id"])
        for category in ("reflection", "dream"):
            for row in store.all(category):
                if row["status"] in {"生成中", "阅读中"}:
                    protected_plans.add(row["id"])
        for row in store.all("diary"):
            if row["status"] != "已生成":
                protected_plans.add(row.get("plan_id", ""))
        for row in store.all("activity_memory"):
            protected_events.add(row.get("experience_id", ""))
        for row in store.all("review"):
            if row["status"] not in {"已保存", "已取消", "失败"}:
                protected_events.add(row.get("payload", {}).get("experience_id", ""))
        for row in store.all("share_receipt"):
            if row["outcome"] in {"MaiBot 已受理", "生成中"}:
                protected_events.add(row.get("experience_id", ""))
        # 正在使用的出行链保留整个关联链。
        while True:
            linked = {p.get(key, "") for p in plans if p["id"] in protected_plans for key in ("travel_id", "activity_id")}
            if linked <= protected_plans:
                break
            protected_plans.update(linked)

        # 数量上限统计本类记录；被生活关联占用的条目在页面单列。
        def select(rows, days, count, field, protected):
            ordered = sorted(rows, key=lambda r: self.time(r[field]))
            deleted = {r["id"] for r in ordered if days and self.time(r[field]) < now - timedelta(days=days) and r["id"] not in protected}
            excess = max(0, len(rows) - len(deleted) - count) if count else 0
            for row in ordered:
                if not excess:
                    break
                if row["id"] not in protected and row["id"] not in deleted:
                    deleted.add(row["id"])
                    excess -= 1
            return deleted

        terminal = [p for p in plans if p["status"] in {"completed", "cancelled"}]
        delete = {"plan": select(terminal, cfg.plan_days, cfg.plan_count, "end", protected_plans)}
        for plan in plans:
            if plan["id"] not in delete["plan"]:
                protected_events.add(plan.get("experience_id", ""))
        protected_events.update(e["id"] for e in events if e.get("keep") or e["importance"] >= 7 or self.time(e["occurred_at"]) >= now - timedelta(days=1))
        ordinary = [e for e in events if not e.get("keep") and e["importance"] < 7]
        delete["experience"] = select(ordinary, cfg.experience_days, cfg.experience_count, "occurred_at", protected_events)
        summary = []
        for category, label, rows, days, count, protected in (
            ("plan", "已结束日程", terminal, cfg.plan_days, cfg.plan_count, protected_plans),
            ("experience", "普通生活经历", ordinary, cfg.experience_days, cfg.experience_count, protected_events),
        ):
            summary.append(dict(category=category, label=label, total=len(rows), days=days, limit=count, protected=sum(r["id"] in protected for r in rows), remove=len(delete[category])))
        for category, label, field in (("job", "后台任务", "created_at"), ("review", "资料核对", "created_at"), ("share_receipt", "主动交流回执", "created")):
            rows = store.all(category)
            protected = set()
            for row in rows:
                if category == "job" and row["status"] == "进行中" or category == "review" and row["status"] not in {"已保存", "已取消", "失败"} or category == "share_receipt" and (row["outcome"] in {"MaiBot 已受理", "生成中"} or self.time(row.get("sent_at") or row[field]) >= now - timedelta(minutes=max(1440, self.config.proactive.cooldown_minutes, self.config.proactive.retry_minutes))):
                    protected.add(row["id"])
            delete[category] = select(rows, cfg.log_days, cfg.log_count, field, protected)
            summary.append(dict(category=category, label=label, total=len(rows), days=cfg.log_days, limit=cfg.log_count, protected=len(protected), remove=len(delete[category])))
        for category, label, field, days, count in (
            ("model_usage", "模型调用明细", "at", cfg.usage_days, cfg.usage_count),
            ("emotion", "情绪变化", "at", cfg.log_days, cfg.log_count),
            ("effect", "情绪影响", "expires_at", cfg.log_days, cfg.log_count),
            ("thread", "已结束跟进", "resolved_at", cfg.log_days, cfg.log_count),
            ("place_search", "地点搜索缓存", "searched_at", cfg.world_days, cfg.world_count),
        ):
            rows = store.all(category)
            if category == "thread":
                rows = [r for r in rows if r["status"] in {"resolved", "expired"}]
            protected = {r["id"] for r in rows if category == "effect" and r["status"] == "active"}
            delete[category] = select(rows, days, count, field, protected)
            summary.append(dict(category=category, label=label, total=len(rows), days=days,
                                limit=count, protected=len(protected), remove=len(delete[category])))
        states = store.all("person_state")
        delete["person_state"] = {r["id"] for r in states if self.time(r["expires_at"]) <= now}
        summary.append(dict(category="person_state", label="人物近况", total=len(states), days=0,
                            limit=0, protected=len(states)-len(delete["person_state"]),
                            remove=len(delete["person_state"]), policy="到期清理"))
        # 分享索引只保存仍可能成为候选的内容。
        valid_notices = set()
        for row in store.all("thread"):
            if row["status"] == "open":
                valid_notices.update(("thread:" + row["id"], f"thread:{row['id']}:{row.get('updated_at', row['due_at'])}"))
        for row in plans:
            if row["status"] in {"scheduled", "active"}:
                valid_notices.add(f"reminder:{row['id']}:{row['start']}")
            if row["status"] == "awaiting_confirmation":
                valid_notices.add(f"confirmation:{row['id']}")
        valid_notices.update(f"issue:{r['id']}:{r['reason']}" for r in store.all("schedule_issue") if r["id"] not in delete["plan"])
        sharing_updates = {}
        for row in store.all("sharing"):
            notices = [key for key in row.get("notice_ids", []) if key in valid_notices or
                       key.startswith("anniversary:") and key.rsplit(":", 1)[-1] >= now.date().isoformat() or
                       key.startswith("greeting:") and key.rsplit(":", 2)[-2] >= now.date().isoformat()]
            events_in_window = {e["id"] for e in events if self.time(e["occurred_at"]) >= now - timedelta(hours=6)}
            experiences = [key for key in row.get("experience_ids", []) if key in events_in_window]
            if notices != row.get("notice_ids", []) or experiences != row.get("experience_ids", []):
                sharing_updates[row["id"]] = {**row, "notice_ids": notices, "experience_ids": experiences}
        self.sharing_updates = sharing_updates
        summary.append(dict(category="sharing_index", label="旧分享索引", total=sum(len(r.get("notice_ids", [])) + len(r.get("experience_ids", [])) for r in store.all("sharing")), days=0, limit=0, protected=0,
                            remove=sum(len(store.get("sharing", key).get("notice_ids", [])) + len(store.get("sharing", key).get("experience_ids", [])) - len(row["notice_ids"]) - len(row["experience_ids"]) for key, row in sharing_updates.items()), policy="话题到期清理"))
        for category in ("news", "research", "reading"):
            rows = store.all(category, omit=("content", "results"))
            delete[category] = select(rows, cfg.world_days, cfg.world_count, "retrieved_at", set())
            label = {"news": "订阅资讯", "research": "检索缓存", "reading": "网页缓存"}[category]
            summary.append(dict(category=category, label=label, total=len(rows), days=cfg.world_days,
                                limit=cfg.world_count, protected=0, remove=len(delete[category])))
        for category in ("research_task", "reflection", "schedule_issue", "schedule_notice"):
            delete[category] = {r["id"] for r in store.all(category) if r["id"] in delete["plan"]}
        delete["adjustment"] = {r["id"] for r in store.all("adjustment") if r["plan_id"] in delete["plan"]}
        for category in ("occurrence", "routine_issue"):
            delete[category] = {r["id"] for r in store.all(category) if r["id"].rsplit(":", 1)[-1] < now.date().isoformat()}
        auxiliary = ("research_task", "reflection", "schedule_issue", "schedule_notice", "adjustment", "occurrence", "routine_issue")
        summary.append(dict(category="related", label="旧日程附属记录", policy="随日程清理", total=sum(len(store.all(c)) for c in auxiliary), days=0, limit=0, protected=0, remove=sum(len(delete[c]) for c in auxiliary)))
        histories = {e["id"]: e["history"][-cfg.history_count:] for e in events if e["id"] not in delete["experience"] and cfg.history_count and len(e.get("history", [])) > cfg.history_count}
        summary.append(dict(category="history", label="经历旧修订", total=sum(len(e.get("history", [])) for e in events), days=0, limit=cfg.history_count, protected=0, remove=sum(len(store.get("experience", key)["history"]) - len(value) for key, value in histories.items())))
        for category, field, label in (("relationship", "history", "关系变化历史"), ("goal", "outline_history", "目标分解历史")):
            rows = store.all(category)
            summary.append(dict(category=category+"_history", label=label, total=sum(len(r.get(field, [])) for r in rows),
                                days=0, limit=cfg.history_count, protected=0,
                                remove=sum(max(0, len(r.get(field, []))-cfg.history_count) for r in rows) if cfg.history_count else 0))
        exceptions = [day for r in store.all("routine") for day in r.get("exceptions", [])]
        summary.append(dict(category="routine_exceptions", label="重复日程跳过日期", total=len(exceptions), days=0, limit=0,
                            protected=0, remove=sum(day < now.date().isoformat() for day in exceptions), policy="过期清理"))
        for category, label in (("diary", "生活日记"), ("diary_publication", "空间发布记录"),
                                ("book_note", "阅读笔记"), ("dream", "梦境")):
            summary.append(dict(category=category, label=label, total=store.count(category), days=0,
                                limit=0, protected=0, remove=0, policy="长期保留"))
        return delete, histories, summary

    def preview(self, now):
        _, _, rows = self.collect(now)
        return {"rows": rows, "total": sum(r["remove"] for r in rows), "last": self.store.find("runtime", "retention") or {}, "enabled": self.config.retention.enabled}

    def clean(self, now):
        delete, histories, summary = self.collect(now)
        store = self.store
        with store.db:
            for row in self.sharing_updates.values():
                if row["notice_ids"] or row["experience_ids"]:
                    store.put("sharing", row)
                else:
                    store.delete("sharing", row["id"])
            for plan_id in delete["plan"]:
                note = store.find("book_note", plan_id)
                if note:
                    note["plan_status"] = store.get("plan", plan_id)["status"]
                    store.put("book_note", note)
            for category, ids in delete.items():
                for record_id in ids:
                    store.delete(category, record_id)
            for record_id, history in histories.items():
                store.put("experience", {**store.get("experience", record_id), "history": history})
            if self.config.retention.history_count:
                for category, field in (("relationship", "history"), ("goal", "outline_history")):
                    for row in store.all(category):
                        if len(row.get(field, [])) > self.config.retention.history_count:
                            store.put(category, {**row, field: row[field][-self.config.retention.history_count:]})
            for row in store.all("routine"):
                kept = [day for day in row.get("exceptions", []) if day >= now.date().isoformat()]
                if kept != row.get("exceptions", []):
                    store.put("routine", {**row, "exceptions": kept})
            for category, field in (("sharing", "experience_ids"), ("preference", "evidence_ids")):
                for row in store.all(category):
                    kept = [key for key in row.get(field, []) if key not in delete["experience"]]
                    if kept != row.get(field, []):
                        store.put(category, {**row, field: kept})
            result = {"id": "retention", "at": now.isoformat(), "removed": sum(r["remove"] for r in summary), "rows": summary}
            store.put("runtime", result)
        return result

    def daily(self, now):
        last = self.store.find("runtime", "retention")
        if self.config.retention.enabled and (not last or self.time(last["at"]).date() < now.date()):
            return self.clean(now)
