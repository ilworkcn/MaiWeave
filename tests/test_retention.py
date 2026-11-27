"""清理与阅读、分享、日程、记忆核对的关联测试。"""
from datetime import timedelta

from test_plugin import plugin
from maiweave.core.models import ExperienceInput, PlanInput


def event(p, now, summary='旧经历', importance=3):
    return p.engine.record_experience(ExperienceInput(summary=summary, occurred_at=now.isoformat(), source='observed', importance=importance, evidence='测试记录'), '', now)


async def test_cleanup_preserves_links_and_book_pages(plugin):
    p = plugin
    now = p.now()
    old = now - timedelta(days=200)
    plan = p.engine.create_plan(PlanInput(title='散步', start=old.isoformat(), end=(old+timedelta(minutes=10)).isoformat()), '', old)
    with p.store.db:
        p.store.put("state", {**p.engine.state(), "updated_at": old.isoformat()})
    p.engine.advance(old+timedelta(minutes=11))
    plan = p.store.get('plan',plan['id'])
    with p.store.db:
        p.store.put('book_note', {'id':plan['id'],'book_id':'archived','status':'已完成','created_at':old.isoformat(),'finished_at':old.isoformat(),'at':old.isoformat()})
        p.store.put('reflection', {'id':plan['id'],'status':'已完成'})
        p.store.put('schedule_issue', {'id':plan['id']})
    preview = await p.ui_retention()
    assert next(r for r in preview['rows'] if r['category']=='plan')['remove'] == 1
    result = await p.ui_cleanup()
    assert result['removed'] >= 1
    assert p.store.find('plan',plan['id']) is None
    assert p.store.find('schedule_issue',plan['id']) is None
    assert p.store.get('book_note',plan['id'])['plan_status']=='completed'
    await p.ui_books()
    p.spatial.reconcile(now)
    await p.continuity.process(now)
    await p.books.process(now)
    await p.diary.process(now)
    p.rhythm.learn(now)


async def test_protected_records_and_zero_limits(plugin):
    p = plugin
    now=p.now();old=now-timedelta(days=200)
    rows=[event(p,old,summary=str(i)) for i in range(6)]
    with p.store.db:
        p.store.put('activity_memory',{'id':'next','experience_id':rows[0]['id']})
        p.store.put('review',{'id':'review','status':'待确认','payload':{'experience_id':rows[1]['id']},'created_at':old.isoformat()})
        p.store.put('share_receipt',{'id':'pending','outcome':'MaiBot 已受理','experience_id':rows[2]['id'],'created':old.isoformat()})
    await p.ui_keep_experience(rows[3]['id'], True)
    important=event(p,old,importance=7)
    p.config.retention.experience_count=1
    p.config.retention.history_count=0
    p.retention.clean(now)
    assert all(p.store.find('experience',r['id']) for r in rows[:4])
    assert p.store.find('experience',important['id'])
    assert all(not p.store.find('experience',r['id']) for r in rows[4:])
    await p.ui_keep_experience(rows[3]['id'],False)
    p.retention.clean(now)
    assert p.store.find('experience',rows[3]['id']) is None
    cfg=p.config.retention
    cfg.experience_days=cfg.experience_count=0
    retained=event(p,old)
    p.retention.clean(now)
    assert p.store.find('experience',retained['id'])


async def test_daily_cleanup_and_receipt_quota(plugin):
    p=plugin;now=p.now();old=now-timedelta(days=200)
    p.config.retention.log_count=1
    with p.store.db:
        for i,at in enumerate([old,now-timedelta(minutes=10),now-timedelta(minutes=5)]):
            p.store.put('share_receipt',{'id':str(i),'created':at.isoformat(),'sent_at':at.isoformat(),'session':'s','outcome':'实际已发送'})
        p.store.put('job',{'id':'running','status':'进行中','created_at':old.isoformat()})
        p.store.put('job',{'id':'done','status':'已完成','created_at':old.isoformat()})
    p.retention.clean(now)
    assert len(p.living.receipts.sent_times('s',now.tzinfo))==2
    assert p.store.find('job','running') and not p.store.find('job','done')
    assert p.retention.daily(now) is None
    assert p.retention.daily(now+timedelta(days=1)) is not None
    p.config.retention.enabled=False
    assert p.retention.daily(now+timedelta(days=2)) is None


async def test_history_trimming_and_reference_compaction(plugin):
    p=plugin;now=p.now();old=now-timedelta(days=200)
    row=event(p,old);keep=event(p,old,importance=8)
    with p.store.db:
        p.store.put('experience',{**keep,'history':[{'revision':n} for n in range(15)]})
        p.store.put('sharing',{'id':'s','experience_ids':[row['id'],keep['id']]})
        p.store.put('preference',{'id':'p','evidence_ids':[row['id'],keep['id']],'score':3,'samples':2})
    changes=p.store.db.total_changes
    preview=p.retention.preview(now)
    assert p.store.db.total_changes==changes
    assert preview['total']>=6
    p.retention.clean(now)
    assert len(p.store.get('experience',keep['id'])['history'])==10
    assert p.store.find('sharing','s') is None
    assert p.store.get('preference','p')['samples']==2
    assert not p.living.pending_share_result({**row,'source':'simulated','plan_id':'cleaned'})

async def test_real_reading_continues_after_old_schedule_cleanup(plugin):
    from test_books import upload, begin_reading, answer
    p=plugin
    book=await upload(p)
    now,plan=await begin_reading(p,book)
    end=now+timedelta(minutes=16)
    p.engine.advance(end)
    await p.books.process(end)
    future=now+timedelta(days=200)
    p.retention.clean(future)
    assert p.store.find('plan',plan['id']) is None
    assert (await p.ui_books())['notes'][0]['thoughts']=='想起安静的下午。'
    assert p.books.context()['notes'][0]['question']=='下一章会写什么？'
    p.now=lambda:future
    p.engine.advance(future)
    next_plan=await p.ui_book_action('schedule',book_id=book['id'],minutes=15)
    answer(p,quote='夜里一盏灯照着书页。')
    p.engine.advance(future+timedelta(seconds=2))
    await p.books.process(future+timedelta(seconds=2))
    assert '想起安静的下午' in p.ctx.llm.generate.call_args.args[0]
    p.engine.advance(future+timedelta(minutes=16))
    await p.books.process(future+timedelta(minutes=16))
    assert p.store.get('book_note',next_plan['id'])['status']=='已完成'
    assert len((await p.ui_books())['notes'])==2


async def test_active_goal_and_travel_chain_survive_count_limit(plugin):
    p=plugin;now=p.now();old=now-timedelta(days=200)
    with p.store.db:
        p.store.put('goal',{'id':'goal','status':'active'})
        for key,fields in [('trip',{}),('goalplan',{'goal_id':'goal','travel_id':'trip'}),('old',{})]:
            p.store.put('plan',{'id':key,'status':'completed','end':old.isoformat(),**fields})
    p.config.retention.plan_count=1
    preview=p.retention.preview(now)
    assert next(r for r in preview['rows'] if r['category']=='plan')['protected']==2
    p.retention.clean(now)
    assert p.store.find('plan','trip') and p.store.find('plan','goalplan')
    assert p.store.find('plan','old') is None

async def test_old_config_adds_retention_and_keeps_preferences(plugin):
    from maibot_sdk.config import rebuild_plugin_config_data
    defaults=plugin.get_default_config()
    old={key:value for key,value in defaults.items() if key!='retention'}
    old['plugin']['config_version']='0.9.0'
    old['proactive']['daily_limit']=7
    upgraded=rebuild_plugin_config_data(plugin.get_default_config(),old)
    assert upgraded['retention']['experience_count']==3000
    assert upgraded['proactive']['daily_limit']==7
    assert upgraded['plugin']['config_version']=='0.13.4'

async def test_old_receipts_without_timezone(plugin):
    now=plugin.now()
    with plugin.store.db:
        plugin.store.put('share_receipt',{'id':'legacy','outcome':'实际已发送','session':'s','created':(now-timedelta(days=40)).replace(tzinfo=None).isoformat(),'sent_at':(now-timedelta(days=40)).replace(tzinfo=None).isoformat()})
        plugin.store.put('share_receipt',{'id':'today','outcome':'实际已发送','session':'s','created':now.replace(tzinfo=None).isoformat(),'sent_at':now.replace(tzinfo=None).isoformat()})
    plugin.retention.clean(now)
    assert plugin.store.find('share_receipt','legacy') is None
    assert plugin.store.find('share_receipt','today')


async def test_incremental_records_cleanup_and_long_term_content(plugin):
    p = plugin
    now = p.now()
    old = (now - timedelta(days=200)).isoformat()
    current = now.isoformat()
    with p.store.db:
        for category, field in (("model_usage", "at"), ("emotion", "at"), ("place_search", "searched_at")):
            p.store.put(category, {"id": "old", field: old})
            p.store.put(category, {"id": "new", field: current})
        for status in ("active", "expired", "resolved"):
            p.store.put("effect", {"id": status, "status": status, "expires_at": old})
        p.store.put("thread", {"id": "done", "status": "resolved", "resolved_at": old})
        p.store.put("thread", {"id": "open", "status": "open", "created_at": old, "due_at": current})
        p.store.put("person_state", {"id": "old", "expires_at": old})
        p.store.put("person_state", {"id": "new", "expires_at": (now+timedelta(hours=1)).isoformat()})
        p.store.put("review", {"id": "failed", "status": "失败", "created_at": old})
        p.store.put("review", {"id": "pending", "status": "待确认", "created_at": old})
        p.store.put("relationship", {"id": "person", "closeness": 80, "history": list(range(25))})
        p.store.put("place", {"id": "park", "name": "公园"})
        for category, status in (("diary", "已生成"), ("diary_publication", "已发布"), ("book_note", "已完成"), ("dream", "已生成")):
            p.store.put(category, {"id": "saved", "status": status})
    preview = p.retention.preview(now)
    result = p.retention.clean(now)
    assert preview["total"] == result["removed"]
    for category in ("model_usage", "emotion", "place_search", "person_state"):
        assert p.store.find(category, "old") is None
        assert p.store.find(category, "new")
    assert p.store.find("effect", "active")
    assert not p.store.find("effect", "expired")
    assert not p.store.find("effect", "resolved")
    assert not p.store.find("thread", "done") and p.store.find("thread", "open")
    assert not p.store.find("review", "failed") and p.store.find("review", "pending")
    relation = p.store.get("relationship", "person")
    assert relation["history"] == list(range(15, 25)) and relation["closeness"] == 80
    assert p.store.find("place", "park")
    for category in ("diary", "diary_publication", "book_note", "dream"):
        assert p.store.find(category, "saved")
        assert next(row for row in preview["rows"] if row["category"] == category)["policy"] == "长期保留"
    assert p.retention.preview(now)["total"] == 0


async def test_usage_count_limit_summary_and_zero_limits(plugin):
    from maiweave.integrations.usage import summary
    p = plugin
    now = p.now()
    p.config.retention.usage_count = 2
    with p.store.db:
        for i in range(4):
            p.store.put("model_usage", {"id": str(i), "at": (now-timedelta(minutes=4-i)).isoformat(),
                "feature": "空间文案", "status": "成功", "prompt_tokens": 10, "completion_tokens": 5,
                "total_tokens": 15, "prompt_cache_hit_tokens": 0, "seconds": 1})
    p.retention.clean(now)
    assert {r["id"] for r in p.store.all("model_usage")} == {"2", "3"}
    stats = summary(p.store, now, 30)
    assert stats["totals"]["calls"] == 2 and stats["totals"]["total"] == 30
    p.config.retention.usage_days = p.config.retention.usage_count = 0
    p.retention.clean(now+timedelta(days=200))
    assert len(p.store.all("model_usage")) == 2


async def test_goal_history_and_expired_routine_exceptions(plugin):
    p = plugin
    now = p.now()
    today = now.date().isoformat()
    old = (now-timedelta(days=1)).date().isoformat()
    future = (now+timedelta(days=1)).date().isoformat()
    with p.store.db:
        p.store.put("goal", {"id": "goal", "status": "active", "steps": ["当前步骤"], "outline_history": list(range(15))})
        p.store.put("routine", {"id": "routine", "exceptions": [old, today, future]})
    preview = p.retention.preview(now)
    result = p.retention.clean(now)
    assert result["removed"] == preview["total"] == 6
    assert p.store.get("goal", "goal")["outline_history"] == list(range(5, 15))
    assert p.store.get("goal", "goal")["steps"] == ["当前步骤"]
    assert p.store.get("routine", "routine")["exceptions"] == [today, future]
    p.config.retention.history_count = 0
    p.retention.clean(now)
    assert len(p.store.get("goal", "goal")["outline_history"]) == 10


async def test_image_cleanup_limits_files_and_keeps_text(plugin):
    import os
    from pathlib import Path
    p = plugin
    now = p.now()
    folder = Path(p.ctx.paths.data_dir) / "diary_images"
    folder.mkdir()
    p.config.retention.image_count = 2
    p.config.retention.image_max_mb = 1
    for index in range(4):
        path = folder / f"image-{index}.png"
        path.write_bytes(b"x" * 400000)
        at = now - timedelta(days=100 if index == 0 else 4-index)
        os.utime(path, (at.timestamp(), at.timestamp()))
        with p.store.db:
            p.store.put("diary", {"id": str(index), "status": "已生成", "content": "日记正文",
                                 "image_file": "diary_images/" + path.name, "image_status": "已生成"})
    (folder / "orphan.png").write_bytes(b"unused")
    preview = next(row for row in p.retention.preview(now)["rows"] if row["category"] == "diary_images")
    assert preview["remove"] == 3
    p.retention.clean(now)
    assert {path.name for path in folder.iterdir()} == {"image-2.png", "image-3.png"}
    assert p.store.get("diary", "0")["content"] == "日记正文"
    assert "image_file" not in p.store.get("diary", "0")
    assert "清理" in p.store.get("diary", "0")["image_status"]


async def test_image_cleanup_waits_for_publication(plugin):
    import os
    from pathlib import Path
    p = plugin
    now = p.now()
    folder = Path(p.ctx.paths.data_dir) / "diary_images"
    folder.mkdir()
    path = folder / "publishing.png"
    path.write_bytes(b"image")
    old = (now - timedelta(days=100)).timestamp()
    os.utime(path, (old, old))
    with p.store.db:
        p.store.put("diary_publication", {"id": "old", "status": "发布中", "content": "空间正文",
                                         "image_file": "diary_images/publishing.png"})
        p.store.put("image_test", {"id": "recent", "image_file": "diary_images/publishing.png"})
    p.retention.clean(now)
    assert path.exists()
    with p.store.db:
        p.store.put("diary_publication", {**p.store.get("diary_publication", "old"), "status": "已发布"})
    p.retention.clean(now)
    assert not path.exists()
    assert p.store.get("diary_publication", "old")["status"] == "已发布"
    assert "image_file" not in p.store.get("diary_publication", "old")
    assert "image_file" not in p.store.get("image_test", "recent")


async def test_wallet_retention_keeps_balances_order_and_delivery(plugin):
    p = plugin
    now = p.now()
    old = now - timedelta(days=200)
    w = p.engine.wallet
    with p.store.db:
        for key, status, owed in [("甲", "借入", 7), ("乙", "已还清", 0), ("丙", "已还清", 0)]:
            p.store.put("money_request", {"id": key, "session": key, "source_session": key, "purpose": "车费",
                        "title": "车费", "user": key, "date": old.date().isoformat(), "status": status, "amount": 10, "owed": owed})
            w._post("来款:" + key, 10, "借到车费", old, key, key)
        p.store.put("share_receipt", {"id": "已报信", "session": "甲", "record_id": "installment:7:甲",
                    "outcome": "实际已发送", "created": old.isoformat(), "sent_at": old.isoformat(), "settled": True})
        p.store.put("sharing", {"id": "乙", "notice_ids": ["repayment:乙"], "experience_ids": []})
        w._post("消费:旧早餐", -8, "早餐", old)
        w._post("消费:新早餐", -6, "早餐", now)
    before = w.account()
    preview = p.retention.preview(now)
    assert not p.store.find("sharing", "甲")
    assert p.retention.clean(now)["removed"] == preview["total"]
    assert w.account() == before
    assert not p.store.find("payment", "消费:旧早餐")
    assert p.store.find("payment", "来款:甲") and p.store.find("payment", "消费:新早餐")
    assert p.store.get("money_request", "甲")["owed"] == 7
    assert not p.store.find("share_receipt", "已报信")
    assert "installment:7:甲" in p.store.get("sharing", "甲")["notice_ids"]
    assert not p.store.find("money_request", "乙") and not p.store.find("sharing", "乙")
    assert p.store.find("money_request", "丙")
    assert p.retention.preview(now)["total"] == 0


async def test_clean_finished_memory_and_interrupted_upload(plugin):
    import os
    p = plugin
    now = p.now()
    old = now - timedelta(days=200)
    past, future = event(p, old), event(p, old, "下个月再去看花")
    with p.store.db:
        for row, next_action in [(past, ""), (future, "去看花")]:
            p.store.put("activity_memory", {"id": row["id"], "experience_id": row["id"],
                        "updated_at": old.isoformat(), "next_action": next_action,
                        "next_date": (now + timedelta(days=30)).date().isoformat() if next_action else ""})
        for key, active, enabled in [("曾经散步", False, True), ("常做早餐", True, True), ("已忽略", False, False)]:
            p.store.put("habit", {"id": key, "active": active, "enabled": enabled, "last_seen": old.isoformat()})
    stale, fresh = [p.books.begin(name, "", "")["id"] for name in ["旧书.txt", "新书.txt"]]
    path = p.books.directory / (stale + ".txt")
    os.utime(path, (old.timestamp(), old.timestamp()))
    fresh_path = p.books.directory / (fresh + ".txt")
    os.utime(fresh_path, (now.timestamp(), now.timestamp()))
    p.retention.clean(now)
    assert not path.exists() and not p.store.find("book_upload", stale)
    assert fresh_path.exists() and p.store.find("book_upload", fresh)
    assert not p.store.find("activity_memory", past["id"]) and not p.store.find("experience", past["id"])
    assert p.store.find("activity_memory", future["id"]) and p.store.find("experience", future["id"])
    assert not p.store.find("habit", "曾经散步")
    assert p.store.find("habit", "常做早餐") and p.store.find("habit", "已忽略")


async def test_low_limits_keep_food_budget_and_latest_work_progress(plugin):
    p, now = plugin, plugin.now()
    p.config.retention.plan_count = p.config.retention.plan_days = 1
    p.config.rhythm.learning_days = 3
    with p.store.db:
        p.store.put("routine", {"id": "月班", "status": "active"})
        for key, days, extra in [("午餐", 7, {"kind": "meal", "cost": 18}),
                                 ("上月", 35, {"kind": "work", "routine_id": "月班", "shift_scene": {"done": True}}),
                                 ("上上月", 65, {"kind": "work", "routine_id": "月班", "shift_scene": {"done": True}})]:
            at = (now - timedelta(days=days)).isoformat()
            p.store.put("plan", {"id": key, "status": "completed", "start": at, "end": at, **extra})
    before = p.engine.wallet.spending_room(now, p.store.all("plan"))
    p.retention.clean(now)
    assert p.engine.wallet.spending_room(now, p.store.all("plan")) == before
    assert p.store.find("plan", "上月") and not p.store.find("plan", "上上月")


async def test_reload_releases_interrupted_jobs_and_reviews(plugin):
    p = plugin
    old = (p.now() - timedelta(days=30)).isoformat()
    with p.store.db:
        p.store.put("job", {"id": "旧任务", "created_at": old, "status": "进行中"})
        for status in ("待确认", "检查中", "待写入", "写入中"):
            p.store.put("review", {"id": status, "created_at": old, "status": status})
    await p.on_unload()
    await p.on_load()
    assert p.store.get("job", "旧任务")["status"] == "已取消"
    assert all(p.store.get("review", key)["status"] == "失败" for key in ("检查中", "待写入", "写入中"))
    p.retention.clean(p.now())
    assert not p.store.find("job", "旧任务")
    assert [r["id"] for r in p.store.all("review")] == ["待确认"]


async def test_cleanup_respects_chat_processing_windows(plugin):
    p, now = plugin, plugin.now()
    p.config.retention.log_days = p.config.retention.log_count = 1
    with p.store.db:
        for session, hours, pending in [("旧", 72, False), ("刚看过", 1, False), ("待处理", 80, True)]:
            p.chat_schedule.mark(session, now - timedelta(hours=hours))
            row = p.store.get("runtime", "chat_schedule:" + session)
            p.store.put("runtime", {**row, "pending": pending})
        for hours in (4, 36, 48.1, 50):
            at = (now - timedelta(hours=hours)).isoformat()
            p.store.put("share_receipt", {"id": str(hours), "created": at, "sent_at": at,
                        "session": "测试私聊", "outcome": "实际已发送",
                        **({"followup": "answered", "responded_at": (now - timedelta(minutes=12)).isoformat()} if hours == 48.1 else {})})
    p.retention.clean(now)
    assert not p.store.find("runtime", "chat_schedule:旧")
    assert all(p.store.find("runtime", "chat_schedule:" + k) for k in ("刚看过", "待处理"))
    assert {r["id"] for r in p.living.receipts.context("测试私聊", now)} == {"4", "36"}
    assert not p.store.find("share_receipt", "50")
    assert p.store.find("share_receipt", "48.1")
    p.retention.clean(now + timedelta(minutes=9))
    assert not p.store.find("share_receipt", "48.1")


async def test_missing_picture_can_be_generated_again_after_cleanup(plugin):
    p, now = plugin, plugin.now()
    with p.store.db:
        p.store.put("diary_publication", {"id": "草稿", "status": "草稿", "content": "今天做了蛋包饭",
                    "image_file": "diary_images/lunch.png", "image_prompt": "盘里的蛋包饭", "image_status": "已生成"})
        p.store.put("image_test", {"id": "测试图", "image_file": "diary_images/lunch.png"})
        p.store.put("experience_image", {"id": "已删除的经历", "image_file": "diary_images/missing.png"})
    preview = p.retention.preview(now)
    assert p.retention.clean(now)["removed"] == preview["total"]
    draft = p.store.get("diary_publication", "草稿")
    assert draft["image_prompt"] == "盘里的蛋包饭" and draft["content"] == "今天做了蛋包饭"
    assert "image_file" not in draft and "image_file" not in p.store.get("image_test", "测试图")
    assert not p.store.find("experience_image", "已删除的经历")
    assert p.retention.preview(now)["total"] == 0
