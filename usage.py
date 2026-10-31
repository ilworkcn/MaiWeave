"""记录生活模型调用，汇总用量和耗时。"""

from datetime import datetime, timedelta
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo


async def generate(ctx, store, config, feature, prompt):
    started = perf_counter()
    record = {"id": uuid4().hex, "at": datetime.now(ZoneInfo(config.life.timezone)).isoformat(),
              "feature": feature, "task": config.life.model_task, "model": "", "status": "失败", "error": "",
              "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
              "prompt_cache_hit_tokens": None}
    try:
        result = await ctx.llm.generate(prompt, task_name=config.life.model_task)
        record.update(model=result.get("model") or result.get("model_name", ""),
                      status="成功" if result["success"] else "失败", error=result.get("error", ""))
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens"):
            record[key] = result.get(key)
        return result
    except Exception as error:
        record["error"] = str(error)
        raise
    finally:
        record["seconds"] = round(perf_counter() - started, 3)
        with store.db:
            store.put("model_usage", record)


def summary(store, now, days=7, feature=""):
    if days not in {1, 7, 30}:
        raise ValueError("请选择今天、近 7 天或近 30 天")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    rows = [r for r in store.all("model_usage") if start <= datetime.fromisoformat(r["at"]) <= now
            and (not feature or r["feature"] == feature)]

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
