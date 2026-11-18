"""
统一调度：

1) 订阅接口刷新中间层（公共 + VIP 混编）
2) 按片并发搜索：公共推后端，VIP 新货进 AI 异步队列
3) 全部切片跑完 → 再次刷新中间层 → 重新切片

VIP 不再单独串行搜索轮，避免雅虎闲置扎堆抢 SEM。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from src.pipeline import storage
from src.pipeline.search_engine import run_search_round
from src.runtime_board import BOARD
from src.subscription_middle import get_middle
from src.vip.runner import VipAiQueue


logger = logging.getLogger(__name__)


def run_public_slice(
    *,
    first_run: bool = False,
    should_stop: Optional[Callable[[], bool]] = None,
    vip_ai: Optional[VipAiQueue] = None,
) -> Dict[str, Any]:
    """处理中间层下一片（可含混编 VIP）；若切片耗尽则先刷新再取片。"""
    storage.ensure_dirs()
    middle = get_middle()
    t0 = time.time()

    if not middle.enabled():
        summary = run_search_round(first_run=first_run, board_replace=True)
        _enqueue_vip_pending(summary, vip_ai=vip_ai, first_run=first_run)
        summary["cycle_elapsed_seconds"] = round(time.time() - t0, 2)
        summary["mode"] = "legacy_full"
        return summary

    if middle.slices_exhausted():
        if should_stop and should_stop():
            return {"ok": False, "skipped": True, "reason": "stopped"}
        refresh_meta = middle.refresh_from_api()
        # GUI 只展示公共；骨架仍可带 VIP 行（渲染时过滤）
        BOARD.replace_subscription_skeleton(middle.public_only_jobs())
        logger.info(
            "纪元刷新 epoch=%s public=%s vip=%s mixed=%s slices=%s",
            refresh_meta.get("epoch_id"),
            refresh_meta.get("public_jobs"),
            refresh_meta.get("vip_jobs"),
            refresh_meta.get("jobs"),
            refresh_meta.get("slices"),
            extra={"gui_console": True},
        )

    peeked = middle.peek_slice()
    if peeked is None:
        logger.warning("中间层无订阅，稍后重试")
        return {
            "ok": True,
            "skipped": True,
            "reason": "empty_middle",
            "cycle_elapsed_seconds": round(time.time() - t0, 2),
        }

    slice_idx, slice_total, jobs = peeked
    stats = middle.stats()
    epoch_id = int(stats.get("epoch_id") or 0)
    n_vip = sum(
        1
        for j in jobs
        if str(getattr(j, "delivery", "") or "") == "vip"
    )
    run_id = f"e{epoch_id}-s{slice_idx + 1}/{slice_total}-{int(time.time())}"

    logger.info(
        "切片开始 epoch=%s slice=%s/%s jobs=%s vip_in_slice=%s first_run=%s",
        epoch_id,
        slice_idx + 1,
        slice_total,
        len(jobs),
        n_vip,
        first_run,
        extra={"gui_console": True},
    )

    summary = run_search_round(
        first_run=first_run,
        jobs=jobs,
        ingest_meta={
            "format": "middle_slice",
            "epoch_id": epoch_id,
            "slice_index": slice_idx,
            "slice_total": slice_total,
            "subscription_count": len(jobs),
            "vip_in_slice": n_vip,
        },
        run_id=run_id,
        board_replace=False,
        finalize_board=False,
    )
    _enqueue_vip_pending(summary, vip_ai=vip_ai, first_run=first_run)
    middle.advance_slice()

    epoch_done = middle.slices_exhausted()
    if epoch_done:
        BOARD.end_round(finalize_round=True)
        logger.info(
            "纪元完成 epoch=%s slices=%s，下轮将重新拉订阅+混编 VIP",
            epoch_id,
            slice_total,
            extra={"gui_console": True},
        )

    elapsed = time.time() - t0
    summary["cycle_elapsed_seconds"] = round(elapsed, 2)
    summary["mode"] = "middle_slice"
    summary["epoch_id"] = epoch_id
    summary["slice_index"] = slice_idx
    summary["slice_total"] = slice_total
    summary["epoch_done"] = epoch_done
    summary["vip_in_slice"] = n_vip

    logger.info(
        "切片耗时=%.1fs epoch=%s slice=%s/%s jobs=%s vip=%s fresh=%s vip_pending=%s",
        elapsed,
        epoch_id,
        slice_idx + 1,
        slice_total,
        summary.get("jobs"),
        n_vip,
        summary.get("fresh_items"),
        summary.get("vip_pending_count"),
        extra={"gui_console": True},
    )
    return summary


def _enqueue_vip_pending(
    summary: Dict[str, Any],
    *,
    vip_ai: Optional[VipAiQueue],
    first_run: bool,
) -> None:
    pending: List[Dict[str, Any]] = list(summary.pop("vip_pending", None) or [])
    summary["vip_pending_count"] = len(pending)
    if not pending or first_run or vip_ai is None:
        return
    vip_ai.put(pending, first_run=False)


def run_cycle(
    *,
    first_run: bool = False,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """兼容旧名：跑中间层一片。"""
    return run_public_slice(first_run=first_run, should_stop=should_stop)


class UnifiedRunner:
    """中间层切片循环（公共+VIP 混编）；VIP AI 异步。"""

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self._thread: Optional[threading.Thread] = None
        self.running = False
        self.paused = False
        self.total_rounds = 0
        self.total_failures = 0
        self.last_error = ""
        self.last_success_at = 0.0
        self.last_round_seconds = 0.0
        self.started_at = 0.0
        self._public_baseline_done = False

        self.vip_ai = VipAiQueue(log=self.log)
        self._failover_hb = None

    def start(self, *, run_baseline: bool = True) -> None:
        if self._thread and self._thread.is_alive():
            if self.paused:
                self.resume()
            return
        self._stop.clear()
        self._pause.set()
        self.running = True
        self.paused = False
        self.started_at = time.time()
        self.vip_ai.start()
        try:
            from src.failover.primary import PrimaryHeartbeat

            self._failover_hb = PrimaryHeartbeat(log=self.log)
            self._failover_hb.start()
        except Exception as exc:
            self.log.warning("灾备心跳启动失败（忽略）: %s", exc)
        self._thread = threading.Thread(
            target=self._loop, args=(run_baseline,), daemon=True
        )
        self._thread.start()
        self.log.warning("统一守护已启动（中间层混编 VIP → 切片搜索；VIP AI 异步）")

    def pause(self) -> None:
        self._pause.clear()
        self.paused = True
        self.log.warning("统一守护已暂停")

    def resume(self) -> None:
        self._pause.set()
        self.paused = False
        self.log.warning("统一守护已继续")

    def stop(self) -> None:
        self._stop.set()
        self._pause.set()
        self.running = False
        self.paused = False
        try:
            if self._failover_hb is not None:
                self._failover_hb.stop()
        except Exception:
            pass
        self._failover_hb = None
        try:
            self.vip_ai.stop()
        except Exception:
            pass
        self.log.warning("统一守护已停止")

    def status_text(self) -> str:
        if not self.running:
            state = "未启动"
        elif self.paused:
            state = "已暂停"
        else:
            state = "运行中"
        last_ok = "-"
        if self.last_success_at:
            last_ok = time.strftime("%H:%M:%S", time.localtime(self.last_success_at))
        mid = {}
        try:
            mid = get_middle().stats()
        except Exception:
            mid = {}
        return (
            f"状态={state} | 片次={self.total_rounds} | 失败={self.total_failures} "
            f"| 上片耗时={self.last_round_seconds:.1f}s | 上次成功={last_ok} "
            f"| 中间层={mid.get('jobs', '-')}条"
            f"(公{mid.get('public_jobs', '-')}+VIP{mid.get('vip_jobs', '-')})"
            f"/{mid.get('slices', '-')}片 "
            f"进度={mid.get('slice_index', '-')}/{mid.get('slices', '-')} "
            f"| VIP-AI={getattr(self.vip_ai, 'processed', 0)} "
            f"| 错={(self.last_error or '-')[:60]}"
        )

    def _loop(self, run_baseline: bool) -> None:
        try:
            middle = get_middle()
            if middle.enabled():
                middle.refresh_from_api()
                BOARD.replace_subscription_skeleton(middle.public_only_jobs())
        except Exception as exc:
            self.log.exception("启动刷新中间层失败: %s", exc)

        while not self._stop.is_set():
            self._pause.wait(0.2)
            if self._stop.is_set():
                break
            if self.paused:
                time.sleep(0.3)
                continue

            first_run = bool(run_baseline) and not self._public_baseline_done
            t0 = time.time()
            try:
                summary = run_public_slice(
                    first_run=first_run,
                    should_stop=lambda: self._stop.is_set() or not self._pause.is_set(),
                    vip_ai=self.vip_ai,
                )
                self.last_round_seconds = time.time() - t0
                self.last_success_at = time.time()
                self.last_error = ""
                self.total_rounds += 1
                if first_run and summary.get("epoch_done"):
                    self._public_baseline_done = True
                    self.log.info("基线纪元完成，后续切片进入增量推送/VIP AI")

                if summary.get("reason") == "empty_middle":
                    end = time.time() + 15
                    while time.time() < end and not self._stop.is_set():
                        time.sleep(0.3)
            except Exception as exc:
                self.total_failures += 1
                self.last_error = str(exc)[:500]
                self.last_round_seconds = time.time() - t0
                self.log.exception("切片失败: %s", exc)
                end = time.time() + 15
                while time.time() < end and not self._stop.is_set():
                    time.sleep(0.3)

        self.running = False
