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
    assert upgraded['plugin']['config_version']=='0.10.3'

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
