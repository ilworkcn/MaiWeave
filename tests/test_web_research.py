"""自主见闻与日程、回顾、分享、缓存及清理的联动。"""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_plugin import plugin
from maiweave.core.models import PlanInput


def prepare(p):
    p.config.world.search_enabled = p.config.world.explore_enabled = True
    p.ctx.web = SimpleNamespace(
        search=AsyncMock(return_value={'results': [{'title':'土豆饼做法','url':'https://example.com/potato','snippet':'土豆切丝后煎熟'}]}),
        fetch=AsyncMock(return_value={'title':'土豆饼做法','content':'土豆切丝，加少量面粉，煎至两面金黄。'}))
    now = p.now().replace(hour=12, minute=0, second=0, microsecond=0)
    with p.store.db:
        p.store.put('state', {**p.engine.state(),'updated_at':(now-timedelta(seconds=1)).isoformat(),'location':'住处'})
    return now


def plan(p, now, autonomous=False):
    return p.engine.create_plan(PlanInput(title='上网了解土豆饼', kind='study', location='住处',
        start=now.isoformat(), end=(now+timedelta(minutes=20)).isoformat(), research_query='土豆饼做法',
        research_freshness='knowledge'), '', now, autonomous=autonomous)


async def test_research_plan_to_sources_reflection_share_and_continuation(plugin):
    p=plugin;now=prepare(p)
    p.config.life.autonomous=True
    p.ctx.llm.generate.return_value={'success':True,'response':json.dumps({'title':'查土豆饼做法','kind':'study','minutes':20,
        'location':'住处','motivation':'想试试昨天想到的菜','research_query':'土豆饼做法','research_freshness':'knowledge'})}
    await p.living.choose_activity(now)
    item=p.store.all('plan')[0]
    assert item['research_query']=='土豆饼做法'
    p.engine.advance(now+timedelta(seconds=1))
    await p.world.process(now)
    assert p.store.get('research_task',item['id'])['status']=='资料就绪'
    assert p.store.all('experience')==[]
    await p.world.process(now+timedelta(minutes=1))
    assert p.ctx.web.search.await_count==1
    p.engine.advance(now+timedelta(minutes=21))
    event=p.store.all('experience')[0]
    assert p.living.pending_share_result(event)
    p.ctx.llm.generate.return_value={'success':True,'response':json.dumps({
        'activity':'上网了解','subject':'土豆饼','summary':'读到了土豆饼的做法，想试试脆边','details':{'资料摘要':'土豆切丝，加少量面粉','个人感想':'想试试脆边'},
        'enjoyment':3,'importance':5,'score_reason':'找到想尝试的做法','next_action':'下次做饭试试土豆饼','completed':True})}
    await p.continuity.process(now+timedelta(minutes=21))
    event=p.store.get('experience',event['id'])
    assert 'https://example.com/potato' in event['details']['资料来源']
    assert event['importance']==5 and not p.living.pending_share_result(event)
    assert p.living.select_share([event],'a',now)[0]['id']==event['id']
    assert p.store.all('activity_memory')[0]['next_action']=='下次做饭试试土豆饼'
    prompt=p.ctx.llm.generate.call_args.args[0]
    assert '煎至两面金黄' in prompt
    assert p.store.get('research_task',item['id'])['status']=='已完成'
    p.config.life.autonomous=False
    p.chat_context.kinds['a']='group'
    assert '土豆' in p.chat_context.build('a',text='土豆饼你有什么感想？')
    assert '土豆' in json.dumps(p.diary.material(now.date(),now+timedelta(minutes=22)),ensure_ascii=False)
    queried=await p.world_tool('status',query='土豆饼',stream_id='a')
    assert queried['discoveries'][0]['subject']=='土豆饼'
    detail=await p.ui_record_detail('research_task',item['id'],'sources')
    assert '土豆切丝' in detail['text']


async def test_cache_dedup_expiry_and_sql_page(plugin):
    p=plugin;now=prepare(p)
    first=await p.world.search('  土豆饼做法  ',now)
    cached=await p.world.search('土豆饼做法',now+timedelta(hours=5))
    assert first['id']==cached['id'] and cached['cached']
    await p.world.search('土豆饼做法',now+timedelta(days=2),'knowledge')
    assert p.ctx.web.search.await_count==1
    await p.world.search('土豆饼做法',now+timedelta(hours=7))
    assert p.ctx.web.search.await_count==2 and len(p.store.all('research'))==1
    await p.world.read('https://example.com/potato',now)
    await p.world.read('https://example.com/potato',now+timedelta(hours=2))
    assert p.ctx.web.fetch.await_count==1
    with p.store.db:
        for i in range(45):
            p.store.put('reading',{'id':str(i),'title':str(i),'url':'https://example.com/'+str(i),
                'content':'正文'*10000,'retrieved_at':(now+timedelta(minutes=i)).isoformat()})
    page=await p.ui_world(page=2)
    assert len(page['readings'])==20 and page['pagination']['pages']==3
    assert all('content' not in row for row in page['readings'])
    assert page['readings'][0]['id']=='24'
    await p.ui_delete_world('reading','24')
    assert p.store.find('reading','24') is None


async def test_daily_interval_and_configuration(plugin):
    p=plugin;now=prepare(p)
    p.config.world.search_enabled=False
    assert not (await p.world.exploration_status(now))['ready']
    p.config.world.search_enabled=True
    item=plan(p,now,True);p.engine.advance(now+timedelta(seconds=1))
    await p.world.process(now)
    assert not (await p.world.exploration_status(now+timedelta(minutes=179)))['ready']
    assert (await p.world.exploration_status(now+timedelta(minutes=180)))['ready']
    p.config.world.explore_daily_limit=1
    assert not (await p.world.exploration_status(now+timedelta(hours=4)))['ready']
    assert (await p.world.exploration_status(now+timedelta(days=1)))['ready']
    assert (await p.world.exploration_status(now))['used']==1


async def test_cancel_during_fetch_does_not_publish(plugin):
    p=plugin;now=prepare(p);item=plan(p,now);p.engine.advance(now+timedelta(seconds=1))
    async def cancelled(**kwargs):
        p.engine.transition(item['id'],'cancel','',now+timedelta(minutes=1))
        return {'title':'做法','content':'正文'}
    p.ctx.web.fetch.side_effect=cancelled
    await p.world.process(now)
    assert p.store.get('research_task',item['id'])['status']=='已取消'
    await p.continuity.process(now+timedelta(minutes=30))
    assert not p.store.all('reflection')


async def test_failed_search_retries_and_partial_page_uses_summary(plugin):
    p=plugin;now=prepare(p);item=plan(p,now);p.engine.advance(now+timedelta(seconds=1))
    p.ctx.web.search.side_effect=RuntimeError('联网暂时失败')
    with pytest.raises(RuntimeError):await p.world.process(now)
    await p.world.process(now+timedelta(minutes=1))
    assert p.ctx.web.search.await_count==1
    p.ctx.web.search.side_effect=None
    p.ctx.web.fetch.side_effect=RuntimeError('网页不可读')
    await p.world.process(now+timedelta(minutes=5))
    row=p.store.get('research_task',item['id'])
    assert row['attempts']==2 and row['sources'][0]['scope']=='搜索摘要'
    assert row['sources'][0]['error']=='网页不可读'


async def test_pending_sources_are_not_simulated_and_cache_clean_keeps_snapshot(plugin):
    p=plugin;now=prepare(p);item=plan(p,now)
    p.engine.advance(now+timedelta(minutes=21))
    await p.continuity.process(now+timedelta(minutes=21))
    assert p.ctx.llm.generate.await_count==0
    await p.world.process(now+timedelta(minutes=21))
    p.config.retention.world_days=1
    p.retention.clean(now+timedelta(days=2))
    assert not p.store.all('reading') and not p.store.all('research')
    assert p.store.get('research_task',item['id'])['sources'][0]['content']


async def test_manual_schedule_uses_next_gap_and_validates_mode(plugin):
    p=plugin;now=prepare(p)
    p.now=lambda:now
    busy=p.engine.create_plan(PlanInput(title='吃饭',kind='meal',start=now.isoformat(),end=(now+timedelta(minutes=30)).isoformat(),location='住处'),'',now)
    result=await p.ui_schedule_research('土豆饼做法','knowledge')
    assert p.store.get('plan',result['id'])['start']==busy['end']
    assert '已安排' in result['message']
    with pytest.raises(ValueError):await p.ui_schedule_research('','current')
    with pytest.raises(ValueError):await p.ui_schedule_research('主题','wrong')


async def test_model_failure_retries_preserve_sources(plugin):
    p=plugin;now=prepare(p);item=plan(p,now);p.engine.advance(now+timedelta(seconds=1))
    await p.world.process(now)
    p.engine.advance(now+timedelta(minutes=21))
    p.ctx.llm.generate.return_value={'success':False,'error':'模型暂时不可用'}
    with pytest.raises(RuntimeError):await p.continuity.reflect(item['id'],now+timedelta(minutes=21))
    assert p.ctx.llm.generate.await_count==3
    assert p.store.get('research_task',item['id'])['status']=='资料就绪'
    page=await p.ui_world()
    assert page['explorations'][0]['status']=='感想整理失败，将自动重试'
    assert p.ctx.web.search.await_count==1


async def test_upgrade_preserves_settings_and_adds_world_defaults(plugin):
    old=plugin.get_default_config()
    old['plugin']['config_version']='0.9.5'
    old['life']['interests']=['画画']
    old['world']['search_enabled']=True
    for key in ['explore_enabled','explore_topics','explore_daily_limit','explore_interval_minutes']:
        old['world'].pop(key)
    for key in ['world_days','world_count']:
        old['retention'].pop(key)
    from maibot_sdk.config import rebuild_plugin_config_data
    upgraded=rebuild_plugin_config_data(plugin.get_default_config(),old)
    assert upgraded['plugin']['config_version']=='0.11.3'
    assert upgraded['world']['search_enabled'] and upgraded['life']['interests']==['画画']
    assert upgraded['world']['explore_daily_limit']==3
    assert upgraded['world']['explore_interval_minutes']==180
    assert upgraded['retention']['world_count']==300
