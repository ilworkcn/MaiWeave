<div align="center">

![MaiWeave：聊天之外，也有自己的生活](https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/banner.png)

<img src="https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/icon.png" width="128" alt="MaiWeave 插件图标">

# MaiWeave · 麦织｜角色生活引擎

**给 MaiBot 一套自己的生活，让你的聊天也能改变它的一天。**

随着相处积累熟悉、信任与亲近，麦麦会记住你的近况、生日和彼此的约定，结合你们的关系主动问候、关心后续。它也会安排作息、读书、出门，带着经历回来聊天，你的一句邀约也能改变它的日程。

自主安排日程 · 亲密关系与主动关心 · 聊天改变计划 · 真实天气与地点 · 读书写日记 · 日记发空间

[![Release](https://img.shields.io/github/v/release/ilworkcn/MaiWeave?label=Release&color=7ba989)](https://github.com/ilworkcn/MaiWeave/releases)
[![MaiBot](https://img.shields.io/badge/MaiBot-1.3.19–1.4.x-7ba989)](https://docs.maibot.chat/)
[![SDK](https://img.shields.io/badge/SDK-2.21.x-79a7b8)](https://github.com/Mai-with-u/maibot-plugin-sdk)
[![Python](https://img.shields.io/badge/Python-3.11%2B-79a7b8?logo=python&logoColor=white)](https://github.com/ilworkcn/MaiWeave/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/License-GPL--3.0-orange)](https://github.com/ilworkcn/MaiWeave/blob/main/LICENSE)

[![Stars](https://img.shields.io/github/stars/ilworkcn/MaiWeave?style=flat&label=Stars&color=e8b44c)](https://github.com/ilworkcn/MaiWeave/stargazers)
[![Last Commit](https://img.shields.io/github/last-commit/ilworkcn/MaiWeave?label=Last%20Commit&color=7ba989)](https://github.com/ilworkcn/MaiWeave/commits/main/)
[![Issues](https://img.shields.io/github/issues/ilworkcn/MaiWeave?label=Issues&color=c58b75)](https://github.com/ilworkcn/MaiWeave/issues)

[下载安装](https://github.com/ilworkcn/MaiWeave/releases/latest) · [使用手册](https://github.com/ilworkcn/MaiWeave/blob/main/docs/使用手册.md) · [配置指南](https://github.com/ilworkcn/MaiWeave/blob/main/docs/配置指南.md) · [状态 API](https://github.com/ilworkcn/MaiWeave/blob/main/docs/只读状态API.md) · [更新日志](https://github.com/ilworkcn/MaiWeave/blob/main/CHANGELOG.md)

</div>

MaiWeave 为 MaiBot 添加自主日程、睡眠作息、情绪、阅读和日记。开启生活引擎后，你没找它聊天的时候，麦麦也会按时间安排吃饭、休息、学习和出行；你回来时，可以问问它这一天过得怎么样。

**你说的话能改变日程，日程里的经历也会回到聊天。** 约它明天读书、临时改个时间，或取消一次出门，都能直接在对话里完成。安排活动时会参考接入的天气和城市地点；阅读时会读取你上传的正文，保存进度和感想，下次接着读、接着聊。

麦麦也会安排时间上网了解感兴趣的事，记下来源和感想，把想尝试的事放进之后的日程。读过的书、聊过的话、没做完的事，会成为下一天生活的一部分。

它记得你的近况，也有自己的牵挂：合适的时候来道声早晚安，分享刚遇到的小事，或在生日和纪念日送来祝福。一天结束，还能把日记中的生活片段整理成随笔，发到 QQ 空间。

## 🌱 有了生活，聊天会有什么不同？

下面这些，就是你和麦麦可以发生的日常互动。表中的对话是举例。

| 你做了什么 | 麦麦怎么接上这件事 |
| --- | --- |
| 🌙 **私聊说“晚安，我去睡了”，转头又在群里冒泡** | 同一人物的近期状态会在群聊、私聊间接续，麦麦可能抓包：“你刚才不是说去睡了吗，怎么还在这里？” |
| 🗓️ **约它明天下午读书，后来又说“改到晚上吧”** | 约定会写进日程，改时间也会跟着调整；聊定的调休、请假会影响之后的自主安排。到了阅读时间，它会继续读书、留下笔记 |
| 🌧️ **原本准备去公园，出门前天气变了** | 安排会参考当地天气和搜索到的真实地点，外出阅读可以改到室内，继续同一本书 |
| 📖 **上传一本小说，过两天问“你上次那个疑问有答案了吗？”** | 阅读会读取书籍正文，保存进度、感想和疑问。续读和聊天都能接上这本书之前的笔记 |
| 🔎 **问它“最近有没有发现什么有意思的？”** | 开启自主获取见闻后，麦麦会按兴趣安排上网阅读，留下来源和感想，把想尝试的事接进后续生活 |
| 💤 **半夜找它聊天，或者告诉它“我现在有点难过”** | 按作息入睡，结合你们的关系和配置的唤醒条件决定是否醒来陪伴；也可以设置全天候交流 |
| 💌 **你没主动找它，它却想起了你** | 起床、睡前或遇到值得分享的小事时，结合近期聊天与关系判断是否发来问候，聊聊刚读的书或今天的经历 |
| 🎂 **告诉它生日，或记下你们的一个纪念日** | 日期关联到 MaiBot 人物画像，支持公历、农历，到了日子可以向指定聊天流发送提醒 |
| 📝 **刷空间时，看到麦麦更新了今天的日常** | 从当天日记整理一篇文字随笔，分享读书、出门和生活中的感想。可以先预览修改再发，也可以开启写完日记后自动发布 |
| 🏡 **忙了一天，回来问“你今天都干嘛了？”** | 它可以带着已经留下的活动经历继续聊天；你也能打开 WebUI，看一天的日程、状态变化和它写下的日记 |

### 从一个约定，到下一次聊天

你上传一本小说，约麦麦明天下午读一会儿。它把阅读排进日程，到了时间读取正文，留下感想和疑问；下次续读，带上这本书之前的笔记。遇到有意思的地方，可能主动来找你聊。你再问“上次那个问题有答案了吗？”，话题就从那里继续。

**你参与安排 → 麦麦按日程行动 → 留下具体经历 → 带回下一次相处。**

天气、地点与书籍内容来自接入的资料，角色的出行、作息和身体状态由生活引擎模拟。经历保留来源和细节，可以补充、更正与回看；同步到原生记忆时，模型会核对已有内容，遇到冲突由你确认。

你可以让麦麦按作息休息，也可以选择全天候陪伴；群聊和私聊各自带上多少生活细节，都能在配置中调整。

## 📖 上传一本书，开始阅读

1. 在 **MaiWeave · 生活 → 书架与阅读** 上传 TXT、Markdown 或 EPUB。
2. 开启自主安排，让麦麦在读书时自行选书；也可以选中一本，点击 **安排阅读日程**。
3. 读完后翻看笔记，或直接在聊天里点名这本书，问问它的感想。

每本书分别保存进度与笔记。续读会参考该书之前的感想和疑问，聊天中也能用上对应笔记。书架支持归档、恢复和容量设置。[查看阅读操作说明 →](https://github.com/ilworkcn/MaiWeave/blob/main/docs/使用手册.md#书架与阅读)

自主阅读默认每天最多 90 分钟，单次最多 40 分钟，两次之间留出 60 分钟做别的事。在「日程与成长 → 日常阅读」可以修改每日上限，-1 表示每日无上限，0 表示关闭自主阅读；单次时长和间隔在「高级设置 → 自主阅读节奏」调整；聊天和书架里手动约好的阅读照常执行。

### 📷 实机演示：主动分享与续聊

<p align="center">
  <a href="https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/reading-share.png"><img src="https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/reading-share.png" width="560" alt="麦麦读过书后主动分享对人物的感想"></a><br>
  <strong>麦麦主动来聊书</strong> · 读到达也哄妹妹的那段，麦麦发来了三条消息。
</p>

<p align="center">
  <a href="https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/reading-followup.png"><img src="https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/reading-followup.png" width="560" alt="追问上次的疑问，麦麦结合续读内容继续讨论"></a><br>
  <strong>接着上次的话题聊</strong> · 读者问上次的问题有答案了吗，麦麦说：“我觉得只答了一半。”
</p>

### 🌙 实机演示：睡前来道声晚安

<p align="center">
  <a href="https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/goodnight-chat.png"><img src="https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/goodnight-chat.png" width="560" alt="麦麦睡前道晚安，记得对方昨晚加班，叮嘱早点休息"></a><br>
  <strong>睡前也惦记着你</strong> · 麦麦准备睡觉了，还记得你昨晚加班辛苦，过来抱抱、道声晚安。
</p>

## 📝 把一天的日常，写进 QQ 空间

麦麦写完日记，可以从当天的生活片段中整理一篇随笔，发到自己的 QQ 空间。日记和空间文案分别保存，你可以先看看它写了什么，再修改、发布。

在 **日记与梦境 → 日记详情 → 日记发空间** 中生成和编辑文案。发布默认关闭，可选择手动发布，或随当天写日记日程完成后自动发布，每天一篇；可见范围支持好友、所有人和仅自己。

需要 **MaiBot 统一 QQ 连接器与已登录的 SnowLuma**，沿用当前 QQ 登录。**NapCat 连接方式尚未测试，空间发布功能的兼容性待验证。** [查看空间发布步骤 →](https://github.com/ilworkcn/MaiWeave/blob/main/docs/使用手册.md#日记发空间)

## 🏡 在 WebUI 查看和管理生活

**此刻生活**展示当前活动与进度、地点天气、每日时间轴，以及最近的阅读感想与日记。主动交流回执和运行记录可展开查看。日程、经历和日记连在一起，你可以查看安排、补充细节，再回头读麦麦写下的一天。

### 📷 实机演示：麦麦的一天

<p align="center">
  <a href="https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/life-overview.png"><img src="https://raw.githubusercontent.com/ilworkcn/MaiWeave/main/assets/life-overview.png" width="1000" alt="新版此刻生活实机界面：麦麦正在厨房做晚饭，展示活动进度、精力心情和全天日程时间线"></a><br>
  <sub>正在做什么、为什么这样安排、今天过得怎样，打开「此刻生活」就能看到。</sub>
</p>


<details>
<summary>展开查看页面与功能</summary>

| 页面 | 可以做什么 |
| --- | --- |
| 此刻生活 | 看活动进度、每日轨迹、状态曲线、阅读感想与日记 |
| 日程与作息 | 安排活动、调整时间、管理重复规则与历史 |
| 天气与见闻 | 查看天气、订阅资讯、检索与阅读联网资料 |
| 地点与移动 | 管理城市地点、路线、交通方式与模拟出行耗时 |
| 经历与记忆 | 补充经历细节、查看修订历史、同步原生记忆 |
| 书架与阅读 | 管理书籍、安排阅读、翻看进度和笔记 |
| 成长与接续 | 推进长期目标、作品阶段、习惯和待跟进话题 |
| 人物与纪念日 | 关联人物画像、设置关系、生日与纪念日 |
| 日记与梦境 | 回顾一天、查看日记素材、睡眠与梦境；生成和发布空间随笔 |
| 模型用量 | 查看 Token、调用次数、耗时，以及最近 10 次聊天注入的字符数和内容组成 |

</details>

页面使用中文，支持记录翻页，保存和模型处理操作会显示进度与结果。快速查状态可发送 **`/maiweave`**；想听麦麦自己讲，就问“你今天过得怎么样？”

### 精力会影响什么？

精力随活动变化：睡眠每小时恢复 10 点，休息恢复 5 点；工作、学习和出行消耗 6 点，其他活动消耗 3 点。降到 25 或以下时，麦麦会优先休息，普通主动分享等恢复后再聊，重要经历和提醒照常处理。聊天会参考当前精力，累了说话可以简短一些，睡眠静默按作息设置执行。

## 🚀 安装与设置

适配 **MaiBot 1.3.19–1.4.x、插件 SDK 2.21.x**，搭配支持 WebUI v2 页面协议的 WebUI。

1. 从 [Release](https://github.com/ilworkcn/MaiWeave/releases/latest) 下载对应版本的 ZIP 安装包。
2. 在 MaiBot 的 **插件扩展 → 更多操作 → 从 ZIP 安装插件** 中上传，然后启用。
3. 打开配置，选好时区，写一点兴趣和生活背景。模型任务默认用 `utils`，也可以从下拉框选择。

商店安装会自动安装所需依赖。手动安装方式见下方。

第一次使用，可以先选好这几项：

| 想怎么用 | 去哪里设置 |
| --- | --- |
| 让麦麦开始读书 | 开启「自主安排活动」，在「书架与阅读」上传书籍，麦麦安排阅读时就会自行选书 |
| 让麦麦有自己的日常 | 填写兴趣爱好、生活背景，调整入睡和起床时间 |
| 全天候交流 | 关闭「遵循日常作息」 |
| 调整聊天中的生活细节 | 在「聊天上下文」为群聊、私聊分别选择精简、均衡或丰富，并调整内容分类与字符上限；当前活动、经历和接续按话题整理，同一内容合并展示 |
| 按城市安排外出 | 设置天气城市，开启 MaiBot 联网搜索和自动搜索生活地点 |
| 记住你和重要的日子 | 连接 MaiBot 记忆，在「人物与纪念日」选择人物画像，填写生日、纪念日和提醒聊天流 |
| 收到主动消息和祝福 | 开启主动交流，在个人资料中选择「交流与提醒聊天流」 |

**接上麦麦已有的记忆。** 在 MaiBot 人物画像中选中你，补上生活备注、生日和纪念日，让之后的相处接着这些记忆继续。同机运行的 MaiBot 1.4 可自动连接，状态在人物页面查看。[记忆连接设置 →](https://github.com/ilworkcn/MaiWeave/blob/main/docs/配置指南.md)

模型任务可在插件配置的「模型设置」中按功能选择；更新频率、容量和数据保留集中在「高级设置」。详见[配置指南](docs/配置指南.md)。

天气由 Open-Meteo 提供，联网见闻和地点搜索使用 MaiBot 已配置的联网服务。主动交流方式、发送频率和聊天细节等选项，见[配置指南](https://github.com/ilworkcn/MaiWeave/blob/main/docs/配置指南.md)。

<details>
<summary>手动安装：解压目录与 uv 命令</summary>


1. 下载 Release 安装包，解压到 MaiBot 的 `plugins/MaiWeave` 目录，该目录下应直接包含 `_manifest.json` 和 `requirements.txt`。
2. 在 **MaiBot 根目录**打开终端，用 uv 将插件依赖安装到 MaiBot 的虚拟环境：

```bash
uv pip install --python .venv -r plugins/MaiWeave/requirements.txt
```

3. 重启 MaiBot，在 WebUI 中启用 MaiWeave，再按上方说明配置。

命令中的 `.venv` 是 MaiBot 的虚拟环境目录；如果插件目录或虚拟环境使用了其他名称，替换为实际路径。

</details>


## 📚 使用与致谢

[文档目录](docs/README.md)按用途汇总了下面这些说明。

- [使用手册](https://github.com/ilworkcn/MaiWeave/blob/main/docs/使用手册.md)：日程、阅读、生日提醒等操作。
- [配置指南](https://github.com/ilworkcn/MaiWeave/blob/main/docs/配置指南.md)：配置项、默认值与连接设置。
- [功能与数据说明](https://github.com/ilworkcn/MaiWeave/blob/main/docs/架构与接口.md)：日程、经历、聊天与记忆如何配合。
- [只读状态 API](https://github.com/ilworkcn/MaiWeave/blob/main/docs/只读状态API.md)：两个只读接口的调用方式、完整字段与场景示例。

运行明细每天按保留天数和条数整理，可在「经历与记忆」预览清理；日记、空间发布记录和阅读笔记长期保存。

人物画像和通用记忆沿用 MaiBot 的 A_Memorix。生活记录、书籍文件和解析正文保存在插件数据目录；备份时保留整个数据目录与配置。

MaiWeave 的早期设计参考了 [Mai_life](https://github.com/octmicy/Mai_life)。感谢 [MaiBot 插件 SDK](https://github.com/Mai-with-u/maibot-plugin-sdk) 和 [lunar-python](https://github.com/6tail/lunar-python)。

感谢每一位为 MaiWeave 提出建议、反馈问题和参与测试的朋友。你们分享的使用体验、发现的小问题和想到的新点子，让麦麦的日常一点点变得更丰富。

项目采用 [GPL-3.0](https://github.com/ilworkcn/MaiWeave/blob/main/LICENSE) 许可证。
