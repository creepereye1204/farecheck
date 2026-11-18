"""판정 요약 저장. 영수증 이미지와 차량번호는 저장하지 않는다. ARCHITECTURE.md"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone

from app.models import Verdict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    origin_name TEXT,
    dest_name TEXT,
    grade TEXT NOT NULL,
    ratio REAL,
    reason_codes TEXT,
    rule_version TEXT,
    user_lang TEXT
)
"""


class CaseStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def save_case(self, origin_name: str, dest_name: str, verdict: Verdict, user_lang: str) -> str:
        case_id = "c_" + secrets.token_hex(8)
        with self._lock:
            self._conn.execute(
                "INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?)",
                (case_id, datetime.now(timezone.utc).isoformat(), origin_name, dest_name,
                 verdict.grade, verdict.ratio, json.dumps([r.code for r in verdict.reasons]),
                 verdict.rule_version, user_lang),
            )
            self._conn.commit()
        return case_id

    def get_case(self, case_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["reason_codes"] = json.loads(d["reason_codes"] or "[]")
        return d
