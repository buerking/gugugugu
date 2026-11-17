"""
本地去重库（SQLite）。

按 (scope_id, item_key) 唯一；正式环境 scope_id = subscription_id。
列名仍为 sub_user_id（兼容旧库），取值已改为订阅 ID。
item_key = source_site:item_id。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Dict, Iterable, List, Optional, Set


_LOCK = threading.Lock()


class SeenDB:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_schema(self) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS seen_items (
                        sub_user_id TEXT NOT NULL,
                        item_key    TEXT NOT NULL,
                        source_site TEXT,
                        item_id     TEXT,
                        keyword     TEXT,
                        title       TEXT,
                        price       INTEGER,
                        first_seen  INTEGER NOT NULL,
                        last_seen   INTEGER NOT NULL,
                        pushed      INTEGER NOT NULL DEFAULT 0,
                        pushed_at   INTEGER,
                        PRIMARY KEY (sub_user_id, item_key)
                    );
                    CREATE INDEX IF NOT EXISTS idx_seen_user_time
                        ON seen_items(sub_user_id, last_seen);
                    CREATE TABLE IF NOT EXISTS watermark (
                        cache_key TEXT PRIMARY KEY,
                        last_success_at INTEGER NOT NULL
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def item_key(item: dict) -> str:
        return f"{item.get('source_site')}:{item.get('id')}"

    def filter_unseen(self, sub_user_id: str, items: List[dict]) -> List[dict]:
        if not items:
            return []
        keys = [self.item_key(it) for it in items]
        known: Set[str] = set()
        with _LOCK:
            conn = self._connect()
            try:
                # 分批 IN，避免超长 SQL
                for i in range(0, len(keys), 400):
                    chunk = keys[i : i + 400]
                    placeholders = ",".join("?" * len(chunk))
                    rows = conn.execute(
                        f"SELECT item_key FROM seen_items "
                        f"WHERE sub_user_id=? AND item_key IN ({placeholders})",
                        [sub_user_id, *chunk],
                    ).fetchall()
                    known.update(r[0] for r in rows)
            finally:
                conn.close()
        return [it for it in items if self.item_key(it) not in known]

    def mark_seen(
        self,
        sub_user_id: str,
        items: Iterable[dict],
        *,
        keyword: str = "",
        pushed: bool = False,
    ) -> None:
        now = int(time.time())
        rows = []
        for it in items:
            key = self.item_key(it)
            if not it.get("id"):
                continue
            rows.append(
                (
                    sub_user_id,
                    key,
                    str(it.get("source_site") or ""),
                    str(it.get("id") or ""),
                    keyword or str(it.get("matched_keyword") or ""),
                    str(it.get("name") or "")[:200],
                    int(it.get("price") or 0),
                    now,
                    now,
                    1 if pushed else 0,
                    now if pushed else None,
                )
            )
        if not rows:
            return
        with _LOCK:
            conn = self._connect()
            try:
                conn.executemany(
                    """
                    INSERT INTO seen_items (
                        sub_user_id, item_key, source_site, item_id, keyword,
                        title, price, first_seen, last_seen, pushed, pushed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(sub_user_id, item_key) DO UPDATE SET
                        last_seen=excluded.last_seen,
                        title=excluded.title,
                        price=excluded.price,
                        pushed=CASE
                            WHEN excluded.pushed=1 THEN 1
                            ELSE seen_items.pushed
                        END,
                        pushed_at=CASE
                            WHEN excluded.pushed=1 THEN excluded.pushed_at
                            ELSE seen_items.pushed_at
                        END
                    """,
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    def get_watermark(self, cache_key: str) -> Optional[int]:
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT last_success_at FROM watermark WHERE cache_key=?",
                    (cache_key,),
                ).fetchone()
                return int(row[0]) if row else None
            finally:
                conn.close()

    def set_watermark(self, cache_key: str, ts: int) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO watermark(cache_key, last_success_at) VALUES(?,?)
                    ON CONFLICT(cache_key) DO UPDATE SET last_success_at=excluded.last_success_at
                    """,
                    (cache_key, int(ts)),
                )
                conn.commit()
            finally:
                conn.close()

    def stats(self) -> Dict[str, int]:
        with _LOCK:
            conn = self._connect()
            try:
                total = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
                pushed = conn.execute(
                    "SELECT COUNT(*) FROM seen_items WHERE pushed=1"
                ).fetchone()[0]
                return {"total": int(total), "pushed": int(pushed)}
            finally:
                conn.close()
