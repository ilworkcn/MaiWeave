"""使用 SQLite 保存生活业务记录。"""

import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS records (category TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(category, id))")
        self.db.commit()

    def get(self, category: str, record_id: str) -> dict:
        row = self.db.execute("SELECT data FROM records WHERE category=? AND id=?", (category, record_id)).fetchone()
        if row is None:
            raise ValueError(f"找不到{category}记录：{record_id}")
        return json.loads(row[0])

    def find(self, category: str, record_id: str) -> dict | None:
        row = self.db.execute("SELECT data FROM records WHERE category=? AND id=?", (category, record_id)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, category: str, *, omit: tuple[str, ...] = ()) -> list[dict]:
        expression = "json_remove(data," + ",".join("?" for _ in omit) + ")" if omit else "data"
        rows = self.db.execute(f"SELECT {expression} FROM records WHERE category=? ORDER BY rowid",
                               (*("$." + key for key in omit), category)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def page(self, category: str, page: int = 1, size: int = 20, *, omit: tuple[str, ...] = (), time_field: str = "retrieved_at") -> dict:
        total = self.db.execute("SELECT count(*) FROM records WHERE category=?", (category,)).fetchone()[0]
        pages = max(1, (total + size - 1) // size)
        page = min(pages, max(1, page))
        expression = "json_remove(data," + ",".join("?" for _ in omit) + ")" if omit else "data"
        rows = self.db.execute(f"SELECT {expression} FROM records WHERE category=? ORDER BY json_extract(data,?) DESC, rowid DESC LIMIT ? OFFSET ?",
                               (*("$." + key for key in omit), category, "$." + time_field, size, (page - 1) * size)).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "page": page, "pages": pages, "total": total}

    def put(self, category: str, record: dict) -> dict:
        self.db.execute("INSERT INTO records(category,id,data) VALUES(?,?,?) ON CONFLICT(category,id) DO UPDATE SET data=excluded.data", (category, record["id"], json.dumps(record, ensure_ascii=False)))
        return record

    def close(self):
        self.db.close()

    def delete(self, category: str, record_id: str):
        self.db.execute("DELETE FROM records WHERE category=? AND id=?", (category, record_id))
