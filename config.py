"""插件配置及 WebUI 中文标签。"""

from maibot_sdk import Field, PluginConfigBase


def setting(label: str, **kwargs):
    """使用 SDK 的字段元数据指定中文显示名称。"""
    extra = kwargs.pop("json_schema_extra", {})
    return Field(json_schema_extra={"label": label, **extra}, **kwargs)


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件信息"
    config_version: str = setting("配置版本", default="0.2.0", description="配置版本，由插件维护")


class LifeConfig(PluginConfigBase):
    __ui_label__ = "角色生活"
    enabled: bool = setting("启用生活引擎", default=True, description="控制生活推进和对话工具")
    timezone: str = setting("生活时区", default="Asia/Shanghai", description="用于日程、作息和相对日期")
    tick_seconds: int = setting("状态更新间隔（秒）", default=60, ge=10, description="每次推进生活的间隔")
    autonomous: bool = setting("自主安排活动", default=True, description="空闲时由角色选择活动")
    interests: list[str] = setting("兴趣爱好", default_factory=lambda: ["阅读", "散步"], description="自主活动的兴趣来源")
    background: str = setting("生活背景", default="", description="补充角色的生活背景，留空沿用宿主人格")
    sleep_hour: int = setting("习惯入睡时间（整点）", default=23, ge=0, le=23, description="夜间睡眠窗口开始时间")
    wake_hour: int = setting("习惯起床时间（整点）", default=7, ge=0, le=23, description="夜间睡眠窗口结束时间")
    model_task: str = setting("生活规划模型任务", default="utils", description="使用 MaiBot 已配置的模型任务")
    planning_interval_minutes: int = setting("自主规划间隔（分钟）", default=30, ge=5, description="两次自主规划的最短间隔")


class ProactiveConfig(PluginConfigBase):
    __ui_label__ = "主动交流"
    enabled: bool = setting("启用主动交流", default=False, description="根据重要经历请求宿主主动开口")
    session_ids: list[str] = setting("接收聊天流", default_factory=list, description="填写 /maiweave 显示的真实聊天流 ID")
    daily_limit: int = setting("每日主动请求上限", default=2, ge=0, description="每个聊天流每天的请求次数")
    cooldown_minutes: int = setting("主动交流间隔（分钟）", default=180, ge=1, description="两次请求的最短间隔")
    start_hour: int = setting("允许开始时间（整点）", default=9, ge=0, le=23, description="主动交流时间窗口起点")
    end_hour: int = setting("允许结束时间（整点）", default=22, ge=0, le=23, description="不包含这个整点")


class MemoryConfig(PluginConfigBase):
    __ui_label__ = "原生记忆"
    enabled: bool = setting("启用原生记忆连接", default=False, description="读取和同步宿主 A_Memorix")
    base_url: str = setting("宿主网页地址", default="http://127.0.0.1:8001", description="MaiBot WebUI 的地址")
    session_cookie: str = setting("网页登录凭据", default="", description="已登录 WebUI 的 maibot_session Cookie 值", json_schema_extra={"input_type": "password"})


class WorldConfig(PluginConfigBase):
    __ui_label__ = "天气与外部世界"
    weather_enabled: bool = setting("启用实时天气", default=True, description="使用 Open-Meteo 实时天气数据")
    city: str = setting("天气城市", default="", description="填写角色所在城市；留空不请求天气", json_schema_extra={"placeholder": "例如：上海"})
    country_code: str = setting("国家或地区代码", default="", description="可选两字母代码，用于区分同名城市，例如 CN")
    weather_minutes: int = setting("天气更新间隔（分钟）", default=60, ge=15, description="实时天气刷新频率")
    news_enabled: bool = setting("启用订阅资讯", default=False, description="读取已配置的 RSS 或 Atom 信息源")
    feed_urls: list[str] = setting("资讯订阅地址", default_factory=list, description="每项填写一个 RSS 或 Atom 地址")
    news_minutes: int = setting("资讯更新间隔（分钟）", default=120, ge=15, description="资讯刷新频率")
    search_enabled: bool = setting("启用宿主联网搜索", default=False, description="使用 MaiBot 已配置的统一联网服务，无需另填搜索密钥")


class RhythmConfig(PluginConfigBase):
    __ui_label__ = "重复日程与习惯"
    horizon_days: int = setting("重复日程预排天数", default=7, ge=1, le=30, description="提前生成未来几天的重复安排")
    habits_enabled: bool = setting("启用习惯学习", default=True, description="从完成的活动中学习时间和地点偏好")
    learning_days: int = setting("习惯观察窗口（天）", default=21, ge=3, le=90, description="分析最近多少天的活动")
    habit_min_days: int = setting("形成习惯所需天数", default=3, ge=2, description="同类活动至少在多少个不同日期出现")
    follow_habits: bool = setting("自主活动参考习惯", default=True, description="在习惯时间优先选择已形成的活动")


class DreamConfig(PluginConfigBase):
    __ui_label__ = "睡眠与梦境"
    enabled: bool = setting("启用梦境", default=True, description="足够长的睡眠结束后生成一次梦境")
    minimum_hours: float = setting("产生梦境的最低睡眠时长（小时）", default=3, ge=0.5, le=12, description="短暂休息不会自动生成梦境")
    mood_effect: bool = setting("梦境影响醒后心情", default=True, description="允许梦境带来小幅情绪变化")


class MaiWeaveConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    life: LifeConfig = Field(default_factory=LifeConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    world: WorldConfig = Field(default_factory=WorldConfig)
    rhythm: RhythmConfig = Field(default_factory=RhythmConfig)
    dreams: DreamConfig = Field(default_factory=DreamConfig)
