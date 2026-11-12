"""日记、空间文案、适配器回执与页面任务的联动。"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_plugin import plugin
from maiweave.integrations.qzone import DiaryPublisher
from maiweave.core.models import PlanInput


def prepare(p):
    now = p.now()
    day = now.date().isoformat()
    p.config.diary.publish_mode = "手动发布"
    p.ctx.call_capability = AsyncMock(side_effect=adapter)
    p.ctx.llm.generate.return_value = {"success": True, "response": "午后读了几页书，忽然想起昨天留下的问题。"}
    with p.store.db:
        p.store.put("diary", {"id": day, "day": day, "status": "已生成", "complete": False,
                             "content": "私人日记内容", "plan_id": "writing", "material": {
            "活动": [{"title": "读书", "kind": "leisure", "status": "completed", "session_id": "", "participants": []},
                     {"title": "私人约定SECRET", "kind": "social", "status": "completed", "session_id": "private"}],
            "经历": [{"title": "散步", "summary": "闻到桂花香", "session_id": "", "source": "simulated"},
                     {"title": "私聊SECRET", "session_id": "private"}]}})
    return now, day


async def adapter(capability, *, api_name, version, timeout_ms, args):
    assert capability == "api.call" and version == "1"
    name = api_name
    if name.endswith("get_version_info"):
        return {"status": "ok", "retcode": 0, "data": {"app_name": "SnowLuma"}}
    if name.endswith("get_login_info"):
        return {"user_id": 123456}
    if name.endswith("send_qzone_msg"):
        return {"status": "ok", "retcode": 0, "data": {"tid": "post1"}}
    raise AssertionError(name)


async def test_diary_draft_edit_publish_receipt_and_regeneration(plugin):
    p=plugin;now,day=prepare(p)
    row=await p.diary_publisher.prepare(day,now)
    assert row['status']=='草稿'
    prompt=p.ctx.llm.generate.call_args.args[0]
    assert 'SECRET' not in prompt and '私人日记内容' not in prompt and '读书' in prompt
    assert p.store.all('model_usage')[-1]['feature']=='空间文案'
    await p.ui_diary_publish_action('save',day,'编辑后的生活随笔')
    await p.diary_publisher.publish(day,now)
    result=await p.ui_diary_publication(day)
    assert result['record']['tid']=='post1' and result['record']['status']=='已发布'
    assert result['record']['published_at'] and result['record']['account']=='123456'
    assert p.ctx.call_capability.call_args.kwargs['args']['params']=={'content':'编辑后的生活随笔','ugc_right':4}
    assert (await p.ui_diaries())['entries'][0]['publication_status']=='已发布'
    await p.diary_publisher.publish(day,now)
    assert p.ctx.call_capability.await_count==3
    diary=p.store.get('diary',day);diary['content']='重新生成的日记'
    with p.store.db:p.store.put('diary',diary)
    assert (await p.ui_diary_publication(day))['record']['content']=='编辑后的生活随笔'


async def test_auto_waits_diary_schedule_completion_once(plugin):
    p=plugin;now,day=prepare(p);p.config.diary.publish_mode='日记完成后自动发布'
    plan=p.engine.create_plan(PlanInput(title='写日记',kind='diary',start=now.isoformat(),end=(now+timedelta(minutes=1)).isoformat()),'',now)
    diary=p.store.get('diary',day);diary['plan_id']=plan['id']
    with p.store.db:p.store.put('diary',diary)
    p.engine.advance(now)
    await p.diary_publisher.process(now)
    p.ctx.call_capability.assert_not_awaited()
    p.engine.advance(now+timedelta(minutes=2))
    await p.diary_publisher.process(now+timedelta(minutes=2))
    await p.diary_publisher.process(now+timedelta(minutes=3))
    assert p.store.get('diary_publication',day)['status']=='已发布'
    assert p.ctx.llm.generate.await_count==1 and p.ctx.call_capability.await_count==3


async def test_off_mode_and_manual_mode_skip_automatic_publish(plugin):
    p=plugin;now,day=prepare(p)
    for mode in ['关闭','手动发布']:
        p.config.diary.publish_mode=mode
        await p.diary_publisher.process(now)
    p.ctx.call_capability.assert_not_awaited()
    p.config.diary.publish_mode='关闭'
    await p.diary_publisher.prepare(day,now)
    with pytest.raises(ValueError,match='配置'):
        await p.diary_publisher.publish(day,now)


async def test_wrong_client_is_shown_before_publishing(plugin):
    p=plugin;now,day=prepare(p)
    p.ctx.call_capability.return_value={'status':'ok','retcode':0,'data':{'app_name':'NapCat'}}
    p.ctx.call_capability.side_effect=None
    await p.diary_publisher.prepare(day,now)
    with pytest.raises(ValueError,match='SnowLuma'):
        await p.diary_publisher.publish(day,now)
    assert p.ctx.call_capability.await_count==1
    assert p.store.get('diary_publication',day)['status']=='发布失败'
    assert p.diary_publisher.connection['status']=='连接失败'


@pytest.mark.parametrize('result,expected',[(TimeoutError('发送超时'),'待核对'),({'status':'failed','retcode':1,'message':'空间服务拒绝'},'发布失败'),({'success':False,'error':'对端未连接'},'发布失败')])
async def test_failure_receipt_and_explicit_recovery(plugin,result,expected):
    p=plugin;now,day=prepare(p)
    async def call(name,**kwargs):
        if kwargs['api_name'].endswith('send_qzone_msg'):
            if isinstance(result,Exception):raise result
            return result
        return await adapter(name,**kwargs)
    p.ctx.call_capability.side_effect=call
    await p.diary_publisher.prepare(day,now)
    with pytest.raises((TimeoutError,RuntimeError)):
        await p.diary_publisher.publish(day,now)
    assert p.store.get('diary_publication',day)['status']==expected
    assert p.ctx.call_capability.await_count==3
    if expected=='待核对':
        with pytest.raises(ValueError,match='待核对'):p.diary_publisher.save(day,'新文案',now)
        await p.ui_diary_publish_action('confirm_unpublished',day)
    else:p.diary_publisher.save(day,'重试文案',now)
    p.ctx.call_capability.side_effect=adapter
    await p.diary_publisher.publish(day,now)
    assert p.store.get('diary_publication',day)['status']=='已发布'


async def test_two_publish_requests_share_state(plugin):
    p=plugin;now,day=prepare(p);await p.diary_publisher.prepare(day,now)
    entered=asyncio.Event();release=asyncio.Event()
    async def call(name,**kwargs):
        if kwargs['api_name'].endswith('send_qzone_msg'):entered.set();await release.wait()
        return await adapter(name,**kwargs)
    p.ctx.call_capability.side_effect=call
    task=asyncio.create_task(p.diary_publisher.publish(day,now));await entered.wait()
    with pytest.raises(ValueError,match='发布中'):await p.diary_publisher.publish(day,now)
    release.set();await task
    assert p.ctx.call_capability.await_count==3


async def test_background_job_progress_and_model_failure(plugin):
    p=plugin;now,day=prepare(p)
    entered=asyncio.Event();release=asyncio.Event()
    async def model(*args,**kwargs):entered.set();await release.wait();return {'success':True,'response':'今天的随笔'}
    p.ctx.llm.generate.side_effect=model
    job=await p.ui_diary_publish_action('generate',day)
    await entered.wait()
    assert not (await p.ui_operation_status(task_id=job['task_id']))['done']
    assert (await p.ui_diary_publication(day))['record']['status']=='生成中'
    release.set();await asyncio.gather(*list(p.jobs))
    assert (await p.ui_operation_status(task_id=job['task_id']))['done']
    p.ctx.llm.generate.side_effect=None;p.ctx.llm.generate.return_value={'success':False,'error':'模型暂不可用'}
    with pytest.raises(RuntimeError):await p.diary_publisher.prepare(day,now)
    assert p.store.get('diary_publication',day)['status']=='生成失败'
    assert p.ctx.llm.generate.await_count==4


async def test_reload_publication_requires_result_check(plugin):
    p=plugin;now,day=prepare(p)
    p.diary_publisher.save(day,'一篇随笔',now)
    row=p.store.get('diary_publication',day);row['status']='发布中'
    with p.store.db:p.store.put('diary_publication',row)
    p.diary_publisher=DiaryPublisher(p.engine,p.ctx,p.config)
    assert p.store.get('diary_publication',day)['status']=='待核对'
    await p.ui_diary_publish_action('confirm_published',day)
    assert p.store.get('diary_publication',day)['receipt']=='用户在 QQ 空间确认已发布'


async def test_no_public_material_and_visibility(plugin):
    p=plugin;now,day=prepare(p)
    diary=p.store.get('diary',day);original=diary['material'];diary['material']={}
    with p.store.db:p.store.put('diary',diary)
    with pytest.raises(ValueError,match='素材'):await p.diary_publisher.prepare(day,now)
    p.ctx.llm.generate.assert_not_awaited()
    diary['material']=original
    with p.store.db:p.store.put('diary',diary)
    p.config.diary.publish_visibility='仅自己';p.diary_publisher.save(day,'自己看',now)
    await p.diary_publisher.publish(day,now)
    assert p.ctx.call_capability.call_args.kwargs['args']['params']['ugc_right']==64


def test_publication_capability_declared():
    import json
    from pathlib import Path
    manifest=json.loads((Path(__file__).parents[1]/'_manifest.json').read_text(encoding='utf-8'))
    assert 'api.call' in manifest['capabilities']


async def test_connection_shows_native_api_error(plugin):
    plugin.ctx.call_capability = AsyncMock(return_value={"success": False, "error": "对端动作执行失败: action=get_version_info error=对端未连接"})
    with pytest.raises(RuntimeError, match="对端未连接"):
        await plugin.diary_publisher.check_connection()
    assert plugin.diary_publisher.connection["status"] == "连接失败"
    assert "对端未连接" in plugin.diary_publisher.connection["error"]


async def test_diary_finished_after_midnight_is_published_once(plugin):
    p = plugin
    now = p.now().replace(hour=23, minute=59, second=0, microsecond=0)
    p.now = lambda: now
    with p.store.db:
        p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat()})
    now, day = prepare(p)
    p.config.diary.publish_mode = "日记完成后自动发布"
    plan = p.engine.create_plan(PlanInput(title="睡前日记", kind="diary", start=now.isoformat(),
                               end=(now + timedelta(minutes=15)).isoformat()), "", now)
    with p.store.db:
        p.store.put("diary", {**p.store.get("diary", day), "plan_id": plan["id"]})
    finished = now + timedelta(minutes=16)
    p.engine.advance(finished)
    await p.diary_publisher.process(finished)
    await p.diary_publisher.process(finished + timedelta(minutes=1))
    assert p.store.get("diary_publication", day)["status"] == "已发布"
    assert p.ctx.call_capability.await_count == 3
