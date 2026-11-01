"""纪念日历法转换、持久化与提醒。"""

from datetime import date

import pytest

from test_plugin import plugin


async def test_profiles_and_dates_round_trip(plugin):
    person = await plugin.ui_save_profile("小明", "chat-a", "123", "喜欢阅读")
    day = await plugin.ui_save_anniversary(person["id"], "生日", "solar", 2, 29, 2000)
    rows = plugin.anniversaries.upcoming(date(2025, 1, 1), "chat-a")
    assert rows[0]["next_date"] == "2025-02-28"
    assert rows[0]["years"] == 25
    assert plugin.anniversaries.upcoming(date(2025, 1, 1), "chat-b") == []
    await plugin.ui_save_anniversary(person["id"], "生日", "solar", 3, 1, enabled=False, anniversary_id=day["id"])
    await plugin.on_unload()
    await plugin.on_load()
    assert (await plugin.ui_anniversaries())["anniversaries"][0]["enabled"] is False
    await plugin.ui_delete_profile(person["id"])
    assert plugin.store.all("anniversary") == []


async def test_lunar_new_year_and_leap_month_rules(plugin):
    person = await plugin.ui_save_profile("用户", "chat-a")
    first = await plugin.ui_save_anniversary(person["id"], "生日", "lunar", 1, 1)
    assert plugin.anniversaries.occurrence(first, 2024) == date(2024, 2, 10)
    leap = await plugin.ui_save_anniversary(person["id"], "纪念日", "lunar", 2, 1, 2023, True)
    assert plugin.anniversaries.occurrence(leap, 2023) == date(2023, 3, 22)
    assert plugin.anniversaries.occurrence(leap, 2024) == date(2024, 3, 10)
    small = await plugin.ui_save_anniversary(person["id"], "月末", "lunar", 1, 30)
    assert plugin.anniversaries.occurrence(small, 2024) == date(2024, 3, 9)
    rows = plugin.anniversaries.upcoming(date(2024, 1, 1), "chat-a")
    assert next(row for row in rows if row["id"] == first["id"])["next_date"] == "2024-02-10"


@pytest.mark.parametrize("calendar,month,day,year,leap", [("solar", 2, 30, 0, False), ("lunar", 2, 1, 2024, True), ("lunar", 1, 31, 0, False)])
async def test_invalid_dates_are_not_saved(plugin, calendar, month, day, year, leap):
    person = await plugin.ui_save_profile("用户", "chat-a")
    with pytest.raises(ValueError):
        await plugin.ui_save_anniversary(person["id"], "生日", calendar, month, day, year, leap)
    assert plugin.store.all("anniversary") == []


async def test_reminders_use_existing_limits_and_do_not_repeat(plugin):
    now = plugin.now().replace(hour=12)
    person = await plugin.ui_save_profile("小明", "chat-a")
    await plugin.ui_save_anniversary(person["id"], "生日", "solar", now.month, now.day)
    plugin.config.proactive.session_ids = ["chat-a", "chat-b"]
    await plugin.living.share(now)
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    plugin.config.proactive.enabled = True
    await plugin.living.share(now)
    call = plugin.ctx.maisaka.proactive.trigger.await_args
    assert call.args[0] == "chat-a" and "小明的生日" in call.args[1]
    plugin.config.proactive.cooldown_minutes = 1
    await plugin.living.share(now.replace(hour=14))
    assert plugin.ctx.maisaka.proactive.trigger.await_count == 1


async def test_profile_context_is_scoped(plugin):
    await plugin.ui_save_profile("私聊称呼", "chat-a", notes="私人资料")
    assert "私人资料" in plugin.context_text("chat-a")
    assert "私人资料" not in plugin.context_text("chat-b")


async def test_profile_stream_receives_reminder_without_duplicate_configuration(plugin):
    now = plugin.now().replace(hour=12)
    profile = await plugin.ui_save_profile("小明", "chat-profile")
    await plugin.ui_save_anniversary(profile["id"], "生日", "solar", now.month, now.day)
    plugin.config.proactive.enabled = True
    assert plugin.config.proactive.session_ids == []
    await plugin.living.share(now)
    assert plugin.ctx.maisaka.proactive.trigger.await_args.args[0] == "chat-profile"
    plugin.ctx.maisaka.proactive.trigger.reset_mock()
    plugin.config.proactive.session_ids = ["chat-profile", "chat-extra"]
    plugin.store.delete("sharing", "chat-profile")
    await plugin.living.share(now)
    assert plugin.ctx.maisaka.proactive.trigger.await_count == 1
