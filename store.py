"""只存储生活业务记录，不实现通用记忆检索库。"""

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

    def all(self, category: str) -> list[dict]:
        rows = self.db.execute("SELECT data FROM records WHERE category=? ORDER BY rowid", (category,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put(self, category: str, record: dict) -> dict:
        self.db.execute("INSERT INTO records(category,id,data) VALUES(?,?,?) ON CONFLICT(category,id) DO UPDATE SET data=excluded.data", (category, record["id"], json.dumps(record, ensure_ascii=False)))
        return record

    def close(self):
        self.db.close()
