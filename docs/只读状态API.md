# 只读状态 API

用这个接口可以知道麦麦正在做什么、在哪里、有没有睡觉，以及接下来准备做什么。适合给其他插件读取，也可以用来制作状态面板。

## 先跑通一次查询

启用 MaiWeave 后，在调用方插件的 `_manifest.json` 的 `capabilities` 数组中加入 `"api.call"`，与已有能力一起保留。然后把下面的方法放进插件类中，在需要查询时调用 `await self.show_maiweave_status()`。

```python
async def show_maiweave_status(self):
    response = await self.ctx.api.call(
        "ilworkcn.maiweave.get_life_status",
        version="1",
    )
    self.ctx.logger.info(f"MaiWeave 返回：{response}")
```

这里的 `self.ctx` 是 MaiBot 给插件提供的上下文。完整接口名是 `ilworkcn.maiweave.get_life_status`，版本填字符串 `"1"`。接口查询的是麦麦的全局生活状态，群聊、私聊读到同一份结果。

`ctx.api.call()` 直接返回生活状态字典，例如 `response["current"]`、`response["sleeping"]`。调用异常由 MaiBot SDK 抛出，可按调用方插件的错误处理方式记录或展示。

## 在自己的插件里使用

先加入下面这个查询方法，后面的三个示例都调用它：

```python
async def read_maiweave_status(self):
    return await self.ctx.api.call(
        "ilworkcn.maiweave.get_life_status",
        version="1",
    )
```

### 示例一：显示麦麦正在做什么

把这个方法也放进插件类中，调用 `await self.describe_maiweave()` 就能得到一段文字。

```python
async def describe_maiweave(self):
    status = await self.read_maiweave_status()
    if not status["ready"]:
        return "生活状态正在准备中"

    activity = status["current"]
    title = activity["title"] if activity else "空闲"
    location = status["location"] or "地点待登记"
    return (
        f"正在：{title}\n"
        f"地点：{location}\n"
        f"精力：{status['energy']:.0f}\n"
        f"心情：{status['mood']:+.0f}"
    )
```

例如返回：

```text
正在：在住处读一会儿书
地点：住处
精力：70
心情：+18
```

### 示例二：显示睡眠和唤醒状态

`sleeping` 表示当前处于睡眠日程，`awake_until` 表示这次唤醒陪伴会持续到几点。下面按“引擎开关 → 作息开关 → 陪伴时段 → 睡眠”生成展示文字：

```python
async def describe_maiweave_sleep(self):
    from datetime import datetime

    status = await self.read_maiweave_status()
    if not status["ready"]:
        return "生活状态正在准备中"
    if not status["enabled"]:
        return "生活引擎已关闭"
    if not status["schedule_enabled"]:
        return "全天候模式"
    if status["awake_until"]:
        until = datetime.fromisoformat(status["awake_until"])
        return f"已唤醒，陪伴到 {until:%H:%M}"
    if status["effective_sleeping"]:
        return "正在睡觉"
    return "清醒"
```

这个方法返回展示文字。若你的插件想在麦麦睡觉时推迟自己的主动任务，可以用相同字段编写自己的安排规则。

### 示例三：查看下一项安排

```python
async def describe_maiweave_next_plan(self):
    from datetime import datetime

    status = await self.read_maiweave_status()
    if not status["ready"]:
        return "生活状态正在准备中"

    plan = status["next_plan"]
    if plan is None:
        return "接下来暂时空闲"

    start = datetime.fromisoformat(plan["start"])
    end = datetime.fromisoformat(plan["end"])
    return f"{start:%m-%d %H:%M}—{end:%H:%M}，{plan['title']}，地点：{plan['location']}"
```

例如返回：`11-06 18:00—18:40，准备晚饭，地点：厨房`。时间按返回值里的时区显示。

## 完整返回示例

下面是一份阅读中的示例数据：

```json
{
  "version": 1,
  "ready": true,
  "enabled": true,
  "schedule_enabled": true,
  "updated_at": "2026-11-06T17:15:00+08:00",
  "queried_at": "2026-11-06T17:15:05+08:00",
  "effective_sleeping": false,
  "quiet": false,
  "sleep_until": null,
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
```

## 字段速查

| 字段 | 含义 |
| --- | --- |
| `version` | 数据格式版本，当前为整数 `1` |
| `ready` | `true` 表示已有生活状态；`false` 时状态数据为 `null`，配置开关和查询时间仍有值 |
| `enabled` | 生活引擎开关 |
| `schedule_enabled` | 日常作息开关，`false` 为全天候模式 |
| `updated_at` | 精力、心情、位置等生活状态最后更新的时间 |
| `sleeping` | 是否处于睡眠日程，保留原有含义 |
| `effective_sleeping` | 按内部作息规则判断的有效睡眠状态；包含临时唤醒到期、续睡日程尚未生成的阶段 |
| `quiet` | 当前是否按睡眠规则保持聊天静默，与午睡静默开关及聊天发送判断一致 |
| `sleep_until` | 有效睡眠预计结束时间；有效睡眠为 false 或状态未就绪时为 null |
| `queried_at` | 本次查询时间，含时区的 ISO 8601 字符串 |
| `awake_until` | 有效的唤醒陪伴截止时间，到期后返回 `null` |
| `current` | 当前活动；空闲时为 `null` |
| `next_plan` | 下一项安排；没有未来安排时为 `null` |
| `location` | 当前位置，字符串；待登记时为空字符串 |
| `energy` | 精力，数值 |
| `mood` | 心情，数值；正值偏愉快，负值偏低落，零为中性 |

`current` 和 `next_plan` 都包含以下字段：

| 字段 | 含义 |
| --- | --- |
| `id` | 日程 ID |
| `title` | 活动名称 |
| `kind` | 活动类型 |
| `start` / `end` | 开始与结束时间，含时区的 ISO 8601 字符串 |
| `location` | 计划地点；出行途中可以与顶层的当前位置不同 |
| `status` | 保存的日程状态：`scheduled`（待进行）或 `active`（进行中） |

活动类型：`sleep` 睡眠、`meal` 用餐、`work` 工作、`study` 学习、`leisure` 休闲、`travel` 出行、`rest` 休息、`social` 社交、`diary` 写日记。

## 数据什么时候更新

每次调用都读取当前数据。活动和陪伴期限按查询时刻判断；精力、心情和位置随生活引擎更新，更新时间见 `updated_at`。

当前活动从 `scheduled`、`active` 日程中选取，时间范围为 `start <= 当前时间 < end`；下一项安排取未来开始时间最早的一项。活动刚开始、引擎还在等待下一次推进时，`current.status` 可以仍是 `scheduled`。多项活动同时满足条件时沿用引擎的记录顺序，返回第一项。

`sleeping` 和 `awake_until` 分别读取睡眠日程与唤醒记录。全天候模式下，用户手动安排的睡眠仍可出现在活动里。关闭生活引擎后，接口继续提供保存的状态，活动时间与陪伴期限仍按查询时刻判断。

做状态面板时，可以每 30 秒查询一次；其他插件也可以在需要时调用。一次查询只读取记录，麦麦的生活仍由原来的生活循环推进。活动结束或陪伴到期时，即使 `updated_at` 相同，相应字段也会变化。

MaiWeave 自带首页卡片目前展示加载时快照；「此刻生活」页面每 30 秒刷新。两种展示方式的刷新频率由各自页面决定。


## 判断现在是否适合交流

`ilworkcn.maiweave.evaluate_behavior`，接口版本 `"1"`。参数 `behavior` 可选：

- `conversation`：普通对话，使用角色当前的聊天静默规则。
- `proactive`：主动交流，使用内部主动交流的生活状态规则；睡眠、工作和学习期间暂缓。

```python
async def read_conversation_policy(self):
    return await self.ctx.api.call(
        "ilworkcn.maiweave.evaluate_behavior",
        version="1",
        behavior="conversation",
    )
```

例如午睡期间开启静默时返回：

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

| decision | reason | 含义 |
| --- | --- | --- |
| `allow` | `available` | 当前生活状态允许 |
| `defer` | `sleeping` | 睡眠期间暂缓 |
| `defer` | `busy` | 工作或学习期间暂缓主动交流 |
| `abstain` | `disabled` | 生活引擎关闭，由调用方安排 |
| `unknown` | `not_ready` | 生活状态尚未就绪，调用方稍后重查 |

`until` 是本次睡眠或活动的预计结束时间，其余情况为 `null`。日程、作息设置或唤醒状态变化后可重新查询。此接口判断生活状态；实际回复决策、接收对象、发送时段和次数由调用方的功能继续处理。参数值写错时抛出参数错误，调用异常由 SDK 传给调用方。

### 睡眠、午睡与全天候模式

| 场景 | effective_sleeping / quiet | 普通对话 | 主动交流 |
| --- | --- | --- | --- |
| 夜间睡眠 | true / true | 暂缓 | 暂缓 |
| 午睡，开启静默 | true / true | 暂缓 | 暂缓 |
| 午睡，关闭静默 | true / false | 允许 | 暂缓 |
| 临时唤醒陪伴中 | false / false | 允许 | 按当前活动判断 |
| 陪伴已到期，原睡眠尚未结束，续睡日程尚未生成 | true / 按午睡与夜间规则判断 | 按静默设置判断 | 暂缓 |
| 全天候模式 | false / false | 允许 | 用户明确安排的睡眠、工作和学习期间仍暂缓 |

普通对话的允许表示作息规则允许处理，并不表示每条消息都会回复。关闭生活引擎时，行为接口返回 `abstain`。查询只读记录和当前时间，不推进日程、不恢复睡眠、不调用模型。
