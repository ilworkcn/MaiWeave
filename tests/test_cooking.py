"""做饭、用餐与身体状态、接续和页面分类的联动。"""
import json
from datetime import timedelta
from pathlib import Path
import pytest
from maiweave.core.models import PlanInput, RoutineInput
from maiweave.interfaces.webui import KIND
from test_plugin import plugin


def start(plugin,hunger=50):
    now=plugin.now().replace(hour=18,minute=0,second=0,microsecond=0)
    plugin.now=lambda:now
    plugin.config.life.schedule_enabled=False
    with plugin.store.db:
        plugin.store.put("state",{**plugin.engine.state(),"updated_at":now.isoformat(),"hunger":hunger,"energy":80})
    return now


@pytest.mark.parametrize("hunger", [50, 70])
async def test_cooking_then_meal_reduces_hunger_only_after_eating(plugin, hunger):
    now=start(plugin,hunger)
    cook=plugin.engine.create_plan(PlanInput(title="做晚饭",kind="cook",start=now.isoformat(),end=(now+timedelta(minutes=30)).isoformat()),"",now)
    end=now+timedelta(minutes=30)
    plugin.engine.advance(end)
    assert plugin.engine.state()["hunger"]==hunger+2.5
    plugin.ctx.llm.generate.return_value={"success":True,"response":json.dumps({"activity":"烹饪","subject":"晚饭","summary":"面条刚出锅，准备坐下来吃","details":{"结果":"面条做好了"},"enjoyment":2,"importance":3,"score_reason":"做好了晚饭","completed":True,"next_action":"坐下来吃刚做好的面条"})}
    await plugin.continuity.reflect(cook["id"],end)
    memory,=plugin.store.all("activity_memory")
    assert memory["kind"]=="cook"
    plugin.config.life.autonomous=True
    plugin.ctx.llm.generate.return_value={"success":True,"response":json.dumps({"title":"吃晚饭","kind":"meal","minutes":20,"location":"住处","motivation":"面条做好了，坐下来吃","continuation_id":memory["id"]})}
    await plugin.living.choose_activity(end)
    meal=plugin.engine.current(end)
    assert meal["kind"]=="meal" and meal["continuation_id"]==memory["id"]
    assert plugin.engine.time(meal["end"])-plugin.engine.time(meal["start"])==timedelta(minutes=20)
    prompt=plugin.ctx.llm.generate.await_args.args[0]
    assert "上次用餐结束：暂无记录" in prompt
    plugin.now=lambda:end+timedelta(minutes=10)
    assert "吃晚饭" in plugin.context_text("private",text="你吃饭了吗")
    plugin.engine.advance(end+timedelta(minutes=19))
    assert plugin.engine.state()["hunger"]>hunger+2.5
    plugin.engine.advance(end+timedelta(minutes=20))
    assert plugin.engine.state()["hunger"]==pytest.approx(max(0,hunger+50/12-55),abs=0.002)
    assert KIND["cook"]=="做饭" and KIND[meal["kind"]]=="用餐"
    assert any(row["activity"]=="烹饪" for row in plugin.living.activity_balance(end+timedelta(minutes=20))["totals"])


@pytest.mark.parametrize("minutes", [10, 25, 45])
async def test_hungry_meal_uses_planned_duration(plugin, minutes):
    now=start(plugin,70)
    plugin.ctx.llm.generate.return_value={"success":True,"response":json.dumps({"title":"吃晚饭","kind":"meal","minutes":minutes,"location":"住处","motivation":"有些饿了，按这顿饭的内容用餐"})}
    plugin.config.life.autonomous=True
    await plugin.living.choose_activity(now)
    plan=plugin.engine.current(now)
    assert plan["kind"]=="meal"
    assert plugin.engine.time(plan["end"])-now==timedelta(minutes=minutes)
    assert plugin.engine.state()["hunger"]==70


@pytest.mark.parametrize("kind,title",[("cook","为明天备菜"),("leisure","洗碗")])
async def test_preparation_and_cleanup_keep_hunger(plugin,kind,title):
    now=start(plugin)
    plugin.engine.create_plan(PlanInput(title=title,kind=kind,start=now.isoformat(),end=(now+timedelta(minutes=20)).isoformat()),"",now)
    plugin.engine.advance(now+timedelta(minutes=20))
    assert plugin.engine.state()["hunger"]>50


async def test_paused_meal_has_not_finished(plugin):
    now=start(plugin)
    p=plugin.engine.create_plan(PlanInput(title="吃晚饭",kind="meal",start=now.isoformat(),end=(now+timedelta(minutes=20)).isoformat()),"",now)
    plugin.engine.advance(now+timedelta(minutes=5))
    plugin.engine.transition(p["id"],"pause","",now+timedelta(minutes=5))
    plugin.engine.advance(now+timedelta(minutes=30))
    assert plugin.engine.state()["hunger"]>50


def test_cook_is_available_to_routine_and_page(plugin):
    now=start(plugin)
    rule=RoutineInput(title="做午饭",kind="cook",frequency="daily",anchor_date=now.date().isoformat(),start_time="19:00",duration_minutes=30)
    saved=plugin.rhythm.save(rule,"",now)
    plugin.rhythm.materialize(now)
    assert any(p["kind"]=="cook" and p.get("routine_id")==saved["id"] for p in plugin.store.all("plan"))
    html=(Path(__file__).parents[1]/"webui/editor.html").read_text("utf-8")
    assert '"value": "cook", "label": "做饭"' in html
    assert "cook:'做饭'" in html
