"""首页真实记录汇总、跨日轨迹与阅读关联。"""
from datetime import timedelta
import json
from test_plugin import plugin
from maiweave.core.models import PlanInput

async def test_dashboard_empty_is_readonly(plugin):
    before=plugin.store.db.total_changes
    data=await plugin.ui_dashboard()
    assert data['current'] is None and data['timeline']==[]
    assert data['book'] is None and data['diary'] is None
    assert data['usage']['calls']==0
    assert 'history' not in data
    assert plugin.store.db.total_changes==before
    assert '.' not in data['now']
    json.dumps(data,allow_nan=False)

async def test_dashboard_clips_cross_midnight_and_excludes_cancelled(plugin):
    now=plugin.now(); midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
    plan=plugin.engine.create_plan(PlanInput(title='跨日睡眠',kind='sleep',start=(midnight-timedelta(minutes=30)).isoformat(),end=(midnight+timedelta(hours=7)).isoformat()),'',now-timedelta(days=1))
    today=await plugin.ui_dashboard();tomorrow=await plugin.ui_dashboard(1)
    assert today['timeline'][0]['end_minute']==1440
    assert tomorrow['timeline'][0]['start_minute']==0
    assert tomorrow['timeline'][0]['end_minute']==420
    plan['status']='cancelled'
    with plugin.store.db:plugin.store.put('plan',plan)
    assert (await plugin.ui_dashboard())['timeline']==[]

async def test_dashboard_book_note_is_from_current_book(plugin):
    with plugin.store.db:
        plugin.store.put('book',{'id':'a','title':'甲书','author':'甲','status':'阅读中','read_characters':50,'characters':100})
        plugin.store.put('runtime',{'id':'current_book','book_id':'a'})
        for book,at in [('a','2026-01-01'),('b','2026-02-01')]:
            plugin.store.put('book_note',{'id':book,'book_id':book,'status':'已完成','finished_at':at,'thoughts':book+'感想','question':book+'疑问'})
    result=await plugin.ui_dashboard()
    assert result['book']['thoughts']=='a感想' and result['book']['question']=='a疑问'
    assert result['book']['progress']==50
