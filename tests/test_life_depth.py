"""生活规划、跨流近况、分享接话与时间线的联动。"""
import json
from datetime import timedelta
import pytest
from test_plugin import plugin
from test_person_presence import message
from maiweave.core.models import PlanInput, PlanPatch

async def test_daily_balance_and_concrete_next_date(plugin):
    now = plugin.now().replace(hour=12, minute=0, second=0, microsecond=0)
    start = now - timedelta(minutes=30)
    with plugin.store.db:
        plugin.store.put('state', {**plugin.engine.state(), 'updated_at': start.isoformat()})
    plan = plugin.engine.create_plan(PlanInput(title='做土豆饼', kind='meal', start=start.isoformat(), end=now.isoformat()), '', start)
    plugin.engine.advance(start)
    plugin.engine.advance(now)
    plugin.ctx.llm.generate.return_value = {'success': True, 'response': json.dumps({
        'activity':'烹饪','subject':'土豆饼','summary':'土豆饼做好了，边缘很脆','details':{'结果':'边缘酥脆'},
        'enjoyment':3,'importance':5,'score_reason':'试出了喜欢的口感','completed':True,
        'next_action':'周末试着把胡萝卜切丝加入土豆饼','next_date':(now+timedelta(days=2)).date().isoformat()})}
    await plugin.continuity.reflect(plan['id'],now)
    balance = plugin.living.activity_balance(now)
    assert balance['totals']==[{'activity':'烹饪','minutes':30,'sessions':1}]
    assert balance['consecutive']==1
    memory=plugin.store.all('activity_memory')[0]
    assert memory['completed'] and memory['next_date']>(now.date().isoformat())
    timeline=await plugin.ui_life_timeline(plan['id'])
    assert any('胡萝卜' in r['text'] for r in timeline['events'])
    plugin.config.life.autonomous=True
    with plugin.store.db:
        plugin.store.put('state',{**plugin.engine.state(),'hunger':20,'energy':80})
    plugin.ctx.llm.generate.return_value={'success':True,'response':json.dumps({'title':'听音乐','kind':'leisure','location':'住处','minutes':20,'motivation':'刚做完午饭，听一会儿音乐'})}
    await plugin.living.choose_activity(now)
    prompt=plugin.ctx.llm.generate.call_args.args[0]
    assert '今日实际活动' in prompt and '烹饪' in prompt
    eligible=prompt.split('可接续的具体念头：')[1].split('。选择接续')[0]
    assert memory['id'] not in eligible

async def test_expired_and_updated_presence_controls_private_not_group(plugin):
    now=plugin.now().replace(hour=12)
    plugin.now=lambda:now
    for session in ('private','group'):
        await plugin.identify_person(session,'u','qq')
        plugin.anniversaries.save_profile('同一人',session,'u',person_id='qq:u')
    plugin.config.proactive.enabled=True
    plugin.ctx.chat.get_all_streams.return_value=[{'stream_id':'private'},{'stream_id':'group','group_id':'g'}]
    await plugin.management_message(message('group','我去忙了'))
    await plugin.living.share(now)
    decisions=list(plugin.living.share_decisions)
    assert any(r['session']=='private' and r['outcome']=='稍后再聊' for r in decisions)
    assert not any(r['session']=='group' and r['outcome']=='稍后再聊' for r in decisions)
    now+=timedelta(minutes=10)
    await plugin.management_message(message('group','我刚下班了'))
    assert plugin.presence.targets('private',now)[0]['status']=='空闲'
    assert plugin.presence.targets('private',now+timedelta(hours=3))==[]

@pytest.mark.parametrize("naive", [True, False])
async def test_share_response_uses_current_user_and_stream(plugin, naive):
    now=plugin.now()
    receipts=plugin.living.receipts
    plugin.config.proactive.enabled=True
    with plugin.store.db:
        plugin.store.put('share_receipt',{'id':'sent','session':'private','sent_at':(now.replace(tzinfo=None) if naive else now).isoformat(),'created':now.isoformat(),
            'outcome':'实际已发送','topic':'土豆饼的脆边','experience_id':'event'})
    await plugin.management_message(message('private','加胡萝卜应该也好吃'))
    assert receipts.context('private',now)[0]['status']=='waiting'
    result=await plugin.share_followup('sent','用户建议加入胡萝卜','answered',stream_id='private',user_id='u')
    assert result['success']
    assert '用户建议加入胡萝卜' in plugin.context_text('private')
    assert '土豆饼的脆边' not in plugin.context_text('group')
    other=await plugin.share_followup('sent','回应','answered',stream_id='private',user_id='other')
    assert not other['success']
    await plugin.on_unload()
    await plugin.on_load()
    assert plugin.living.receipts.context('private',now)[0]['status']=='answered'

async def test_timeline_has_edits_and_actual_result(plugin):
    now=plugin.now()
    plan=plugin.engine.create_plan(PlanInput(title='散步',location='公园',motivation='想出去吹吹风',start=now.isoformat(),end=(now+timedelta(minutes=20)).isoformat()),'',now)
    plugin.engine.change_plan(plan['id'],PlanPatch(title='在家拉伸',location='住处'),'',now)
    plugin.engine.advance(now)
    plugin.engine.advance(now+timedelta(minutes=20))
    result=await plugin.ui_life_timeline(plan['id'])
    assert result['events'][0]['text']=='想出去吹吹风'
    assert any('公园 → 住处' in e['text'] for e in result['events'])
    assert any(e['title']=='活动结果 · 角色模拟' for e in result['events'])

async def test_unanswered_share_changes_candidate_order(plugin):
    now=plugin.now()
    events=[]
    for name in ('阅读','烹饪'):
        plan=plugin.engine.create_plan(PlanInput(title=name,start=now.isoformat(),end=(now+timedelta(minutes=20)).isoformat()),'',now)
        plan['status']='completed';plan['activity_label']=name
        with plugin.store.db:
            plugin.store.put('plan',plan)
            plugin.store.put('share_receipt',{'id':name,'session':'a','sent_at':now.isoformat(),'share_activity':name,
                'followup':'waiting' if name=='阅读' else 'answered'})
        events.append({'id':name,'plan_id':plan['id'],'importance':5,'occurred_at':now.isoformat()})
    selected,_=plugin.living.select_share(events,'a',now)
    assert selected['id']=='烹饪'


async def test_answered_topic_leaves_time_for_conversation(plugin):
    now=plugin.now().replace(hour=12)
    plugin.config.proactive.enabled=True
    plugin.config.proactive.session_ids=['a']
    with plugin.store.db:
        plugin.store.put('share_receipt',{'id':'reply','session':'a','created':now.isoformat(),
            'sent_at':(now-timedelta(hours=1)).isoformat(),'outcome':'实际已发送',
            'followup':'answered','responded_at':now.isoformat()})
    await plugin.living.share(now+timedelta(minutes=5))
    assert plugin.living.share_decisions[0]['outcome']=='接着聊'
    await plugin.living.share(now+timedelta(minutes=21))
    assert plugin.living.share_decisions[0]['outcome']=='跳过'
    assert '没有待发提醒' in plugin.living.share_decisions[0]['reason']

async def test_reminder_receipt_uses_its_own_reason(plugin):
    now=plugin.now().replace(hour=12)
    plugin.config.proactive.enabled=True
    plugin.config.proactive.session_ids=['a']
    plugin.continuity.save_thread('接着聊午饭','刚才聊到喜欢的热汤','a',now.isoformat(),now)
    await plugin.living.share(now)
    row=plugin.store.all('share_receipt')[0]
    assert row['score']=='本次处理话题跟进'
    assert '午饭' in row['topic']
