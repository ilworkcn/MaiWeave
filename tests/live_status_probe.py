"""实机跨插件验证用入口，通过原生 API 读取 MaiWeave。"""
from maibot_sdk import API, MaiBotPlugin


class StatusProbe(MaiBotPlugin):
    async def on_load(self):
        pass

    async def on_unload(self):
        pass

    async def on_config_update(self, scope, config_data, version):
        pass

    @API("check", description="读取生活状态和两种交流判断", version="1")
    async def check(self):
        return {
            "status": await self.ctx.api.call("ilworkcn.maiweave.get_life_status", version="1"),
            "conversation": await self.ctx.api.call("ilworkcn.maiweave.evaluate_behavior", version="1", behavior="conversation"),
            "proactive": await self.ctx.api.call("ilworkcn.maiweave.evaluate_behavior", version="1", behavior="proactive"),
        }


def create_plugin():
    return StatusProbe()
