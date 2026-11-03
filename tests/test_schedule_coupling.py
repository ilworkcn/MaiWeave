"""日程完成、模型重试和阅读目标的衔接验证。"""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from maiweave.models import PlanInput, GoalInput
from maiweave.dreams import Dreams
from maiweave.continuity import Continuity
from test_plugin import plugin
from test_books import upload, answer

@pytest.mark.parametrize("with_stage", [True, False])
async def test_book_progresses_goal_after_notes_complete(plugin, with_stage):
    now=plugin.now();book=await upload(plugin)
    goal=plugin.engine.create_goal(GoalInput(title='读一章',target_sessions=1),'',now)
    goal['steps']=[{'id':'chapter','sessions':1,'completed_sessions':0,'status':'pending'}] if with_stage else []
    with plugin.store.db:plugin.store.put('goal',goal)
    plan=plugin.engine.create_plan(PlanInput(title='读书',book_id=book['id'],goal_id=goal['id'],goal_step_id='chapter' if with_stage else '',start=now.isoformat(),end=(now+timedelta(minutes=15)).isoformat()),'',now)
    plugin.engine.advance(now+timedelta(minutes=16))
    assert plugin.store.get('goal',goal['id'])['completed_sessions']==0
    answer(plugin);await plugin.books.process(now+timedelta(minutes=16))
    assert plugin.store.get('goal',goal['id'])['status']=='completed'
    event = plugin.store.get('experience', plugin.store.get('plan', plan['id'])['experience_id'])
    assert event['importance'] >= 9 and '读一章' in event['score_reason']
    assert '解开了上次留下的问题' in event['score_reason']
    await plugin.books.process(now+timedelta(minutes=17))
    assert plugin.store.get('goal',goal['id'])['completed_sessions']==1

async def test_diary_recovers_after_window_and_keeps_later_edit(plugin):
    now=plugin.now()
    p=plugin.engine.create_plan(PlanInput(title='日记',kind='diary',diary_day=now.date().isoformat(),start=now.isoformat(),end=(now+timedelta(minutes=15)).isoformat()),'',now)
    with plugin.store.db:plugin.store.put('diary',{'id':now.date().isoformat(),'plan_id':p['id'],'status':'生成失败','created_at':now.isoformat(),'retry_at':(now+timedelta(minutes=20)).isoformat()})
    plugin.engine.advance(now+timedelta(minutes=16));plugin.diary.write=AsyncMock()
    await plugin.diary.process(now+timedelta(minutes=16));plugin.diary.write.assert_not_awaited()
    await plugin.diary.process(now+timedelta(minutes=21));plugin.diary.write.assert_awaited_once()
    old=plugin.store.get('diary',now.date().isoformat());old.update(plan_id='later',created_at=(now+timedelta(minutes=22)).isoformat(),status='已生成')
    with plugin.store.db:plugin.store.put('diary',old)
    plugin.diary.write.reset_mock();await plugin.diary.process(now+timedelta(minutes=23));plugin.diary.write.assert_not_awaited()

async def test_dream_failure_retries_and_restart_recovers(plugin):
    now=plugin.now();p=plugin.engine.create_plan(PlanInput(title='睡眠',kind='sleep',start=now.isoformat(),end=(now+timedelta(hours=8)).isoformat()),'',now)
    end=now+timedelta(hours=8);plugin.engine.advance(end)
    plugin.ctx.llm.generate.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await plugin.dreams.generate(p['id'],end)
    assert plugin.store.get('dream',p['id'])['status']=='生成失败'
    plugin.dreams.generate=AsyncMock();await plugin.dreams.process(end+timedelta(minutes=1));plugin.dreams.generate.assert_not_awaited()
    await plugin.dreams.process(end+timedelta(minutes=6));plugin.dreams.generate.assert_awaited_once()
    row=plugin.store.get('dream',p['id']);row['status']='生成中'
    with plugin.store.db:plugin.store.put('dream',row)
    Dreams(plugin.engine,plugin.ctx,plugin.config)
    assert plugin.store.get('dream',p['id'])['status']=='生成失败'

async def test_reflection_and_goal_retry_after_interruption(plugin):
    now=plugin.now();p=plugin.engine.create_plan(PlanInput(title='画画',start=now.isoformat(),end=(now+timedelta(minutes=15)).isoformat()),'',now)
    end=now+timedelta(minutes=16);plugin.engine.advance(end)
    plugin.ctx.llm.generate.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await plugin.continuity.reflect(p['id'],end)
    plugin.continuity.reflect=AsyncMock()
    await plugin.continuity.process(end+timedelta(minutes=1));plugin.continuity.reflect.assert_not_awaited()
    await plugin.continuity.process(end+timedelta(minutes=6));plugin.continuity.reflect.assert_awaited_once()
    g=plugin.engine.create_goal(GoalInput(title='画一幅画'),'',end)
    with pytest.raises(asyncio.CancelledError):await plugin.continuity.outline(g['id'],end)
    plugin.continuity.outline=AsyncMock();await plugin.continuity.process(end+timedelta(minutes=6));plugin.continuity.outline.assert_awaited_once()

async def test_fatigue_replaces_reading_with_rest(plugin):
    now=plugin.now();book=await upload(plugin)
    p=plugin.engine.create_plan(PlanInput(title='阅读',kind='leisure',book_id=book['id'],flexible=True,start=now.isoformat(),end=(now+timedelta(minutes=30)).isoformat()),'',now,autonomous=True)
    with plugin.store.db:
        state=plugin.engine.state();state['energy']=10;plugin.store.put('state',state)
    plugin.living.adjust(now)
    changed=plugin.store.get('plan',p['id'])
    assert changed['kind']=='rest' and changed['book_id']==''
    plugin.engine.advance(now+timedelta(minutes=31));await plugin.books.process(now+timedelta(minutes=31))
    assert plugin.store.get('book',book['id'])['read_characters']==0
    assert plugin.store.all('book_note')==[]
