"""通过排班、生活推进和私聊工具核对角色收支。"""
import json
from datetime import datetime, timedelta

import pytest

from maiweave.core.engine import LifeEngine
from maiweave.core.models import ActivityChoice, ExperienceInput, PlanInput
from test_plugin import plugin
from test_proactive_modes import prepare


@pytest.mark.parametrize("raw,ready", [(0, 0), (1, 0), (0, 1), (1, 1)])
async def test_meal_options_follow_pantry(plugin, raw, ready):
    wallet = plugin.engine.wallet
    with plugin.store.db:
        plugin.store.put("runtime", {"id": "pantry", "raw": raw, "ready": ready})
    options = wallet.meal_options(wallet.pantry())
    assert ("吃留餐" in options) == bool(ready)
    assert ("现做现吃" in options) == bool(raw)
    meal = ActivityChoice(kind="meal", title="午餐", minutes=20, motivation="吃午饭", location="住处")
    for food, available in [("吃留餐", ready), ("现做现吃", raw)]:
        meal.food = food
        if available:
            assert wallet.food_action(meal) == food
        else:
            with pytest.raises(ValueError) as error:
                wallet.food_action(meal)
            assert str(error.value).endswith(options)


@pytest.mark.parametrize("plugin", ["默认账单"], indirect=True)
async def test_default_monthly_bills(plugin):
    from maibot_sdk.config import rebuild_plugin_config_data

    defaults = plugin.get_default_config()
    for field, bill, amount in (("monthly_rent", "rent", 1600), ("monthly_utilities", "utilities", 180)):
        assert defaults["life"][field] == amount
        assert plugin.store.get("runtime", bill)["amount"] == amount
    saved = {"life": {"monthly_rent": 0, "monthly_utilities": 0}}
    rebuilt = rebuild_plugin_config_data(defaults, saved)
    assert rebuilt["life"]["monthly_rent"] == rebuilt["life"]["monthly_utilities"] == 0


async def test_groceries_cooking_and_leftovers(plugin):
    from maiweave.core.models import ActivityChoice
    from maiweave.core.wallet import Wallet
    w, st, now = plugin.engine.wallet, plugin.store, plugin.now()
    w.set_opening(100)
    for index, (kind, action, cost, bought, raw, ready) in enumerate([
        ("leisure", "", 18, 3, 6, 0), ("cook", "备料", 0, 0, 6, 0),
        ("cook", "做好留餐", 0, 0, 5, 1), ("meal", "吃留餐", 0, 0, 5, 0),
        ("meal", "现做现吃", 0, 0, 4, 0), ("meal", "外食", 10, 0, 4, 0),
    ]):
        choice = ActivityChoice(kind=kind, food=action, groceries=bought, cost=cost, title="生活安排",
                                minutes=20, location="住处", motivation="午餐", research_freshness="")
        key = str(index)
        if action == "吃留餐":
            with st.db:
                st.put("plan", {"id": "午饭", "food": "做好留餐"})
                st.put("experience", {"id": "饭已出锅", "plan_id": "午饭"})
                st.put("activity_memory", {"id": "趁热吃", "experience_id": "饭已出锅"})
            choice = choice.model_copy(update={"continuation_id": "趁热吃", "food": "现做现吃"})
            assert w.food_action(choice) == "吃留餐"
        plan = {**choice.model_dump(), "food": w.food_action(choice), "id": key, "experience_id": key}
        with st.db:
            st.put("experience", {"id": key, "details": {}})
            w.settle(plan, now)
            w.settle(plan, now)
        assert Wallet(st).pantry() == {"id": "pantry", "raw": raw, "ready": ready}
    assert w.account()["balance"] == 72
    assert st.count("payment") == 2
    with pytest.raises(ValueError, match="没有已经做好的"):
        w.food_action(choice.model_copy(update={"food": "吃留餐"}))


async def test_utilities_and_seven_day_spending_room(plugin):
    w = plugin.engine.wallet
    start = datetime.fromisoformat("2028-01-23T09:00:00+08:00")
    w.set_opening(100)
    w.configure_rent(70, 31, start, 40)
    meals = [{"end": (start - timedelta(days=i)).isoformat(), "kind": "meal", "cost": 18} for i in range(1, 8)]
    assert w.spending_room(start, meals) == {"近期账单": 0, "一周饭钱估计": 126, "预留后余款": -26}
    assert w.spending_room(start + timedelta(days=1), meals)["近期账单"] == 110
    due = start.replace(day=31)
    w.refresh(due)
    w.refresh(due)
    assert w.account()["balance"] == 30
    assert w.spending_room(due, [])["近期账单"] == 40
    request, = w.pending(due)
    assert request["amount"] == 10
    w.notice(request, "甲", "店员")
    w.respond(request["id"], "甲", "店员", "give", due)
    w.refresh(due)
    assert w.account()["balance"] == 0
    assert plugin.store.get("payment", "生活杂费:2028-01")["amount"] == -40
    assert w.spending_room(due, [])["近期账单"] == 0
    w.configure_rent(0, 31, due, 40)
    assert "2028-02-29" in w.rent_note()
    next_due = due.replace(month=2, day=29)
    with plugin.store.db:
        w._post("工钱:二月", 55, "书店工钱", next_due)
    w.refresh(next_due)
    w.refresh(next_due)
    assert w.account()["balance"] == 15
    assert not plugin.store.find("payment", "房租:2028-02")
    assert plugin.store.get("payment", "生活杂费:2028-02")["amount"] == -40


async def test_monthly_rent_loan_and_payday(plugin):
    w, store = plugin.engine.wallet, plugin.store
    now = datetime.fromisoformat("2028-01-31T09:00:00+08:00")
    w.set_opening(45)
    w.configure_rent(60, 31, now)
    w.refresh(now)
    assert w.account()["balance"] == 45 and "还差 15" in w.rent_note()
    request, = w.pending(now)
    assert w.planning_note(now) == "暂无"
    w.notice(request, "private", "friend")
    w.respond(request["id"], "private", "friend", "lend", now)
    assert w.account()["balance"] == 0 and "2028-02-29" in w.rent_note()
    assert store.get("money_request", request["id"])["owed"] == 15
    w.refresh(now)
    with store.db:
        store.put("experience", {"id": "shift", "details": {}})
        w.settle({"id": "shift", "title": "值班", "experience_id": "shift", "pay": 75}, now)
    assert store.get("money_request", request["id"])["status"] == "已还清"
    from maiweave.core.wallet import Wallet
    resumed = Wallet(store)
    later = now.replace(month=3)
    resumed.configure_rent(60, 31, later)
    resumed.refresh(later)
    resumed.refresh(later)
    assert resumed.account()["balance"] == 0
    assert store.get("payment", "房租:2028-02")["amount"] == -60
    assert "2028-03 房租待缴" in resumed.rent_note()
    resumed.configure_rent(0, 1, later)
    with store.db:
        store.put("experience", {"id": "shift2", "details": {}})
        resumed.settle({"id": "shift2", "title": "值班", "experience_id": "shift2", "pay": 70}, later)
    resumed.refresh(later + timedelta(days=50))
    assert resumed.account()["balance"] == 10 and resumed.rent_note() == ""
    assert len([p for p in store.all("payment") if p["id"].startswith("房租:")]) == 3


async def test_rent_starts_at_next_due_day(plugin):
    w = plugin.engine.wallet
    now = datetime.fromisoformat("2027-12-20T09:00:00+08:00")
    w.set_opening(100)
    w.configure_rent(30, 1, now)
    w.refresh(now)
    assert w.account()["balance"] == 100 and "2028-01-01" in w.rent_note()
    jan = now.replace(year=2028, month=1, day=1)
    w.refresh(jan)
    w.configure_rent(40, 20, jan)
    w.refresh(jan.replace(day=20))
    assert w.account()["balance"] == 70 and "2028-02-20" in w.rent_note()


async def test_shift_move_leave_and_settlement_after_restart(plugin, monkeypatch):
    p = plugin
    now = p.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    monkeypatch.setattr(p, "now", lambda: now)
    p.engine.wallet.set_opening(20)
    await p.ui_save_place("花店")
    await p.ui_save_route("住处", "花店", 10, "公交", fare=2)
    rule = await p.ui_save_routine("花店早班", now.date().isoformat(), "10:00", 60,
                                  frequency="daily", kind="work", location="花店", pay=50)
    shifts = sorted((r for r in p.store.all("plan") if r.get("routine_id") == rule["id"]), key=lambda r: r["start"])
    shift = shifts[0]
    await p.ui_update_plan(shift["id"], new_start=now.replace(hour=14).isoformat(), new_end=now.replace(hour=15).isoformat())
    assert p.store.get("plan", shift["travel_id"])["status"] == "cancelled"
    await p.ui_routine_action(rule["id"], "skip", shifts[1]["routine_date"])
    assert p.store.get("plan", shifts[1]["id"])["status"] == "cancelled"
    page = await p.ui_space()
    assert page["shifts"][0]["at"] == now.replace(hour=14).isoformat()
    assert page["wallet"]["balance"] == 20
    after = now.replace(hour=15, minute=1)
    p.engine.advance(after)
    assert sorted(r["amount"] for r in p.store.all("payment")) == [-2, 50]
    assert p.engine.wallet.account()["balance"] == 68
    resumed = LifeEngine(p.store, p.engine.timezone, after)
    resumed.advance(after + timedelta(days=1))
    assert resumed.wallet.account()["balance"] == 68
    monkeypatch.setattr(p, "now", lambda: after)
    await p.ui_save_routine("花店晚班", now.date().isoformat(), "17:00", 30, frequency="daily",
                            kind="work", location="花店", pay=30, rule_id=rule["id"])
    assert len([r for r in p.store.all("plan") if r.get("routine_date") == shift["routine_date"] and r["status"] != "cancelled"]) == 1


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
async def test_private_loan_to_wages_and_repayment(plugin, monkeypatch, mode):
    p = plugin
    now = prepare(p, monkeypatch)
    monkeypatch.setattr(p, "now", lambda: now)
    p.config.proactive.mode = mode
    p.config.proactive.session_ids = ["group", "chat-a", "chat-b"]
    peers = [{"stream_id": s, "user_id": s, "platform": "qq", "group_id": "group" if s=="group" else ""}
             for s in p.config.proactive.session_ids]
    p.ctx.chat.get_all_streams.return_value = peers
    for peer in peers:
        with p.store.db:
            p.store.put("profile", {"id": peer["stream_id"], "name": "小林", "session_id": peer["stream_id"],
                "user_id": peer["user_id"], "intimacy_mode": "manual", "intimacy": 2})
    p.engine.wallet.set_opening(2)
    wallet = p.engine.wallet
    wallet.need("买毛线", 10, "", now)
    await p.living.share(now)
    row, = p.store.all("money_request")
    assert row["session"] == "chat-a" and row["amount"] == 8
    if mode == "主动表达":
        p.ctx.maisaka.reply.generate.assert_not_awaited()
        assert p.ctx.send.text.await_args.args == ("买毛线还差8虚拟元，能借我一下吗？下次领了工钱就还你。", "chat-a")
        assert p.wallet_summary()["loans"][0]["status"] == "等对方回复"
        assert p.store.get("money_request", row["id"])["status"] == "待询问"
    assert [r["session"] for r in p.store.all("share_receipt") if r.get("record_id") == "wallet:"+row["id"]] == ["chat-a"]
    key = row["id"]
    async def respond(action, session="chat-a"):
        return await p.wallet_action(action, key, stream_id=session, user_id=session)
    assert not (await respond("lend", "group"))["success"]
    assert not (await respond("lend", "chat-b"))["success"]
    assert (await respond("promise"))["content"]["status"] == "答应稍后给"
    assert wallet.account()["balance"] == 2
    assert (await respond("lend"))["content"]["owed"] == 8
    await respond("lend")
    assert wallet.account()["balance"] == 10
    for i, pay in enumerate([5, 10]):
        rule = await p.ui_save_routine("整理花束", now.date().isoformat(), f"{14+i}:00", 30, frequency="daily",
                                      kind="work", pay=pay, location="住处", simulation=False)
        plan = next(r for r in p.store.all("plan") if r.get("routine_id")==rule["id"])
        end = p.engine.time(plan["end"])
        p.engine.record_experience(ExperienceInput(summary="花束已经包好", occurred_at=end.isoformat(), source="reported",
                                  evidence="已下班", plan_id=plan["id"]), "", end)
        assert p.store.get("money_request",key)["owed"] == max(0, 8 - sum([5,10][:i+1]))
    assert wallet.account()["balance"] == 17
    assert sorted(r["amount"] for r in p.store.all("payment")) == [-5, -3, 5, 8, 10]
    p.ctx.maisaka.proactive.trigger.return_value = {"success": True, "task_id": "proactive:ilworkcn.maiweave:2"}
    await p.living.share(now + timedelta(hours=4))
    returned, = [r for r in p.store.all("share_receipt") if r.get("record_id") == "repayment:" + key]
    assert returned["session"] == "chat-a" and "还清" in returned["topic"]


async def test_two_lenders_repaid_in_deposit_order(plugin):
    w, store, now = plugin.engine.wallet, plugin.store, plugin.now()
    # 甲先申请，乙先到账。两笔借款都从原私聊确认。
    w.set_opening(0)
    w.need("画布", 20, "甲", now - timedelta(days=1))
    first, = w.pending(now - timedelta(days=1))
    w.notice(first, "甲", "甲")
    w.respond(first["id"], "甲", "甲", "promise", now - timedelta(days=1))
    # 独立的第二笔申请先收到钱。
    second = {**first, "id": "乙的申请", "date": now.date().isoformat(), "source_session": "乙",
              "session": "乙", "user": "乙", "title": "花纸", "price": 12, "amount": 12}
    with store.db:
        store.put("money_request", second)
    w.respond("乙的申请", "乙", "乙", "lend", now)
    with store.db:
        w._post("消费:花纸", -12, "花纸", now, "乙")
    w.respond(first["id"], "甲", "甲", "lend", now + timedelta(minutes=1))
    with store.db:
        store.put("experience", {"id": "书店班", "details": {}})
        shift = {"id": "书店班", "experience_id": "书店班", "title": "值班工钱", "pay": 19}
        w.settle(shift, now + timedelta(hours=1))
        w.settle(shift, now + timedelta(hours=1))
    assert store.get("money_request", "乙的申请")["owed"] == 0
    assert store.get("money_request", first["id"])["owed"] == 13
    assert [r["amount"] for r in w.recent_payments("", "")] == [19]
    progress = {"record_id": "installment:13:" + first["id"], "session": "甲"}
    assert w.current_notice(progress, now)
    assert not w.current_notice({**progress, "session": "乙"}, now)
    with store.db:
        w.settle({**shift, "id": "第二班", "pay": 13}, now + timedelta(hours=3))
    assert not w.current_notice(progress, now)


async def test_lunch_before_returning_to_work(plugin, monkeypatch):
    p = plugin
    now = p.now().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
    p.config.life.autonomous = True
    with p.store.db:
        p.store.put("state", {**p.engine.state(), "location": "图书馆", "updated_at": now.isoformat()})
        p.store.put("runtime", {"id": "pantry", "raw": 0, "ready": 1})
    p.engine.create_plan(PlanInput(title="午间值班", kind="work", location="图书馆",
                                   start=(now + timedelta(minutes=30)).isoformat(),
                                   end=(now + timedelta(hours=3)).isoformat()), "", now)

    async def choose(ctx, store, config, feature, prompt, model, validate):
        choice = ActivityChoice(title="吃午饭", kind="meal", minutes=20, motivation="午休吃饭",
                                location="图书馆", food="吃留餐")
        with pytest.raises(ValueError, match="冰箱在家里"):
            validate(choice)
        with pytest.raises(ValueError, match="往返路程"):
            validate(choice.model_copy(update={"location": "住处"}))
        meal = choice.model_copy(update={"food": "外食", "cost": 15, "title": "在馆里吃外卖"})
        validate(meal)
        return meal

    monkeypatch.setattr("maiweave.life.living.generate", choose)
    await p.living.choose_activity(now)
    meal, = [plan for plan in p.store.all("plan") if plan["kind"] == "meal"]
    p.engine.advance(p.engine.time(meal["end"]))
    assert p.engine.wallet.pantry()["ready"] == 1
    assert p.engine.wallet.account()["balance"] == p.config.life.opening_balance - 15


async def test_paid_opportunity_uses_saved_place_and_experience(plugin, monkeypatch):
    p = plugin
    now = p.now().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
    p.config.life.autonomous = True
    with p.store.db:
        p.store.put("activity_memory", {"id":"零工", "experience_id":"花店见闻", "next_action":"店主邀我帮忙包花",
            "activity":"做手工", "subject":"包花", "session_id":"", "kind":"leisure", "location":"图书馆",
            "updated_at":now.isoformat(), "hourly_pay":30})
    p.ctx.llm.generate.side_effect = None
    p.ctx.llm.generate.return_value = {"success":True,"response":json.dumps(dict(title="帮忙包花", kind="work",
        location="图书馆", continuation_id="零工", minutes=60, motivation="接上上次的约定"))}
    await p.living.choose_activity(now)
    job, = [r for r in p.store.all("plan") if r["kind"]=="work"]
    assert job["pay"] == 30 and job["continuation_experience_id"] == "花店见闻"
    p.engine.advance(p.engine.time(job["end"]))
    assert p.engine.wallet.account()["balance"] == p.config.life.opening_balance + 30

@pytest.mark.parametrize("action, credited", [("give", 8), ("decline", 0)])
async def test_shortfall_purchase_and_gift(plugin, monkeypatch, action, credited):
    p = plugin
    now = p.now().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
    monkeypatch.setattr(p, "now", lambda: now)
    p.config.life.autonomous = True
    with p.store.db:
        p.store.put("state", {**p.engine.state(), "updated_at": now.isoformat()})
    p.engine.wallet.set_opening(2)
    p.ctx.llm.generate.side_effect = None
    p.ctx.llm.generate.return_value = {"success": True, "response": json.dumps(dict(title="买毛线", location="住处",
        kind="leisure", minutes=15, cost=10, motivation="给兔子织围巾"))}
    assert "暂缓" in await p.living.choose_activity(now)
    assert not p.store.all("plan")
    request, = p.engine.wallet.pending(now)
    notice = p.engine.wallet.notice(request, "chat-a", "同伴")
    receipt = {"record_id": notice[0], "session": "chat-a"}
    assert p.engine.wallet.current_notice(receipt, now)
    result = await p.wallet_action(action, request["id"], stream_id="chat-a", user_id="同伴")
    assert result["success"] and result["content"]["owed"] == 0
    assert not p.engine.wallet.current_notice(receipt, now)
    if credited:
        await p.living.choose_activity(now)
        plan, = p.store.all("plan")
        end = p.engine.time(plan["end"])
        assert p.engine.wallet.account()["balance"] == 10
        p.engine.advance(end)
        p.engine.advance(end + timedelta(seconds=10))
        assert p.engine.wallet.account()["balance"] == 0
        assert p.store.all("experience")[-1]["details"]["生活支出"] == "10虚拟元"
        assert p.engine.wallet.planning_note(end) == "暂无"
    else:
        p.engine.wallet.need("买毛线", 10, "", now + timedelta(days=1))
        assert len(p.store.all("money_request")) == 1
        assert not p.store.all("payment")


async def test_opening_balance_config_reload(plugin):
    p = plugin
    cfg = p.get_plugin_config_data()
    cfg["life"]["opening_balance"] = 0
    p.set_plugin_config(cfg)
    await p.on_config_update("self", cfg, "0.12.6")
    assert (await p.ui_dashboard())["wallet"]["balance"] == 0
    with p.store.db:
        p.engine.wallet._post("测试工钱", 35, "工钱", p.now())
    cfg["life"]["opening_balance"] = 200
    cfg["life"]["currency_name"] = "麦币"
    p.set_plugin_config(cfg)
    await p.on_config_update("self", cfg, "0.12.6")
    dashboard = await p.ui_dashboard()
    assert dashboard["wallet"]["balance"] == 35
    assert dashboard["currency_name"] == "麦币"
    wallet = p.engine.wallet
    wallet.need("花束包装纸", 40, "", p.now())
    request, = wallet.pending(p.now())
    assert "5麦币" in wallet.notice(request, "chat-a", "伙伴")[1]

@pytest.mark.parametrize("case", ["缺车费", "步行绕行", "刚好够车费", "离线跨日", "补上步道"])
async def test_rent_and_next_day_commute(plugin, monkeypatch, case):
    now = plugin.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(plugin, "now", lambda: now)
    wallet = plugin.engine.wallet
    wallet.set_opening(62 if case == "刚好够车费" else 60)
    await plugin.ui_save_place("街角花房")
    await plugin.ui_save_route("住处", "街角花房", 5, "公交", 2)
    if case == "步行绕行":
        await plugin.ui_save_route("楼下街道", "街角花房", 12, "步行")
    day = tomorrow.date().isoformat()
    rule = await plugin.ui_save_routine("花房早班", day, "09:00", 30, frequency="daily", until=day,
                                        kind="work", location="街角花房", pay=50)
    shift_id = next(p["id"] for p in plugin.store.all("plan") if p.get("routine_id") == rule["id"])
    if case == "离线跨日":
        later = await plugin.ui_save_routine("花房晚班", day, "10:00", 15, frequency="daily", until=day,
                                            kind="work", location="街角花房", pay=20)
    wallet.configure_rent(60, now.day, now)
    if case != "离线跨日":
        wallet.refresh(now)
        plugin.spatial.reconcile(now)
    def read_map():
        return plugin.life_map(sorted(plugin.store.all("plan"), key=lambda p: p["start"]))
    if case == "缺车费":
        assert read_map()["location"] == "住处" and "车费" in read_map()["shifts"][0]["blocking_reason"]
    if case == "补上步道":
        assert plugin.store.get("plan", shift_id)["status"] == "blocked"
        await plugin.ui_save_route("楼下街道", "街角花房", 12, "步行")
        plugin.spatial.reconcile(now)
    if case in {"步行绕行", "补上步道"}:
        assert read_map()["next_stop"]["route"]["minutes"] == 15
    if case == "刚好够车费":
        now = tomorrow - timedelta(minutes=5)
        plugin.engine.advance(now)
        assert wallet.account()["balance"] == 0 and read_map()["journey"]["mode"] == "公交"
    now = tomorrow + timedelta(minutes=76 if case == "离线跨日" else 31)
    plugin.engine.advance(now)
    state = plugin.engine.state()
    shift = plugin.store.get("plan", shift_id)
    entries = [r["amount"] for r in plugin.store.all("payment")]
    if case in {"缺车费", "离线跨日"}:
        assert shift["status"] == "blocked" and "车费" in shift["blocking_reason"]
        assert state["location"] == "住处" and entries == [-60]
    else:
        assert shift["status"] == "completed" and state["location"] == "街角花房"
        assert entries == ([-60, -2, 50] if case == "刚好够车费" else [-60, 50])
    if case == "离线跨日":
        assert next(p for p in plugin.store.all("plan") if p.get("routine_id") == later["id"])["status"] == "blocked"
    plugin.engine.advance(now + timedelta(seconds=30))
    assert len(plugin.store.all("payment")) == len(entries)

@pytest.mark.parametrize("minutes_before,can_work", [(6, True), (5, True), (4, False)])
async def test_private_fare_loan_resumes_original_shift(plugin, monkeypatch, minutes_before, can_work):
    p = plugin
    now = p.now()
    start = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(p, "now", lambda: now)
    await p.ui_save_place("花房")
    await p.ui_save_route("住处", "花房", 5, "公交")
    wallet = p.engine.wallet
    wallet.set_opening(60)
    rule = await p.ui_save_routine("花房早班", str(start.date()), "09:00", 60, frequency="daily",
                                   until=str(start.date()), kind="work", location="花房", session_id="private")
    shift = next(r for r in p.store.all("plan") if r.get("routine_id") == rule["id"])
    wallet.configure_rent(60, now.day, now)
    wallet.refresh(now)
    p.spatial.reconcile(now)
    now = start - timedelta(minutes=minutes_before)
    await p.wallet_action("list", stream_id="private", user_id="friend")
    offer, = wallet.commute_requests("private", "friend", now)
    assert not wallet.commute_requests("other", "friend", now)
    result = await p.wallet_action("lend", offer["id"], 2, stream_id="private", user_id="friend")
    assert result["success"] == can_work
    if can_work:
        assert (await p.wallet_action("lend", offer["id"], 2, stream_id="private", user_id="friend"))["success"]
    now = start + timedelta(hours=1, minutes=1)
    p.engine.advance(now)
    end_shift = p.store.get("plan", shift["id"])
    assert end_shift["status"] == ("completed" if can_work else "blocked")
    assert p.engine.state()["location"] == ("花房" if can_work else "住处")
    assert [r["amount"] for r in p.store.all("payment")] == ([-60, 2, -2, 30, -2] if can_work else [-60])

async def test_rmb_prices_and_shift_defaults(plugin):
    p = plugin
    assert p.engine.wallet.account()["balance"] == p.config.life.opening_balance == 1500
    await p.ui_save_place("车站")
    for mode, fare in [("公交", 2), ("地铁", 3), ("步行", 0)]:
        assert (await p.ui_save_route("住处", "车站", 10, mode))["fare"] == fare
    for price in (0, 6):
        assert (await p.ui_save_route("住处", "车站", 10, "地铁", price))["fare"] == price
    assert (await p.space_tool("save_route", origin="车站", destination="住处", mode="地铁", minutes=12))["fare"] == 6
    day = str((p.now() + timedelta(days=1)).date())
    async def shift(**kwargs):
        return await p.ui_save_routine("花店值班", day, "09:00", 480, frequency="daily", until=day,
                                       location="住处", kind="work", **kwargs)
    rule = await shift()
    assert rule["pay"] == 240
    assert (await shift(rule_id=rule["id"], pay=280))["pay"] == 280
    assert (await shift(rule_id=rule["id"]))["pay"] == 280
    assert (await shift(rule_id=rule["id"], pay=0))["pay"] == 0

async def test_private_wallet_lists_only_visible_posted_payments(plugin):
    p = plugin
    wallet = p.engine.wallet
    now = p.now()
    with p.store.db:
        wallet._post("消费:早餐", -8, "早餐", now)
        wallet._post("工钱:排班", 60, "固定班", now, "private")
        wallet._post("来款:借款", 2, "借车费", now, "private", "同伴")
        wallet._post("消费:另一个约定", -10, "私下送礼", now, "another")
        p.store.put("payment", {"id": "房租:2026-11", "amount": -90, "title": "旧账房租", "at": now.isoformat()})
        p.store.put("payment", {"id": "消费:来源已清理", "amount": -12, "title": "旧账", "at": now.isoformat()})
    result = await p.wallet_action("list", stream_id="private", user_id="同伴")
    assert [r["amount"] for r in result["content"]["payments"]] == [-90, 2, 60, -8]
    assert [r["amount"] for r in wallet.recent_payments("private", "别人")] == [-90, 60, -8]
    assert [r["title"] for r in wallet.recent_payments("another", "别人", 2)] == ["旧账房租", "私下送礼"]
    p.ctx.chat.get_all_streams.return_value = [{"stream_id": "群", "is_group_session": True}]
    group = (await p.wallet_action("list", stream_id="群", user_id="同伴"))["content"]
    assert set(group) == {"payments", "currency_name"}
    assert [r["amount"] for r in group["payments"]] == [-90, -8]
    assert not (await p.wallet_action("lend", "借款", stream_id="群", user_id="同伴"))["success"]
    assert {"wallet", "loans", "payments"}.isdisjoint(await p.space_tool("map", stream_id="群"))
    p.config.context.group_mode = 1
    context = p.chat_context.build("群", text="今天的收支流水呢？")
    assert "旧账房租" in context and "早餐" in context and "借车费" not in context
