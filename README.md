<div align="center">

![MaiWeave：聊天之外，也有自己的生活](assets/banner.png)

# MaiWeave · 麦织

**把你们的对话，织进持续发生的生活。**

与你的约定、窗外的天气、读过的书，都能成为下一次相遇的话题。

[![Release](https://img.shields.io/github/v/release/ilworkcn/MaiWeave?label=Release&color=7ba989)](https://github.com/ilworkcn/MaiWeave/releases)
[![MaiBot](https://img.shields.io/badge/MaiBot-1.3.19–1.3.x-7ba989)](https://docs.maibot.chat/)
[![SDK](https://img.shields.io/badge/SDK-2.21.x-79a7b8)](https://github.com/Mai-with-u/maibot-plugin-sdk)
[![Python](https://img.shields.io/badge/Python-3.11%2B-79a7b8?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/License-GPL--3.0-orange)](LICENSE)

[![Stars](https://img.shields.io/github/stars/ilworkcn/MaiWeave?style=flat&label=Stars&color=e8b44c)](https://github.com/ilworkcn/MaiWeave/stargazers)
[![Last Commit](https://img.shields.io/github/last-commit/ilworkcn/MaiWeave?label=Last%20Commit&color=7ba989)](https://github.com/ilworkcn/MaiWeave/commits/main/)
[![Issues](https://img.shields.io/github/issues/ilworkcn/MaiWeave?label=Issues&color=c58b75)](https://github.com/ilworkcn/MaiWeave/issues)

[下载安装](https://github.com/ilworkcn/MaiWeave/releases/latest) · [使用手册](docs/使用手册.md) · [配置指南](docs/配置指南.md) · [更新日志](CHANGELOG.md)

</div>

MaiWeave 让麦麦拥有**与你相连的日常**。你的一句邀约可以改变麦麦的安排，当地天气可以影响它选择的地点，上传的书会成为它实际读取的内容。做过的事留下进度、感想和待解的问题，再带回之后的日程与对话。

## 🌱 让相处，慢慢有了日常的样子

从持续生活的想法出发，MaiWeave 把更多心思放在了**你能参与、它会记得、下次还能接上的小事**里。

| 你会感受到的变化 | 麦麦会怎么做 |
| --- | --- |
| 🌤️ **恰好想起你的早晚安** | 开启自然早晚安后，结合起床、入睡、人物关系和最近的聊天，判断何时主动问候。问候随着相处发生，每天都有自己的节奏 |
| 💬 **小事也想和你分享** | 做饭时的一点进步、散步中的小感受、读到的一句话，都有机会成为主动话题。选择时兼顾内容和最近聊过的活动，让分享更丰富 |
| 🗓️ **一句邀约，真的改变安排** | 聊天就能新增、改期、暂停、继续或取消日程。你临时有事，它会调整安排；事情结束后，还能补上当时发生的细节 |
| 📖 **读过的内容，留在之后的聊天里** | 读取你上传的书籍正文，按书保存进度、摘句、感想和疑问。续读时带上之前的笔记，你点名问起这本书，也能接着聊 |
| 🌦️ **窗外的变化，影响今天怎么过** | 按活动搜索所在城市的真实地点，结合天气安排外出阅读，预留模拟出行时间；天气变了，可以回到室内接着读 |
| 🤝 **换个聊天的地方，还是熟悉的你** | 关联 MaiBot 原生人物画像，群聊与私聊共享同一人的关系和近期状态。亲密程度影响日程调整与唤醒，生日和纪念日也能记下来 |
| 🧶 **昨天留下的事，今天继续** | 保存作品阶段、长期目标和未完话题，让空闲时的安排有前因后续。日记把当天的经历与心情串起来，随时可以回看 |

### 从一个约定，到下一次聊天

你上传一本小说，约麦麦明天下午读一会儿。它把阅读排进日程，到了时间读取正文，留下感想和疑问；下次续读，带上这本书之前的笔记。遇到有意思的地方，可能主动来找你聊。你再问“上次那个问题有答案了吗？”，话题就从那里继续。

**你参与安排 → 麦麦按日程行动 → 留下具体经历 → 带回下一次相处。**

天气、地点与书籍内容来自接入的资料，角色的出行、作息和身体状态由生活引擎模拟。经历保留来源和细节，可以补充、更正与回看；同步到原生记忆时，模型会核对已有内容，遇到冲突由你确认。

主动交流可选 **自然交流**（由 Planner 结合聊天决定开口）或 **主动表达**（生成后直接发送）。WebUI 显示实际发送或沉默的结果，额度与间隔按实际发送计算。作息也由你选择：按时休息、允许关键词唤醒，或全天候交流。

## 📖 从一本书开始，感受生活的接续

1. 在 **MaiWeave · 生活 → 书架与阅读** 上传 TXT、Markdown 或 EPUB。
2. 开启自主安排，让麦麦在读书时自行选书；也可以选中一本，点击 **安排阅读日程**。
3. 读完后翻看笔记，或直接在聊天里点名这本书，问问它的感想。

每本书分别保存进度与笔记。续读会参考该书之前的感想和疑问，聊天中也能用上对应笔记。书架支持归档、恢复和容量设置。[查看阅读操作说明 →](docs/使用手册.md#书架与阅读)

### 📷 实机演示：主动分享与续聊

<p align="center">
  <a href="assets/reading-share.png"><img src="assets/reading-share.png" width="560" alt="麦麦读过书后主动分享对人物的感想"></a><br>
  <strong>麦麦主动来聊书</strong> · 读到达也哄妹妹的那段，麦麦发来了三条消息。
</p>

<p align="center">
  <a href="assets/reading-followup.png"><img src="assets/reading-followup.png" width="560" alt="追问上次的疑问，麦麦结合续读内容继续讨论"></a><br>
  <strong>接着上次的话题聊</strong> · 读者问上次的问题有答案了吗，麦麦说：“我觉得只答了一半。”
</p>

## 🏡 在 WebUI 里，看见生活的来龙去脉

**此刻生活**显示当前活动、状态变化、自主规划理由和主动交流结果。日程、经历和日记连在一起，你可以查看安排、补充细节，再回头读麦麦写下的一天。

<p align="center">
  <a href="assets/home-life-card.png"><img src="assets/home-life-card.png" width="480" alt="MaiWeave 首页生活卡片，展示带时间的活动快照、地点、精力与心情"></a><br>
  <sub>首页卡片保留加载时的生活快照；进入「此刻生活」查看持续更新的状态。</sub>
</p>

<details>
<summary>展开查看页面与功能</summary>

| 页面 | 可以做什么 |
| --- | --- |
| 此刻生活 | 看活动、精力、心情、规划理由与主动交流回执 |
| 日程与作息 | 安排活动、调整时间、管理重复规则与历史 |
| 天气与见闻 | 查看天气、订阅资讯、检索与阅读联网资料 |
| 地点与移动 | 管理城市地点、路线、交通方式与模拟出行耗时 |
| 经历与记忆 | 补充经历细节、查看修订历史、同步原生记忆 |
| 书架与阅读 | 管理书籍、安排阅读、翻看进度和笔记 |
| 成长与接续 | 推进长期目标、作品阶段、习惯和待跟进话题 |
| 人物与纪念日 | 关联人物画像、设置关系、生日与纪念日 |
| 日记与梦境 | 回顾一天、查看日记素材、睡眠与梦境 |
| 模型用量 | 按日期和功能查看 Token、调用次数与耗时 |

</details>

页面使用中文，支持记录翻页，保存和模型处理操作会显示进度与结果。快速查状态可发送 **`/maiweave`**；想听麦麦自己讲，就问“你今天过得怎么样？”

## 🚀 装起来

适配 **MaiBot 1.3.19–1.3.x、插件 SDK 2.21.x**，搭配支持 WebUI v2 页面协议的 WebUI。

1. 从 [Release](https://github.com/ilworkcn/MaiWeave/releases/latest) 下载对应版本的 ZIP 安装包。
2. 在 MaiBot 的 **插件扩展 → 更多操作 → 从 ZIP 安装插件** 中上传，然后启用。
3. 打开配置，选好时区，写一点兴趣和生活背景。模型任务默认用 `utils`，也可以从下拉框选择。

通过商店安装时，MaiBot 会根据插件声明自动补齐农历转换库 `lunar_python` 和时区库 `tzdata`。手动安装的 uv 命令见下方。

第一次使用，可以先选好这几项：

| 想怎么用 | 去哪里设置 |
| --- | --- |
| 让麦麦开始读书 | 开启「自主安排活动」，在「书架与阅读」上传书籍，麦麦安排阅读时就会自行选书 |
| 让麦麦有自己的日常 | 填写兴趣爱好、生活背景，调整入睡和起床时间 |
| 全天候交流 | 关闭「遵循日常作息」 |
| 按城市安排外出 | 在「天气与见闻」设置城市，开启 MaiBot 联网搜索和自动搜索生活地点；默认城市为大庆、国家代码为 CN |
| 关联人物和生日 | 开启原生记忆连接，确认 MaiBot 网页地址，再到「人物与纪念日」选择画像 |
| 收到主动消息和祝福 | 开启主动交流，模式可选「自然交流」或「主动表达」。在个人资料中选择「交流与提醒聊天流」；群聊等其他对象填入「额外接收聊天流」 |

天气使用 Open-Meteo，联网搜索使用 MaiBot 已配置的联网服务。人物画像和经历同步使用 MaiBot A_Memorix，连接状态可在人物页面查看。

### 手动安装

1. 下载 Release 安装包，解压到 MaiBot 的 `plugins/MaiWeave` 目录，该目录下应直接包含 `_manifest.json` 和 `requirements.txt`。
2. 在 **MaiBot 根目录**打开终端，用 uv 将插件依赖安装到 MaiBot 的虚拟环境：

```bash
uv pip install --python .venv -r plugins/MaiWeave/requirements.txt
```

3. 重启 MaiBot，在 WebUI 中启用 MaiWeave，再按上方说明配置。

命令中的 `.venv` 是 MaiBot 的虚拟环境目录；如果插件目录或虚拟环境使用了其他名称，替换为实际路径。


## 📚 使用与致谢

具体操作见[使用手册](docs/使用手册.md)，默认设置和连接方法见[配置指南](docs/配置指南.md)，接口说明见[架构与接口](docs/架构与接口.md)。

人物画像和通用记忆沿用 MaiBot 的 A_Memorix。生活记录、书籍文件和解析正文保存在插件数据目录；备份时保留整个数据目录与配置。

MaiWeave 的生活引擎设计受到 [Mai_life](https://github.com/octmicy/Mai_life) 启发，并围绕对话互动、现实资料与生活接续持续完善。感谢 [MaiBot 插件 SDK](https://github.com/Mai-with-u/maibot-plugin-sdk) 和 [lunar-python](https://github.com/6tail/lunar-python)。

项目采用 [GPL-3.0](LICENSE) 许可证。
