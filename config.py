"""插件配置及 WebUI 中文标签。"""

from maibot_sdk import Field, PluginConfigBase


def setting(label: str, **kwargs):
    """使用 SDK 的字段元数据指定中文显示名称。"""
    extra = kwargs.pop("json_schema_extra", {})
    return Field(json_schema_extra={"label": label, "hint": kwargs.get("description", ""), **extra}, **kwargs)


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件信息"
    config_version: str = setting("配置版本", default="0.4.0", description="配置版本，由插件维护", json_schema_extra={"disabled": True})


class LifeConfig(PluginConfigBase):
    """先设置角色与作息，再按需要调整模型和更新频率。"""

    __ui_label__ = "角色生活"
    enabled: bool = setting("启用生活引擎", default=True, description="控制生活推进和对话工具")
    autonomous: bool = setting("自主安排活动", default=True, description="空闲时由角色选择活动")
    timezone: str = setting("生活时区（必填）", default="Asia/Shanghai", description="已填中国标准时间，可直接使用；其他地区例如 Asia/Tokyo。")
    model_task: str = setting("生活规划模型任务（必填）", default="utils", description="默认 utils；填写 MaiBot 中已配置的模型任务名称，无需填写密钥。")
    schedule_enabled: bool = setting("遵循日常作息", default=True, description="关闭后不自动安排夜间睡眠，可全天候交流；明确约定的日程仍保留。")
    wake_enabled: bool = setting("允许关键词唤醒", default=True, description="遵循作息时，普通聊天命中关键词可中断睡眠；原消息仍正常进入对话。")
    wake_keywords: list[str] = setting("唤醒关键词（选填）", default_factory=lambda: ["心情不好", "难过", "很低落", "睡不着", "想找人聊聊", "陪陪我"], description="每项一个，按文字包含匹配，不是模型情绪诊断；清空后不触发。可按需要增删默认词。")
    wake_minutes: int = setting("唤醒后保持清醒（分钟）", default=60, ge=1, le=1440, description="期间不自主安排活动；再次命中关键词延长清醒时间，到期后恢复作息。")
    sleep_hour: int = setting("习惯入睡时间（整点）", default=23, ge=0, le=23, description="夜间睡眠窗口开始时间")
    wake_hour: int = setting("习惯起床时间（整点）", default=7, ge=0, le=23, description="夜间睡眠窗口结束时间")
    tick_seconds: int = setting("状态更新间隔（秒）", default=60, ge=10, description="每次推进生活的间隔")
    planning_interval_minutes: int = setting("自主规划间隔（分钟）", default=30, ge=5, description="两次自主规划的最短间隔")
    background: str = setting("生活背景（选填）", default="", description="留空沿用宿主人格；可补充居住环境、日常身份和生活偏好。", json_schema_extra={"x-widget": "textarea", "rows": 2, "placeholder": "例如：住在城市近郊，白天工作，晚上喜欢读书。"})
    interests: list[str] = setting("兴趣爱好（选填）", default_factory=lambda: ["阅读", "散步"], description="已有阅读、散步作为参考，可修改或删除；每项填写一个兴趣。")


class ProactiveConfig(PluginConfigBase):
    """启用后填写接收聊天流，提醒和跟进共用下方时段与额度。"""

    __ui_label__ = "主动交流"
    enabled: bool = setting("启用主动交流", default=False, description="根据重要经历请求宿主主动开口")
    session_ids: list[str] = setting("接收聊天流（启用主动交流后必填）", default_factory=list, description="先在目标聊天发送 /maiweave，再复制回复中的聊天流 ID；每项一个，不是 QQ 号。")
    daily_limit: int = setting("每日主动请求上限", default=2, ge=0, description="每个聊天流每天的请求次数")
    cooldown_minutes: int = setting("主动交流间隔（分钟）", default=180, ge=1, description="两次请求的最短间隔")
    start_hour: int = setting("允许开始时间（整点）", default=9, ge=0, le=23, description="主动交流时间窗口起点")
    end_hour: int = setting("允许结束时间（整点）", default=22, ge=0, le=23, description="不包含这个整点")


class MemoryConfig(PluginConfigBase):
    """按需连接原生记忆；开启后需要有效的宿主地址和登录凭据。"""

    __ui_label__ = "原生记忆"
    enabled: bool = setting("启用原生记忆连接", default=False, description="读取和同步宿主 A_Memorix")
    base_url: str = setting("宿主网页地址（启用记忆后必填）", default="http://127.0.0.1:8001", description="已填本机默认地址；WebUI 在其他机器或端口时修改。")
    session_cookie: str = setting("网页登录凭据（启用记忆后必填）", default="", description="登录 WebUI 后，从浏览器 Cookie 复制 maibot_session 的值；未启用记忆可留空。", json_schema_extra={"x-widget": "password", "input_type": "password", "placeholder": "填写实际的 maibot_session 值"})


class WorldConfig(PluginConfigBase):
    """天气填写城市即可使用；资讯订阅和联网搜索按需开启。"""

    __ui_label__ = "天气与外部世界"
    weather_enabled: bool = setting("启用实时天气", default=True, description="使用 Open-Meteo 实时天气数据")
    search_enabled: bool = setting("启用宿主联网搜索", default=False, description="使用 MaiBot 已配置的统一联网服务，无需另填搜索密钥")
    city: str = setting("天气城市（使用天气时必填）", default="", description="填写角色所在城市；留空不请求天气，示例不会自动作为实际城市。", json_schema_extra={"placeholder": "例如：上海"})
    country_code: str = setting("国家或地区代码（选填）", default="", description="用于区分同名城市；中国填写 CN，日本填写 JP，不需要时留空。", json_schema_extra={"placeholder": "例如：CN"})
    news_enabled: bool = setting("启用订阅资讯", default=False, description="读取已配置的 RSS 或 Atom 信息源")
    feed_urls: list[str] = setting("资讯订阅地址（启用资讯后必填）", default_factory=list, description="每项填写一个真实 RSS 或 Atom 地址，例如 https://站点域名/feed.xml；未启用可留空。")
    weather_minutes: int = setting("天气更新间隔（分钟）", default=60, ge=15, description="实时天气刷新频率")
    news_minutes: int = setting("资讯更新间隔（分钟）", default=120, ge=15, description="资讯刷新频率")


class RhythmConfig(PluginConfigBase):
    """控制日程预排范围和习惯学习；所有数值已有默认值。"""

    __ui_label__ = "重复日程与习惯"
    habits_enabled: bool = setting("启用习惯学习", default=True, description="从完成的活动中学习时间和地点偏好")
    follow_habits: bool = setting("自主活动参考习惯", default=True, description="在习惯时间优先选择已形成的活动")
    learning_days: int = setting("习惯观察窗口（天）", default=21, ge=3, le=90, description="分析最近多少天的活动")
    habit_min_days: int = setting("形成习惯所需天数", default=3, ge=2, description="同类活动至少在多少个不同日期出现")
    horizon_days: int = setting("重复日程预排天数", default=7, ge=1, le=30, description="提前生成未来几天的重复安排")


class DreamConfig(PluginConfigBase):
    """满足睡眠时长后生成梦境；梦境独立保存，不当作现实事实。"""

    __ui_label__ = "睡眠与梦境"
    enabled: bool = setting("启用梦境", default=True, description="足够长的睡眠结束后生成一次梦境")
    mood_effect: bool = setting("梦境影响醒后心情", default=True, description="允许梦境带来小幅情绪变化")
    minimum_hours: float = setting("产生梦境的最低睡眠时长（小时）", default=3, ge=0.5, le=12, description="短暂休息不会自动生成梦境")


class ContinuityConfig(PluginConfigBase):
    """选择活动如何影响后续生活；提醒和跟进仍遵循主动交流开关。"""

    __ui_label__ = "生活连贯性"
    outcomes_enabled: bool = setting("生成具体活动结果", default=True, description="独自模拟活动结束后生成细节、感受和下次接续内容")
    semantic_learning: bool = setting("归并同类活动与学习偏好", default=True, description="使用宿主模型归并不同说法，依据已发生体验更新偏好")
    goal_planning: bool = setting("分解与推进长期目标", default=True, description="将目标拆成可执行阶段，并依据活动结果推进")
    adaptive_schedule: bool = setting("动态调整自主日程", default=True, description="天气、精力与临时事件改变时调整可变动安排")
    reminder_minutes: int = setting("共同安排提前提醒（分钟）", default=30, ge=1, le=120, description="使用已有主动交流开关、时段和额度")
    followup_hours: int = setting("未完成事项跟进间隔（小时）", default=24, ge=1, description="关系中的未解决事项进入一次性跟进队列")


class MaiWeaveConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    life: LifeConfig = Field(default_factory=LifeConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    world: WorldConfig = Field(default_factory=WorldConfig)
    rhythm: RhythmConfig = Field(default_factory=RhythmConfig)
    dreams: DreamConfig = Field(default_factory=DreamConfig)
    continuity: ContinuityConfig = Field(default_factory=ContinuityConfig)
