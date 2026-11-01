"""资料冲突确认、亲密日程、自动认证和后台调度。"""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from maiweave.config import MemoryConfig
from maiweave.memory import NativeMemory
from maiweave.models import PlanInput
from maiweave.review import Assessment
from maiweave.usage import generate
from test_plugin import plugin


def decision(plugin, conflicts=None, level=0):
    plugin.ctx.llm.generate.return_value={"success":True,"response":json.dumps({"conflicts":conflicts or [],"level":level,"relationship":"恋人" if level==3 else "普通关系","reason":"当前关系依据"})}


async def test_birthday_conflict_waits_for_choice(plugin):
    person=plugin.anniversaries.save_profile("小明","a","u")
    old=plugin.anniversaries.save_date(person["id"],"生日","solar",8,3)
    decision(plugin,["生日从8月3日改为8月5日"])
    pending=await plugin.reviews.submit("anniversary",{"profile_id":person["id"],"anniversary_id":old["id"],"title":"生日","calendar":"solar","month":8,"day":5},"a")
    assert pending["status"]=="待确认"
    assert plugin.store.get("anniversary",old["id"])["day"]==3
    result=await plugin.confirm_change(pending["id"],True,stream_id="a")
    assert result["content"]["status"]=="已保存"
    assert plugin.store.get("anniversary",old["id"])["day"]==5
    assert plugin.store.get("review",pending["id"])["forced"]


async def test_cancelled_first_birthday_creates_no_profile(plugin):
    decision(plugin,["日期需要核对"])
    pending=await plugin.personal("save_date",month=8,day=3,stream_id="a",user_id="u")
    assert plugin.store.all("profile")==[]
    await plugin.confirm_change(pending["content"]["id"],False,stream_id="a")
    assert plugin.store.all("anniversary")==[]


async def test_native_edit_waits_and_updates_relationship(plugin):
    plugin.memory.people=AsyncMock(return_value={"items":[{"person_id":"p","person_name":"小明","profile_text":"朋友"}]})
    plugin.memory.edit_person=AsyncMock(return_value={"success":True})
    person=plugin.anniversaries.save_profile("小明","a","u",person_id="p")
    decision(plugin,["关系从朋友改为恋人"],3)
    record=await plugin.reviews.submit("edit_person",{"person_id":"p","text":"现在是恋人"})
    plugin.memory.edit_person.assert_not_awaited()
    await plugin.reviews.decide(record["id"],True)
    plugin.memory.edit_person.assert_awaited_once_with("p","现在是恋人")
    assert plugin.store.get("profile",person["id"])["intimacy"]==3


async def test_malformed_model_response_retries_and_counts(plugin):
    plugin.ctx.llm.generate.side_effect=[{"success":True,"response":"格式有误"},{"success":True,"response":'{"conflicts":[],"level":1}'}]
    response=await generate(plugin.ctx,plugin.store,plugin.config,"资料核对","核对资料",Assessment)
    assert response.level==1 and len(plugin.store.all("model_usage"))==2


async def test_background_wait_does_not_block_tick(plugin):
    plugin.worker.cancel()
    try: await plugin.worker
    except asyncio.CancelledError: pass
    started=asyncio.Event();release=asyncio.Event()
    async def slow(now):started.set();await release.wait()
    plugin.world.refresh=slow
    await asyncio.wait_for(plugin.tick(),0.5)
    await asyncio.wait_for(started.wait(),0.5)
    task=plugin.background["外部世界"]
    await asyncio.wait_for(plugin.tick(),0.5)
    assert plugin.background["外部世界"] is task
    assert not task.done()
    release.set();await asyncio.gather(*plugin.background.values())


def test_auto_cookie_reads_host_file(tmp_path):
    memory=NativeMemory(MemoryConfig(enabled=True));memory.root=tmp_path
    (tmp_path/"data").mkdir();(tmp_path/"data/webui.json").write_text(json.dumps({"access_token":"测试令牌"}),encoding="utf-8")
    from urllib.parse import quote
    assert memory.cookie()==quote("测试令牌",safe="")
    (tmp_path/"webui.json").write_text(json.dumps({"access_token":"new-token"}),encoding="utf-8")
    assert memory.cookie()=="new-token"


async def test_intimacy_controls_schedule_influence(plugin):
    now=plugin.now()
    plan=plugin.spatial.schedule(PlanInput(title="读书",kind="study",start=now.isoformat(),end=(now+timedelta(hours=1)).isoformat()),"",now,autonomous=True)
    with pytest.raises(ValueError,match="空闲"):
        plugin.living.check_influence("a","u",[plan])
    person=plugin.anniversaries.save_profile("小明","a","u")
    with plugin.store.db:plugin.store.put("profile",{**person,"intimacy":2,"intimacy_mode":"manual"})
    plugin.living.check_influence("a","u",[plan])
    with pytest.raises(ValueError,match="空闲"):
        plugin.living.check_influence("a","u",[{**plan,"kind":"sleep"}])


async def test_ui_review_returns_before_model_finishes(plugin):
    gate=asyncio.Event()
    async def slow(*args,**kwargs):await gate.wait();return {"success":True,"response":'{"conflicts":[]}'}
    plugin.ctx.llm.generate.side_effect=slow
    queued=await asyncio.wait_for(plugin.ui_change_begin("profile",json.dumps({"name":"小明","session_id":"a"})),0.5)
    assert (await plugin.ui_change_status(queued["review_id"]))["status"]=="检查中"
    gate.set();await asyncio.gather(*list(plugin.jobs))
    assert (await plugin.ui_change_status(queued["review_id"]))["status"]=="已保存"


async def test_auto_profile_reviews_native_identity_and_infers_relationship(plugin):
    plugin.ctx.person = SimpleNamespace(get_value=AsyncMock(return_value='u'))
    plugin.memory.people = AsyncMock(return_value={"items": [{"person_id": "p", "person_name": "小明", "profile_text": "当前恋人"}]})
    decision(plugin, level=3)
    result = await plugin.reviews.submit("profile", {"person_id": "p", "session_id": "a", "name": "",
        "intimacy_mode": "auto", "intimacy": 0, "relationship": "普通关系"})
    assert result["status"] == "已保存"
    assert result["payload"]["name"] == "小明"
    assert "intimacy" not in result["payload"] and "relationship" not in result["payload"]
    person = plugin.store.all("profile")[0]
    assert person["name"] == "小明" and person["intimacy"] == 3

