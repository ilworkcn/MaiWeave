<div align="center">

![MaiWeave：聊天之外，也有自己的生活](assets/banner.png)

# MaiWeave · 麦织

**聊完之后，麦麦的日子也在继续。**

[![Release](https://img.shields.io/github/v/release/ilworkcn/MaiWeave?label=Release)](https://github.com/ilworkcn/MaiWeave/releases)
[![MaiBot](https://img.shields.io/badge/MaiBot-1.3.19–1.3.x-7ba989)](https://docs.maibot.chat/)
[![License](https://img.shields.io/badge/License-GPL--3.0-orange)](LICENSE)

[下载安装](https://github.com/ilworkcn/MaiWeave/releases/latest) · [使用手册](docs/使用手册.md) · [配置指南](docs/配置指南.md)

</div>

MaiWeave 是一个让 MaiBot 过上日常生活的插件。

它可以读一会儿书、出门散步、找家咖啡馆坐坐，晚上把今天的事写进日记。你问“上次那本书读到哪里了”，它能接着已有的进度聊；你约它明晚看电影，这件事也会进到它的安排里。

临时有事？直接说“改到后天吧”。日程会跟着对话变，生活里的经历也会带回聊天。

## 🌿 可以一起做些什么

- **约时间，也能改主意。** 聊天就能新增、改期、暂停或取消日程，固定活动可以设成每周重复。
- **让麦麦有自己的爱好。** 它会结合兴趣、精力和天气安排活动，把没读完的书、没做完的事接着做下去。
- **记住你和重要的日子。** 关联 MaiBot 的人物画像，记录关系、亲密程度、生日和纪念日，支持公历与农历。
- **去看看所在的城市。** 按活动找公园、餐厅、咖啡馆或商场，保存找到的地点，之后再去可以直接复用。
- **翻翻它的日记。** 写日记也是一项日程，里面有当天做过的事、心情和还惦记着的事。睡醒后偶尔还有梦可讲。
- **按你的节奏相处。** 可以保留作息和睡眠唤醒，也可以开启全天候陪聊；主动聊天、约定提醒和生日祝福都能单独配置。

生活状态、日程、人物、日记和地点都有中文 WebUI。需要看它在忙什么，打开 **MaiWeave · 生活**，或者发一句 **`/maiweave`**。

## 💬 试着这样聊

> 明晚七点一起看电影吧。
>
> 明天临时有事，改到后天八点。
>
> 每周一、三、五晚上散步半小时。
>
> 我的生日是农历八月初三。
>
> 昨天那本书读到哪里了？今天接着看吧。

## 🚀 装起来

适配 **MaiBot 1.3.19–1.3.x、插件 SDK 2.21.x**，搭配支持 WebUI v2 页面协议的 WebUI。

1. 从 [Release](https://github.com/ilworkcn/MaiWeave/releases/latest) 下载 `MaiWeave-0.6.9.zip`。
2. 在 MaiBot 的 **插件扩展 → 更多操作 → 从 ZIP 安装插件** 中上传，然后启用。
3. 打开配置，选好时区，写一点兴趣和生活背景。模型任务默认用 `utils`，也可以从下拉框选择。

想让它全天候陪聊，关闭「遵循日常作息」；想让它知道天气和当地地点，到「天气与见闻」里设置城市。人物与纪念日需要先开启原生记忆连接。

<details>
<summary>手动安装与 uv 依赖</summary>

把插件解压到 `plugins/MaiWeave`，确保这个目录下能直接看到 `_manifest.json`。在 **MaiBot 根目录**运行：

```bash
uv pip install --python .venv -r plugins/MaiWeave/requirements.txt
```

通过 WebUI 安装的插件目录由宿主分配，命令中的路径按实际目录修改。

</details>

## 📖 想多了解一点

[使用手册](docs/使用手册.md)里有具体操作，[配置指南](docs/配置指南.md)里有默认值和连接方法。版本变化见[更新记录](CHANGELOG.md)，开发接口见[架构与接口](docs/架构与接口.md)。

人物画像和通用记忆沿用 MaiBot 的 A_Memorix；生活记录保存在插件数据目录的 `maiweave.sqlite3`。更新插件时保留配置和数据即可。

感谢 [Mai_life](https://github.com/octmicy/Mai_life) 带来的灵感，以及 [MaiBot 插件 SDK](https://github.com/Mai-with-u/maibot-plugin-sdk) 和 [lunar-python](https://github.com/6tail/lunar-python)。项目使用 [GPL-3.0](LICENSE) 许可证。
