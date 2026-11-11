# 只读状态 API

[文档目录](README.md) · [使用手册](使用手册.md) · [配置指南](配置指南.md)

其他 MaiBot 插件可以通过这两个接口读取麦麦的生活状态，用来显示近况，或判断自己的交流任务适合在什么时候进行。

**接口只负责查询。** 调用会读取已有记录，结合当前时间返回结果。日程推进、睡眠唤醒、模型请求和消息发送由各自原有功能执行；调用方收到的“允许”或“暂缓”也是一份查询结果。

[开始调用](#在插件中调用) · [状态字段](#life-status) · [交流判断](#behavior) · [展示示例](#展示示例) · [刷新与异常](#刷新时间与异常)

## 选择接口

| 想了解什么 | 接口 | 参数 |
| --- | --- | --- |
| 当前活动、下一项安排、地点、精力、心情和睡眠 | `ilworkcn.maiweave.get_life_status` | 无 |
| 当前生活状态是否适合普通对话或主动交流 | `ilworkcn.maiweave.evaluate_behavior` | `behavior` |

两个接口的调用版本都是字符串 `"1"`，返回字典中的 `version` 为整数 `1`。查询的是角色共享的生活状态，群聊和私聊读取同一份数据，调用时无需传聊天流 ID 或用户 ID。

## 在插件中调用

MaiWeave 与调用方插件需要在同一个 MaiBot 实例中加载。在调用方 `_manifest.json` 的 `capabilities` 数组里加入 `"api.call"`，保留其他已有能力。

把下面的方法放进你的 `MaiBotPlugin` 子类，在需要时调用：

```python
async def read_maiweave_status(self):
    return await self.ctx.api.call(
        "ilworkcn.maiweave.get_life_status",
        version="1",
    )

async def read_maiweave_behavior(self, behavior: str):
    return await self.ctx.api.call(
        "ilworkcn.maiweave.evaluate_behavior",
        version="1",
        behavior=behavior,
    )
```

`self.ctx` 是 MaiBot 提供给插件的上下文，`ctx.api.call()` 直接返回结果字典。读取方式是 `status["current"]` 或 `result["decision"]`，外层没有 `success`、`result` 包装。这是 MaiBot 的跨插件调用接口，使用时通过 SDK 调用。

<a id="life-status"></a>

## 生活状态：get_life_status

### 返回示例

下面是一份正在阅读的示例，时间采用角色配置的生活时区：

```json
{
  "version": 1,
  "ready": true,
  "enabled": true,
  "schedule_enabled": true,
  "updated_at": "2026-11-11T17:15:00+08:00",
  "queried_at": "2026-11-11T17:15:05+08:00",
  "sleeping": false,
  "effective_sleeping": false,
  "quiet": false,
  "sleep_until": null,
  "awake_until": null,
  "current": {
    "id": "reading-example",
    "title": "在住处读一会儿书",
    "kind": "study",
    "start": "2026-11-11T17:00:00+08:00",
    "end": "2026-11-11T17:30:00+08:00",
    "location": "住处",
    "status": "active"
  },
  "next_plan": {
    "id": "meal-example",
    "title": "准备晚饭",
    "kind": "meal",
    "start": "2026-11-11T18:00:00+08:00",
    "end": "2026-11-11T18:40:00+08:00",
    "location": "厨房",
    "status": "scheduled"
  },
  "location": "住处",
  "energy": 70.055,
  "mood": 18.161
}
```

### 状态与时间字段

下表中的 `null` 对应 Python 的 `None`。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `version` | 整数 | 返回格式版本，当前为 `1` |
| `ready` | 布尔值 | 是否已有保存的生活状态 |
| `enabled` | 布尔值 | 「启用生活引擎」的配置值 |
| `schedule_enabled` | 布尔值 | 「遵循日常作息」的配置值；`false` 为全天候模式 |
| `queried_at` | 字符串 | 本次查询时间，含时区的 ISO 8601 时间 |
| `updated_at` | 字符串或 `null` | 保存的生活状态最后更新时间；尚无状态时为 `null` |
| `location` | 字符串或 `null` | 保存的当前位置；未填写时为空字符串，尚无状态时为 `null` |
| `energy` | 数值或 `null` | 精力，范围 0～100；尚无状态时为 `null` |
| `mood` | 数值或 `null` | 心情，范围 -100～100；正值偏愉快，负值偏低落，0 为中性；尚无状态时为 `null` |
| `current` | 活动对象或 `null` | 查询时刻所处的活动；空闲或尚无状态时为 `null` |
| `next_plan` | 活动对象或 `null` | 未来开始时间最早的安排；没有后续安排或尚无状态时为 `null` |

`ready` 与 `enabled` 分别表示“有没有数据”和“引擎是否开启”。关闭生活引擎后，已保存的数据仍可查询；插件本身需要保持加载，才能提供接口。

### 睡眠字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `sleeping` | 布尔值或 `null` | 当前活动是否为 `sleep` 类型；尚无状态时为 `null` |
| `effective_sleeping` | 布尔值或 `null` | 按作息开关、临时唤醒和睡眠安排判断，角色现在是否处于睡眠；尚无状态时为 `null` |
| `quiet` | 布尔值或 `null` | 当前是否按睡眠规则保持聊天静默；尚无状态时为 `null` |
| `sleep_until` | 字符串或 `null` | 当前有效睡眠的预计结束时间；清醒、全天候模式、引擎关闭或尚无状态时为 `null` |
| `awake_until` | 字符串或 `null` | 临时唤醒记录中仍有效的陪伴截止时间；无记录或已经到期时为 `null` |

选择字段时，可以按下面的用途判断：

- 显示“当前日程是否在睡觉”：用 `sleeping`。
- 显示“角色现在是否处于睡眠”：用 `effective_sleeping`。
- 显示“当前作息是否要求聊天静默”：用 `quiet`。

午睡关闭静默时，`effective_sleeping` 可以为 `true`、`quiet` 为 `false`。全天候模式下，手动安排的睡眠仍能出现在 `current` 中，此时 `sleeping` 为 `true`，`effective_sleeping` 和 `quiet` 为 `false`。

临时唤醒到期后，原睡眠还没结束、续睡日程尚未生成时，可能得到以下字段组合：

```json
{
  "current": null,
  "sleeping": false,
  "awake_until": null,
  "effective_sleeping": true,
  "quiet": true,
  "sleep_until": "2026-11-11T14:00:00+08:00"
}
```

这是午睡静默开启时的部分字段示例。接口按保存的原睡眠结束时间判断当前状态，续睡安排由生活循环处理。如果这时已有其他当前活动，会按该活动判断。

### 活动对象

`current` 与 `next_plan` 使用相同结构：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | 字符串 | 日程 ID |
| `title` | 字符串 | 活动名称 |
| `kind` | 字符串 | 活动类型，见下表 |
| `start` | 字符串 | 开始时间，含时区 |
| `end` | 字符串 | 结束时间，含时区 |
| `location` | 字符串 | 计划地点，未指定时为空字符串；出行途中可与顶层当前位置不同 |
| `status` | 字符串 | 保存的日程状态：`scheduled`（待进行）或 `active`（进行中） |

| kind | 含义 | kind | 含义 | kind | 含义 |
| --- | --- | --- | --- | --- | --- |
| `sleep` | 睡眠 | `meal` | 用餐 | `work` | 工作 |
| `study` | 学习 | `leisure` | 休闲 | `travel` | 出行 |
| `rest` | 休息 | `social` | 社交 | `diary` | 写日记 |

当前活动满足 `start <= 查询时间 < end`，下一项安排满足 `start > 查询时间`。两者都从 `scheduled`、`active` 的日程中选择。暂停、取消、已完成和待确认的安排不在这两个字段中。

活动刚开始、生活循环尚未推进时，`current.status` 仍可能是 `scheduled`。同时有多项活动符合条件时，当前活动取记录顺序中的第一项，下一项安排取开始时间最早的一项。

### 尚无生活状态时

`ready` 为 `false` 时，`version`、`enabled`、`schedule_enabled` 和 `queried_at` 仍有值，其余字段均为 `null`。页面可显示“生活状态正在准备中”，稍后再次读取。

<a id="behavior"></a>

## 交流判断：evaluate_behavior

### 参数

| 参数 | 类型 | 必填 | 可选值 |
| --- | --- | --- | --- |
| `behavior` | 字符串 | 是 | `conversation` 或 `proactive` |

- `conversation`：读取普通对话的睡眠静默判断。
- `proactive`：读取主动交流的生活状态判断，包括睡眠、工作和学习。

### 返回示例

午睡开启静默时，查询 `conversation` 得到：

```json
{
  "version": 1,
  "behavior": "conversation",
  "queried_at": "2026-11-11T13:05:00+08:00",
  "decision": "defer",
  "reason": "sleeping",
  "message": "正在睡觉，稍后再聊",
  "until": "2026-11-11T13:30:00+08:00"
}
```

### 返回字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `version` | 整数 | 返回格式版本，当前为 `1` |
| `behavior` | 字符串 | 本次传入的行为类型 |
| `queried_at` | 字符串 | 本次查询时间，含时区 |
| `decision` | 字符串 | 判断结果，见下表 |
| `reason` | 字符串 | 原因代码，适合用于程序判断 |
| `message` | 字符串 | 中文说明，适合直接展示 |
| `until` | 字符串或 `null` | 返回 `defer` 时对应睡眠或活动的预计结束时间，其他结果为 `null` |

| decision | reason | 含义 |
| --- | --- | --- |
| `allow` | `available` | 当前生活状态适合这类交流 |
| `defer` | `sleeping` | 当前处于相关睡眠时段 |
| `defer` | `busy` | 正在工作或学习，主动交流适合稍后进行 |
| `abstain` | `disabled` | 生活引擎已关闭，交流由调用方自行安排 |
| `unknown` | `not_ready` | 生活状态尚未就绪，可稍后查询 |

判断顺序为：先看生活引擎是否开启，再看状态是否就绪，最后判断所选行为。因此引擎关闭且状态未就绪时返回 `abstain`。

程序可以使用 `decision` 和 `reason` 分支处理；`message` 用来展示原因。`until` 是当前安排的预计结束时间，日程修改或唤醒后可能变化，调用方在准备执行自己的任务时可以再次查询。

### 各种状态下的结果

下表以生活引擎开启且状态就绪为前提：

| 场景 | effective_sleeping | quiet | conversation | proactive |
| --- | --- | --- | --- | --- |
| 夜间实际睡眠 | `true` | `true` | `defer` | `defer` |
| 午睡，开启静默 | `true` | `true` | `defer` | `defer` |
| 午睡，关闭静默 | `true` | `false` | `allow` | `defer` |
| 休息活动 `rest` | `false` | `false` | `allow` | `allow` |
| 工作或学习 | `false` | `false` | `allow` | `defer` |
| 空闲或其他活动 | `false` | `false` | `allow` | `allow` |
| 临时唤醒陪伴中 | `false` | `false` | `allow` | 按当前活动判断 |
| 陪伴到期，原睡眠仍有效，当前空闲 | `true` | 按午睡静默与夜间时段判断 | 按 `quiet` 判断 | `defer` |
| 全天候模式 | `false` | `false` | `allow` | 当前活动为睡眠、工作或学习时 `defer`，其他为 `allow` |

夜间按配置的入睡与起床整点判断，包含入睡整点、到起床整点结束；实际睡眠仍以睡眠安排和唤醒记录为依据。

`proactive` 提供的是**生活状态这一项判断**。接收对象、允许发送时段、每日次数、发送间隔和待分享内容由调用方自己的发送流程处理。`allow` 表示这项判断通过，实际是否发送仍取决于对应功能。查询 `conversation` 也只是读取静默判断，实际回复由 MaiBot 聊天流程决定。

## 展示示例

以下方法与前面的两个查询方法一起放在调用方插件类中。

### 显示当前活动

```python
async def describe_maiweave(self):
    status = await self.read_maiweave_status()
    if not status["ready"]:
        return "生活状态正在准备中"
    activity = status["current"]
    title = activity["title"] if activity else "空闲"
    location = status["location"] or "地点待登记"
    return (
        f"正在：{title}\n地点：{location}\n"
        f"精力：{status['energy']:.0f}\n心情：{status['mood']:+.0f}"
    )
```

### 显示作息与陪伴时间

```python
async def describe_maiweave_sleep(self):
    from datetime import datetime

    status = await self.read_maiweave_status()
    if not status["enabled"]:
        return "生活引擎已关闭"
    if not status["ready"]:
        return "生活状态正在准备中"
    if not status["schedule_enabled"]:
        return "全天候模式"
    if status["awake_until"]:
        until = datetime.fromisoformat(status["awake_until"])
        return f"已经醒来，陪伴到 {until:%H:%M}"
    if status["effective_sleeping"]:
        until = datetime.fromisoformat(status["sleep_until"])
        return f"正在睡觉，预计 {until:%H:%M} 结束"
    return "清醒"
```

### 显示主动交流判断

```python
async def describe_maiweave_proactive(self):
    from datetime import datetime

    result = await self.read_maiweave_behavior("proactive")
    if result["decision"] == "defer":
        until = datetime.fromisoformat(result["until"])
        return f"{result['message']}，预计到 {until:%H:%M}"
    return result["message"]
```

这些示例都只返回文字，适合放到调用方自己的状态页面。

## 刷新时间与异常

**两种时间各有用途。** `queried_at` 表示本次查询时刻；`updated_at` 表示精力、心情、位置等保存状态的更新时间。活动和唤醒期限按查询时间判断，因此即使 `updated_at` 相同，睡眠或当前活动也可能因时间到达而变化。

状态面板可按需要设置刷新间隔，例如每 30 秒查询一次；准备判断某个任务时也可以即时调用。每次调用使用各自的查询时间，分别调用两个接口可能恰好跨过某个活动的结束时刻。

| 情况 | 如何处理 |
| --- | --- |
| `ready=false` 或 `decision=unknown` | 展示状态准备中的提示，稍后查询 |
| `enabled=false` 或 `decision=abstain` | 展示生活引擎已关闭；调用方按自己的功能设置运行 |
| `behavior` 写错 | 接口会抛出参数错误，改为 `conversation` 或 `proactive` |
| 接口找不到或调用失败 | 检查 MaiWeave 是否已加载、安装版本是否提供该接口、调用方是否声明 `api.call`，并查看 MaiBot 日志中的具体错误 |

SDK 会将调用异常交给调用方。异常与 `ready=false` 含义不同：前者是本次调用失败，后者是已成功读到“尚无生活状态”的结果。
