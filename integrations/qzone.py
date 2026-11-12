"""把日记中的公开生活素材整理成随笔，通过 SnowLuma 发布到 QQ 空间。"""

import asyncio
import json
from datetime import datetime, timedelta

from .usage import generate


class DiaryPublisher:
    def __init__(self, engine, context, config):
        self.engine, self.ctx, self.config = engine, context, config
        self.store = engine.store
        self.connection = {"status": "尚未检查空间连接", "error": "", "account": ""}
        with self.store.db:
            for row in self.store.all("diary_publication"):
                if row["status"] in {"生成中", "发布中"}:
                    publishing = row["status"] == "发布中"
                    row.update(status="待核对" if publishing else "生成失败",
                               error="发布时插件已重载，请在 QQ 空间核对结果" if publishing else "文案生成已中断，可以重新生成")
                    self.store.put("diary_publication", row)

    def view(self, day):
        return {"record": self.store.find("diary_publication", day) or {}, "connection": self.connection,
                "mode": self.config.diary.publish_mode, "visibility": self.config.diary.publish_visibility}

    def put(self, row):
        with self.store.db:
            self.store.put("diary_publication", row)
        return row

    async def check_connection(self):
        self.connection.update(status="检查中", error="", account="")
        try:
            response = await self.ctx.call_capability("api.call", api_name="adapter.snowluma.system.get_version_info", version="1", timeout_ms=15000, args={})
            if response.get("status") != "ok" or response.get("retcode") != 0:
                raise RuntimeError(response.get("error") or response.get("message") or response.get("wording") or "读取 QQ 客户端信息失败")
            app = response["data"].get("app_name", "")
            if "snowluma" not in app.lower():
                raise ValueError(f"当前客户端为 {app or '未知'}，日记发空间需要连接 SnowLuma")
            account = await self.ctx.call_capability("api.call", api_name="adapter.snowluma.system.get_login_info", version="1", timeout_ms=15000, args={})
            if not account or not account.get("user_id"):
                raise ValueError("请先在 SnowLuma 登录 QQ")
            self.connection.update(status="已连接 SnowLuma", account=str(account["user_id"]))
        except Exception as error:
            self.connection.update(status="连接失败", error=str(error))
            raise
        return self.connection

    def editable(self, day):
        row = self.store.find("diary_publication", day)
        if row and row["status"] in {"生成中", "发布中", "已发布", "待核对"}:
            raise ValueError("这篇文案当前为" + row["status"] + "，请查看发布记录")
        return row

    async def prepare(self, day, now):
        old = self.editable(day)
        diary = self.store.get("diary", day)
        if diary["status"] != "已生成":
            raise ValueError("请先完成这一天的日记")
        material = diary["material"]
        plans = [{k: p.get(k) for k in ("title", "kind", "status", "location", "start", "end")}
                 for p in material.get("活动", []) if not p.get("session_id") and not p.get("participants")
                 and p["status"] in {"completed", "active"} and p["kind"] != "diary"]
        events = [{k: e.get(k) for k in ("title", "summary", "source", "details")}
                  for e in material.get("经历", []) if not e.get("session_id") and not e.get("participants")]
        if not plans and not events:
            raise ValueError("这篇日记还没有可整理成空间随笔的个人生活素材")
        row = {"id": day, "day": day, "content": old.get("content", "") if old else "",
               "status": "生成中", "error": "", "created_at": now.isoformat(), "plan_id": diary.get("plan_id", "")}
        self.put(row)
        try:
            personality = await self.ctx.config.get("personality")
            prompt = ("根据日记中的个人生活素材，写一篇适合发 QQ 空间的中文生活随笔，第一人称，约100到300字。"
                      "直接返回正文。挑一两件有感触的小事，自然表达心情和还惦记的念头。"
                      "内容面向空间读者，讲自己的活动、阅读和见闻；涉及其他人的私聊和身份信息留在私人日记中。"
                      "活动完成情况、阅读进度及具体事实沿用素材，进行中的事情写当前进展。"
                      f"日期：{day}；角色：{json.dumps(personality, ensure_ascii=False)}；"
                      f"素材：{json.dumps({'活动': plans, '经历': events}, ensure_ascii=False)}")
            result = await generate(self.ctx, self.store, self.config, "空间文案", prompt)
            row.update(content=result["response"].strip(), status="草稿")
        except asyncio.CancelledError:
            row.update(status="生成失败", error="文案生成已中断，可以重新生成")
            raise
        except Exception as error:
            row.update(status="生成失败", error=str(error))
            raise
        finally:
            self.put(row)
        return row

    def save(self, day, content, now):
        old = self.editable(day)
        self.store.get("diary", day)
        content = content.strip()
        if not content:
            raise ValueError("请填写空间文案")
        row = {**(old or {}), "id": day, "day": day, "content": content, "status": "草稿", "error": "",
               "updated_at": now.isoformat()}
        self.put(row)
        return {"message": "空间文案已保存"}

    async def publish(self, day, now):
        if self.config.diary.publish_mode == "关闭":
            raise ValueError("请在生活日记配置中选择手动发布或自动发布")
        row = self.store.get("diary_publication", day)
        if row["status"] == "已发布":
            return row
        self.editable(day)
        if row["status"] != "草稿" or not row.get("content"):
            raise ValueError("请先生成或保存空间文案")
        # 发布前先占用这篇草稿，页面按钮与自动日程共用同一个发布状态。
        row.update(status="发布中", error="", visibility=self.config.diary.publish_visibility)
        self.put(row)
        sent = False
        try:
            await self.check_connection()
            visibility = {"所有人": 1, "好友": 4, "仅自己": 64}[row["visibility"]]
            sent = True
            result = await self.ctx.call_capability("api.call", api_name="adapter.snowluma.qzone.send_qzone_msg", version="1", timeout_ms=65000,
                                                    args={"params": {"content": row["content"], "ugc_right": visibility}})
            if result.get("status") != "ok" or result.get("retcode") != 0:
                sent = False
                raise RuntimeError(result.get("error") or result.get("message") or result.get("wording") or "空间发布失败")
            row.update(status="已发布", published_at=datetime.now(now.tzinfo).isoformat(),
                       tid=(result.get("data") or {}).get("tid", ""), account=self.connection["account"])
        except asyncio.CancelledError:
            row.update(status="待核对" if sent else "发布失败", error="发布已中断，请查看 QQ 空间中的实际结果")
            raise
        except Exception as error:
            row.update(status="待核对" if sent else "发布失败", error=(str(error) + "；请在 QQ 空间核对是否已发布" if sent else str(error)))
            raise
        finally:
            self.put(row)
        return row

    def reconcile(self, day, published, now):
        row = self.store.get("diary_publication", day)
        if row["status"] != "待核对":
            raise ValueError("请选择待核对的发布记录")
        row.update(status="已发布" if published else "草稿", error="", checked_at=now.isoformat())
        if published:
            row.update(published_at=now.isoformat(), receipt="用户在 QQ 空间确认已发布")
        self.put(row)
        return {"message": "已记为发布成功" if published else "已恢复草稿，可以重新发布"}

    async def process(self, now):
        if self.config.diary.publish_mode != "日记完成后自动发布":
            return
        for date in (now.date() - timedelta(days=1), now.date()):
            day = date.isoformat()
            diary = self.store.find("diary", day)
            if not diary or diary["status"] != "已生成":
                continue
            plan = self.store.find("plan", diary.get("plan_id", ""))
            if not plan or plan["status"] != "completed":
                continue
            # 昨晚开始、今天写完的日记，也在完成后发布。
            if date != now.date() and self.engine.time(plan["end"]).astimezone(now.tzinfo).date() != now.date():
                continue
            if self.store.find("diary_publication", day):
                continue
            await self.prepare(day, now)
            await self.publish(day, now)
