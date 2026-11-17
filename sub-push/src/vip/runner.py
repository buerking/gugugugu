"""
大客户 VIP：

- 搜索已混编进中间层切片（与公共同池，利于煤炉填闲置空隙）
- 本模块只保留 AI+邮件异步队列
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from src.runtime_board import BOARD
from src.vip.deliver import deliver_vip_pending


logger = logging.getLogger(__name__)


class VipAiQueue:
    """单消费者异步队列：串行跑 AI+推送，避免邮件/DeepSeek 并发踩踏。"""

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger
        self._q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.processed = 0
        self.failures = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="vip-ai-queue", daemon=True
        )
        self._thread.start()
        self.log.info("VIP AI 异步队列已启动")

    def stop(self) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except Exception:
            pass

    def put(self, pending: List[Dict[str, Any]], *, first_run: bool = False) -> None:
        if first_run or not pending:
            return
        self._q.put({"pending": list(pending), "enqueued_at": time.time()})

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            pending = list(item.get("pending") or [])
            try:
                deliver_vip_pending(pending, first_run=False)
                self.processed += 1
            except Exception as exc:
                self.failures += 1
                self.log.exception("VIP AI 队列任务失败: %s", exc)
                try:
                    sids = [
                        str(row.get("board_sid") or "")
                        for row in pending
                        if row.get("board_sid")
                    ]
                    BOARD.end_round(
                        error=str(exc),
                        only_sids=sids or None,
                        finalize_round=False,
                    )
                except Exception:
                    pass
