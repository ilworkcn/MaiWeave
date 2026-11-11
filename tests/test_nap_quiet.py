"""午睡静默、正常回复、唤醒与主动交流的联动。"""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from maibot_sdk.config import rebuild_plugin_config_data

from maiweave.core.models import ExperienceInput, PlanInput
from test_plugin import plugin
from test_sleep_identity import linked_person


@pytest.mark.parametrize("session", ["private", "group"])
@pytest.mark.parametrize("kind,hour,quiet,expected", [
    ("sleep", 13, True, True), ("sleep", 13, False, False),
    ("sleep", 23, False, True), ("rest", 13, True, False),
])
async def test_nap_setting_controls_context_and_send(plugin, session, kind, hour, quiet, expected):
    now = plugin.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    plugin.now = lambda: now
    plugin.config.life.nap_quiet = quiet
    plugin.chat_context.kinds[session] = session
    plugin.engine.create_plan(PlanInput(title="午睡" if kind == "sleep" else "闭目休息", kind=kind,
        start=now.isoformat(), end=(now + timedelta(minutes=20)).isoformat()), "", now)
    policy = json.loads(plugin.context_text(session).split("\n")[-1])["sleep_policy"]
    assert policy["sleeping"] == (kind == "sleep")
    assert policy["quiet"] is expected
    assert await plugin.sleep_before_send({"session_id": session}) == {"action": "abort" if expected else "continue"}
    assert await plugin.sleep_before_send({"session_id": session}, storage_message=False) == {"action": "continue"}


@pytest.mark.parametrize("quiet", [True, False])
async def test_nap_defers_share_until_sleep_finishes(plugin, quiet):
    now = plugin.now().replace(hour=13, minute=0, second=0, microsecond=0)
    plugin.now = lambda: now
    plugin.config.life.nap_quiet = quiet
    plugin.config.proactive.enabled = True
    plugin.config.proactive.session_ids = ["private"]
    plugin.engine.create_plan(PlanInput(title="午睡", kind="sleep", start=now.isoformat(),
        end=(now + timedelta(minutes=20)).isoformat()), "", now)
    plugin.engine.record_experience(ExperienceInput(summary="散步看到了像小船的叶子", importance=6,
        occurred_at=now.isoformat(), source="simulated", evidence="角色模拟"), "private", now)
    await plugin.living.share(now)
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    later = now + timedelta(minutes=21)
    plugin.engine.advance(later)
    assert not plugin.living.quiet_sleep(later)
    await plugin.living.share(later)
    plugin.ctx.maisaka.proactive.trigger.assert_awaited_once()


async def test_nap_wake_chat_extension_goodnight_and_end(plugin):
    await linked_person(plugin)
    now = plugin.now().replace(hour=13, minute=0, second=0, microsecond=0)
    plugin.now = lambda: now
    end = now + timedelta(minutes=40)
    plugin.engine.create_plan(PlanInput(title="午睡", kind="sleep", start=now.isoformat(), end=end.isoformat()), "", now)
    assert not plugin.living.wake_for_message("今天在做什么", "private", now, "u")
    assert plugin.living.quiet_sleep(now)
    assert plugin.living.wake_for_message("陪陪我", "private", now, "u")
    assert not plugin.living.quiet_sleep(now)
    previous = plugin.living.awake_until(now)
    assert plugin.living.wake_for_message("继续聊聊", "group", now + timedelta(minutes=2), "u")
    assert plugin.living.awake_until(now) > previous
    assert plugin.living.wake_for_message("先聊到这里，晚安", "group", now + timedelta(minutes=3), "u")
    assert plugin.living.quiet_sleep(now + timedelta(minutes=3))
    plugin.engine.advance(end)
    assert not plugin.living.quiet_sleep(end)
    plugin.ctx.send.text.assert_not_awaited()


async def test_nap_all_day_mode_and_wake_switch(plugin):
    await linked_person(plugin)
    now = plugin.now().replace(hour=13, minute=0, second=0, microsecond=0)
    plugin.engine.create_plan(PlanInput(title="午睡", kind="sleep", start=now.isoformat(),
        end=(now + timedelta(minutes=20)).isoformat()), "", now)
    plugin.config.life.wake_enabled = False
    assert not plugin.living.wake_for_message("陪陪我", "private", now, "u")
    assert plugin.living.quiet_sleep(now)
    plugin.config.life.schedule_enabled = False
    assert not plugin.living.quiet_sleep(now)


async def test_native_upgrade_adds_nap_setting_and_preserves_values(plugin):
    defaults = plugin.get_default_config()
    old = deepcopy(defaults)
    old["plugin"]["config_version"] = "0.10.1"
    old["life"].pop("nap_quiet")
    old["life"].update(wake_minutes=35, sleep_hour=22, wake_hour=6)
    upgraded = rebuild_plugin_config_data(defaults, old)
    assert upgraded["life"]["nap_quiet"] is True
    assert upgraded["life"]["wake_minutes"] == 35
    assert upgraded["life"]["sleep_hour"] == 22
    assert upgraded["life"]["wake_hour"] == 6
    assert upgraded["plugin"]["config_version"] == "0.11.3"
    upgraded["life"]["nap_quiet"] = False
    assert rebuild_plugin_config_data(defaults, upgraded)["life"]["nap_quiet"] is False
