# 只读生活状态 API

MaiWeave 提供 `get_life_status`，供其他 MaiBot 插件读取角色此刻的生活状态，也供展示组件共用数据。调用方可以据此展示活动、安排自己的任务或决定是否主动交流。

## 快速接入

MaiWeave 启用并加载后，在调用方插件的异步方法中使用：

```python
response = await self.ctx.api.call(
    "ilworkcn.maiweave.get_life_status",
    version="1",
)
if not response["success"]:
    self.ctx.logger.warning("读取 MaiWeave 状态失败：%s", response["error"])
    return

status = response["result"]
if not status["ready"]:
    self.ctx.logger.info("MaiWeave 尚未建立生活状态记录")
    return

activity = status["current"]
title = activity["title"] if activity else "空闲"
self.ctx.logger.info("麦麦正在：%s，精力：%s", title, status["energy"])
```

上例的 `return` 只结束调用方当前方法，具体业务如何继续由调用方安排。

| 项目 | 值 |
| --- | --- |
| 提供方插件 ID | `ilworkcn.maiweave` |
| API 名称 | `get_life_status` |
| 完整名称 | `ilworkcn.maiweave.get_life_status` |
| API 版本 | 字符串 `"1"` |
| 业务入参 | 无 |
| SDK 调用入口 | `self.ctx.api.call(...)` |
| 访问方式 | 经 MaiBot 转发的跨插件调用 |

注册时的 `public=True` 表示允许其他插件调用。此接口通过原生插件 API 服务提供，无需另外配置 URL、Cookie 或访问令牌。

## 返回示例

SDK 跨插件调用的成功结果包含两层：外层是调用结果，`result` 是生活状态。下面是一份阅读中的示例数据：

```json
{
  "success": true,
  "result": {
    "version": 1,
    "ready": true,
    "enabled": true,
    "schedule_enabled": true,
    "updated_at": "2026-11-06T17:15:00+08:00",
    "sleeping": false,
    "awake_until": null,
    "current": {
      "id": "reading-example",
      "title": "在住处读一会儿书",
      "kind": "study",
      "start": "2026-11-06T17:00:00+08:00",
      "end": "2026-11-06T17:30:00+08:00",
      "location": "住处",
      "status": "active"
    },
    "next_plan": {
      "id": "meal-example",
      "title": "准备晚饭",
      "kind": "meal",
      "start": "2026-11-06T18:00:00+08:00",
      "end": "2026-11-06T18:40:00+08:00",
      "location": "厨房",
      "status": "scheduled"
    },
    "location": "住处",
    "energy": 70.055,
    "mood": 18.161
  }
}
```

## 状态字段

下表描述 `result` 中的字段。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `version` | integer | 返回结构版本，当前为 `1`，与插件发布版本分开编号 |
| `ready` | boolean | 是否已有生活状态记录 |
| `enabled` | boolean | `life.enabled` 配置值，即生活引擎开关 |
| `schedule_enabled` | boolean | 日常作息开关，`false` 对应全天候模式 |
| `updated_at` | string / null | 身体与心情状态最后推进时间，含时区偏移的 ISO 8601 时间 |
| `sleeping` | boolean / null | 查询时刻是否落在一项可执行的睡眠日程内 |
| `awake_until` | string / null | 仍有效的唤醒陪伴截止时间；无记录或已到期时为 `null` |
| `current` | object / null | 查询时刻正在时间范围内的活动；没有匹配活动时为 `null` |
| `next_plan` | object / null | 未来开始时间最早的可执行安排；没有后续安排时为 `null` |
| `location` | string / null | 生活状态中保存的当前位置；尚未登记时可以是空字符串 |
| `energy` | number / null | 当前保存的精力值 |
| `mood` | number / null | 当前保存的心情值，正值偏愉快，负值偏低落，零为中性 |

`ready=false` 时，`updated_at` 到 `mood` 这些状态字段均为 `null`，`version` 与两个配置开关仍有值。

### 活动字段

`current` 与 `next_plan` 使用相同结构：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string | 日程 ID |
| `title` | string | 活动名称 |
| `kind` | string | 活动类型，见下表 |
| `start` / `end` | string | 计划开始与结束时间，ISO 8601 格式，带时区偏移 |
| `location` | string | 计划地点 |
| `status` | string | 已保存的日程状态，本接口选取 `scheduled` 或 `active` |

| kind | 活动 |
| --- | --- |
| `sleep` | 睡眠 |
| `meal` | 用餐 |
| `work` | 工作 |
| `study` | 学习 |
| `leisure` | 休闲 |
| `travel` | 出行 |
| `rest` | 休息 |
| `social` | 社交 |
| `diary` | 写日记 |

当前活动使用 `start <= 查询时间 < end` 匹配。下一项安排使用 `start > 查询时间`，按开始时间选最早一项。暂停、取消、完成、等待确认和阻塞的日程不参与这两项选择。多项活动同时满足当前时间时，沿用引擎当前活动的记录顺序，返回首个匹配项。

`status` 保留数据库值，因此时间已经进入活动窗口、生活循环尚未推进时，`current.status` 仍可能是 `scheduled`。当前活动的计划地点与角色保存的位置分别放在 `current.location` 和顶层 `location`，途中可能不同。

## 睡眠、唤醒和开关怎么读

这几个字段分别描述不同事实，调用方按自己的用途组合使用：

| 情况 | 读取方式 |
| --- | --- |
| 生活引擎已关闭 | `enabled=false`；其余字段仍可用于展示保存的生活数据 |
| 全天候模式 | `schedule_enabled=false`；若用户明确安排睡眠，仍可能出现睡眠活动 |
| 当前在睡眠窗口 | `sleeping=true` |
| 有有效陪伴时段 | `awake_until` 为截止时间 |
| 当前没有活动 | `ready=true` 且 `current=null` |
| 尚未建立状态记录 | `ready=false`，状态字段为 `null` |

`sleeping` 按睡眠日程计算，`awake_until` 按唤醒记录计算，二者独立返回。需要展示“睡眠中但正在陪伴”时，可以结合两个字段；普通对话与主动行为是否继续，由使用接口的插件决定。

本接口返回角色全局状态，群聊和私聊获得同一份数据，调用时无需传入聊天流或用户 ID。它不返回人物亲密程度、用户短期状态或生日等个人资料，也不判断发送额度、冷却或 Planner 是否愿意回复。

## 时间与刷新

一次查询读取已保存的生活状态，并按查询时刻选取当前活动、下一项安排和有效唤醒期限。精力、心情及位置随生活引擎正常更新；查询本身不会推进它们。

因此，连续调用可能得到相同的 `updated_at` 和数值；活动刚结束或陪伴刚到期时，即使 `updated_at` 没变，`current` 或 `awake_until` 也可能变化。`updated_at` 用于了解身体状态的数据时间，不是整个返回对象的修订号。

调用方可以在需要作出决定时查询一次，并在本次处理内复用结果。展示页面可以按自身刷新周期查询。接口采用请求—响应方式，状态变化不会主动推送。

首页卡片与 API 共用 `read_life_status` 读取函数。当前首页卡片是在组件加载时生成快照，卡片标明“加载时快照 · 数据截至”；添加 API 后不会自动变为轮询卡片。「此刻生活」页面继续使用原来的页面查询与刷新流程。

## 查询失败

插件未加载、API 名称或版本无法解析、接口执行失败时，MaiBot API 服务可返回：

```json
{
  "success": false,
  "error": "具体错误信息"
}
```

此时读取 `error` 查看原因；`ready=false` 则是调用成功但尚无生活状态，两者含义不同。RPC 连接或传输层异常仍可能通过 SDK 异常上报，由调用方沿用自己的错误处理流程。

## 接口行为

查询只读取生活记录，不写入日程、状态或人物资料，也不调用模型或创建后台任务。MaiWeave 现有睡眠、唤醒、日程规划、主动交流额度和 Planner 流程继续独立运行。

接口提供状态信息，返回结构中没有 `allow`、`deny`、`busy` 或行为评分。其他插件可按自身需求使用这些信息，查询本身不会改变麦麦的行为。
