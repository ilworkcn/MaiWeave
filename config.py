"""插件配置，由 MaiBot 生成配置文件并提供编辑界面。"""

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件信息"
    config_version: str = Field(default="0.1.0", description="配置版本，由插件维护")


class LifeConfig(PluginConfigBase):
    __ui_label__ = "角色生活"
    enabled: bool = Field(default=True, description="启用生活引擎")
    timezone: str = Field(default="Asia/Shanghai", description="角色生活时区")
    tick_seconds: int = Field(default=60, ge=10, description="生活推进间隔，秒")
    autonomous: bool = Field(default=True, description="空闲时自主安排虚拟活动")
    interests: list[str] = Field(default_factory=lambda: ["阅读", "散步"], description="角色兴趣")
    background: str = Field(default="", description="补充生活背景，留空沿用宿主人格")
    sleep_hour: int = Field(default=23, ge=0, le=23, description="习惯入睡时间，整点")
    wake_hour: int = Field(default=7, ge=0, le=23, description="习惯起床时间，整点")
    model_task: str = Field(default="utils", description="自主活动规划使用的宿主模型任务")
    planning_interval_minutes: int = Field(default=30, ge=5, description="自主规划最小间隔，分钟")


class ProactiveConfig(PluginConfigBase):
    __ui_label__ = "主动交流"
    enabled: bool = Field(default=False, description="允许基于已完成经历主动交流")
    session_ids: list[str] = Field(default_factory=list, description="允许主动交流的真实聊天流 ID")
    daily_limit: int = Field(default=2, ge=0, description="每个聊天流每天最多请求开口次数")
    cooldown_minutes: int = Field(default=180, ge=1, description="两次主动请求间隔，分钟")
    start_hour: int = Field(default=9, ge=0, le=23, description="可主动交流的开始整点")
    end_hour: int = Field(default=22, ge=0, le=23, description="可主动交流的结束整点，不包含")


class MemoryConfig(PluginConfigBase):
    __ui_label__ = "原生记忆"
    enabled: bool = Field(default=False, description="通过官方 WebUI 接口读取和同步 A_Memorix")
    base_url: str = Field(default="http://127.0.0.1:8001", description="MaiBot WebUI 地址")
    session_cookie: str = Field(default="", description="已登录 WebUI 的 maibot_session Cookie 值")


class MaiWeaveConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    life: LifeConfig = Field(default_factory=LifeConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
