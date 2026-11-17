"""
订阅中间层：

- 订阅接口刷新公共缓存（含续期后的生效列表）
- 本地按 ttl_days（默认 7 天）过滤过期项
- 可选把 VIP 大客户与公共混编，均匀散入整表，再按 slice_size 切片
  （煤炉多、闲置少时，片内搜索引擎还会再做 paypay/other 交错）
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from src import settings
from src.subscriptions_index import SearchJob, jobs_from_backend_rows


logger = logging.getLogger(__name__)


def _cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "MIDDLE_LAYER", None) or {})


def _persist_path() -> str:
    raw = str(_cfg().get("persist_path") or "data/subscription_middle.json").strip()
    if os.path.isabs(raw):
        return raw
    return os.path.join(settings.APP_ROOT, raw)


def mix_jobs_evenly(
    public: List[SearchJob], vip: List[SearchJob]
) -> List[SearchJob]:
    """
    把 VIP 均匀插入公共序列，避免 VIP（雅虎占比高）扎堆成一段纯串行。
    公共远多于 VIP 时（煤炉几倍闲置），VIP 会稀疏散在整纪元各片中。
    """
    if not vip:
        return list(public)
    if not public:
        return list(vip)
    n_p, n_v = len(public), len(vip)
    out: List[SearchJob] = []
    pi = 0
    # 用 (vi+1)/(n_v+1) 把 VIP 插在公共序列中段，避免 VIP 总顶在队首
    for vi in range(n_v):
        target = int((vi + 1) * n_p / (n_v + 1))
        while pi < target and pi < n_p:
            out.append(public[pi])
            pi += 1
        out.append(vip[vi])
    while pi < n_p:
        out.append(public[pi])
        pi += 1
    return out


class SubscriptionMiddle:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.rows: List[Dict[str, Any]] = []
        self.public_jobs: List[SearchJob] = []
        self.vip_jobs: List[SearchJob] = []
        self.jobs: List[SearchJob] = []
        self.slices: List[List[SearchJob]] = []
        self.slice_index: int = 0
        self.epoch_id: int = 0
        self.refreshed_at: float = 0.0
        self.last_error: str = ""
        self._load_persist()

    def enabled(self) -> bool:
        return bool(_cfg().get("enabled", True))

    def mix_vip(self) -> bool:
        return bool(_cfg().get("mix_vip", True))

    def slice_size(self) -> int:
        return max(1, int(_cfg().get("slice_size") or 80))

    def ttl_days(self) -> float:
        try:
            return max(0.0, float(_cfg().get("ttl_days") or 7))
        except Exception:
            return 7.0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "jobs": len(self.jobs),
                "public_jobs": len(self.public_jobs),
                "vip_jobs": len(self.vip_jobs),
                "mix_vip": self.mix_vip(),
                "slices": len(self.slices),
                "slice_index": self.slice_index,
                "epoch_id": self.epoch_id,
                "refreshed_at": self.refreshed_at,
                "slice_size": self.slice_size(),
                "ttl_days": self.ttl_days(),
                "last_error": self.last_error,
            }

    def _filter_ttl(self, jobs: List[SearchJob]) -> List[SearchJob]:
        ttl = self.ttl_days()
        if ttl <= 0:
            return list(jobs)
        now = time.time()
        keep: List[SearchJob] = []
        dropped = 0
        for job in jobs:
            try:
                start = float(job.created_time or 0)
            except Exception:
                start = 0.0
            # 无 startTime 时保留（交给后端列表权威性）；有则按 7 天窗过滤
            if start > 0 and (start + ttl * 86400) < now:
                dropped += 1
                continue
            keep.append(job)
        if dropped:
            logger.info("中间层 TTL 过滤掉过期订阅 %s 条（ttl_days=%s）", dropped, ttl)
        return keep

    def _load_vip_jobs(self) -> List[SearchJob]:
        if not self.mix_vip():
            return []
        try:
            from src.vip.loader import load_vip_jobs

            return list(load_vip_jobs() or [])
        except Exception as exc:
            logger.warning("中间层加载 VIP 失败: %s", exc)
            return []

    def _rebuild_slices_unlocked(self) -> None:
        size = self.slice_size()
        jobs = list(self.jobs)
        self.slices = [jobs[i : i + size] for i in range(0, len(jobs), size)] if jobs else []
        self.slice_index = 0

    def _apply_jobs_unlocked(
        self, public: List[SearchJob], vip: List[SearchJob]
    ) -> None:
        self.public_jobs = list(public)
        self.vip_jobs = list(vip)
        if self.mix_vip() and vip:
            self.jobs = mix_jobs_evenly(public, vip)
        else:
            self.jobs = list(public)
        self._rebuild_slices_unlocked()

    def refresh_from_api(self) -> Dict[str, Any]:
        """拉取订阅接口，混编 VIP 后整表替换中间层并重新切片。"""
        # 与 VIP AI 的 use_big_src 互斥，避免 import 到 big 的 src.pipeline
        from src.vip.big_bridge import hold_subpush_src

        with hold_subpush_src():
            from src.pipeline import jpkix_api

            rows = jpkix_api.fetch_all_subscriptions()
        public = jobs_from_backend_rows(rows)
        for j in public:
            j.delivery = "backend"
        public = self._filter_ttl(public)
        vip = self._load_vip_jobs()
        with self._lock:
            self.rows = [dict(r) for r in rows if isinstance(r, dict)]
            self.epoch_id += 1
            self.refreshed_at = time.time()
            self.last_error = ""
            self._apply_jobs_unlocked(public, vip)
            meta = {
                "epoch_id": self.epoch_id,
                "jobs": len(self.jobs),
                "public_jobs": len(self.public_jobs),
                "vip_jobs": len(self.vip_jobs),
                "mix_vip": self.mix_vip(),
                "slices": len(self.slices),
                "slice_size": self.slice_size(),
                "raw_rows": len(self.rows),
            }
            self._save_persist_unlocked()
        logger.info(
            "中间层已刷新 epoch=%s public=%s vip=%s mixed=%s slices=%s size=%s",
            meta["epoch_id"],
            meta["public_jobs"],
            meta["vip_jobs"],
            meta["jobs"],
            meta["slices"],
            meta["slice_size"],
            extra={"gui_console": True},
        )
        return meta

    def ensure_ready(self) -> Dict[str, Any]:
        with self._lock:
            empty = not self.jobs
        if empty:
            return self.refresh_from_api()
        return self.stats()

    def peek_slice(self) -> Optional[Tuple[int, int, List[SearchJob]]]:
        """返回 (slice_index, slice_total, jobs)；无更多片时返回 None。"""
        with self._lock:
            total = len(self.slices)
            if total <= 0 or self.slice_index >= total:
                return None
            idx = self.slice_index
            return idx, total, list(self.slices[idx])

    def advance_slice(self) -> None:
        with self._lock:
            self.slice_index += 1
            self._save_persist_unlocked()

    def slices_exhausted(self) -> bool:
        with self._lock:
            return not self.slices or self.slice_index >= len(self.slices)

    def all_jobs(self) -> List[SearchJob]:
        with self._lock:
            return list(self.jobs)

    def public_only_jobs(self) -> List[SearchJob]:
        with self._lock:
            return list(self.public_jobs)

    def _save_persist_unlocked(self) -> None:
        path = _persist_path()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            payload = {
                "epoch_id": self.epoch_id,
                "slice_index": self.slice_index,
                "refreshed_at": self.refreshed_at,
                "rows": self.rows,
                "saved_at": int(time.time()),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception as exc:
            logger.debug("中间层落盘失败: %s", exc)

    def _load_persist(self) -> None:
        path = _persist_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
            public = jobs_from_backend_rows(rows)
            for j in public:
                j.delivery = "backend"
            public = self._filter_ttl(public)
            vip = self._load_vip_jobs()
            with self._lock:
                self.rows = rows
                self.epoch_id = int(data.get("epoch_id") or 0)
                self.refreshed_at = float(data.get("refreshed_at") or 0)
                self._apply_jobs_unlocked(public, vip)
                # 重启后从第 0 片重扫更安全，避免半片水位不一致
                self.slice_index = 0
            logger.info(
                "中间层已从落盘恢复 public=%s vip=%s mixed=%s epoch=%s（切片进度重置为 0）",
                len(public),
                len(vip),
                len(self.jobs),
                self.epoch_id,
            )
        except Exception as exc:
            logger.warning("中间层落盘恢复失败: %s", exc)


_MIDDLE: Optional[SubscriptionMiddle] = None
_MIDDLE_LOCK = threading.Lock()


def get_middle() -> SubscriptionMiddle:
    global _MIDDLE
    with _MIDDLE_LOCK:
        if _MIDDLE is None:
            _MIDDLE = SubscriptionMiddle()
        return _MIDDLE
