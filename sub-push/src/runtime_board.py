"""
运行看板：每条订阅的处理状态（供 GUI 展示）。
仅内存 + 可选落盘最近一轮摘要；订阅主数据一律来自线上接口。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from src import settings


_lock = threading.RLock()


def _state_path() -> str:
    return os.path.join(settings.APP_ROOT, "data", "runtime_board.json")


class RuntimeBoard:
    def __init__(self) -> None:
        self.round_id: str = ""
        self.round_status: str = "idle"  # idle | running | done | error
        self.round_started_at: float = 0.0
        self.round_finished_at: float = 0.0
        self.round_elapsed_seconds: float = 0.0
        self.round_error: str = ""
        # subscription_id -> row
        self.rows: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _row_from_job(job: Any, prev: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        prev = prev or {}
        delivery = str(getattr(job, "delivery", "backend") or "backend")
        if delivery == "vip":
            mt = "VIP"
        elif job.job_type == "keyword":
            mt = "关键词"
        else:
            mt = "卖家"
        return {
            "subscriptionId": str(getattr(job, "subscription_id", "") or ""),
            "matchType": mt,
            "webId": int(getattr(job, "backend_web_id", 0) or 0),
            "site": str(getattr(job, "web_id", "") or ""),
            "target": (
                str(job.keyword)
                if job.job_type == "keyword"
                else str(job.seller_id)
            ),
            "priceMin": job.price_min if job.job_type == "keyword" else 0,
            "priceMax": job.price_max if job.job_type == "keyword" else 0,
            "startTime": job.created_time or 0,
            "status": "搜索中",
            "rawCount": int(prev.get("rawCount") or 0),
            "freshCount": int(prev.get("freshCount") or 0),
            "reportedCount": int(prev.get("reportedCount") or 0),
            "error": "",
            "_api_by_source": {},
            "_counts_from_this_round": False,
        }

    def begin_round(
        self,
        run_id: str,
        jobs: List[Any],
        *,
        replace: bool = True,
    ) -> None:
        """
        replace=True：整表重建（兼容旧整轮）。
        replace=False：只 upsert 本片/本批任务，保留其它行（切片 + VIP 并行）。
        """
        with _lock:
            old = dict(self.rows)
            self.round_id = run_id
            self.round_status = "running"
            if replace or not self.round_started_at:
                self.round_started_at = time.time()
            self.round_finished_at = 0.0
            self.round_elapsed_seconds = 0.0
            self.round_error = ""
            if replace:
                self.rows = {}
            for job in jobs:
                sid = str(getattr(job, "subscription_id", "") or "")
                if not sid:
                    continue
                prev = old.get(sid) or self.rows.get(sid) or {}
                self.rows[sid] = self._row_from_job(job, prev)

    def note_search_unit(
        self,
        subscription_id: str,
        *,
        source: str,
        api_n: int,
        err: Optional[str] = None,
    ) -> None:
        """搜索单元完成时即时刷新看板（不等整轮 gather 结束）。"""
        sid = str(subscription_id or "")
        src = str(source or "").strip().lower() or "?"
        with _lock:
            row = self.rows.get(sid)
            if not row:
                return
            by = row.setdefault("_api_by_source", {})
            if not isinstance(by, dict):
                by = {}
                row["_api_by_source"] = by
            by[src] = int(api_n or 0)
            if not row.get("_counts_from_this_round"):
                row["_counts_from_this_round"] = True
                row["freshCount"] = 0
                row["reportedCount"] = 0
            row["rawCount"] = int(sum(int(v or 0) for v in by.values()))
            row["status"] = "搜索中"
            if err:
                msg = f"{src}:{err}"[:280]
                prev_err = str(row.get("error") or "").strip()
                row["error"] = f"{prev_err}; {msg}" if prev_err else msg

    def set_job(
        self,
        subscription_id: str,
        *,
        status: Optional[str] = None,
        raw_count: Optional[int] = None,
        fresh_count: Optional[int] = None,
        reported_count: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        sid = str(subscription_id or "")
        with _lock:
            row = self.rows.get(sid)
            if not row:
                return
            if status is not None:
                row["status"] = status
            if raw_count is not None:
                row["rawCount"] = int(raw_count)
                row["_counts_from_this_round"] = True
            if fresh_count is not None:
                row["freshCount"] = int(fresh_count)
            if reported_count is not None:
                row["reportedCount"] = int(reported_count)
            if error is not None:
                row["error"] = str(error)[:300]

    def end_round(
        self,
        *,
        error: str = "",
        only_sids: Optional[List[str]] = None,
        finalize_round: bool = True,
    ) -> None:
        """
        only_sids：只收尾指定订阅（切片/VIP），避免并行任务互相清状态。
        finalize_round=False：更新相关行但不把整轮标成 done。
        """
        sid_set = {str(x) for x in (only_sids or []) if str(x)}
        with _lock:
            if finalize_round:
                self.round_finished_at = time.time()
                if self.round_started_at:
                    self.round_elapsed_seconds = (
                        self.round_finished_at - self.round_started_at
                    )
                self.round_status = "error" if error else "done"
                self.round_error = error or ""
            elif error:
                self.round_error = error
            for sid, row in self.rows.items():
                if sid_set and sid not in sid_set:
                    continue
                if row.get("status") in ("排队中", "搜索中", "待AI", "上报中", "AI审核中"):
                    # 已有更明确终态的不覆盖；仅收尾仍在途的
                    if row.get("status") in ("排队中", "搜索中"):
                        row["status"] = "已结束" if not error else "异常结束"
            self._save_unlocked()

    def snapshot(self) -> Dict[str, Any]:
        with _lock:
            return {
                "round_id": self.round_id,
                "round_status": self.round_status,
                "round_elapsed_seconds": self.round_elapsed_seconds,
                "round_error": self.round_error,
                "rows": [dict(v) for v in self.rows.values()],
            }

    def clear_stats(self) -> None:
        """清空本地面板统计（不删线上订阅）。"""
        with _lock:
            self.round_id = ""
            self.round_status = "idle"
            self.round_elapsed_seconds = 0.0
            self.round_error = ""
            for row in self.rows.values():
                row["status"] = "待运行"
                row["rawCount"] = 0
                row["freshCount"] = 0
                row["reportedCount"] = 0
                row["error"] = ""
            self._save_unlocked()

    def replace_subscription_skeleton(self, jobs: List[Any]) -> None:
        """刷新订阅列表骨架，保留已有统计若同 id。"""
        with _lock:
            old = dict(self.rows)
            self.rows = {}
            for job in jobs:
                sid = str(getattr(job, "subscription_id", "") or "")
                if not sid:
                    continue
                prev = old.get(sid) or {}
                delivery = str(getattr(job, "delivery", "backend") or "backend")
                if delivery == "vip":
                    mt = "VIP"
                elif job.job_type == "keyword":
                    mt = "关键词"
                else:
                    mt = "卖家"
                self.rows[sid] = {
                    "subscriptionId": sid,
                    "matchType": mt,
                    "webId": int(getattr(job, "backend_web_id", 0) or 0),
                    "site": str(getattr(job, "web_id", "") or ""),
                    "target": (
                        str(job.keyword)
                        if job.job_type == "keyword"
                        else str(job.seller_id)
                    ),
                    "priceMin": job.price_min if job.job_type == "keyword" else 0,
                    "priceMax": job.price_max if job.job_type == "keyword" else 0,
                    "startTime": job.created_time or 0,
                    "status": prev.get("status") or "待运行",
                    "rawCount": int(prev.get("rawCount") or 0),
                    "freshCount": int(prev.get("freshCount") or 0),
                    "reportedCount": int(prev.get("reportedCount") or 0),
                    "error": prev.get("error") or "",
                }
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        path = _state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "round_id": self.round_id,
                        "round_status": self.round_status,
                        "round_elapsed_seconds": self.round_elapsed_seconds,
                        "round_error": self.round_error,
                        "rows": list(self.rows.values()),
                        "saved_at": int(time.time()),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception:
            pass

    def load(self) -> None:
        path = _state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with _lock:
                self.round_id = str(data.get("round_id") or "")
                self.round_status = str(data.get("round_status") or "idle")
                self.round_elapsed_seconds = float(data.get("round_elapsed_seconds") or 0)
                self.round_error = str(data.get("round_error") or "")
                self.rows = {}
                for row in data.get("rows") or []:
                    if isinstance(row, dict) and row.get("subscriptionId") is not None:
                        sid = str(row["subscriptionId"])
                        self.rows[sid] = row
        except Exception:
            pass


BOARD = RuntimeBoard()
BOARD.load()
