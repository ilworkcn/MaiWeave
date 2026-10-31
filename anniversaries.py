"""个人资料与按公历、农历逐年计算的纪念日。"""

from calendar import monthrange
from datetime import date
from uuid import uuid4

from lunar_python import Lunar, LunarMonth, Solar


class Anniversaries:
    def __init__(self, store):
        self.store = store

    def save_profile(self, name, session_id, user_id="", notes="", profile_id="", person_id=None):
        if not name.strip() or not session_id.strip():
            raise ValueError("请填写姓名或称呼，以及所属聊天流 ID")
        previous = self.store.get("profile", profile_id) if profile_id else {}
        record = {"id": profile_id or uuid4().hex, "name": name.strip(), "session_id": session_id.strip(),
                  "user_id": user_id.strip(), "notes": notes.strip(),
                  "person_id": previous.get("person_id", "") if person_id is None else person_id.strip()}
        with self.store.db:
            self.store.put("profile", record)
        return record

    def save_date(self, profile_id, title, calendar, month, day, year=0, leap_month=False, enabled=True, anniversary_id=""):
        self.store.get("profile", profile_id)
        if not title.strip() or calendar not in {"solar", "lunar"}:
            raise ValueError("请填写纪念日名称，并选择公历或农历")
        if not 1 <= month <= 12 or not 1 <= day <= (31 if calendar == "solar" else 30):
            raise ValueError("月份应为 1 至 12；公历日期为 1 至 31，农历为 1 至 30")
        if year != 0 and not 1900 <= year <= 2100:
            raise ValueError("起始年份填 1900 至 2100，未知填 0")
        if calendar == "solar":
            date(year or 2000, month, day)
            leap_month = False
        elif year:
            lunar_month = LunarMonth.fromYm(year, -month if leap_month else month)
            if lunar_month is None or day > lunar_month.getDayCount():
                raise ValueError("所填农历年份中不存在这个日期或闰月")
        if anniversary_id:
            self.store.get("anniversary", anniversary_id)
        record = {"id": anniversary_id or uuid4().hex, "profile_id": profile_id, "title": title.strip(),
                  "calendar": calendar, "month": month, "day": day, "year": year,
                  "leap_month": leap_month, "enabled": enabled}
        with self.store.db:
            self.store.put("anniversary", record)
        return record

    @staticmethod
    def occurrence(record, year):
        month, day = record["month"], record["day"]
        if record["calendar"] == "solar":
            return date(year, month, min(day, monthrange(year, month)[1]))
        lunar_month = LunarMonth.fromYm(year, -month if record["leap_month"] else month)
        if lunar_month is None:
            lunar_month = LunarMonth.fromYm(year, month)
        solar = Lunar.fromYmd(year, lunar_month.getMonth(), min(day, lunar_month.getDayCount())).getSolar()
        return date(solar.getYear(), solar.getMonth(), solar.getDay())

    def upcoming(self, today, session=None):
        profiles = {p["id"]: p for p in self.store.all("profile")}
        lunar_year = Solar.fromYmd(today.year, today.month, today.day).getLunar().getYear()
        result = []
        for item in self.store.all("anniversary"):
            profile = profiles[item["profile_id"]]
            if session is not None and profile["session_id"] != session:
                continue
            year = max(item["year"], today.year if item["calendar"] == "solar" else lunar_year)
            occurrence = self.occurrence(item, year)
            if occurrence < today:
                year += 1
                occurrence = self.occurrence(item, year)
            result.append({**item, "person": profile["name"], "session_id": profile["session_id"],
                           "calendar_label": "公历" if item["calendar"] == "solar" else "农历",
                           "date_label": f"{'闰' if item['leap_month'] else ''}{item['month']}月{item['day']}日",
                           "enabled_label": "提醒开启" if item["enabled"] else "提醒关闭",
                           "next_date": occurrence.isoformat(), "days_left": (occurrence - today).days,
                           "years": year - item["year"] if item["year"] else None})
        return sorted(result, key=lambda row: row["next_date"])

    def delete(self, kind, record_id):
        if kind not in {"profile", "anniversary"}:
            raise ValueError("请选择个人资料或纪念日")
        self.store.get(kind, record_id)
        with self.store.db:
            if kind == "profile":
                for item in self.store.all("anniversary"):
                    if item["profile_id"] == record_id:
                        self.store.delete("anniversary", item["id"])
            self.store.delete(kind, record_id)
        return {"success": True}
