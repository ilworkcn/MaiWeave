"""插件配置及 WebUI 中文标签。"""

from typing import Literal
from pydantic import field_validator

from maibot_sdk import Field, PluginConfigBase
from zoneinfo import available_timezones


def setting(label: str, **kwargs):
    """使用 SDK 的字段元数据指定中文显示名称。"""
    extra = kwargs.pop("json_schema_extra", {})
    if label == "生活时区（必填）" or label == "生活时区":
        extra["choices"] = sorted(available_timezones())
    if "（整点）" in label:
        extra["choices"] = list(range(24))
    return Field(json_schema_extra={"label": label, "hint": kwargs.get("description", ""), **extra}, **kwargs)


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件信息"
    config_version: str = setting("配置版本", default="0.11.0", description="配置结构变更时更新。当前安装的插件版本见页面顶部。", json_schema_extra={"disabled": True})


class LifeConfig(PluginConfigBase):
    """先设置角色与作息，再按需要调整模型和更新频率。"""

    __ui_label__ = "角色生活"
    enabled: bool = setting("启用生活引擎", default=True, description="控制生活推进和对话工具")
    autonomous: bool = setting("自主安排活动", default=True, description="空闲时由角色选择活动")
    timezone: str = setting("生活时区（必填）", default="Asia/Shanghai", description="已填中国标准时间，可直接使用；其他地区例如 Asia/Tokyo。")
    model_task: str = setting("生活规划模型任务（必填）", default="utils", description="从 MaiBot 已配置的模型任务中选择，默认 utils；任务选项在插件加载时更新。")
    schedule_enabled: bool = setting("遵循日常作息", default=True, description="关闭后切换为全天候模式，已有约定继续执行。")
    nap_quiet: bool = setting("午睡期间保持安静", default=True, description="日间睡眠时保持安静，醒来后接上当前话题。日间按入睡与起床时间之外判断；关闭后可在日间睡眠时回复，唤醒沿用现有设置。")
    wake_enabled: bool = setting("允许聊天唤醒", default=True, description="亲密人物可通过关键词或当前陪伴请求唤醒角色，群聊和私聊共用人物关系。")
    wake_keywords: list[str] = setting("唤醒关键词（选填）", default_factory=lambda: ["心情不好", "难过", "很低落", "睡不着", "想找人聊聊", "陪陪我"], description="每项填写一个词。聊天中出现这些词时唤醒角色；可增删默认词，清空词表可关闭匹配。")
    wake_minutes: int = setting("唤醒后保持清醒（分钟）", default=60, ge=1, le=1440, description="同一个人在群聊或私聊继续聊天都会延长清醒时间；道晚安或时间到期后继续剩余睡眠。")
    sleep_hour: int = setting("习惯入睡时间（整点）", default=23, ge=0, le=23, description="夜间睡眠窗口开始时间")
    wake_hour: int = setting("习惯起床时间（整点）", default=7, ge=0, le=23, description="夜间睡眠窗口结束时间")
    tick_seconds: int = setting("状态更新间隔（秒）", default=60, ge=10, description="每次推进生活的间隔")
    planning_interval_minutes: int = setting("自主规划间隔（分钟）", default=30, ge=5, description="生活情况保持一致时的规划间隔；活动结束、日程与目标变化会触发新规划。")
    background: str = setting("生活背景（选填）", default="", description="留空沿用 MaiBot 人格；可补充居住环境、日常身份和生活偏好。", json_schema_extra={"x-widget": "textarea", "rows": 2, "placeholder": "例如：住在城市近郊，白天工作，晚上喜欢读书。"})
    interests: list[str] = setting("兴趣爱好（选填）", default_factory=lambda: ["阅读", "散步"], description="已有阅读、散步作为参考，可修改或删除；每项填写一个兴趣。")


class ProactiveConfig(PluginConfigBase):
    """启用后向个人资料和额外列表中的聊天流发送消息，提醒和跟进共用时段与额度。"""

    __ui_label__ = "主动交流"
    enabled: bool = setting("启用主动交流", default=False, description="根据重要经历请求 MaiBot 主动开口")
    mode: Literal["自然交流", "主动表达"] = setting("主动交流模式", default="自然交流", description="自然交流：由 Planner 决定是否开口，可能保持沉默。主动表达：选好分享内容后，直接生成并发送。", json_schema_extra={"choices": ["自然交流", "主动表达"]})
    natural_greetings: bool = setting("作息问候", default=False, description="随角色起床和睡前安排问候，使用所选主动交流模式。共用发送额度和间隔，群聊在近期互动后道晚安。")
    session_ids: list[str] = setting("额外接收聊天流（选填）", default_factory=list, description="个人资料中选择的聊天流自动用于主动交流和纪念日提醒。这里填写额外的群聊或其他聊天流；可发送 /maiweave 获取 ID。")
    share_threshold: int = setting("经历分享最低分", default=5, ge=1, le=10, description="分数越高，主动分享的经历越少。生日祝福、约定提醒按各自时间触发。", json_schema_extra={"choices": list(range(1, 11))})
    daily_limit: int = setting("每日主动发送上限", default=4, ge=0, description="每个聊天流每天最多发送几次，分段回复计一次；实际次数取决于当天经历和待跟进事项。")
    cooldown_minutes: int = setting("主动交流间隔（分钟）", default=60, ge=1, description="两次实际发送的最短间隔")
    retry_minutes: int = setting("未发送后重新判断间隔（分钟）", default=60, ge=1, description="选择稍后交流或生成、发送失败后，同一话题等待多久再判断；到达话题截止时间后结束尝试。")
    start_hour: int = setting("允许开始时间（整点）", default=9, ge=0, le=23, description="经历分享与提醒的时间窗口起点；早晚安跟随作息。")
    end_hour: int = setting("允许结束时间（整点）", default=22, ge=0, le=23, description="经历分享与提醒的时间窗口终点；早晚安跟随作息。")


class MemoryConfig(PluginConfigBase):
    """按需连接原生记忆；开启后需要有效的 MaiBot 地址和登录凭据。"""

    __ui_label__ = "原生记忆"
    enabled: bool = setting("启用原生记忆连接", default=False, description="读取和同步 MaiBot A_Memorix")
    base_url: str = setting("MaiBot 网页地址（启用记忆后必填）", default="http://127.0.0.1:8001", description="已填本机默认地址；WebUI 在其他机器或端口时修改。")
    session_cookie: str = setting("网页登录凭据（远程 MaiBot 选填）", default="", description="自动读取 MaiBot 的 host/webui.json 登录；1.3 版本沿用旧凭据路径。远程 1.4 填登录密码，并在网页地址中指定目标 Bot 的代理路径。", json_schema_extra={"x-widget": "password", "input_type": "password", "placeholder": "1.4 填登录密码；1.3 填 maibot_session 值"})


class WorldConfig(PluginConfigBase):
    """天气填写城市即可使用；资讯订阅和联网搜索按需开启。"""

    __ui_label__ = "天气与外部世界"
    weather_enabled: bool = setting("启用实时天气", default=True, description="使用 Open-Meteo 实时天气数据")
    search_enabled: bool = setting("启用 MaiBot 联网搜索", default=False, description="控制地点搜索、联网检索和网页读取；服务在 MaiBot「模型管理 → 功能分配 → 联网搜索」选择。")
    city: str = setting("天气城市（使用天气时必填）", default="大庆", description="角色生活所在城市，天气和地点搜索共用此项。", json_schema_extra={"placeholder": "例如：大庆"})
    country_code: str = setting("国家或地区代码（选填）", default="CN", description="有同名城市时可以补充：中国填 CN，日本填 JP；选填。", json_schema_extra={"placeholder": "例如：CN"})
    places_enabled: bool = setting("按城市自动搜索生活地点", default=False, description="自主规划按活动需求搜索当地场所，如餐厅、咖啡馆或商场；已有合适地点可直接复用；新搜索需要开启 MaiBot 联网搜索总开关。")
    explore_enabled: bool = setting("自主获取见闻", default=False, description="在日程中安排上网了解，结合兴趣、订阅资讯与未完念头选择话题。")
    explore_topics: list[str] = setting("关注主题（选填）", default_factory=list, description="例如本地展览、烹饪、天文；留空时参考角色兴趣与生活接续。")
    explore_daily_limit: int = setting("每日自主检索活动上限", default=3, ge=1, le=20, description="每次活动搜索一个主题，最多阅读两篇网页；手动检索单独执行。")
    explore_interval_minutes: int = setting("自主检索最短间隔（分钟）", default=180, ge=10, description="按检索活动开始时间计算，模型在可用日程中选择时机。")
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
    """睡够设定时长后，角色会记下一段梦境，并带着醒后的心情开始新一天。"""

    __ui_label__ = "睡眠与梦境"
    enabled: bool = setting("启用梦境", default=True, description="足够长的睡眠结束后生成一次梦境")
    mood_effect: bool = setting("梦境影响醒后心情", default=True, description="允许梦境带来小幅情绪变化")
    minimum_hours: float = setting("产生梦境的最低睡眠时长（小时）", default=3, ge=0.5, le=12, description="睡满这个时长后生成梦境")


class ContinuityConfig(PluginConfigBase):
    """选择活动如何影响后续生活；提醒和跟进仍遵循主动交流开关。"""

    __ui_label__ = "生活连贯性"
    outcomes_enabled: bool = setting("生成具体活动结果", default=True, description="独自模拟活动结束后生成细节、感受和下次接续内容")
    semantic_learning: bool = setting("归并同类活动与学习偏好", default=True, description="使用 MaiBot 模型归并不同说法，依据已发生体验更新偏好")
    goal_planning: bool = setting("分解与推进长期目标", default=True, description="将目标拆成可执行阶段，并依据活动结果推进")
    adaptive_schedule: bool = setting("动态调整自主日程", default=True, description="天气、精力与临时事件改变时调整可变动安排")
    reminder_minutes: int = setting("共同安排提前提醒（分钟）", default=30, ge=1, le=120, description="使用已有主动交流开关、时段和额度")
    followup_hours: int = setting("未完成事项跟进间隔（小时）", default=24, ge=1, description="关系中的未解决事项进入一次性跟进队列")


class DiaryConfig(PluginConfigBase):
    """模型会安排写日记的时间，写好的日记可在「日记与梦境」中查看，也可发布到 QQ 空间。"""

    __ui_label__ = "生活日记"
    publish_mode: Literal["关闭", "手动发布", "日记完成后自动发布"] = setting("日记发空间", default="关闭", description="通过已连接的 SnowLuma 发布纯文字生活随笔。自动模式在写日记日程结束后发布当天日记，每天一篇。")
    publish_visibility: Literal["所有人", "好友", "仅自己"] = setting("空间可见范围", default="好友", description="生成文案后可在日记详情预览、编辑和发布。")
    enabled: bool = setting("自动写日记", default=True, description="由生活规划模型安排写日记的时间，进入活动窗口后回顾当天生活")


class BooksConfig(PluginConfigBase):
    __ui_label__ = "书架与阅读"
    max_books: int = setting("最多书籍数", default=100, ge=1, le=1000, description="包含已归档书籍；删除书籍可释放名额。")
    storage_mb: int = setting("书架文件总容量（MB）", default=200, ge=1, le=10240, description="计算原始文件、解析后的正文和上传中的文件。")
    max_file_mb: int = setting("单本文件容量（MB）", default=20, ge=1, le=100, description="同时作为 EPUB 解压内容上限，正文解析后还会检查书架总容量。")
    reading_chars: int = setting("每次阅读正文上限（字）", default=4000, ge=500, le=12000, description="每次读取当前位置的一段正文，下次从保存的位置继续。")
    context_chars: int = setting("对话书架信息上限（字符）", default=4000, ge=1000, le=16000, description="限制书目和近期笔记带入对话的总长度；详细笔记可按书查询。")


class ContextConfig(PluginConfigBase):
    """按聊天场景选择生活信息，详细记录通过工具读取。"""

    __ui_label__ = "聊天上下文"
    @field_validator("group_mode", "private_mode", mode="before")
    @classmethod
    def migrate_mode(cls, value):
        """将已有文字档位对应到滑块的三个位置。"""
        return {"精简": 1, "均衡": 2, "丰富": 3}.get(value, value)

    group_mode: int = setting("群聊信息量", default=1, ge=1, le=3, description="1 精简：当前状态与相关话题；2 均衡：补充近期生活；3 丰富：带入更多细节。实际长度受字符上限约束。", json_schema_extra={"ui_type": "slider", "step": 1})
    private_mode: int = setting("私聊信息量", default=2, ge=1, le=3, description="1 精简：当前状态与相关话题；2 均衡：补充近期生活；3 丰富：带入更多细节。实际长度受字符上限约束。", json_schema_extra={"ui_type": "slider", "step": 1})
    group_chars: int = setting("群聊注入上限（字符）", default=2400, ge=1200, le=20000, description="每次群聊的 Planner 判断或回复，最多自动带入多少字符的 MaiWeave 规则与生活信息（含标点、字段名）。只控制本插件自动注入部分；MaiBot 人设、聊天历史和工具查询结果另计。字符数与 Token 数不同。调低更省上下文，调高能带入更多细节；已有记录照常保存，需要时可通过工具查询。")
    private_chars: int = setting("私聊注入上限（字符）", default=4800, ge=1200, le=20000, description="每次私聊的 Planner 判断或回复，最多自动带入多少字符的 MaiWeave 规则与生活信息（含标点、字段名）。只控制本插件自动注入部分；MaiBot 人设、聊天历史和工具查询结果另计。字符数与 Token 数不同。调低更省上下文，调高能带入更多细节；已有记录照常保存，需要时可通过工具查询。")
    schedule: bool = setting("带入日程", default=True, description="带入后续安排和日程变动；当前活动与睡眠状态始终保留。")
    people: bool = setting("带入人物与纪念日", default=True, description="带入当前聊天人物的近况、关系、生活备注和近期纪念日。")
    reading: bool = setting("带入阅读笔记", default=True, description="点名书籍时优先带入对应笔记，更多内容可通过书架工具查询。")
    continuity: bool = setting("带入生活接续", default=True, description="带入未完话题、活动进度和目标，生活功能继续按各自开关运行。")
    experiences: bool = setting("带入近期经历", default=True, description="按模式补充最近的生活经历，精简模式在聊到经历时带入。")
    sharing: bool = setting("带入主动分享接话", default=True, description="带入本聊天流实际发送的分享和回应，供模型接续话题。")
    environment: bool = setting("带入天气与地点", default=True, description="按话题和模式补充已开启的天气、资讯和城市地点。")
    dreams: bool = setting("带入梦境与习惯", default=True, description="带入已开启的梦境与习惯信息，精简模式在相关话题中使用。")


class RetentionConfig(PluginConfigBase):
    """每天整理已结束的记录，保留正在使用的生活关联。"""

    __ui_label__ = "数据保留"
    enabled: bool = setting("每日自动清理", default=True, description="每天清理一次；经历与记忆页面可预览数量和立即清理。")
    log_days: int = setting("运行记录保留天数", default=7, ge=0, description="主动交流回执、已结束任务、资料核对、情绪变化及已结束跟进记录；0 表示按数量管理。")
    log_count: int = setting("每类运行记录上限", default=1000, ge=0, description="每类分别计数；0 表示按天数管理，两项均为 0 时长期保留。")
    usage_days: int = setting("模型调用明细保留天数", default=30, ge=0, description="Token 统计按保留的明细汇总；0 表示按数量管理。")
    usage_count: int = setting("模型调用明细条数上限", default=10000, ge=0, description="保留最近的调用明细；0 表示按天数管理，两项均为 0 时长期保留。")
    plan_days: int = setting("已结束日程保留天数", default=90, ge=0, description="保留习惯观察期和仍有任务关联的日程；0 表示按数量管理。")
    plan_count: int = setting("已结束日程条数上限", default=2000, ge=0, description="优先清理最早结束的日程；0 表示按天数管理。")
    experience_days: int = setting("普通经历保留天数", default=180, ge=0, description="7分及以上、长期保留和仍用于生活接续的经历继续保存；0 表示按数量管理。")
    experience_count: int = setting("普通经历条数上限", default=3000, ge=0, description="优先清理最早发生的普通经历；0 表示按天数管理。")
    world_days: int = setting("网络资料保留天数", default=30, ge=1, description="资讯、检索、网页与地点搜索缓存的保留时间；活动感想与来源随经历继续保存。")
    world_count: int = setting("每类网络资料条数上限", default=300, ge=20, description="资讯、检索、网页与地点搜索分别计数，优先清理最早获取的资料。")
    history_count: int = setting("每条记录保留历史条数", default=10, ge=0, description="经历修订、关系变化和目标分解分别保留最近的历史；0 表示保留全部版本。")


class MaiWeaveConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    life: LifeConfig = Field(default_factory=LifeConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    world: WorldConfig = Field(default_factory=WorldConfig)
    rhythm: RhythmConfig = Field(default_factory=RhythmConfig)
    dreams: DreamConfig = Field(default_factory=DreamConfig)
    diary: DiaryConfig = Field(default_factory=DiaryConfig)
    continuity: ContinuityConfig = Field(default_factory=ContinuityConfig)
    books: BooksConfig = Field(default_factory=BooksConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
