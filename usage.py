"""记录生活模型调用，汇总用量和耗时。"""

import asyncio
import json
from datetime import datetime, timedelta
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo


async def _once(ctx, store, config, feature, prompt):
    started = perf_counter()
    record = {"id": uuid4().hex, "at": datetime.now(ZoneInfo(config.life.timezone)).isoformat(),
              "feature": feature, "task": config.life.model_task, "model": "", "status": "失败", "error": "",
              "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
              "prompt_cache_hit_tokens": None}
    try:
        result = await asyncio.wait_for(ctx.llm.generate(prompt, task_name=config.life.model_task), timeout=90)
        record.update(model=result.get("model") or result.get("model_name", ""),
                      status="成功" if result["success"] else "失败", error=result.get("error", ""))
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens"):
            record[key] = result.get(key)
        if result["success"] and result.get("response") and not any(
                record[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens"):
                record[key] = None
        return result
    except Exception as error:
        record["error"] = str(error)
        raise
    finally:
        record["seconds"] = round(perf_counter() - started, 3)
        with store.db:
            store.put("model_usage", record)


async def generate(ctx, store, config, feature, prompt, model=None, validate=None):
    for attempt in range(3):
        try:
            result = await _once(ctx, store, config, feature, prompt)
            if not result["success"]:
                raise RuntimeError(result.get("error") or "模型请求失败")
            text = result["response"].strip()
            if not text:
                raise ValueError("模型返回了空内容")
            if model:
                if text.startswith("```"):
                    text = "\n".join(text.splitlines()[1:-1])
                value = model.model_validate_json(text)
                if validate:
                    validate(value)
                return value
            return result
        except (TimeoutError, ConnectionError, OSError, RuntimeError, ValueError) as error:
            if attempt == 2:
                raise
            ctx.logger.warning("%s第%s次调用失败，将重试：%s", feature, attempt + 1, error)
            if isinstance(error, ValueError):
                prompt += "\n请重新输出，修正上次结果的问题：" + str(error)
            await asyncio.sleep(2 ** attempt)


def summary(store, now, days=7, feature=""):
    if days not in {1, 7, 30}:
        raise ValueError("请选择今天、近 7 天或近 30 天")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    rows = [r for r in store.all("model_usage") if start <= datetime.fromisoformat(r["at"]) <= now
            and (not feature or r["feature"] == feature)]

    # 已有记录中的全零成功调用来自未提供用量的模型服务。
    rows = [{**r, "total_tokens": None} if r["status"] == "成功" and not any(
        r.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")) else r for r in rows]

    def totals(items):
        known = [r for r in items if r["total_tokens"] is not None]
        return {"calls": len(items), "known_calls": len(known), "missing_calls": len(items) - len(known),
                "input": sum(r["prompt_tokens"] or 0 for r in items),
                "output": sum(r["completion_tokens"] or 0 for r in items),
                "total": sum(r["total_tokens"] for r in known),
                "cached": sum(r["prompt_cache_hit_tokens"] or 0 for r in items),
                "success_rate": round(100 * sum(r["status"] == "成功" for r in items) / len(items), 1) if items else 0,
                "seconds": round(sum(r["seconds"] for r in items) / len(items), 2) if items else 0}

    features = [{"feature": name, **totals([r for r in rows if r["feature"] == name])} for name in sorted({r["feature"] for r in rows})]
    daily = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        daily.append({"day": day, **totals([r for r in rows if r["at"][:10] == day])})
    recent = [{**r, "at": r["at"].replace("T", " ")[:19],
               "usage_label": str(r["total_tokens"]) if r["total_tokens"] is not None else "用量未返回"}
              for r in sorted(rows, key=lambda r: r["at"], reverse=True)[:100]]
    return {"totals": totals(rows), "features": features, "daily": daily, "recent": recent}
